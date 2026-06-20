from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import uuid4
import re


@dataclass
class WorkflowRunRecord:
    run_id: str
    task_id: str
    thread_id: str
    request_id: str | None
    backend: str
    status: str
    started_at: str
    finished_at: str | None = None
    fallback_used: bool = False
    error_stage: str | None = None
    error_message: str | None = None
    traceback: str | None = None
    stage_durations_ms: dict[str, int] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class WorkflowCheckpointRecord:
    checkpoint_id: str
    observable_checkpoint_id: str
    run_id: str
    task_id: str
    kind: str
    stage: str | None
    thread_id: str
    request_id: str | None
    status: str
    created_at: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowRunSummary:
    run_id: str
    task_id: str
    thread_id: str
    request_id: str | None
    backend: str
    status: str
    started_at: str
    finished_at: str | None
    fallback_used: bool
    error_stage: str | None
    error_message: str | None
    stage_durations_ms: dict[str, int]


_lock = RLock()
_runs: dict[str, WorkflowRunRecord] = {}
_task_latest_run: dict[str, str] = {}
_task_run_history: dict[str, list[str]] = defaultdict(list)
_stage_duration_sum_ms: Counter[str] = Counter()
_stage_duration_count: Counter[str] = Counter()
_stage_failure_count: Counter[str] = Counter()
_run_status_count: Counter[str] = Counter()
_backend_count: Counter[str] = Counter()
_recent_checkpoints: list[WorkflowCheckpointRecord] = []
_checkpoint_total = 0
_checkpoint_kind_count: Counter[str] = Counter()
_MAX_RECENT_CHECKPOINTS = 300


def _slug_token(value: str, default: str = "item", max_len: int = 32) -> str:
    token = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", value.strip().lower()).strip("-")
    token = re.sub(r"-+", "-", token)
    if not token:
        token = default
    return token[:max_len]


def _observable_task_id(task: TaskRecord) -> str:
    request = task.request
    product = _slug_token(request.product_goal, "goal", 24)
    competitor = _slug_token(request.competitors[0], "competitor", 18) if request.competitors else "global"
    stage = _slug_token(task.memory_state.last_stage if task.memory_state and task.memory_state.last_stage else task.status.value, "stage", 18)
    scope = _slug_token(task.id.split("_")[-1], "task", 12)
    return f"task_{product}_{competitor}_{stage}_{scope}"


def _observable_checkpoint_id(task_id: str, kind: str, stage: str | None, checkpoint_id: str) -> str:
    parts = [task_id, kind]
    if stage:
        parts.append(stage)
    parts.append(checkpoint_id.split("_")[-1])
    return "cp_" + "_".join(_slug_token(part, "x", 18) for part in parts)


def _new_run_id() -> str:
    return f"wf_{uuid4().hex[:12]}"


def _new_checkpoint_id() -> str:
    return f"cp_{uuid4().hex[:12]}"


def start_run(
    task_id: str,
    thread_id: str,
    request_id: str | None = None,
    backend: str = "langgraph",
) -> str:
    run_id = _new_run_id()
    record = WorkflowRunRecord(
        run_id=run_id,
        task_id=task_id,
        thread_id=thread_id,
        request_id=request_id,
        backend=backend,
        status="running",
        started_at=_now_iso(),
    )
    with _lock:
        _runs[run_id] = record
        _task_latest_run[task_id] = run_id
        _task_run_history[task_id].append(run_id)
        _run_status_count["running"] += 1
        _backend_count[backend] += 1
        record.events.append(
            {
                "kind": "run_start",
                "timestamp": _now_iso(),
                "thread_id": thread_id,
                "request_id": request_id,
                "backend": backend,
            }
        )
    return run_id


def record_checkpoint(
    task_id: str,
    kind: str,
    *,
    stage: str | None = None,
    status: str = "ok",
    payload: dict[str, Any] | None = None,
) -> str | None:
    with _lock:
        run_id = _task_latest_run.get(task_id)
        if run_id is None:
            return None
        record = _runs.get(run_id)
        if record is None:
            return None
        checkpoint_id = _new_checkpoint_id()
        checkpoint = WorkflowCheckpointRecord(
            checkpoint_id=checkpoint_id,
            observable_checkpoint_id=_observable_checkpoint_id(task_id, kind, stage, checkpoint_id),
            run_id=run_id,
            task_id=task_id,
            kind=kind,
            stage=stage,
            thread_id=record.thread_id,
            request_id=record.request_id,
            status=status,
            created_at=_now_iso(),
            payload=deepcopy(payload or {}),
        )
        checkpoint_payload = asdict(checkpoint)
        record.checkpoints.append(checkpoint_payload)
        record.events.append(
            {
                "kind": "checkpoint",
                "checkpoint_id": checkpoint.checkpoint_id,
                "checkpoint_kind": kind,
                "stage": stage,
                "status": status,
                "timestamp": checkpoint.created_at,
            }
        )
        _recent_checkpoints.append(checkpoint)
        global _checkpoint_total
        _checkpoint_total += 1
        _checkpoint_kind_count[kind] += 1
        if len(_recent_checkpoints) > _MAX_RECENT_CHECKPOINTS:
            del _recent_checkpoints[: len(_recent_checkpoints) - _MAX_RECENT_CHECKPOINTS]
        return checkpoint.checkpoint_id


def record_stage(task_id: str, stage: str, duration_ms: int, status: str = "ok") -> None:
    with _lock:
        run_id = _task_latest_run.get(task_id)
        if run_id is None:
            return
        record = _runs.get(run_id)
        if record is None:
            return
        duration = max(0, int(duration_ms))
        record.stage_durations_ms[stage] = duration
        record.events.append(
            {
                "kind": "stage",
                "stage": stage,
                "status": status,
                "duration_ms": duration,
                "timestamp": _now_iso(),
            }
        )
        _stage_duration_sum_ms[stage] += duration
        _stage_duration_count[stage] += 1
        if status != "ok":
            _stage_failure_count[stage] += 1


def record_event(task_id: str, stage: str, message: str, status: str, extra: dict[str, Any] | None = None) -> None:
    with _lock:
        run_id = _task_latest_run.get(task_id)
        if run_id is None:
            return
        record = _runs.get(run_id)
        if record is None:
            return
        payload: dict[str, Any] = {
            "kind": "event",
            "stage": stage,
            "message": message,
            "status": status,
            "timestamp": _now_iso(),
        }
        if extra:
            payload["extra"] = deepcopy(extra)
        record.events.append(payload)


def finish_run(
    task_id: str,
    status: str,
    *,
    fallback_used: bool = False,
    error_stage: str | None = None,
    error_message: str | None = None,
    traceback: str | None = None,
) -> None:
    with _lock:
        run_id = _task_latest_run.get(task_id)
        if run_id is None:
            return
        record = _runs.get(run_id)
        if record is None:
            return
        previous_status = record.status
        if previous_status in _run_status_count:
            _run_status_count[previous_status] = max(0, _run_status_count[previous_status] - 1)
        record.status = status
        record.finished_at = _now_iso()
        record.fallback_used = fallback_used
        record.error_stage = error_stage
        record.error_message = error_message
        record.traceback = traceback
        _run_status_count[status] += 1
        record.events.append(
            {
                "kind": "run_finish",
                "status": status,
                "fallback_used": fallback_used,
                "error_stage": error_stage,
                "error_message": error_message,
                "timestamp": _now_iso(),
            }
        )


def get_task_snapshot(task_id: str) -> dict[str, Any]:
    with _lock:
        run_ids = list(_task_run_history.get(task_id, []))
        latest_run = _runs.get(run_ids[-1]) if run_ids else None
        if latest_run is None:
            return {
                "task_id": task_id,
                "latest_run": None,
                "run_history": [],
                "recent_checkpoints": [],
                "metrics": _task_metrics(),
            }
        return {
            "task_id": task_id,
            "latest_run": asdict(latest_run),
            "run_history": [asdict(_runs[run_id]) for run_id in run_ids if run_id in _runs],
            "recent_checkpoints": [asdict(item) for item in _recent_checkpoints if item.task_id == task_id],
            "metrics": _task_metrics(),
        }


def get_runtime_snapshot() -> dict[str, Any]:
    with _lock:
        return {
            "active_runs": sum(1 for record in _runs.values() if record.status == "running"),
            "run_status_count": dict(_run_status_count),
            "backend_count": dict(_backend_count),
            "stage_duration_sum_ms": dict(_stage_duration_sum_ms),
            "stage_duration_count": dict(_stage_duration_count),
            "stage_failure_count": dict(_stage_failure_count),
            "total_runs": len(_runs),
            "total_checkpoints": _checkpoint_total,
            "checkpoint_kind_count": dict(_checkpoint_kind_count),
            "latest_task_ids": list(_task_latest_run.keys())[-20:],
        }


def get_recent_checkpoints(task_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    with _lock:
        items = [item for item in _recent_checkpoints if task_id is None or item.task_id == task_id]
        return [asdict(item) for item in items[-limit:]]


def _task_metrics() -> dict[str, Any]:
    return {
        "run_count": len(_runs),
        "active_runs": sum(1 for record in _runs.values() if record.status == "running"),
        "completed_runs": _run_status_count.get("completed", 0),
        "failed_runs": _run_status_count.get("failed", 0),
        "fallback_runs": sum(1 for record in _runs.values() if record.fallback_used),
        "average_stage_duration_ms": {
            stage: (
                round(_stage_duration_sum_ms[stage] / _stage_duration_count[stage], 2)
                if _stage_duration_count[stage]
                else 0
            )
            for stage in _stage_duration_sum_ms
        },
    }


def render_prometheus_metrics() -> str:
    with _lock:
        lines = [
            "# HELP ci_agent_workflow_runs_total Total workflow runs by status.",
            "# TYPE ci_agent_workflow_runs_total counter",
        ]
        for status, value in sorted(_run_status_count.items()):
            lines.append(f'ci_agent_workflow_runs_total{{status="{status}"}} {value}')

        lines.extend([
            "# HELP ci_agent_workflow_active_runs Current active workflow runs.",
            "# TYPE ci_agent_workflow_active_runs gauge",
            f"ci_agent_workflow_active_runs {sum(1 for record in _runs.values() if record.status == 'running')}",
            "# HELP ci_agent_workflow_stage_duration_ms Workflow stage duration sum in milliseconds.",
            "# TYPE ci_agent_workflow_stage_duration_ms counter",
        ])
        for stage, value in sorted(_stage_duration_sum_ms.items()):
            lines.append(f'ci_agent_workflow_stage_duration_ms_sum{{stage="{stage}"}} {value}')
            lines.append(f'ci_agent_workflow_stage_duration_ms_count{{stage="{stage}"}} {_stage_duration_count[stage]}')
            avg = round(value / _stage_duration_count[stage], 2) if _stage_duration_count[stage] else 0
            lines.append(f'ci_agent_workflow_stage_duration_ms_avg{{stage="{stage}"}} {avg}')
        lines.extend([
            "# HELP ci_agent_workflow_stage_failures_total Workflow stage failure count.",
            "# TYPE ci_agent_workflow_stage_failures_total counter",
        ])
        for stage, value in sorted(_stage_failure_count.items()):
            lines.append(f'ci_agent_workflow_stage_failures_total{{stage="{stage}"}} {value}')
        lines.extend([
            "# HELP ci_agent_workflow_checkpoints_total Total workflow checkpoints recorded.",
            "# TYPE ci_agent_workflow_checkpoints_total counter",
            f"ci_agent_workflow_checkpoints_total {_checkpoint_total}",
        ])
        for kind, value in sorted(_checkpoint_kind_count.items()):
            lines.append(f'ci_agent_workflow_checkpoint_kind_total{{kind="{kind}"}} {value}')
        return "\n".join(lines) + "\n"


__all__ = [
    "finish_run",
    "get_recent_checkpoints",
    "get_runtime_snapshot",
    "get_task_snapshot",
    "record_checkpoint",
    "record_event",
    "record_stage",
    "render_prometheus_metrics",
    "start_run",
]