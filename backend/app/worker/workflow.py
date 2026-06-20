from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
import traceback
from datetime import datetime, timezone
from copy import deepcopy
from typing import TypedDict

logger = logging.getLogger(__name__)

try:
    from langgraph.graph import END, StateGraph
except Exception:  # pragma: no cover - fallback keeps local tests lightweight.
    StateGraph = None
    END = "END"

# Checkpointer 通过独立模块管理，支持 PostgreSQL / Memory 双后端 + 降级
from app.services.checkpointer import (
    get_checkpointer,
    get_checkpointer_kind,
    get_state_history,
    get_state_snapshot,
    make_thread_config,
    reset_checkpointer,
    resume_workflow,
)

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
from app.services.workflow_observability import (
    finish_run,
    record_checkpoint,
    record_event as record_workflow_event,
    record_stage as record_workflow_stage,
    start_run,
)
from app.services.llm import LLMClient, LLMError, LLMNotConfiguredError, llm_client
from app.services.bad_case import bad_case_manager
from app.core.config import memory_settings, search_settings

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


from typing import Annotated, TypedDict


class WorkflowState(TypedDict):
    """工作流状态。

    使用 Annotated + reducer 避免并发写入冲突：
    - task: 使用 last_write_wins reducer，多个节点写入时保留最后一个
    - events: 使用 operator.add reducer，多个节点可以追加事件
    """
    task: Annotated[TaskRecord, lambda old, new: new if new is not None else old]
    events: Annotated[list, lambda old, new: (old or []) + (new or [])]




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


def _version_tag(pack_id: str, version: int) -> str:
    return f"{pack_id}:v{version}"


def _sync_decision_history(task: TaskRecord, version_meta: DecisionPackVersion) -> None:
    task.decision_history = [
        item for item in task.decision_history
        if not (item.pack_id == version_meta.pack_id and item.version == version_meta.version)
    ]
    task.decision_history.append(version_meta)
    task.decision_history.sort(key=lambda item: (item.version, item.created_at))


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
    """将当前阶段的决策产物写入记忆系统。

    在 writer/reviewer/repair 三个阶段结束时调用，把决策包、证据、冲突、
    修复补丁、复核反馈等材料拆分为记忆块并持久化。写入后更新 task 的
    memory_state.latest_memory_ids，并通过 _add_event 记录写入数量。

    Args:
        task: 当前任务记录，必须已包含 decision_pack
        stage: 工作流阶段，取值 writer/reviewer/repair
        repair_summary: 修复阶段专用的修复摘要，仅 stage=repair 时传入
        repair_notes: 修复阶段的详细备注列表，仅 stage=repair 时传入
        review: 复核评分，reviewer 阶段传入；若未传则从 task.review 取

    Returns:
        本次写入的记忆块列表，空列表表示 task 无 decision_pack
    """
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
    """从记忆系统中召回与当前任务相关的历史决策。

    在 reviewer 复核阶段和 repair 修复回流阶段调用，构建查询（产品目标 +
    竞品列表 + 复核备注 + 冲突裁决）后通过 Hybrid RAG 检索历史记忆块。
    召回结果会更新 memory_state.latest_memory_ids，并通过 _add_event 记录
    召回数量和摘要，供后续修复 prompt 注入使用。

    Args:
        task: 当前任务记录

    Returns:
        召回的记忆块列表，每项为 (DecisionMemoryItem, score) 二元组，
        score 越高表示越相关。空列表表示未召回到可复用的历史记忆。
    """
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
        top_k=memory_settings.recall_top_k,
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
    _record_workflow_checkpoint(task, "stage_start", stage=stage, status="running", payload={"task_status": task.status.value})
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
    stage_status = "failed" if task.status == TaskStatus.failed else "ok"
    record_workflow_stage(task.id, stage, duration, status=stage_status)
    _record_workflow_checkpoint(
        task,
        "stage_end",
        stage=stage,
        status=task.status.value,
        payload={
            "duration_ms": duration,
            "metrics": metrics.model_dump(mode="json"),
        },
    )


def _record_task_error(task: TaskRecord, stage: str, exc: Exception) -> None:
    task.last_error_stage = stage
    task.last_error_message = str(exc)
    task.last_error_traceback = traceback.format_exc()
    state = _ensure_memory_state(task)
    state.last_error_stage = stage
    state.last_error_message = task.last_error_message
    _record_workflow_checkpoint(
        task,
        "error",
        stage=stage,
        status="failed",
        payload={
            "error_message": task.last_error_message,
            "traceback": task.last_error_traceback,
        },
    )


def _record_workflow_checkpoint(
    task: TaskRecord,
    kind: str,
    *,
    stage: str | None = None,
    status: str = "ok",
    payload: dict[str, object] | None = None,
) -> str | None:
    checkpoint_id = record_checkpoint(task.id, kind, stage=stage, status=status, payload=payload)
    if checkpoint_id:
        state = _ensure_memory_state(task)
        state.last_checkpoint_id = checkpoint_id
        if stage:
            state.last_stage = stage
    return checkpoint_id


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
    record_workflow_event(task.id, stage, message, task.status.value)


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
        # 为每个竞品都创建 comment 任务，确保 user_feedback 维度全覆盖
        for competitor in task.request.competitors:
            research_tasks.append(
                ResearchTask(
                    competitor=competitor,
                    source_type="comment",
                    query_or_url=task.request.comments,
                    dimension=EvidenceDimension.user_feedback,
                    priority=1,
                )
            )
        # 当评论中包含价格/特性关键词时，额外生成 pricing/feature 维度的证据
        comments_lower = task.request.comments.lower()
        pricing_keywords = ["价格", "定价", "元", "价", "cost", "price", "pricing", "¥", "$"]
        feature_keywords = ["风力", "风速", "噪音", "分贝", "续航", "电池", "档位", "充电", "便携", "重量",
                            "wind", "speed", "noise", "battery", "portable", "feature", "功能", "参数"]
        if any(kw in comments_lower for kw in pricing_keywords):
            for competitor in task.request.competitors:
                research_tasks.append(
                    ResearchTask(
                        competitor=competitor,
                        source_type="comment",
                        query_or_url=task.request.comments,
                        dimension=EvidenceDimension.pricing,
                        priority=2,
                    )
                )
        if any(kw in comments_lower for kw in feature_keywords):
            for competitor in task.request.competitors:
                research_tasks.append(
                    ResearchTask(
                        competitor=competitor,
                        source_type="comment",
                        query_or_url=task.request.comments,
                        dimension=EvidenceDimension.feature,
                        priority=2,
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
    logger.info(f"Planner generated {len(research_tasks)} research tasks for task {task.id}")
    for rt in research_tasks:
        logger.info(f"  ResearchTask: {rt.source_type} | {rt.dimension} | {rt.competitor}")
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
    except LLMError as e:
        task.status = TaskStatus.failed
        _add_event(task, "writer", f"LLM 输出验证失败: {str(e)}", TaskStatus.failed)

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
        notes.append("所有 Evidence ID 有效")
        notes.append("所有决策动作均绑定 Evidence ID")
        notes.append("????????? Evidence ID")
        notes.append("Evidence ID 可追溯")
    else:
        notes.append("存在无效的 Evidence ID")
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
            notes.append("pricing_insights 缺少定价证据")
            notes.append("pricing_insights ??")
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
    elif task.review.score >= REVIEW_PUBLISH_THRESHOLD and task.review.hallucination_risk == "low":
        _finalize_publish(task)
        state.last_reviewer_status = ReviewStatus.approved
        state.retry_reason = None
        task.status = TaskStatus.completed
        task.memory_state = state
    elif _should_retry(task):
        state.last_reviewer_status = ReviewStatus.needs_retry
        state.retry_reason = task.review.notes[0] if task.review and task.review.notes else "Reviewer 评分不足，需要修复回流"
        task.status = TaskStatus.running
        task.memory_state = state
        _add_event(task, "reviewer", f"Reviewer 未通过，进入修复回流：{state.retry_reason}")
    else:
        state.last_reviewer_status = ReviewStatus.approved
        state.retry_reason = None
        task.status = TaskStatus.completed
        task.memory_state = state
        _add_event(task, "reviewer", "Reviewer 复核通过：达到完成条件，未触发发布阈值")

    _add_event(task, "reviewer", "Reviewer 已完成引用核验", task.status)
    _record_stage_end(task, "reviewer", start_time)
    return {"task": task}


def repair(state: WorkflowState) -> WorkflowState:
    task = state["task"]
    start_time = _record_stage_start(task, "repair")

    if task.status == TaskStatus.failed:
        _add_event(task, "repair", "任务已失败，跳过修复", TaskStatus.failed)
        _record_stage_end(task, "repair", start_time)
        return {"task": task}

    recalled = _recall_decision_memory(task)
    if not _repair_decision_pack(task, recalled):
        _add_event(task, "repair", "未能生成可用的修复版本，终止回流")
        _record_stage_end(task, "repair", start_time)
        return {"task": task}

    _add_event(task, "repair", f"已完成修复回流，准备重新写作 v{task.decision_pack.version}")
    _record_stage_end(task, "repair", start_time)
    return {"task": task}


def repair(state: WorkflowState) -> WorkflowState:
    task = state["task"]
    start_time = _record_stage_start(task, "repair")

    if task.status == TaskStatus.failed:
        _add_event(task, "repair", "任务已失败，跳过修复", TaskStatus.failed)
        _record_stage_end(task, "repair", start_time)
        return {"task": task}

    recalled = _recall_decision_memory(task)
    if not _repair_decision_pack(task, recalled):
        _add_event(task, "repair", "未能生成可用的修复版本，终止回流")
        _record_stage_end(task, "repair", start_time)
        return {"task": task}

    _add_event(task, "repair", f"已完成修复回流，准备重新写作 v{task.decision_pack.version}")
    _record_stage_end(task, "repair", start_time)
    return {"task": task}


def route_after_reviewer(state: WorkflowState) -> str:
    task = state["task"]
    state_snapshot = _ensure_memory_state(task)
    if task.review is None:
        return END
    if task.review.score >= REVIEW_PUBLISH_THRESHOLD and task.review.hallucination_risk == "low":
        return END

    # ============ 死循环三层防御 ============
    # 在决定是否进入 repair/writer 循环前，检查 LoopGuard
    try:
        from app.services.loop_guard import get_loop_guard

        thread_id = task.thread_id or task.id
        guard = get_loop_guard(thread_id)
        # 用 review score + iteration 作为状态指纹
        state_fingerprint = f"score={task.review.score:.3f}|risk={task.review.hallucination_risk}|iter={state_snapshot.current_iteration}|rewrite={task.rewrite_round}"
        guard_result = guard.check(state_fingerprint, state_fingerprint)
        if guard_result.blocked:
            _add_event(
                task,
                "reviewer",
                f"死循环防御触发 [{guard_result.layer}]: {guard_result.reason}，终止循环",
                task.status,
            )
            _detect_and_record_bad_cases(task)
            return END
    except Exception as e:
        logger.warning(f"LoopGuard 检查失败，忽略: {e}")

    if task.review.score < REVIEW_RETRY_THRESHOLD or task.review.hallucination_risk == "high":
        if state_snapshot.current_iteration >= 1 or task.rewrite_round >= 1:
            _detect_and_record_bad_cases(task)
            return END
        return "repair"

    if not search_settings.is_configured and memory_settings.allow_degraded_mode:
        _add_event(task, "reviewer", "搜索API未配置，启用降级模式，允许输出决策包")
        _detect_and_record_bad_cases(task)
        return END

    if state_snapshot.current_iteration >= 1 or task.rewrite_round >= 1:
        _detect_and_record_bad_cases(task)
        return END
    return END


def _detect_and_record_bad_cases(task: TaskRecord) -> None:
    """检测并记录任务中的 Bad Case。

    workflow 在后台线程中同步执行，使用 asyncio.run() 调用 async 的
    bad_case_manager 方法，通过 AsyncSessionLocal 创建独立 session。
    """
    try:
        task_dict = task.model_dump(mode="json")

        async def _run():
            from app.db.session import AsyncSessionLocal
            async with AsyncSessionLocal() as session:
                return await bad_case_manager.detect_bad_cases_from_task(task_dict, session)

        bad_cases = asyncio.run(_run())
        if bad_cases:
            case_ids = ", ".join([bc.id for bc in bad_cases])
            _add_event(task, "bad_case", f"检测到 {len(bad_cases)} 个 Bad Case: {case_ids}")
            logger.info(f"Task {task.id} - Bad Cases detected: {len(bad_cases)}")
    except Exception as e:
        logger.error(f"Failed to detect bad cases for task {task.id}: {e}")


def route_after_coverage(state: WorkflowState) -> str:
    return "conflict_resolver"


def build_graph():
    if StateGraph is None:
        return None
    # 获取各节点的 RetryPolicy（不可用时为空字典）
    try:
        from app.services.retry_policy import get_node_retry_policies

        retry_policies = get_node_retry_policies()
    except Exception:
        retry_policies = {}

    graph = StateGraph(WorkflowState)

    # 添加节点时应用 RetryPolicy（不可用时为空字典）
    # 注意：不同 LangGraph 版本 add_node 的 retry_policy 参数支持不同，需兼容处理
    def _add_node(name: str, func):
        policy = retry_policies.get(name)
        if policy is not None:
            try:
                graph.add_node(name, func, retry=policy)
            except TypeError:
                # 旧版本不支持 retry 参数，降级为普通添加
                graph.add_node(name, func)
        else:
            graph.add_node(name, func)

    _add_node("planner", planner)
    _add_node("research", research)
    _add_node("evidence", evidence_normalizer)
    _add_node("coverage_gate", coverage_gate)
    _add_node("conflict_resolver", conflict_resolver)
    _add_node("writer", writer)
    _add_node("reviewer", reviewer)
    _add_node("repair", repair)

    graph.set_entry_point("planner")
    graph.add_edge("planner", "research")
    graph.add_edge("research", "evidence")
    graph.add_edge("evidence", "coverage_gate")
    graph.add_conditional_edges("coverage_gate", route_after_coverage, {"conflict_resolver": "conflict_resolver"})
    graph.add_edge("conflict_resolver", "writer")
    graph.add_edge("writer", "reviewer")
    graph.add_conditional_edges("reviewer", route_after_reviewer, {"repair": "repair", END: END})
    graph.add_edge("repair", "writer")

    # Human-in-the-loop 中断配置
    try:
        from app.services.hitl import make_interrupt_config

        interrupt_config = make_interrupt_config()
    except Exception:
        interrupt_config = {}

    checkpointer = get_checkpointer()
    if checkpointer is not None:
        logger.info(
            f"build_graph 使用 {get_checkpointer_kind()} checkpointer, "
            f"RetryPolicy 节点: {list(retry_policies.keys())}, "
            f"HITL 中断: {interrupt_config}"
        )
        return graph.compile(checkpointer=checkpointer, **interrupt_config)
    return graph.compile(**interrupt_config)


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


def run_competitive_intelligence_workflow(task: TaskRecord, thread_id: str | None = None, request_id: str | None = None) -> TaskRecord:
    """执行竞争情报分析工作流。
    
    优先使用 LangGraph 执行；若 LangGraph 不可用，降级到顺序执行。
    """
    thread_id = thread_id or task.id
    run_id = start_run(task.id, thread_id, request_id, backend="langgraph" if StateGraph is not None else "fallback")
    task.run_id = run_id
    task.thread_id = thread_id
    task.request_id = request_id
    state = _ensure_memory_state(task)
    state.current_run_id = run_id
    state.last_stage = "workflow_start"
    task.memory_state = state
    _record_workflow_checkpoint(
        task,
        "workflow_start",
        stage="workflow",
        status="running",
        payload={
            "backend": "langgraph" if StateGraph is not None else "fallback",
            "thread_id": thread_id,
            "request_id": request_id,
        },
    )
    task_store.update(task)

    graph = build_graph()
    fallback_error: Exception | None = None
    if graph is not None:
        logger.info(f"任务 {task.id} 使用 LangGraph 执行工作流 (thread_id={thread_id}, checkpointer={get_checkpointer_kind()})")
        try:
            config = make_thread_config(thread_id)
            result = graph.invoke({"task": task}, config=config)
            task = result["task"]
            task.run_id = run_id
            task.thread_id = thread_id
            task.request_id = request_id
            _record_workflow_checkpoint(
                task,
                "workflow_end",
                stage="workflow",
                status=task.status.value,
                payload={
                    "backend": "langgraph",
                    "fallback_used": False,
                },
            )
            _summarize_metrics(task)
            task_store.update(task)
            finish_run(task.id, task.status.value, fallback_used=False)
            return task
        except Exception as e:
            logger.exception("LangGraph 执行失败，降级到 fallback")
            _record_task_error(task, "langgraph", e)
            task.status = TaskStatus.failed
            record_workflow_event(task.id, "workflow", f"LangGraph 执行失败，切换到 fallback: {e}", "failed")
            task_store.update(task)
            fallback_error = e

    logger.info(f"任务 {task.id} 使用 fallback 模式执行工作流")
    try:
        task = run_fallback(task)
        task.run_id = run_id
        task.thread_id = thread_id
        task.request_id = request_id
        _record_workflow_checkpoint(
            task,
            "workflow_end",
            stage="workflow",
            status=task.status.value,
            payload={
                "backend": "fallback",
                "fallback_used": fallback_error is not None,
            },
        )
    except Exception as exc:
        _record_task_error(task, "fallback", exc)
        task.status = TaskStatus.failed
        task_store.append_event(task, "workflow", f"fallback 执行失败: {exc}", TaskStatus.failed)
        task_store.update(task)
        finish_run(
            task.id,
            task.status.value,
            fallback_used=fallback_error is not None,
            error_stage="fallback",
            error_message=str(exc),
            traceback=task.last_error_traceback,
        )
        raise

    if task.status in (TaskStatus.completed, TaskStatus.failed, TaskStatus.cancelled):
        _record_workflow_checkpoint(
            task,
            "workflow_end",
            stage="workflow",
            status=task.status.value,
            payload={
                "backend": "fallback",
                "fallback_used": fallback_error is not None,
            },
        )
    finish_run(
        task.id,
        task.status.value,
        fallback_used=True,
        error_stage="langgraph" if fallback_error is not None else None,
        error_message=str(fallback_error) if fallback_error is not None else None,
        traceback=task.last_error_traceback if fallback_error is not None else None,
    )
    return task


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


def resume_interrupted_workflow(task: TaskRecord) -> TaskRecord | None:
    """从最后一个 checkpoint 恢复中断的工作流。

    基于 LangGraph Checkpointer 实现：
    1. 通过 thread_id 定位最后的 checkpoint
    2. 调用 graph.invoke(None, config) 从中断点继续
    3. 如果没有 checkpoint 或恢复失败，返回 None

    Args:
        task: 任务记录（需要 task.thread_id）

    Returns:
        恢复后的 task 或 None
    """
    thread_id = task.thread_id or task.id
    graph = build_graph()
    if graph is None:
        logger.warning("LangGraph 不可用，无法恢复工作流")
        return None

    try:
        # 检查是否有可恢复的 checkpoint
        snapshot = get_state_snapshot(graph, thread_id)
        if snapshot is None:
            logger.info(f"任务 {task.id} 没有可恢复的 checkpoint")
            return None

        if not snapshot.next:
            # next 为空表示工作流已完成
            logger.info(f"任务 {task.id} 工作流已完成，无需恢复")
            return task

        logger.info(f"任务 {task.id} 从 checkpoint 恢复，当前节点: {snapshot.next}")
        record_workflow_event(
            task.id,
            "workflow",
            f"从 checkpoint 恢复执行，当前节点: {snapshot.next}",
            "running",
        )

        result = resume_workflow(graph, thread_id)
        if result is None:
            return None

        resumed_task = result.get("task", task)
        task_store.update(resumed_task)
        return resumed_task
    except Exception as e:
        logger.error(f"恢复工作流失败: {e}", exc_info=True)
        record_workflow_event(task.id, "workflow", f"恢复工作流失败: {e}", "failed")
        return None


def get_workflow_state_history(task: TaskRecord) -> list:
    """获取工作流状态历史（Time Travel 支持）。

    Args:
        task: 任务记录

    Returns:
        状态快照列表（按时间倒序）
    """
    thread_id = task.thread_id or task.id
    graph = build_graph()
    if graph is None:
        return []
    return get_state_history(graph, thread_id)


__all__ = [
    "VALID_RERUN_STAGES",
    "WorkflowCancelled",
    "build_graph",
    "get_workflow_state_history",
    "resume_interrupted_workflow",
    "rerun_from_stage",
    "run_competitive_intelligence_workflow",
]
