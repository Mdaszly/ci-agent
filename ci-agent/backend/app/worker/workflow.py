from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from copy import deepcopy
from typing import TypedDict

logger = logging.getLogger(__name__)

try:
    from langgraph.graph import END, StateGraph
except Exception:  # pragma: no cover - fallback keeps local tests lightweight.
    END = "__end__"
    StateGraph = None

from app.models.schemas import (
    AnalysisStrategy,
    BudgetUsage,
    Claim,
    Conflict,
    CoverageGateResult,
    DecisionAction,
    DecisionChunkType,
    DecisionMemoryItem,
    DecisionPack,
    DecisionPackStatus,
    DecisionPackVersion,
    Evidence,
    EvidenceDimension,
    ResearchPlan,
    ResearchTask,
    ReviewScore,
    ReviewStatus,
    SourceType,
    TaskMetrics,
    TaskRecord,
    TaskStatus,
    WorkflowMemoryState,
)
from app.services.decision_memory import (
    build_memory_chunks,
    get_task_memory_items,
    mark_superseded,
    search_decision_memory,
    upsert_decision_memory,
)
from app.services.store import task_store
from app.services.llm import LLMClient, LLMError, LLMNotConfiguredError, llm_client

VALID_RERUN_STAGES = {"planner", "research", "evidence", "coverage_gate", "conflict_resolver", "writer", "reviewer"}
REQUIRED_DIMENSIONS = [
    EvidenceDimension.feature,
    EvidenceDimension.pricing,
    EvidenceDimension.user_feedback,
]


class WorkflowCancelled(Exception):
    def __init__(self, task_id: str):
        self.task_id = task_id
        super().__init__(f"task {task_id} cancelled")


class WorkflowState(TypedDict):
    task: TaskRecord




def _ensure_not_cancelled(task: TaskRecord, stage: str) -> None:
    if task.status == TaskStatus.cancelled:
        _add_event(task, stage, "任务已停止，终止后续工作流", TaskStatus.cancelled)
        task_store.update(task)
        raise WorkflowCancelled(task.id)


def _get_profile(task: TaskRecord):
    return task.request.analysis_profile


def _get_mandatory_dimensions(task: TaskRecord) -> list[EvidenceDimension]:
    return _get_profile(task).mandatory_dimensions()


def _get_scoring_context(task: TaskRecord) -> str:
    profile = _get_profile(task)
    parts = [task.request.product_goal, " ".join(task.request.competitors)]
    if profile.focus_attributes:
        parts.append(" ".join(profile.focus_attributes))
    if profile.our_product_hints:
        parts.append(profile.our_product_hints)
    return " ".join(parts)


def _build_strategy_writer_section(task: TaskRecord) -> str:
    profile = _get_profile(task)
    weights = profile.resolved_weights()
    weight_lines = "\n".join(
        f"- {dimension}: {weight:.0%}"
        for dimension, weight in sorted(weights.items(), key=lambda item: item[1], reverse=True)
        if weight > 0
    )
    focus_line = "、".join(profile.focus_attributes) if profile.focus_attributes else "（未指定，按品类常识推断）"
    hints = profile.our_product_hints or "（未提供）"
    mandatory = ", ".join(d.value for d in profile.mandatory_dimensions())

    strategy_guidance = {
        AnalysisStrategy.cost_leadership: (
            "优先回答：我方能否以更低价格或更高性价比取胜；"
            "定价结论必须有 pricing 证据支撑，否则标注待补证据。"
        ),
        AnalysisStrategy.performance: (
            "优先回答：核心参数/体验是否显著优于竞品；"
            "battlecard 应突出可量化产品力差异。"
        ),
        AnalysisStrategy.hybrid: (
            "优先回答：同价位下我方应主打风力、静音还是续航；"
            "用「单位价格下的核心指标」做交叉分析。"
        ),
        AnalysisStrategy.custom: (
            "按用户自定义权重排序分析重点；"
            "高权重维度必须有充分证据，低权重维度可简要带过。"
        ),
    }

    return f"""
## 分析策略
- 模式: {profile.strategy_label()} ({profile.strategy.value})
- 维度权重:
{weight_lines}
- 必达维度: {mandatory}
- 重点关注属性: {focus_line}
- 我方产品提示: {hints}
- 策略指引: {strategy_guidance[profile.strategy]}
- 若高权重维度证据不足，不得做强结论，应输出「待补证据：需要 XX 竞品官方数据」
"""


def _extract_keywords(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,}", text)
    seen: set[str] = set()
    keywords: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            keywords.append(token)
    return keywords[:12]


def _build_focus_search_query(competitor: str, focus_attributes: list[str], product_goal: str) -> str:
    parts = [competitor, *focus_attributes, product_goal]
    return " ".join(part for part in parts if part)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ensure_metrics(task: TaskRecord) -> TaskMetrics:
    if task.metrics is None:
        task.metrics = TaskMetrics(
            total_duration_ms=0,
            stage_durations={},
            evidence_count=0,
            conflict_count=0,
            intervention_count=0,
        )
    return task.metrics


def _record_stage_start(task: TaskRecord, stage: str) -> int:
    task.updated_at = datetime.now(timezone.utc)
    return _now_ms()


def _record_stage_end(task: TaskRecord, stage: str, start_time: int) -> None:
    metrics = _ensure_metrics(task)
    duration = max(0, _now_ms() - start_time)
    metrics.stage_durations[stage] = duration
    metrics.total_duration_ms = sum(metrics.stage_durations.values())
    metrics.evidence_count = len(task.evidence)
    metrics.conflict_count = len(task.conflicts)
    metrics.intervention_count = len([event for event in task.events if event.stage in {"force_rerun", "memory_recall", "repair", "publish"}])
    task.metrics = metrics
    task.updated_at = datetime.now(timezone.utc)


def _add_event(task: TaskRecord, stage: str, message: str, status: TaskStatus | None = None) -> None:
    from app.models.schemas import TaskEvent

    task.events.append(
        TaskEvent(
            task_id=task.id,
            stage=stage,
            message=message,
            status=status or task.status,
        )
    )
    if status is not None:
        task.status = status
    task.updated_at = datetime.now(timezone.utc)


def _summarize_metrics(task: TaskRecord) -> None:
    metrics = _ensure_metrics(task)
    metrics.total_duration_ms = sum(metrics.stage_durations.values())
    metrics.evidence_count = len(task.evidence)
    metrics.conflict_count = len(task.conflicts)
    metrics.intervention_count = len([event for event in task.events if event.stage in {"force_rerun", "memory_recall", "repair", "publish"}])
    task.metrics = metrics


def _ensure_not_cancelled(task: TaskRecord, stage: str) -> None:
    if task.memory_state is None:
        task.memory_state = WorkflowMemoryState(
            current_pack_version=task.decision_pack.version if task.decision_pack else 1,
            current_iteration=task.rewrite_round,
        )
    if task.decision_pack is not None:
        task.memory_state.current_pack_version = max(task.memory_state.current_pack_version, task.decision_pack.version)
    task.memory_state.current_iteration = max(task.memory_state.current_iteration, task.rewrite_round)
    return task.memory_state


def _version_tag(pack_id: str, version: int) -> str:
    return f"{pack_id}:v{version}"


def _sync_decision_history(task: TaskRecord, version_meta: DecisionPackVersion) -> None:
    task.decision_history = [
        item for item in task.decision_history
        if not (item.pack_id == version_meta.pack_id and item.version == version_meta.version)
    ]
    task.decision_history.append(version_meta)
    task.decision_history.sort(key=lambda item: (item.version, item.created_at))


def _archive_current_pack(task: TaskRecord, reason: str) -> None:
    pack = task.decision_pack
    if pack is None:
        return
    pack.status = DecisionPackStatus.superseded
    if pack.version_meta is not None:
        pack.version_meta.status = DecisionPackStatus.superseded
        pack.version_meta.superseded_by = None
    _add_event(task, "versioning", reason)
    mark_superseded(task.id, pack_id=pack.id, version=pack.version)


def _persist_decision_pack(task: TaskRecord, stage: str, source_refs: list[str] | None = None) -> DecisionPackVersion | None:
    pack = task.decision_pack
    if pack is None:
        return None

    state = _ensure_memory_state(task)
    state.current_pack_version = max(state.current_pack_version, pack.version)
    state.current_iteration = max(state.current_iteration, task.rewrite_round)

    version_meta = DecisionPackVersion(
        pack_id=pack.id,
        version=pack.version,
        parent_pack_id=_version_tag(pack.id, pack.version - 1) if pack.version > 1 else None,
        superseded_by=None,
        status=pack.status,
        task_id=task.id,
        stage=stage,
        iteration=state.current_iteration,
        source_refs=list(source_refs or pack.source_refs or []),
        risk_level=task.review.hallucination_risk if task.review else "medium",
    )

    pack.version_meta = version_meta
    pack.source_refs = list(dict.fromkeys(version_meta.source_refs or pack.source_refs or []))
    _sync_decision_history(task, version_meta)

    if pack.version > 1:
        previous_version = pack.version - 1
        for item in task.decision_history:
            if item.pack_id == pack.id and item.version == previous_version:
                item.superseded_by = _version_tag(pack.id, pack.version)
                item.status = DecisionPackStatus.superseded
                break
        mark_superseded(task.id, pack_id=pack.id, version=previous_version)

    task.memory_state = state
    task_store.update(task)
    return version_meta


def _commit_decision_memory(
    task: TaskRecord,
    stage: str,
    *,
    repair_summary: str | None = None,
    repair_notes: list[str] | None = None,
    review: ReviewScore | None = None,
) -> list[DecisionMemoryItem]:
    pack = task.decision_pack
    if pack is None:
        return []

    state = _ensure_memory_state(task)
    review_snapshot = review if review is not None else (task.review if stage == "reviewer" else None)
    items = build_memory_chunks(
        task.id,
        pack,
        evidence=task.evidence,
        conflicts=task.conflicts,
        review=review_snapshot,
        repair_summary=repair_summary if stage == "repair" else None,
        repair_notes=repair_notes or (),
        stage=stage,
        iteration=state.current_iteration,
        source_refs=pack.source_refs or [task.request.product_goal],
    )
    inserted = upsert_decision_memory(items)
    state.latest_memory_ids = [item.id for item in inserted]
    task.memory_state = state
    task_store.update(task)
    return inserted


def _recall_decision_memory(task: TaskRecord, *, stage: str = "reviewer") -> list[tuple[DecisionMemoryItem, float]]:
    if task.decision_pack is None:
        return []
    profile = _get_profile(task)
    query = " ".join(
        [
            task.request.product_goal,
            " ".join(task.request.competitors),
            task.decision_pack.summary,
            " ".join(profile.focus_attributes),
            task.review.notes[0] if task.review and task.review.notes else "",
        ]
    ).strip()
    recalled = search_decision_memory(
        query,
        task_id=task.id,
        top_k=5,
        chunk_types=[
            DecisionChunkType.decision,
            DecisionChunkType.conflict,
            DecisionChunkType.repair,
            DecisionChunkType.reviewer_feedback,
        ],
        stage=stage,
        include_superseded=True,
    )
    state = _ensure_memory_state(task)
    state.latest_memory_ids = [item.id for item, _ in recalled]
    state.last_recall_count = len(recalled)
    state.last_recall_summary = "; ".join(
        f"{item.chunk_type.value}@v{item.version}:{score:.2f}" for item, score in recalled[:3]
    ) or None
    task.memory_state = state
    if recalled:
        summary = "; ".join(f"{item.chunk_type.value}@v{item.version}:{score:.2f}" for item, score in recalled)
        _add_event(task, "memory_recall", f"召回历史记忆 {len(recalled)} 条：{summary}")
    else:
        _add_event(task, "memory_recall", "未召回到可复用的历史记忆")
    return recalled


def _repair_notes_from_memory(task: TaskRecord, recalled: list[tuple[DecisionMemoryItem, float]]) -> list[str]:
    notes: list[str] = []
    if task.review and task.review.notes:
        notes.extend(task.review.notes[:3])
    for item, score in recalled[:3]:
        notes.append(f"召回 {item.chunk_type.value} v{item.version}（相似度 {score:.2f}）")
        if item.summary:
            notes.append(f"参考摘要：{item.summary[:120]}")
    if task.coverage and task.coverage.missing_dimensions:
        notes.append(f"补齐缺失维度：{[d.value for d in task.coverage.missing_dimensions]}")
    return notes


def _repair_decision_pack(task: TaskRecord, recalled: list[tuple[DecisionMemoryItem, float]]) -> bool:
    state = _ensure_memory_state(task)
    if task.decision_pack is None:
        return False
    if task.review is None:
        return False
    if task.review.score >= REVIEW_RETRY_THRESHOLD and task.review.hallucination_risk == "low":
        state.last_reviewer_status = ReviewStatus.approved
        task.memory_state = state
        task_store.update(task)
        return False

    repair_notes = _repair_notes_from_memory(task, recalled)
    repair_summary = "; ".join(repair_notes[:4]) if repair_notes else "根据 Reviewer 意见进行局部补丁修复"
    source_refs = task.decision_pack.source_refs or [task.request.product_goal]

    _archive_current_pack(task, "进入 repair 节点，准备生成补丁版本")
    previous_pack = deepcopy(task.decision_pack)
    new_pack = deepcopy(previous_pack)
    new_pack.parent_pack_id = previous_pack.id
    new_pack.superseded_by = None
    new_pack.status = DecisionPackStatus.draft
    new_pack.summary = f"{previous_pack.summary}（补丁版）"
    new_pack.version = previous_pack.version + 1
    new_pack.source_refs = list(dict.fromkeys(source_refs + [note for note in repair_notes if note]))
    new_pack.version_meta = None

    if task.review.notes:
        new_pack.summary = f"{new_pack.summary}｜修复聚焦：{task.review.notes[0][:80]}"

    task.decision_pack = new_pack
    _persist_decision_pack(task, "repair", source_refs=new_pack.source_refs)
    _add_event(task, "repair", f"已生成决策包修复版 v{task.decision_pack.version}")
    _commit_decision_memory(task, "repair", repair_summary=repair_summary, repair_notes=repair_notes)
    state.current_iteration += 1
    state.last_recall_count = len(recalled)
    state.last_recall_summary = "; ".join(
        f"{item.chunk_type.value}@v{item.version}:{score:.2f}" for item, score in recalled[:3]
    ) or None
    state.last_reviewer_status = ReviewStatus.needs_retry
    state.retry_reason = task.review.notes[0] if task.review.notes else "reviewer 需要进一步修复"
    task.rewrite_round += 1
    task.memory_state = state
    task_store.update(task)
    return True


def _should_retry(task: TaskRecord) -> bool:
    state = _ensure_memory_state(task)
    if task.status in (TaskStatus.failed, TaskStatus.cancelled):
        return False
    if task.review is None:
        return False
    if state.current_iteration >= state.max_iterations:
        return False
    if task.review.score < REVIEW_RETRY_THRESHOLD:
        return True
    if task.review.hallucination_risk == "high":
        return True
    return False


def _should_stop(task: TaskRecord) -> bool:
    state = _ensure_memory_state(task)
    if task.status in (TaskStatus.failed, TaskStatus.cancelled):
        return True
    if task.review is None:
        return False
    if task.review.score >= REVIEW_PUBLISH_THRESHOLD and task.review.hallucination_risk == "low":
        return True
    if state.current_iteration >= state.max_iterations:
        return True
    return False


def _finalize_publish(task: TaskRecord) -> None:
    state = _ensure_memory_state(task)
    if task.decision_pack is None:
        return
    task.decision_pack.status = DecisionPackStatus.approved
    if task.decision_pack.version_meta is not None:
        task.decision_pack.version_meta.status = DecisionPackStatus.approved
        task.decision_pack.version_meta.risk_level = task.review.hallucination_risk if task.review else "medium"
    state.last_reviewer_status = ReviewStatus.approved
    state.retry_reason = None
    task.memory_state = state
    _add_event(task, "publish", f"决策包 v{task.decision_pack.version} 已发布")


def research(state: WorkflowState) -> WorkflowState:
    task = state["task"]
    start_time = _record_stage_start(task, "research")
    task.status = TaskStatus.running
    
    # 如果有 research_plan，使用并行执行
    if task.research_plan and task.research_plan.tasks:
        evidence = _execute_research_plan_parallel(task)
    else:
        # fallback 到原有串行逻辑（兼容旧路径）
        evidence = _execute_research_serial(task)
    
    task.evidence = evidence
    _add_event(task, "research", f"已生成 {len(evidence)} 条标准化证据")
    _record_stage_end(task, "research", start_time)
    return {"task": task}


def _execute_research_plan_parallel(task: TaskRecord) -> list[Evidence]:
    """并行执行 research_plan 中的所有任务"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from app.services.research_executor import execute_task
    
    tasks = task.research_plan.tasks
    evidence: list[Evidence] = []
    warning_count = 0
    search_count = 0
    
    with ThreadPoolExecutor(max_workers=4) as executor:
        task_list = list(tasks)
        future_to_task = {executor.submit(execute_task, item): item for item in task_list}

        for future in as_completed(future_to_task):
            task_item = future_to_task[future]
            try:
                result = future.result()
                if result:
                    evidence.extend(result)
                    if task_item.source_type == "search":
                        search_count += len(result)
                else:
                    warning_count += 1
            except Exception as e:
                logger.warning(f"任务执行异常 {task_item.source_type}: {e}")
                warning_count += 1
    
    search_note = f"，搜索补充 {search_count} 条证据" if search_count > 0 else ""
    
    if warning_count > 0:
        _add_event(task, "research", f"并行执行 {len(tasks)} 个采集任务，其中 {warning_count} 个失败{search_note}", TaskStatus.running)
    else:
        _add_event(task, "research", f"并行执行 {len(tasks)} 个采集任务{search_note}")
    
    return evidence


def _execute_research_serial(task: TaskRecord) -> list[Evidence]:
    """原有串行逻辑（fallback）- 复用 execute_task 处理各类型任务"""
    request = task.request
    evidence: list[Evidence] = []
    search_note = ""

    from app.services.research_executor import execute_task

    url_bindings = request.get_url_bindings()
    for competitor, url in url_bindings:
        research_task = ResearchTask(
            competitor=competitor,
            source_type="url",
            query_or_url=url,
            dimension=EvidenceDimension.feature,
            priority=1,
        )
        result = execute_task(research_task)
        if result:
            evidence.extend(result)

    for competitor in request.competitors:
        search_task = ResearchTask(
            competitor=competitor,
            source_type="search",
            query_or_url=f"{competitor} {request.product_goal}",
            dimension=EvidenceDimension.feature,
            priority=2,
        )
        result = execute_task(search_task)
        if result:
            evidence.extend(result)
            search_note = f"，搜索补充 {len(result)} 条证据"

    # Comment 任务
    if request.comments:
        from app.services.research_executor import execute_task as exec_task

        comment_task = ResearchTask(
            competitor=request.competitors[0],
            source_type="comment",
            query_or_url=request.comments,
            dimension=EvidenceDimension.user_feedback,
            priority=1,
        )
        result = exec_task(comment_task)
        if result:
            evidence.extend(result)

    # Image 任务
    for image_name in request.image_names:
        from app.services.research_executor import execute_task as exec_task

        image_task = ResearchTask(
            competitor=request.competitors[0],
            source_type="image",
            query_or_url=image_name,
            dimension=EvidenceDimension.positioning,
            priority=1,
        )
        result = exec_task(image_task)
        if result:
            evidence.extend(result)

    return evidence


def evidence_normalizer(state: WorkflowState) -> WorkflowState:
    task = state["task"]
    
    from app.services.evidence_scorer import evidence_scorer
    
    context = _get_scoring_context(task)
    focus_attributes = _get_profile(task).focus_attributes

    task.evidence = [
        evidence_scorer.score(ev, context, focus_attributes=focus_attributes)
        for ev in task.evidence
    ]
    
    task.claims = [
        Claim(
            statement=item.claim,
            dimension=item.dimension,
            competitor=item.competitor,
            evidence_ids=[item.id],
            confidence=item.quality_score,
        )
        for item in task.evidence
    ]
    _add_event(task, "evidence", "已将外部输入标记为不可信来源，并转换为 Claim，完成证据评分")
    return {"task": task}


QUALITY_THRESHOLD = 0.5
CREDIBILITY_THRESHOLD = 0.4
COVERAGE_PASS_THRESHOLD = 0.7
REVIEW_RETRY_THRESHOLD = 0.6
REVIEW_PUBLISH_THRESHOLD = 0.8


def _compute_coverage(task: TaskRecord) -> CoverageGateResult:
    profile = _get_profile(task)
    mandatory_dimensions = profile.mandatory_dimensions()
    weights = profile.resolved_weights()

    high_quality_evidence = [
        ev for ev in task.evidence
        if getattr(ev, "quality_score", 0.0) >= QUALITY_THRESHOLD
        and getattr(ev, "credibility_score", 0.0) >= CREDIBILITY_THRESHOLD
    ]

    covered_high_quality = sorted(
        {item.dimension for item in high_quality_evidence},
        key=lambda item: item.value,
    )

    covered_all = sorted(
        {item.dimension for item in task.evidence},
        key=lambda item: item.value,
    )

    missing_dimensions = [item for item in mandatory_dimensions if item not in covered_high_quality]

    low_quality_dimensions = []
    for dimension in mandatory_dimensions:
        if dimension in covered_all and dimension not in covered_high_quality:
            low_quality_dimensions.append(dimension)

    weight_total = sum(weights.get(d.value, 0.0) for d in mandatory_dimensions) or 1.0
    weighted_score = round(
        sum(weights.get(d.value, 0.0) for d in covered_high_quality if d in mandatory_dimensions)
        / weight_total,
        2,
    )

    gap_queries = []
    focus_suffix = " ".join(profile.focus_attributes) if profile.focus_attributes else ""
    for competitor in task.request.competitors:
        for dimension in missing_dimensions:
            query = f"{competitor} {dimension.value} {focus_suffix}".strip()
            gap_queries.append(f"{query} evidence")
        for dimension in low_quality_dimensions:
            gap_queries.append(
                f"需补充 {competitor} {dimension.value} 维度的高质量证据（当前证据质量不足）"
            )

    passed = (
        weighted_score >= COVERAGE_PASS_THRESHOLD
        and len(missing_dimensions) == 0
        and len(low_quality_dimensions) == 0
    )

    return CoverageGateResult(
        passed=passed,
        score=weighted_score,
        covered_dimensions=covered_high_quality,
        missing_dimensions=missing_dimensions,
        gap_queries=gap_queries,
    )


def _coverage_message(coverage: CoverageGateResult, low_quality_dimensions: list) -> str:
    if coverage.passed:
        return "证据覆盖达标，所有维度均有高质量证据支持"
    if low_quality_dimensions:
        return f"证据存在质量缺口，{[d.value for d in low_quality_dimensions]} 维度证据质量不足"
    return "证据存在缺口，首版以缺口查询提示展示"


def coverage_gate(state: WorkflowState) -> WorkflowState:
    task = state["task"]
    start_time = _record_stage_start(task, "coverage_gate")

    task.coverage = _compute_coverage(task)
    covered_all = sorted({item.dimension for item in task.evidence}, key=lambda item: item.value)
    low_quality_dimensions = [
        d for d in _get_mandatory_dimensions(task)
        if d in covered_all and d not in task.coverage.covered_dimensions
    ]
    _add_event(task, "coverage_gate", _coverage_message(task.coverage, low_quality_dimensions))

    supplemented = _supplement_search(task)
    if supplemented:
        for item in supplemented:
            task.claims.append(
                Claim(
                    statement=item.claim,
                    dimension=item.dimension,
                    competitor=item.competitor,
                    evidence_ids=[item.id],
                    confidence=item.quality_score,
                )
            )
        task.coverage = _compute_coverage(task)
        _add_event(
            task,
            "coverage_gate",
            f"补搜完成，新增 {len(supplemented)} 条证据；"
            f"覆盖状态：{'达标' if task.coverage.passed else '未达标'}",
        )

    _record_stage_end(task, "coverage_gate", start_time)
    return {"task": task}


def _supplement_search(task: TaskRecord) -> list[Evidence]:
    if not task.coverage or task.coverage.passed:
        return []

    if task.research_round >= 1:
        return []

    if len(task.evidence) >= task.request.budget.max_sources:
        return []

    from app.services.search_adapter import search_adapter
    from app.services.evidence_scorer import evidence_scorer

    if not search_adapter.config.is_configured:
        return []

    gap_queries = task.coverage.gap_queries if task.coverage else []
    if not gap_queries:
        return []

    _add_event(task, "coverage_gate", f"证据覆盖未达标，开始补搜一轮，共 {len(gap_queries)} 个缺口查询")

    additional_evidence: list[Evidence] = []
    competitors = task.request.competitors[:2]
    for competitor in competitors:
        for query in gap_queries[:2]:
            if f"{competitor} " in query or query.startswith(competitor):
                results = search_adapter.search(query, competitor)
                additional_evidence.extend(results)
        if len(additional_evidence) >= 3:
            break
    if not additional_evidence:
        for query in gap_queries[:3]:
            results = search_adapter.search(query, task.request.competitors[0])
            additional_evidence.extend(results)

    if not additional_evidence:
        _add_event(task, "coverage_gate", "补搜未获取到新证据")
        return []

    context = _get_scoring_context(task)
    focus_attributes = _get_profile(task).focus_attributes
    scored = [
        evidence_scorer.score(ev, context, focus_attributes=focus_attributes)
        for ev in additional_evidence
    ]
    task.evidence.extend(scored)
    task.research_round += 1
    return scored


def conflict_resolver(state: WorkflowState) -> WorkflowState:
    """检测并裁决证据冲突"""
    task = state["task"]
    start_time = _record_stage_start(task, "conflict_resolver")
    task.conflicts = []
    
    conflicts = _detect_conflicts(task.evidence, task.claims)
    
    for conflict in conflicts:
        task.conflicts.append(conflict)
        
        # 标记被否决的证据
        rejected_ids = conflict.claim_ids[1:]  # 第一个是保留的，其余是被否决的
        for ev in task.evidence:
            for claim in task.claims:
                if claim.id in rejected_ids and ev.id in claim.evidence_ids:
                    # 在 quote 中添加否决标记
                    ev.quote = f"⚠️ 已被冲突裁决否决: {ev.quote}"
    
    if conflicts:
        _add_event(task, "conflict_resolver", f"发现 {len(conflicts)} 个冲突，已按 credibility 裁决")
    else:
        _add_event(task, "conflict_resolver", "未发现可裁决冲突，保留所有带引用 Claim")
    
    _record_stage_end(task, "conflict_resolver", start_time)
    return {"task": task}


def _detect_conflicts(evidence: list[Evidence], claims: list[Claim]) -> list[Conflict]:
    """检测证据冲突"""
    conflicts = []
    
    # 按竞品和维度分组
    from collections import defaultdict
    grouped = defaultdict(list)
    
    for claim in claims:
        key = (claim.competitor, claim.dimension)
        grouped[key].append(claim)
    
    for (competitor, dimension), group_claims in grouped.items():
        if len(group_claims) < 2:
            continue
        
        # 检查同组内的冲突
        for i, claim_a in enumerate(group_claims):
            for claim_b in group_claims[i+1:]:
                if _is_conflicting(claim_a, claim_b, evidence):
                    # 按 credibility 裁决
                    ev_a = [ev for ev in evidence if ev.id in claim_a.evidence_ids]
                    ev_b = [ev for ev in evidence if ev.id in claim_b.evidence_ids]
                    
                    cred_a = sum(ev.credibility_score for ev in ev_a) / len(ev_a) if ev_a else 0.5
                    cred_b = sum(ev.credibility_score for ev in ev_b) / len(ev_b) if ev_b else 0.5
                    
                    # 保留 credibility 更高的
                    if cred_a >= cred_b:
                        kept_claim = claim_a
                        rejected_claim = claim_b
                    else:
                        kept_claim = claim_b
                        rejected_claim = claim_a
                    
                    conflict = Conflict(
                        claim_ids=[kept_claim.id, rejected_claim.id],
                        resolution=f"保留 credibility={max(cred_a, cred_b):.2f} 的 Claim",
                        rationale=f"同竞品 {competitor} 同维度 {dimension.value} 的主张存在矛盾",
                        confidence=0.7,
                    )
                    conflicts.append(conflict)
    
    return conflicts


def _is_conflicting(claim_a: Claim, claim_b: Claim, evidence: list[Evidence]) -> bool:
    """判断两个 Claim 是否冲突"""
    # 获取对应的 Evidence
    ev_a_list = [ev for ev in evidence if ev.id in claim_a.evidence_ids]
    ev_b_list = [ev for ev in evidence if ev.id in claim_b.evidence_ids]
    
    if not ev_a_list or not ev_b_list:
        return False
    
    # 检查价格数字不一致
    import re
    price_pattern = r'\$?\d{1,4}(?:[.,]\d{1,2})?'
    
    for ev_a in ev_a_list:
        prices_a = re.findall(price_pattern, ev_a.quote)
        for ev_b in ev_b_list:
            prices_b = re.findall(price_pattern, ev_b.quote)
            if prices_a and prices_b and prices_a != prices_b:
                return True
    
    # 检查抓取成功 vs 抓取失败
    failed_a = any("抓取失败" in ev.quote for ev in ev_a_list)
    failed_b = any("抓取失败" in ev.quote for ev in ev_b_list)
    if failed_a != failed_b:
        return True
    
    # 检查功能「有/无」对立
    has_keywords = ["有", "支持", "提供", "包含", "enable", "support"]
    no_keywords = ["无", "不支持", "不提供", "缺少", "disable", "lack"]
    
    for ev_a in ev_a_list:
        for ev_b in ev_b_list:
            has_a = any(kw in ev_a.claim.lower() for kw in has_keywords)
            no_a = any(kw in ev_a.claim.lower() for kw in no_keywords)
            has_b = any(kw in ev_b.claim.lower() for kw in has_keywords)
            no_b = any(kw in ev_b.claim.lower() for kw in no_keywords)
            
            if (has_a and no_b) or (no_a and has_b):
                return True
    
    return False


def _build_writer_prompt(task: TaskRecord) -> str:
    evidence_list = []
    for evidence in task.evidence:
        evidence_list.append(f"""
Evidence ID: {evidence.id}
来源类型: {evidence.source_type.value}
竞品: {evidence.competitor}
维度: {evidence.dimension.value}
主张: {evidence.claim}
引用: {evidence.quote}
置信度: {evidence.confidence}
""")
    
    evidence_context = "\n".join(evidence_list)
    
    # 检查是否有定价相关证据
    has_pricing_evidence = any(ev.dimension == EvidenceDimension.pricing for ev in task.evidence)
    
    coverage_info = ""
    if task.coverage:
        coverage_info = f"""
证据覆盖状态: {'达标' if task.coverage.passed else '未达标'}
已覆盖维度: {[d.value for d in task.coverage.covered_dimensions]}
缺失维度: {[d.value for d in task.coverage.missing_dimensions]}
"""

    product_hint = ""
    goal_lower = task.request.product_goal.lower()
    if any(keyword in goal_lower for keyword in ["风扇", "fan", "续航", "风力", "噪音", "便携"]):
        product_hint = """
## 品类分析提示（消费品/硬件）
- 优先对比维度：风力/风速、噪音、续航、便携性、价格带
- 定位应回答：同价位下应主打性价比、性能还是静音体验
- 缺少参数或价格证据时，必须标注「待补证据」，不得编造具体数值
"""
    strategy_section = _build_strategy_writer_section(task)

    prompt = f"""
你是一个专业的竞品情报分析师。请根据以下结构化证据，为产品目标提供差异化定位建议、MVP 功能优先级建议、定价洞察和竞争卡片。

## 产品目标
{task.request.product_goal}
{product_hint}
{strategy_section}

## 竞品列表
{', '.join(task.request.competitors)}

## 可用证据（你只能使用这些证据进行分析）
{evidence_context}

## 证据覆盖情况
{coverage_info}

## 定价证据状态
{'存在定价相关证据，可生成定价洞察' if has_pricing_evidence else '无定价相关证据，pricing_insights 必须为空数组'}

## 输出格式要求
请严格按照以下 JSON 格式输出，不要输出任何其他内容：

{{
  "summary": "对整体分析的简要总结",
  "positioning": [
    {{
      "title": "定位建议标题",
      "dimension": "positioning",
      "recommendation": "具体的差异化定位建议",
      "rationale": "建议的理由，必须引用证据",
      "evidence_ids": ["ev_xxx"],
      "priority": "P0"
    }}
  ],
  "mvp_priorities": [
    {{
      "title": "MVP 功能优先级标题",
      "dimension": "feature",
      "recommendation": "具体的功能优先级建议",
      "rationale": "建议的理由，必须引用证据",
      "evidence_ids": ["ev_xxx"],
      "priority": "P0"
    }}
  ],
  "pricing_insights": [
    {{
      "title": "定价洞察标题",
      "dimension": "pricing",
      "recommendation": "具体的定价策略建议",
      "rationale": "建议的理由，必须引用证据",
      "evidence_ids": ["ev_xxx"],
      "priority": "P0"
    }}
  ],
  "battlecard": [
    {{
      "title": "竞争卡片标题",
      "dimension": "positioning",
      "recommendation": "具体的竞争策略建议",
      "rationale": "建议的理由，必须引用证据",
      "evidence_ids": ["ev_xxx"],
      "priority": "P0"
    }}
  ]
}}

## 规则约束
1. 只能使用提供的 Evidence ID，不得编造。
2. 每条建议必须绑定至少一个 Evidence ID。
3. 如果 Coverage Gate 未达标，只能输出补证据建议和临时判断，不能伪造完整结论。
4. 不允许引用原始 URL 内容中没有出现在 Evidence 的事实。
5. 输出必须是严格的 JSON 格式，不要输出 Markdown。
6. **无证据不编造**：如果没有定价相关证据（dimension 为 pricing），pricing_insights 必须为空数组 []。
7. **battlecard 可基于现有证据生成**：即使没有专门的 battlecard 维度证据，也可基于其他维度证据生成竞争策略卡片。
8. **只做局部修复**：如果进入重写/修复轮次，只允许调整差异项，不得整体重写整份决策包。
9. **输出补丁信息**：修复时必须显式说明保留项、删除项、新增项和新增证据。
10. **版本递增**：修复版本必须基于上一版继续演进，不允许回滚成空白草稿。
"""
    
    if task.rewrite_round > 0 and task.review and task.review.notes:
        prompt += f"""
        
## 修正指引（来自 Reviewer）

以下是对你上一次输出的审核意见，请据此进行修改：

{task.review.notes}

请根据上述意见修改你的分析报告，重点关注指出的问题并进行改进。
"""
    
    return prompt.strip()


def _build_rule_based_decision_pack(task: TaskRecord) -> DecisionPack:
    evidence_by_dimension: dict[EvidenceDimension, list[Evidence]] = {}
    for evidence in task.evidence:
        evidence_by_dimension.setdefault(evidence.dimension, []).append(evidence)

    def _choose_evidence(dimension: EvidenceDimension) -> list[str]:
        items = sorted(
            evidence_by_dimension.get(dimension, []),
            key=lambda item: (getattr(item, "quality_score", 0.0), getattr(item, "credibility_score", 0.0)),
            reverse=True,
        )
        return [item.id for item in items[:2]]

    positioning_ids = _choose_evidence(EvidenceDimension.positioning) or _choose_evidence(EvidenceDimension.feature) or [task.evidence[0].id]
    feature_ids = _choose_evidence(EvidenceDimension.feature) or positioning_ids
    pricing_ids = _choose_evidence(EvidenceDimension.pricing)
    if not pricing_ids:
        pricing_ids = []

    summary_parts = [task.request.product_goal]
    if task.review and task.review.notes:
        summary_parts.append(task.review.notes[0])
    if task.coverage and task.coverage.missing_dimensions:
        summary_parts.append(f"待补维度：{','.join(d.value for d in task.coverage.missing_dimensions)}")

    return DecisionPack(
        positioning=[
            DecisionAction(
                title="差异化定位",
                dimension=EvidenceDimension.positioning,
                recommendation=f"围绕 {task.request.product_goal[:40]} 提炼与竞品的核心差异，并优先突出 {task.request.competitors[0]} 的空白点。",
                rationale="基于现有证据和覆盖缺口生成首版定位建议。",
                evidence_ids=positioning_ids,
                priority="P0",
            )
        ],
        mvp_priorities=[
            DecisionAction(
                title="MVP 优先级",
                dimension=EvidenceDimension.feature,
                recommendation="优先补齐高权重维度对应的最小闭环能力，再扩展差异化卖点。",
                rationale="基于 feature 证据和 Reviewer 可读的局部修复策略生成。",
                evidence_ids=feature_ids,
                priority="P0",
            )
        ],
        pricing_insights=[
            DecisionAction(
                title="定价洞察",
                dimension=EvidenceDimension.pricing,
                recommendation="当前定价证据不足时，先把价格策略标记为待补证据，不输出具体价格结论。",
                rationale="无 pricing 证据时只允许输出保守判断。",
                evidence_ids=pricing_ids,
                priority="P1",
            )
        ] if pricing_ids else [],
        battlecard=[
            DecisionAction(
                title="竞争卡片",
                dimension=EvidenceDimension.positioning,
                recommendation="对照竞品证据列出可直接反击的卖点和需要回避的弱项。",
                rationale="基于现有证据生成可执行的首版 battlecard。",
                evidence_ids=positioning_ids,
                priority="P0",
            )
        ],
        summary="；".join(part for part in summary_parts if part),
        status=DecisionPackStatus.draft,
    )


def _validate_evidence_ids(task: TaskRecord, evidence_ids: list[str]) -> tuple[bool, list[str]]:
    """校验 evidence_ids 是否都存在于任务的证据中"""
    valid_ids = {item.id for item in task.evidence}
    invalid_ids = [eid for eid in evidence_ids if eid not in valid_ids]
    return (len(invalid_ids) == 0, invalid_ids)


def _ensure_memory_state(task: TaskRecord) -> WorkflowMemoryState:
    if task.memory_state is None:
        task.memory_state = WorkflowMemoryState()
    return task.memory_state


def _decision_pack_signature(pack: DecisionPack) -> str:
    payload = {
        "id": pack.id,
        "version": pack.version,
        "summary": pack.summary,
        "positioning": [action.model_dump(mode="json") for action in pack.positioning],
        "mvp_priorities": [action.model_dump(mode="json") for action in pack.mvp_priorities],
        "pricing_insights": [action.model_dump(mode="json") for action in pack.pricing_insights],
        "battlecard": [action.model_dump(mode="json") for action in pack.battlecard],
        "source_refs": pack.source_refs,
    }
    return _hash_text(str(payload))


def _current_pack_version(task: TaskRecord) -> int:
    state = _ensure_memory_state(task)
    if task.decision_pack is not None:
        return max(state.current_pack_version, task.decision_pack.version)
    return state.current_pack_version


def _archive_current_pack(task: TaskRecord, reason: str) -> None:
    if task.decision_pack is None:
        return
    current_pack = deepcopy(task.decision_pack)
    current_pack.status = DecisionPackStatus.superseded
    current_pack.superseded_by = None
    version_meta = current_pack.version_meta or DecisionPackVersion(
        pack_id=current_pack.id,
        version=current_pack.version,
        parent_pack_id=current_pack.parent_pack_id,
        superseded_by=current_pack.superseded_by,
        status=current_pack.status,
        task_id=task.id,
        stage="archive",
        iteration=_ensure_memory_state(task).current_iteration,
        source_refs=current_pack.source_refs,
        risk_level="medium",
    )
    version_meta.status = DecisionPackStatus.superseded
    version_meta.stage = "archive"
    version_meta.task_id = task.id
    current_pack.version_meta = version_meta
    task.decision_history.append(version_meta)
    mark_superseded(task.id, pack_id=current_pack.id, version=current_pack.version)
    _add_event(task, "memory_commit", f"已归档决策包 v{current_pack.version}：{reason}")


def _next_version_meta(task: TaskRecord, *, stage: str, source_refs: list[str], risk_level: str = "medium") -> DecisionPackVersion:
    memory_state = _ensure_memory_state(task)
    parent_pack_id = task.decision_pack.id if task.decision_pack else None
    version = memory_state.current_pack_version + 1 if task.decision_pack else memory_state.current_pack_version
    memory_state.current_pack_version = version
    return DecisionPackVersion(
        pack_id=task.decision_pack.id if task.decision_pack else task.id,
        version=version,
        parent_pack_id=parent_pack_id,
        superseded_by=None,
        status=DecisionPackStatus.draft,
        task_id=task.id,
        stage=stage,
        iteration=memory_state.current_iteration,
        source_refs=source_refs,
        risk_level=risk_level,
    )


def _persist_decision_pack(task: TaskRecord, stage: str, source_refs: list[str], risk_level: str = "medium") -> None:
    if task.decision_pack is None:
        return
    meta = _next_version_meta(task, stage=stage, source_refs=source_refs, risk_level=risk_level)
    task.decision_pack.version = meta.version
    task.decision_pack.parent_pack_id = meta.parent_pack_id
    task.decision_pack.superseded_by = meta.superseded_by
    task.decision_pack.status = meta.status
    task.decision_pack.source_refs = list(source_refs)
    task.decision_pack.version_meta = meta
    task.decision_history.append(meta)


def _commit_decision_memory(
    task: TaskRecord,
    stage: str,
    *,
    repair_summary: str | None = None,
    repair_notes: list[str] | None = None,
    review: ReviewScore | None = None,
) -> list[DecisionMemoryItem]:
    if task.decision_pack is None:
        return []
    review_snapshot = review if review is not None else (task.review if stage == "reviewer" else None)
    items = build_memory_chunks(
        task.id,
        task.decision_pack,
        evidence=task.evidence,
        conflicts=task.conflicts,
        review=review_snapshot,
        repair_summary=repair_summary if stage == "repair" else None,
        repair_notes=repair_notes or (),
        stage=stage,
        iteration=_ensure_memory_state(task).current_iteration,
        source_refs=task.decision_pack.source_refs or [task.request.product_goal],
    )
    upsert_decision_memory(items)
    state = _ensure_memory_state(task)
    state.latest_memory_ids = [item.id for item in items]
    _add_event(task, stage, f"已写入 {len(items)} 个决策记忆块")
    return items


def _recall_decision_memory(task: TaskRecord) -> list[tuple[DecisionMemoryItem, float]]:
    state = _ensure_memory_state(task)
    query_parts = [task.request.product_goal, " ".join(task.request.competitors)]
    if task.review and task.review.notes:
        query_parts.extend(task.review.notes)
    if task.conflicts:
        query_parts.extend(conflict.resolution for conflict in task.conflicts)
    query = " ".join(part for part in query_parts if part).strip()
    recalled = search_decision_memory(
        query,
        task_id=task.id,
        top_k=5,
        chunk_types=[
            DecisionChunkType.decision,
            DecisionChunkType.conflict,
            DecisionChunkType.repair,
            DecisionChunkType.reviewer_feedback,
        ],
        include_superseded=True,
    )
    state.latest_memory_ids = [item.id for item, _ in recalled]
    if recalled:
        summary = "; ".join(f"{item.chunk_type.value}@v{item.version}:{score:.2f}" for item, score in recalled)
        _add_event(task, "memory_recall", f"召回历史记忆 {len(recalled)} 条：{summary}")
    else:
        _add_event(task, "memory_recall", "未召回到可复用的历史记忆")
    return recalled


def _repair_notes_from_memory(task: TaskRecord, recalled: list[tuple[DecisionMemoryItem, float]]) -> list[str]:
    notes: list[str] = []
    if task.review and task.review.notes:
        notes.extend(task.review.notes[:3])
    for item, score in recalled[:3]:
        notes.append(f"召回 {item.chunk_type.value} v{item.version}（相似度 {score:.2f}）")
        if item.summary:
            notes.append(f"参考摘要：{item.summary[:120]}")
    if task.coverage and task.coverage.missing_dimensions:
        notes.append(f"补齐缺失维度：{[d.value for d in task.coverage.missing_dimensions]}")
    return notes


def _repair_decision_pack(task: TaskRecord, recalled: list[tuple[DecisionMemoryItem, float]]) -> bool:
    state = _ensure_memory_state(task)
    if task.decision_pack is None:
        return False
    if task.review is None:
        return False
    if task.review.score >= REVIEW_RETRY_THRESHOLD and task.review.hallucination_risk == "low":
        state.last_reviewer_status = ReviewStatus.approved
        return False

    repair_notes = _repair_notes_from_memory(task, recalled)
    repair_summary = "; ".join(repair_notes[:4]) if repair_notes else "根据 Reviewer 意见进行局部补丁修复"
    source_refs = task.decision_pack.source_refs or [task.request.product_goal]

    _archive_current_pack(task, "进入 repair 节点，准备生成补丁版本")
    previous_pack = deepcopy(task.decision_pack)
    new_pack = deepcopy(previous_pack)
    new_pack.id = previous_pack.id
    new_pack.parent_pack_id = previous_pack.id
    new_pack.superseded_by = None
    new_pack.status = DecisionPackStatus.draft
    new_pack.summary = f"{previous_pack.summary}（补丁版）"
    new_pack.version = previous_pack.version + 1
    new_pack.source_refs = list(dict.fromkeys(source_refs + [note for note in repair_notes if note]))
    new_pack.version_meta = None

    if task.review.notes:
        new_pack.summary = f"{new_pack.summary}｜修复聚焦：{task.review.notes[0][:80]}"

    task.decision_pack = new_pack
    _persist_decision_pack(task, "repair", source_refs=new_pack.source_refs)
    _add_event(task, "repair", f"已生成决策包修复版 v{task.decision_pack.version}")
    _commit_decision_memory(task, "repair", repair_summary=repair_summary, repair_notes=repair_notes)
    state.current_iteration += 1
    state.last_reviewer_status = ReviewStatus.needs_retry
    state.retry_reason = task.review.notes[0] if task.review.notes else "reviewer 需要进一步修复"
    task.rewrite_round += 1
    return True


def _should_retry(task: TaskRecord) -> bool:
    state = _ensure_memory_state(task)
    if task.status == TaskStatus.failed:
        return False
    if task.review is None:
        return False
    if state.current_iteration >= state.max_iterations:
        return False
    if task.review.score < REVIEW_RETRY_THRESHOLD:
        return True
    if task.review.hallucination_risk == "high":
        return True
    return False


def _should_stop(task: TaskRecord) -> bool:
    state = _ensure_memory_state(task)
    if task.status == TaskStatus.failed:
        return True
    if state.current_iteration >= state.max_iterations:
        return True
    if task.review is None:
        return False
    if task.review.score >= REVIEW_PUBLISH_THRESHOLD and task.review.hallucination_risk == "low":
        return True
    return False


def _finalize_publish(task: TaskRecord) -> None:
    state = _ensure_memory_state(task)
    if task.decision_pack is None:
        return
    task.decision_pack.status = DecisionPackStatus.approved
    if task.decision_pack.version_meta is not None:
        task.decision_pack.version_meta.status = DecisionPackStatus.approved
        task.decision_pack.version_meta.risk_level = task.review.hallucination_risk if task.review else "medium"
    state.last_reviewer_status = ReviewStatus.approved
    state.retry_reason = None
    _add_event(task, "publish", f"决策包 v{task.decision_pack.version} 已发布")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ensure_metrics(task: TaskRecord) -> TaskMetrics:
    if task.metrics is None:
        task.metrics = TaskMetrics(
            total_duration_ms=0,
            stage_durations={},
            evidence_count=0,
            conflict_count=0,
            intervention_count=0,
        )
    return task.metrics


def _record_stage_start(task: TaskRecord, stage: str) -> int:
    task.updated_at = datetime.now(timezone.utc)
    return _now_ms()


def _record_stage_end(task: TaskRecord, stage: str, start_time: int) -> None:
    metrics = _ensure_metrics(task)
    duration = max(0, _now_ms() - start_time)
    metrics.stage_durations[stage] = duration
    metrics.total_duration_ms = sum(metrics.stage_durations.values())
    metrics.evidence_count = len(task.evidence)
    metrics.conflict_count = len(task.conflicts)
    metrics.intervention_count = len([event for event in task.events if event.stage in {"force_rerun", "memory_recall", "repair", "publish"}])
    task.metrics = metrics
    task.updated_at = datetime.now(timezone.utc)


def _add_event(task: TaskRecord, stage: str, message: str, status: TaskStatus | None = None) -> None:
    from app.models.schemas import TaskEvent

    task.events.append(
        TaskEvent(
            task_id=task.id,
            stage=stage,
            message=message,
            status=status or task.status,
        )
    )
    if status is not None:
        task.status = status
    task.updated_at = datetime.now(timezone.utc)


def _summarize_metrics(task: TaskRecord) -> None:
    metrics = _ensure_metrics(task)
    metrics.total_duration_ms = sum(metrics.stage_durations.values())
    metrics.evidence_count = len(task.evidence)
    metrics.conflict_count = len(task.conflicts)
    metrics.intervention_count = len([event for event in task.events if event.stage in {"force_rerun", "memory_recall", "repair", "publish"}])
    task.metrics = metrics



def planner(state: WorkflowState) -> WorkflowState:
    task = state["task"]
    start_time = _record_stage_start(task, "planner")
    task.status = TaskStatus.running

    profile = _get_profile(task)
    focus_attributes = profile.focus_attributes
    research_tasks: list[ResearchTask] = []
    url_bindings = task.request.get_url_bindings()

    for competitor, url in url_bindings:
        research_tasks.append(
            ResearchTask(
                competitor=competitor,
                source_type="url",
                query_or_url=url,
                dimension=EvidenceDimension.feature,
                priority=1,
            )
        )

    for competitor in task.request.competitors:
        query = f"{competitor} {task.request.product_goal}"
        if focus_attributes:
            query = _build_focus_search_query(competitor, focus_attributes, task.request.product_goal)
        research_tasks.append(
            ResearchTask(
                competitor=competitor,
                source_type="search",
                query_or_url=query,
                dimension=EvidenceDimension.feature,
                priority=2,
            )
        )
        if focus_attributes and any(attr in ("价格", "定价", "price", "pricing") for attr in focus_attributes):
            research_tasks.append(
                ResearchTask(
                    competitor=competitor,
                    source_type="search",
                    query_or_url=f"{competitor} {' '.join(focus_attributes)} 价格 促销",
                    dimension=EvidenceDimension.pricing,
                    priority=2,
                )
            )

    if task.request.comments:
        research_tasks.append(
            ResearchTask(
                competitor=task.request.competitors[0],
                source_type="comment",
                query_or_url=task.request.comments,
                dimension=EvidenceDimension.user_feedback,
                priority=1,
            )
        )

    for image_name in task.request.image_names:
        research_tasks.append(
            ResearchTask(
                competitor=task.request.competitors[0],
                source_type="image",
                query_or_url=image_name,
                dimension=EvidenceDimension.positioning,
                priority=1,
            )
        )

    task.research_plan = ResearchPlan(
        tasks=research_tasks,
        dimensions=_get_mandatory_dimensions(task),
        keywords=_extract_keywords(task.request.product_goal) + focus_attributes,
    )
    _add_event(task, "planner", f"已拆解竞品范围、输入来源；分析策略={profile.strategy_label()}，关注属性={focus_attributes or '默认'}")
    _record_stage_end(task, "planner", start_time)
    return {"task": task}


def writer(state: WorkflowState) -> WorkflowState:
    task = state["task"]
    start_time = _record_stage_start(task, "writer")
    task.status = TaskStatus.running

    if not task.evidence:
        _add_event(task, "writer", "错误：没有可用证据，无法生成决策包", TaskStatus.failed)
        _record_stage_end(task, "writer", start_time)
        return {"task": task}

    try:
        prompt = _build_writer_prompt(task)
        result = llm_client.chat_completion_json_sync([
            {"role": "system", "content": "你是一个专业的竞品情报分析师，擅长基于结构化证据进行分析。"},
            {"role": "user", "content": prompt},
        ])

        def _build_actions(items: list[dict[str, object]], fallback_dimension: EvidenceDimension) -> list[DecisionAction]:
            actions: list[DecisionAction] = []
            for item in items:
                action = DecisionAction(
                    title=str(item["title"]),
                    dimension=EvidenceDimension(str(item.get("dimension", fallback_dimension.value))),
                    recommendation=str(item["recommendation"]),
                    rationale=str(item["rationale"]),
                    evidence_ids=list(item["evidence_ids"]),
                    priority=str(item.get("priority", "P0")),
                )
                valid, invalid_ids = _validate_evidence_ids(task, action.evidence_ids)
                if not valid:
                    raise LLMError(f"LLM 输出包含无效的 Evidence ID: {invalid_ids}")
                actions.append(action)
            return actions

        pricing_actions: list[DecisionAction] = []
        if any(ev.dimension == EvidenceDimension.pricing for ev in task.evidence):
            pricing_actions = _build_actions(list(result.get("pricing_insights", [])), EvidenceDimension.pricing)
            pricing_evidence_ids = {ev.id for ev in task.evidence if ev.dimension == EvidenceDimension.pricing}
            for action in pricing_actions:
                if not any(eid in pricing_evidence_ids for eid in action.evidence_ids):
                    raise LLMError(f"pricing_insights 必须引用定价相关证据，但 {action.evidence_ids} 中无定价证据")

        task.decision_pack = DecisionPack(
            positioning=_build_actions(list(result.get("positioning", [])), EvidenceDimension.positioning),
            mvp_priorities=_build_actions(list(result.get("mvp_priorities", [])), EvidenceDimension.feature),
            pricing_insights=pricing_actions,
            battlecard=_build_actions(list(result.get("battlecard", [])), EvidenceDimension.positioning),
            summary=str(result.get("summary", "决策包已生成")),
        )
        _persist_decision_pack(task, "writer", [task.request.product_goal])
        _commit_decision_memory(task, "writer")
        _add_event(task, "writer", f"已通过 LLM 生成带 Evidence ID 的决策包 v{task.decision_pack.version}")
    except LLMNotConfiguredError:
        task.decision_pack = _build_rule_based_decision_pack(task)
        _persist_decision_pack(task, "writer", [task.request.product_goal])
        _commit_decision_memory(task, "writer")
        _add_event(task, "writer", f"LLM 未配置，已生成规则版决策包 v{task.decision_pack.version}")

    _record_stage_end(task, "writer", start_time)
    return {"task": task}


def _build_reviewer_prompt(task: TaskRecord) -> str:
    pack = task.decision_pack
    evidence_lines = []
    for item in task.evidence[:20]:
        evidence_lines.append(
            f"- {item.id} | {item.dimension.value} | {item.competitor} | {item.claim[:80]}"
        )
    review_notes = "；".join(task.review.notes) if task.review and task.review.notes else "无"
    pack_summary = pack.summary if pack else "无决策包"
    pack_version = pack.version if pack else 0
    recall_count = task.memory_state.last_recall_count if task.memory_state else 0
    return "\n".join([
        f"任务目标: {task.request.product_goal}",
        f"竞品: {'，'.join(task.request.competitors)}",
        f"决策包版本: {pack_version}",
        f"当前回流轮次: {task.rewrite_round}",
        f"最近 Reviewer 结论: {review_notes}",
        f"最近召回条目数: {recall_count}",
        f"决策包摘要: {pack_summary}",
        "证据清单:",
        *evidence_lines,
        "请只返回 JSON，字段包括 score_adjustment、hallucination_risk、notes。",
        "其中 score_adjustment 取值范围建议为 -0.2 到 0.2。",
    ])


def reviewer(state: WorkflowState) -> WorkflowState:
    task = state["task"]
    start_time = _record_stage_start(task, "reviewer")

    if task.status == TaskStatus.failed:
        _add_event(task, "reviewer", "任务已失败，跳过复核", TaskStatus.failed)
        task.review = ReviewScore(score=0.0, citation_precision=0.0, claim_support_rate=0.0, hallucination_risk="high", notes=["任务已失败，未进行复核"])
        _record_stage_end(task, "reviewer", start_time)
        return {"task": task}

    evidence_by_id = {item.id: item for item in task.evidence}
    support_map = {
        EvidenceDimension.positioning: {EvidenceDimension.positioning, EvidenceDimension.user_feedback},
        EvidenceDimension.feature: {EvidenceDimension.feature, EvidenceDimension.pricing, EvidenceDimension.user_feedback},
        EvidenceDimension.pricing: {EvidenceDimension.pricing},
        EvidenceDimension.user_feedback: {EvidenceDimension.user_feedback},
        EvidenceDimension.risk: {EvidenceDimension.risk, EvidenceDimension.user_feedback},
    }
    action_ids: list[str] = []
    dimension_supported = True
    empty_recommendation = False
    pricing_insights_valid = True
    pricing_insights_has_pricing_evidence = True
    pricing_evidence_ids = {ev.id for ev in task.evidence if ev.dimension == EvidenceDimension.pricing}

    if task.decision_pack and task.decision_pack.pricing_insights:
        for action in task.decision_pack.pricing_insights:
            for eid in action.evidence_ids:
                if eid not in evidence_by_id:
                    pricing_insights_valid = False
                    break
            if action.evidence_ids and not any(eid in pricing_evidence_ids for eid in action.evidence_ids):
                pricing_insights_has_pricing_evidence = False

    battlecard_valid = True
    battlecard_has_evidence = True
    if task.decision_pack and task.decision_pack.battlecard:
        for action in task.decision_pack.battlecard:
            for eid in action.evidence_ids:
                if eid not in evidence_by_id:
                    battlecard_valid = False
                    break
            if not action.evidence_ids:
                battlecard_has_evidence = False

    if task.decision_pack:
        all_actions = task.decision_pack.positioning + task.decision_pack.mvp_priorities + task.decision_pack.pricing_insights + task.decision_pack.battlecard
        for action in all_actions:
            action_ids.extend(action.evidence_ids)
            allowed = support_map[action.dimension]
            cited_dimensions = {evidence_by_id[item].dimension for item in action.evidence_ids if item in evidence_by_id}
            dimension_supported = dimension_supported and bool(cited_dimensions & allowed)
            if not action.recommendation.strip():
                empty_recommendation = True

    ids_supported = all(item in evidence_by_id for item in action_ids)
    coverage_passed = bool(task.coverage and task.coverage.passed)
    rule_supported = ids_supported and dimension_supported and not empty_recommendation
    base_score = 0.88 if rule_supported and coverage_passed else 0.68 if rule_supported else 0.42
    hallucination_risk = "low" if rule_supported and coverage_passed else "medium" if rule_supported else "high"
    notes: list[str] = []

    if ids_supported:
        notes.append("????????? Evidence ID")
        notes.append("Evidence ID 可追溯")
    else:
        notes.append("????????? Evidence ID")
    notes.append("决策动作引用了匹配维度的 Evidence" if dimension_supported else "存在维度不匹配的引用")
    if empty_recommendation:
        notes.append("存在空的 recommendation")
    if not coverage_passed:
        notes.append("Coverage Gate 未完全达标，输出应作为补证据后的临时建议")
    if task.decision_pack and task.decision_pack.pricing_insights:
        if not pricing_insights_valid:
            notes.append("pricing_insights 存在无效的 evidence_ids")
            hallucination_risk = "high"
        if not pricing_insights_has_pricing_evidence:
            notes.append("pricing_insights ??")
            notes.append("pricing_insights 缺少定价证据")
            hallucination_risk = "high"
    if task.decision_pack and task.decision_pack.battlecard:
        if not battlecard_valid:
            notes.append("battlecard 存在无效的 evidence_ids")
            hallucination_risk = "high"
        if not battlecard_has_evidence:
            notes.append("battlecard 缺少证据支撑，可能存在幻觉风险")
            if hallucination_risk != "high":
                hallucination_risk = "medium"

    llm_adjustment = 0.0
    try:
        if task.decision_pack and (task.decision_pack.positioning or task.decision_pack.pricing_insights or task.decision_pack.battlecard):
            result = llm_client.chat_completion_json_sync([
                {"role": "system", "content": "你是一个专业的情报复核员，负责检查竞品分析决策包的质量。"},
                {"role": "user", "content": _build_reviewer_prompt(task)},
            ])
            llm_adjustment = float(result.get("score_adjustment", 0.0))
            llm_hallucination = result.get("hallucination_risk", hallucination_risk)
            notes.extend(result.get("notes", []))
            if llm_hallucination == "high":
                hallucination_risk = "high"
            elif llm_hallucination == "medium" and hallucination_risk == "low":
                hallucination_risk = "medium"
            _add_event(task, "reviewer", "已通过 LLM 完成智能复核")
        else:
            _add_event(task, "reviewer", "无决策包需要 LLM 复核")
    except LLMNotConfiguredError:
        notes.append("LLM 未配置，仅进行规则校验")
        _add_event(task, "reviewer", "LLM 未配置，仅进行规则校验")
    except LLMError as e:
        notes.append(f"LLM 复核失败: {str(e)[:50]}")
        _add_event(task, "reviewer", f"LLM 复核失败，仅进行规则校验: {str(e)[:50]}")

    final_score = max(0.0, min(1.0, base_score + llm_adjustment))
    claim_support_rate = 1.0 if rule_supported else (0.7 if llm_adjustment > -0.2 else 0.4)
    task.review = ReviewScore(
        score=final_score,
        citation_precision=1.0 if ids_supported else 0.0,
        claim_support_rate=claim_support_rate,
        hallucination_risk=hallucination_risk,
        notes=notes or ["复核完成"],
    )
    _recall_decision_memory(task)
    _commit_decision_memory(task, "reviewer")
    state = _ensure_memory_state(task)

    if not rule_supported:
        state.last_reviewer_status = ReviewStatus.rejected
        state.retry_reason = "规则校验未通过，无法进入修复回流"
        task.status = TaskStatus.failed
        task.memory_state = state
        _add_event(task, "reviewer", "Reviewer 复核失败：规则校验未通过", TaskStatus.failed)
    elif _should_retry(task):
        state.last_reviewer_status = ReviewStatus.needs_retry
        state.retry_reason = task.review.notes[0] if task.review and task.review.notes else "Reviewer 评分不足，需要修复回流"
        task.status = TaskStatus.running
        task.memory_state = state
        _add_event(task, "reviewer", f"Reviewer 未通过，进入修复回流：{state.retry_reason}")
    else:
        _finalize_publish(task)
        state.last_reviewer_status = ReviewStatus.approved
        state.retry_reason = None
        task.status = TaskStatus.completed
        task.memory_state = state

    _add_event(task, "reviewer", "Reviewer 已完成引用核验", task.status)
    _record_stage_end(task, "reviewer", start_time)
    return {"task": task}


def route_after_reviewer(state: WorkflowState) -> str:
    task = state["task"]
    state_snapshot = _ensure_memory_state(task)
    if task.review is None:
        return END
    if task.review.score >= REVIEW_PUBLISH_THRESHOLD and task.review.hallucination_risk == "low":
        return END
    if state_snapshot.current_iteration >= 1 or task.rewrite_round >= 1:
        return END
    if task.review.score < REVIEW_RETRY_THRESHOLD or task.review.hallucination_risk == "high":
        return "writer"
    return END


def route_after_coverage(state: WorkflowState) -> str:
    return "conflict_resolver"


def build_graph():
    if StateGraph is None:
        return None
    graph = StateGraph(WorkflowState)
    graph.add_node("planner", planner)
    graph.add_node("research", research)
    graph.add_node("evidence", evidence_normalizer)
    graph.add_node("coverage_gate", coverage_gate)
    graph.add_node("conflict_resolver", conflict_resolver)
    graph.add_node("writer", writer)
    graph.add_node("reviewer", reviewer)
    graph.set_entry_point("planner")
    graph.add_edge("planner", "research")
    graph.add_edge("research", "evidence")
    graph.add_edge("evidence", "coverage_gate")
    graph.add_conditional_edges("coverage_gate", route_after_coverage, {"conflict_resolver": "conflict_resolver"})
    graph.add_edge("conflict_resolver", "writer")
    graph.add_edge("writer", "reviewer")
    graph.add_conditional_edges("reviewer", route_after_reviewer, {"writer": "writer", END: END})
    return graph.compile()


def run_fallback(task: TaskRecord) -> TaskRecord:
    if task.metrics is None:
        task.metrics = TaskMetrics(total_duration_ms=0, stage_durations={}, evidence_count=0, conflict_count=0, intervention_count=0)
    state: WorkflowState = {"task": task}
    state = planner(state)
    state = research(state)
    state = evidence_normalizer(state)
    state = coverage_gate(state)
    state = conflict_resolver(state)

    while True:
        state = writer(state)
        task = state["task"]
        if task.status == TaskStatus.failed:
            break
        state = reviewer(state)
        task = state["task"]
        if task.status == TaskStatus.failed:
            break
        if _should_stop(task):
            break
        if not _should_retry(task):
            break
        recalled = _recall_decision_memory(task)
        if not _repair_decision_pack(task, recalled):
            break
        state = {"task": task}

    task = state["task"]
    if task.status not in (TaskStatus.failed, TaskStatus.cancelled):
        if task.review and task.review.score >= REVIEW_PUBLISH_THRESHOLD and task.review.hallucination_risk == "low":
            task.status = TaskStatus.completed
        elif task.review is not None and task.review.score >= 0.6:
            task.status = TaskStatus.completed
        elif task.review is not None:
            task.status = TaskStatus.failed
    _summarize_metrics(task)
    task_store.update(task)
    return task


def run_competitive_intelligence_workflow(task: TaskRecord) -> TaskRecord:
    return run_fallback(task)


def rerun_from_stage(task: TaskRecord, stage: str) -> TaskRecord:
    if stage not in VALID_RERUN_STAGES:
        raise ValueError(f"无效的重跑阶段: {stage}")
    state: WorkflowState = {"task": task}
    if stage == "writer":
        state = writer(state)
        task = state["task"]
        if task.status != TaskStatus.failed:
            state = reviewer(state)
            task = state["task"]
    elif stage == "reviewer":
        state = reviewer(state)
        task = state["task"]
    task_store.update(task)
    return task


__all__ = [
    "VALID_RERUN_STAGES",
    "WorkflowCancelled",
    "build_graph",
    "rerun_from_stage",
    "run_competitive_intelligence_workflow",
]
