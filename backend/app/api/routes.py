from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse

from app.core.auth import AuthSubject, get_auth_subject
from app.core.config import rate_limit_settings
from app.core.limiter import limiter
from app.core.security import (
    estimate_budget_usage,
    validate_file_upload,
    validate_image_name,
    validate_image_upload,
    validate_public_url,
)
from app.models.schemas import (
    InterventionRequest,
    TaskCreateRequest,
    TaskRecord,
    TaskStatus,
    new_task_id,
)
from app.services.decision_memory import get_all_memory_items, get_task_memory_items, search_decision_memory
from app.services.memory_store import get_memory_store
from app.services.store import task_store
from app.services.workflow_observability import get_recent_checkpoints, get_task_snapshot
from app.worker.workflow import VALID_RERUN_STAGES, rerun_from_stage, run_competitive_intelligence_workflow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

SYNC_MODE_DEFAULT = os.getenv("TASK_SYNC_MODE", "false").lower() == "true"
LOOSE_VALIDATION_DEFAULT = os.getenv("TASK_LOOSE_VALIDATION", "false").lower() == "true"


def _memory_layer_count(layer_view) -> int:
    if hasattr(layer_view, "count"):
        return int(layer_view.count())
    if hasattr(layer_view, "items"):
        return len(layer_view.items)
    return 0


@router.get("/memory/stats")
def get_memory_stats(subject: AuthSubject = Depends(get_auth_subject)) -> dict:
    memory_store = get_memory_store()
    return {
        "total_items": len(get_all_memory_items()),
        "by_type": {
            "working": _memory_layer_count(memory_store.working_memory),
            "short_term": _memory_layer_count(memory_store.short_term_memory),
            "long_term": _memory_layer_count(memory_store.long_term_memory),
        },
        "by_status": {"active": len(get_all_memory_items())},
        "recent_checkpoints": len(get_recent_checkpoints(limit=20)),
        "total_checkpoints": len(get_recent_checkpoints(limit=1000)),
    }




def _resolve_task_reference(task_ref: str) -> TaskRecord | None:
    task = task_store.get(task_ref)
    if task is not None:
        return task

    lowered_ref = task_ref.lower()
    suffix = task_ref.split("_")[-1]
    for candidate in _iter_task_candidates():
        for ref in [candidate.id, candidate.run_id, candidate.thread_id, candidate.request_id]:
            if not ref:
                continue
            lowered = ref.lower()
            if ref == task_ref or ref.endswith(task_ref) or task_ref.endswith(ref):
                return candidate
            if ref.split("_")[-1] == suffix:
                return candidate
            if lowered_ref in lowered or lowered in lowered_ref:
                return candidate
    return None


def _run_workflow_in_thread(task_id: str, request_id: str | None = None) -> None:
    task = task_store.get(task_id)
    if task is None:
        logger.error("Task %s not found in store", task_id)
        return

    try:
        task = run_competitive_intelligence_workflow(task, thread_id=task.id, request_id=request_id)
        task_store.update(task)
    except Exception as exc:
        logger.exception("Workflow failed for task %s", task_id)
        latest_task = task_store.get(task_id)
        if latest_task is not None:
            latest_task.status = TaskStatus.failed
            task_store.append_event(latest_task, "failed", f"任务失败：{exc}", TaskStatus.failed)
            task_store.update(latest_task)


@router.post("/tasks", response_model=TaskRecord)
@limiter.limit(rate_limit_settings.task_create, exempt_when=_skip_options)
def create_task(
    request: Request,
    payload: TaskCreateRequest,
    subject: AuthSubject = Depends(get_auth_subject),
    x_sync_mode: Annotated[str | None, Header(alias="X-Sync-Mode")] = None,
    x_loose_validation: Annotated[str | None, Header(alias="X-Loose-Validation")] = None,
    x_request_id: Annotated[str | None, Header(alias="X-Request-Id")] = None,
) -> TaskRecord:
    sync_mode = (x_sync_mode and x_sync_mode.lower() == "true") or SYNC_MODE_DEFAULT
    loose_validation = (
        (x_loose_validation and x_loose_validation.lower() == "true")
        or LOOSE_VALIDATION_DEFAULT
        or SYNC_MODE_DEFAULT
    )
    request_id = _current_request_id(request, x_request_id)

    if not loose_validation:
        for url in payload.urls:
            validate_public_url(str(url))
        for binding in payload.competitor_urls:
            validate_public_url(str(binding.url))

        source_count = payload.count_sources()
        if source_count > payload.budget.max_sources:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="输入来源数量超过任务预算")
        estimated_tokens, estimated_cost = estimate_budget_usage(source_count)
        if estimated_tokens > payload.budget.max_tokens or estimated_cost > payload.budget.max_cost_usd:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="预估 token 或成本超过任务预算")
        for image_name in payload.image_names:
            validate_image_name(image_name)

    task = task_store.create(TaskRecord(id=new_task_id("task"), request=payload))

    if sync_mode:
        try:
            return run_competitive_intelligence_workflow(task, thread_id=task.id, request_id=request_id)
        except Exception as exc:
            task.status = TaskStatus.failed
            task_store.append_event(task, "failed", f"任务失败：{exc}", TaskStatus.failed)
            task_store.update(task)
            raise HTTPException(status_code=500, detail="任务执行失败") from exc

    queued_task = task.model_copy(deep=True)
    threading.Thread(
        target=_run_workflow_in_thread,
        args=(task.id, request_id),
        daemon=True,
    ).start()
    return queued_task


@router.get("/tasks", response_model=list[TaskRecord])
def list_tasks(subject: AuthSubject = Depends(get_auth_subject)) -> list[TaskRecord]:
    return task_store.list()


@router.get("/tasks/{task_id}", response_model=TaskRecord)
def get_task(task_id: str, subject: AuthSubject = Depends(get_auth_subject)) -> TaskRecord:
    return _get_task_or_404(task_id)


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, subject: AuthSubject = Depends(get_auth_subject)) -> dict:
    task = task_store.cancel(task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return {"status": task.status.value, "task": task}


@router.get("/tasks/{task_id}/memory")
def get_task_memory(task_id: str, subject: AuthSubject = Depends(get_auth_subject)) -> dict:
    task = _resolve_task_reference(task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return {
        "task_id": task.id,
        "memory_state": task.memory_state,
        "decision_history": task.decision_history,
        "memory_items": [item.model_dump(mode="json") for item in get_task_memory_items(task.id)],
    }


@router.get("/tasks/{task_id}/checkpoints")
def get_task_checkpoints(task_id: str, subject: AuthSubject = Depends(get_auth_subject)) -> list[dict]:
    task = _resolve_task_reference(task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    checkpoints = get_recent_checkpoints(task_id=task.id, limit=100)
    return checkpoints if isinstance(checkpoints, list) else []


@router.get("/tasks/{task_id}/context")
def get_task_context(task_id: str, subject: AuthSubject = Depends(get_auth_subject)) -> dict:
    task = _resolve_task_reference(task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    request = task.request

    def estimate(text: str) -> int:
        return max(1, len(text) // 4)

    nodes: list[dict] = []

    planner_text = " ".join([
        request.product_goal,
        " ".join(request.competitors),
        request.comments or "",
        request.analysis_profile.strategy.value,
    ]).strip()
    planner_tokens = estimate(planner_text)
    nodes.append({"node": "planner", "prompt_tokens": planner_tokens, "completion_tokens": max(1, planner_tokens // 8)})

    evidence_text = " ".join(f"{item.claim} {item.quote}" for item in task.evidence)
    research_tokens = estimate(evidence_text)
    nodes.append({"node": "research", "prompt_tokens": research_tokens, "completion_tokens": max(1, research_tokens // 10)})

    decision_text = task.decision_pack.summary if task.decision_pack else ""
    writer_text = decision_text + " " + " ".join(claim.statement for claim in task.claims)
    writer_tokens = estimate(writer_text)
    nodes.append({"node": "writer", "prompt_tokens": writer_tokens, "completion_tokens": max(1, writer_tokens // 6)})

    review_text = " ".join(task.review.notes) if task.review else ""
    reviewer_tokens = estimate(review_text)
    nodes.append({"node": "reviewer", "prompt_tokens": reviewer_tokens, "completion_tokens": max(1, reviewer_tokens // 12)})

    memory_text = " ".join(item.summary for item in get_task_memory_items(task.id))
    memory_tokens = estimate(memory_text)
    nodes.append({"node": "memory", "prompt_tokens": memory_tokens, "completion_tokens": max(1, memory_tokens // 12)})

    for node in nodes:
        node["total_tokens"] = int(node["prompt_tokens"]) + int(node["completion_tokens"])
        node["context_limit"] = task.request.budget.max_tokens
        node["utilization"] = round(node["total_tokens"] / task.request.budget.max_tokens, 4)

    return {
        "task_id": task.id,
        "nodes": nodes,
        "total_tokens": sum(int(node["total_tokens"]) for node in nodes),
        "context_limit": task.request.budget.max_tokens,
    }


@router.get("/memory/items")
def list_memory_items(
    page: int = 1,
    page_size: int = 20,
    chunk_type: str | None = None,
    sort_order: str = "desc",
    subject: AuthSubject = Depends(get_auth_subject),
) -> dict:
    all_items = get_all_memory_items()
    if not all_items:
        for task in task_store.list():
            all_items.extend(get_task_memory_items(task.id))

    if chunk_type:
        all_items = [item for item in all_items if item.chunk_type.value == chunk_type]

    reverse = sort_order.lower() != "asc"
    all_items = sorted(all_items, key=lambda item: item.created_at, reverse=reverse)

    start = max(0, (page - 1) * page_size)
    end = start + page_size
    return {
        "items": [item.model_dump(mode="json") for item in all_items[start:end]],
        "total": len(all_items),
    }


@router.get("/memory/search")
def search_memory(
    q: str,
    limit: int = 10,
    sort_order: str = "desc",
    subject: AuthSubject = Depends(get_auth_subject),
) -> list[dict]:
    def _searchable_text(item) -> str:
        payload_text = json.dumps(item.payload, ensure_ascii=False, default=str)
        return f"{item.summary} {item.embedding_text} {payload_text} {' '.join(item.source_refs)}"

    exact_matches: list[dict] = []
    q_lower = q.lower()
    for task in task_store.list():
        for item in get_task_memory_items(task.id):
            if q_lower in _searchable_text(item).lower():
                exact_matches.append(item.model_dump(mode="json"))

    if exact_matches:
        exact_matches.sort(key=lambda item: item.get("created_at", ""), reverse=sort_order.lower() != "asc")
        return exact_matches[:limit]

    matches = search_decision_memory(q, top_k=limit, include_superseded=True)
    if matches:
        items = [item.model_dump(mode="json") for item, _ in matches]
        items.sort(key=lambda item: item.get("created_at", ""), reverse=sort_order.lower() != "asc")
        return items[:limit]

    fallback: list[dict] = []
    for task in task_store.list():
        for item in get_task_memory_items(task.id):
            searchable = _searchable_text(item).lower()
            if q_lower in searchable:
                fallback.append(item.model_dump(mode="json"))
                if len(fallback) >= limit:
                    fallback.sort(key=lambda item: item.get("created_at", ""), reverse=sort_order.lower() != "asc")
                    return fallback[:limit]
    fallback.sort(key=lambda item: item.get("created_at", ""), reverse=sort_order.lower() != "asc")
    return fallback[:limit]


@router.get("/memory/stats")
def get_memory_stats(subject: AuthSubject = Depends(get_auth_subject)) -> dict:
    memory_store = get_memory_store()
    items = get_all_memory_items()
    if not items:
        for task in task_store.list():
            items.extend(get_task_memory_items(task.id))

    return {
        "total_items": len(items),
        "by_type": {
            "working": len(memory_store.working_memory.items),
            "short_term": len(memory_store.short_term_memory.items),
            "long_term": len(memory_store.long_term_memory.items),
        },
        "by_status": {"active": len(items)},
        "recent_checkpoints": len(get_recent_checkpoints(limit=20)),
        "total_checkpoints": len(get_recent_checkpoints(limit=1000)),
    }


@router.post("/tasks/{task_id}/interventions")
def create_intervention(task_id: str, payload: InterventionRequest, subject: AuthSubject = Depends(get_auth_subject)) -> dict:
    task = _get_task_or_404(task_id)

    if payload.action == "force_rerun":
        if payload.stage is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"force_rerun 操作需要指定 stage 参数，有效阶段: {VALID_RERUN_STAGES}")
        if payload.stage not in VALID_RERUN_STAGES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"无效的阶段: {payload.stage}，有效阶段: {VALID_RERUN_STAGES}")
        if task.status not in (TaskStatus.completed, TaskStatus.failed):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"只有已完成或失败的任务才能强制重跑，当前状态: {task.status.value}")

        metadata = {
            "target": payload.target,
            "target_id": payload.target_id,
            "action": payload.action,
            "reason": payload.reason,
            "stage": payload.stage,
            "previous_status": task.status.value,
            "previous_decision_pack_id": task.decision_pack.id if task.decision_pack else None,
            "previous_review_score": task.review.score if task.review else None,
        }
        task_store.append_event(task, "human_intervention", json.dumps(metadata, ensure_ascii=False), task.status)

        try:
            task = rerun_from_stage(task, payload.stage)
            return {
                "status": "rerun_completed",
                "task_id": task_id,
                "intervention": payload,
                "new_task_status": task.status.value,
            }
        except Exception as exc:
            logger.exception("Force rerun failed for task %s", task_id)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"强制重跑失败: {str(exc)}") from exc

    metadata = {
        "target": payload.target,
        "target_id": payload.target_id,
        "action": payload.action,
        "reason": payload.reason,
        "stage": payload.stage,
    }
    task_store.append_event(task, "human_intervention", json.dumps(metadata, ensure_ascii=False), task.status)
    return {"status": "recorded", "task_id": task_id, "intervention": payload}


@router.get("/tasks/{task_id}/debug")
def debug_task(task_id: str, subject: AuthSubject = Depends(get_auth_subject)) -> dict:
    _get_task_or_404(task_id)
    return get_task_snapshot(task_id)


@router.post("/uploads/images")
@limiter.limit(rate_limit_settings.upload)
async def validate_image(request: Request, file: UploadFile, subject: AuthSubject = Depends(get_auth_subject)) -> dict[str, str]:
    await validate_image_upload(file)
    return {"filename": file.filename or "image", "status": "accepted"}


@router.post("/uploads/files")
@limiter.limit(rate_limit_settings.upload)
async def upload_files(request: Request, files: list[UploadFile], subject: AuthSubject = Depends(get_auth_subject)) -> dict:
    results = []
    for file in files:
        try:
            file_info = await validate_file_upload(file)
            results.append({
                "filename": file_info["filename"],
                "content_type": file_info["content_type"],
                "size": file_info["size"],
                "is_image": file_info["is_image"],
                "status": "success",
                "message": "文件上传成功",
            })
        except HTTPException as exc:
            results.append({
                "filename": file.filename or "unknown",
                "content_type": file.content_type,
                "size": 0,
                "is_image": False,
                "status": "error",
                "message": exc.detail,
            })
        except Exception as exc:
            results.append({
                "filename": file.filename or "unknown",
                "content_type": file.content_type,
                "size": 0,
                "is_image": False,
                "status": "error",
                "message": f"上传失败: {str(exc)}",
            })

    success_count = sum(1 for r in results if r["status"] == "success")
    return {
        "success": success_count == len(files),
        "success_count": success_count,
        "total_count": len(files),
        "files": results,
    }


@router.post("/uploads/file")
@limiter.limit(rate_limit_settings.upload)
async def upload_single_file(request: Request, file: UploadFile, subject: AuthSubject = Depends(get_auth_subject)) -> dict:
    try:
        file_info = await validate_file_upload(file)
        return {
            "success": True,
            "filename": file_info["filename"],
            "content_type": file_info["content_type"],
            "size": file_info["size"],
            "is_image": file_info["is_image"],
            "message": "文件上传成功",
        }
    except HTTPException as exc:
        raise exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"上传失败: {str(exc)}")


@router.get("/tasks/{task_id}/stream")
def stream_task_events(task_id: str, subject: AuthSubject = Depends(get_auth_subject)) -> StreamingResponse:
    _get_task_or_404(task_id)

    def event_stream():
        sent_count = 0
        while True:
            current_task = task_store.get(task_id)
            if current_task is None:
                break

            events = current_task.events
            while sent_count < len(events):
                event = events[sent_count]
                if hasattr(event, "model_dump_json"):
                    yield f"data: {event.model_dump_json()}\n\n"
                else:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                sent_count += 1

            if current_task.status in (TaskStatus.completed, TaskStatus.failed):
                yield "data: [DONE]\n\n"
                break

            time.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")