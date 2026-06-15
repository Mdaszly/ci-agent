from __future__ import annotations

import hashlib
import logging
import time
from typing import TypedDict

logger = logging.getLogger(__name__)

try:
    from langgraph.graph import END, StateGraph
except Exception:  # pragma: no cover - fallback keeps local tests lightweight.
    END = "__end__"
    StateGraph = None

from app.models.schemas import (
    BudgetUsage,
    Claim,
    Conflict,
    CoverageGateResult,
    DecisionAction,
    DecisionPack,
    Evidence,
    EvidenceDimension,
    ResearchPlan,
    ResearchTask,
    ReviewScore,
    SourceType,
    TaskMetrics,
    TaskRecord,
    TaskStatus,
)
from app.services.store import task_store
from app.services.llm import LLMClient, LLMError, LLMNotConfiguredError

llm_client = LLMClient()


REQUIRED_DIMENSIONS = [
    EvidenceDimension.feature,
    EvidenceDimension.pricing,
    EvidenceDimension.user_feedback,
]


class WorkflowState(TypedDict):
    task: TaskRecord


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _add_event(
    task: TaskRecord,
    stage: str,
    message: str,
    status: TaskStatus = TaskStatus.running,
) -> None:
    task_store.append_event(task, stage, message, status)


def _record_stage_start(task: TaskRecord, stage: str) -> float:
    """记录阶段开始时间，返回时间戳（毫秒）"""
    if task.metrics is not None:
        return time.time() * 1000
    return 0.0


def _record_stage_end(task: TaskRecord, stage: str, start_time: float) -> None:
    """记录阶段结束时间，计算耗时并存储到 stage_durations"""
    if task.metrics is not None and start_time > 0:
        end_time = time.time() * 1000
        duration_ms = int(end_time - start_time)
        if task.metrics.stage_durations is None:
            task.metrics.stage_durations = {}
        task.metrics.stage_durations[stage] = duration_ms


def _summarize_metrics(task: TaskRecord) -> None:
    """汇总任务指标"""
    if task.metrics is None:
        return
    
    # total_duration_ms: 计算所有阶段耗时总和
    if task.metrics.stage_durations:
        task.metrics.total_duration_ms = sum(task.metrics.stage_durations.values())
    else:
        task.metrics.total_duration_ms = 0
    
    # evidence_count
    task.metrics.evidence_count = len(task.evidence)
    
    # conflict_count
    task.metrics.conflict_count = len(task.conflicts)
    
    # intervention_count: 计算 stage == "human_intervention" 的数量
    task.metrics.intervention_count = sum(1 for event in task.events if event.stage == "human_intervention")


def planner(state: WorkflowState) -> WorkflowState:
    task = state["task"]
    start_time = _record_stage_start(task, "planner")
    
    task.status = TaskStatus.running
    source_count = len(task.request.urls) + (1 if task.request.comments else 0) + len(task.request.image_names)
    estimated_tokens = 1200 + source_count * 900
    estimated_cost = round(estimated_tokens / 1000 * 0.002, 4)
    task.budget_usage = BudgetUsage(
        estimated_sources=source_count,
        estimated_tokens=estimated_tokens,
        estimated_cost_usd=estimated_cost,
        within_budget=(
            source_count <= task.request.budget.max_sources
            and estimated_tokens <= task.request.budget.max_tokens
            and estimated_cost <= task.request.budget.max_cost_usd
        ),
    )

    # 生成 ResearchPlan
    research_tasks: list[ResearchTask] = []

    # 有 URL → 为每个 URL 生成 url 类型的 ResearchTask，competitor 按轮询映射
    if task.request.urls:
        for index, url in enumerate(task.request.urls):
            competitor = task.request.competitors[index % len(task.request.competitors)]
            research_tasks.append(
                ResearchTask(
                    competitor=competitor,
                    source_type="url",
                    query_or_url=str(url),
                    dimension=EvidenceDimension.feature,
                    priority=1,
                )
            )
    # 无 URL → 为每个竞品生成 search 类型的 ResearchTask，query 为 product_goal
    else:
        for competitor in task.request.competitors:
            research_tasks.append(
                ResearchTask(
                    competitor=competitor,
                    source_type="search",
                    query_or_url=task.request.product_goal,
                    dimension=EvidenceDimension.feature,
                    priority=1,
                )
            )

    # 有 comments → 生成一个 comment 类型的 ResearchTask
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

    # 有 image_names → 为每张图生成 image 类型的 ResearchTask
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

    # keywords 从 product_goal 提取（简单按空格分词）
    keywords = task.request.product_goal.split()

    task.research_plan = ResearchPlan(
        tasks=research_tasks,
        dimensions=list(REQUIRED_DIMENSIONS),
        keywords=keywords,
    )

    _add_event(task, "planner", "已拆解竞品范围、输入来源和首版研究维度")
    _record_stage_end(task, "planner", start_time)
    return {"task": task}


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
        # 使用索引作为 key，因为 ResearchTask 是 Pydantic 模型不可哈希
        task_list = list(tasks)
        future_list = [executor.submit(execute_task, t) for t in task_list]

        for future in as_completed(future_list):
            index = future_list.index(future)
            task_item = task_list[index]
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

    # URL 任务
    for index, url in enumerate(request.urls):
        competitor = request.competitors[index % len(request.competitors)]
        research_task = ResearchTask(
            competitor=competitor,
            source_type="url",
            query_or_url=str(url),
            dimension=EvidenceDimension.feature,
            priority=1,
        )
        result = execute_task(research_task)
        if result:
            evidence.extend(result)

    # Search 任务（仅在无 URL 时执行）
    if not request.urls:
        from app.services.search_adapter import search_adapter

        search_evidence: list[Evidence] = []
        for competitor in request.competitors:
            search_evidence.extend(
                search_adapter.search_for_competitor(competitor, request.product_goal)
            )

        if search_evidence:
            evidence.extend(search_evidence)
            search_note = f"，搜索补充 {len(search_evidence)} 条证据"
        else:
            search_note = "，未提供 URL，且未获取到搜索证据"

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
    
    context = f"{task.request.product_goal} {' '.join(task.request.competitors)}"
    
    task.evidence = [evidence_scorer.score(ev, context) for ev in task.evidence]
    
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


def coverage_gate(state: WorkflowState) -> WorkflowState:
    task = state["task"]
    start_time = _record_stage_start(task, "coverage_gate")
    
    high_quality_evidence = [
        ev for ev in task.evidence
        if getattr(ev, "quality_score", 0.0) >= QUALITY_THRESHOLD
        and getattr(ev, "credibility_score", 0.0) >= CREDIBILITY_THRESHOLD
    ]
    
    covered_high_quality = sorted(
        {item.dimension for item in high_quality_evidence},
        key=lambda item: item.value
    )
    
    covered_all = sorted(
        {item.dimension for item in task.evidence},
        key=lambda item: item.value
    )
    
    missing_dimensions = [item for item in REQUIRED_DIMENSIONS if item not in covered_high_quality]
    
    low_quality_dimensions = []
    for dimension in REQUIRED_DIMENSIONS:
        if dimension in covered_all and dimension not in covered_high_quality:
            low_quality_dimensions.append(dimension)
    
    coverage_count = len([d for d in REQUIRED_DIMENSIONS if d in covered_high_quality])
    score = round(coverage_count / len(REQUIRED_DIMENSIONS), 2)
    
    gap_queries = []
    for dimension in missing_dimensions:
        gap_queries.append(f"{task.request.competitors[0]} {dimension.value} evidence")
    
    for dimension in low_quality_dimensions:
        gap_queries.append(f"需补充 {task.request.competitors[0]} {dimension.value} 维度的高质量证据（当前证据质量不足）")
    
    task.coverage = CoverageGateResult(
        passed=len(missing_dimensions) == 0 and len(low_quality_dimensions) == 0,
        score=score,
        covered_dimensions=covered_high_quality,
        missing_dimensions=missing_dimensions,
        gap_queries=gap_queries,
    )
    
    if task.coverage.passed:
        message = "证据覆盖达标，所有维度均有高质量证据支持"
    elif low_quality_dimensions:
        message = f"证据存在质量缺口，{[d.value for d in low_quality_dimensions]} 维度证据质量不足"
    else:
        message = "证据存在缺口，首版以缺口查询提示展示"
    
    _add_event(task, "coverage_gate", message)
    _supplement_search(task)
    _record_stage_end(task, "coverage_gate", start_time)
    return {"task": task}


def _supplement_search(task: TaskRecord) -> None:
    if not task.coverage or task.coverage.passed:
        return
    
    if task.research_round >= 1:
        return
    
    if len(task.evidence) >= task.request.budget.max_sources:
        return
    
    from app.services.search_adapter import search_adapter
    
    if not search_adapter.config.is_configured:
        return
    
    gap_queries = task.coverage.gap_queries if task.coverage else []
    if not gap_queries:
        return
    
    _add_event(task, "coverage_gate", f"证据覆盖未达标，开始补搜一轮，共 {len(gap_queries)} 个缺口查询")
    
    additional_evidence = []
    for query in gap_queries[:3]:
        results = search_adapter.search(query, task.request.competitors[0])
        additional_evidence.extend(results)
    
    if additional_evidence:
        task.evidence.extend(additional_evidence)
        task.research_round += 1
        _add_event(task, "coverage_gate", f"补搜完成，新增 {len(additional_evidence)} 条证据")
    else:
        _add_event(task, "coverage_gate", "补搜未获取到新证据")


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
    
    prompt = f"""
你是一个专业的竞品情报分析师。请根据以下结构化证据，为产品目标提供差异化定位建议、MVP 功能优先级建议、定价洞察和竞争卡片。

## 产品目标
{task.request.product_goal}

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
"""
    
    if task.rewrite_round > 0 and task.review and task.review.notes:
        prompt += f"""
        
## 修正指引（来自 Reviewer）

以下是对你上一次输出的审核意见，请据此进行修改：

{task.review.notes}

请根据上述意见修改你的分析报告，重点关注指出的问题并进行改进。
"""
    
    return prompt.strip()


def _validate_evidence_ids(task: TaskRecord, evidence_ids: list[str]) -> tuple[bool, list[str]]:
    """校验 evidence_ids 是否都存在于任务的证据中"""
    valid_ids = {item.id for item in task.evidence}
    invalid_ids = [eid for eid in evidence_ids if eid not in valid_ids]
    return (len(invalid_ids) == 0, invalid_ids)


def writer(state: WorkflowState) -> WorkflowState:
    import logging
    import sys
    wf_logger = logging.getLogger(__name__)
    wf_logger.info(f"writer function starting")
    sys.stdout.flush()
    task = state["task"]
    start_time = _record_stage_start(task, "writer")
    wf_logger.info(f"writer function for task {task.id}, evidence count: {len(task.evidence)}")
    sys.stdout.flush()
    
    if not task.evidence:
        _add_event(task, "writer", "错误：没有可用证据，无法生成决策包", TaskStatus.failed)
        task.status = TaskStatus.failed
        _record_stage_end(task, "writer", start_time)
        return {"task": task}

    available_evidence_ids = {item.id for item in task.evidence}
    coverage_missing = task.coverage.missing_dimensions if task.coverage else []
    
    # 检查是否有定价相关证据
    has_pricing_evidence = any(ev.dimension == EvidenceDimension.pricing for ev in task.evidence)
    
    try:
        prompt = _build_writer_prompt(task)
        messages = [
            {"role": "system", "content": "你是一个专业的竞品情报分析师，擅长基于结构化证据进行分析。"},
            {"role": "user", "content": prompt}
        ]
        
        result = llm_client.chat_completion_json_sync(messages)
        
        positioning_actions = []
        for item in result.get("positioning", []):
            # Pydantic 校验
            action = DecisionAction(
                title=item["title"],
                dimension=EvidenceDimension(item.get("dimension", "positioning")),
                recommendation=item["recommendation"],
                rationale=item["rationale"],
                evidence_ids=item["evidence_ids"],
                priority=item.get("priority", "P0"),
            )
            
            # 校验 evidence_ids
            valid, invalid_ids = _validate_evidence_ids(task, action.evidence_ids)
            if not valid:
                raise LLMError(f"LLM 输出包含无效的 Evidence ID: {invalid_ids}")
            
            positioning_actions.append(action)
        
        mvp_actions = []
        for item in result.get("mvp_priorities", []):
            # Pydantic 校验
            action = DecisionAction(
                title=item["title"],
                dimension=EvidenceDimension(item.get("dimension", "feature")),
                recommendation=item["recommendation"],
                rationale=item["rationale"],
                evidence_ids=item["evidence_ids"],
                priority=item.get("priority", "P0"),
            )
            
            # 校验 evidence_ids
            valid, invalid_ids = _validate_evidence_ids(task, action.evidence_ids)
            if not valid:
                raise LLMError(f"LLM 输出包含无效的 Evidence ID: {invalid_ids}")
            
            mvp_actions.append(action)
        
        # 解析 pricing_insights
        pricing_actions = []
        if has_pricing_evidence:
            for item in result.get("pricing_insights", []):
                action = DecisionAction(
                    title=item["title"],
                    dimension=EvidenceDimension(item.get("dimension", "pricing")),
                    recommendation=item["recommendation"],
                    rationale=item["rationale"],
                    evidence_ids=item["evidence_ids"],
                    priority=item.get("priority", "P0"),
                )
                
                # 校验 evidence_ids
                valid, invalid_ids = _validate_evidence_ids(task, action.evidence_ids)
                if not valid:
                    raise LLMError(f"LLM 输出包含无效的 Evidence ID: {invalid_ids}")
                
                # 校验 evidence_ids 是否包含定价证据
                pricing_evidence_ids = {ev.id for ev in task.evidence if ev.dimension == EvidenceDimension.pricing}
                if not any(eid in pricing_evidence_ids for eid in action.evidence_ids):
                    raise LLMError(f"pricing_insights 必须引用定价相关证据，但 {action.evidence_ids} 中无定价证据")
                
                pricing_actions.append(action)
        
        # 解析 battlecard
        battlecard_actions = []
        for item in result.get("battlecard", []):
            action = DecisionAction(
                title=item["title"],
                dimension=EvidenceDimension(item.get("dimension", "positioning")),
                recommendation=item["recommendation"],
                rationale=item["rationale"],
                evidence_ids=item["evidence_ids"],
                priority=item.get("priority", "P0"),
            )
            
            # 校验 evidence_ids
            valid, invalid_ids = _validate_evidence_ids(task, action.evidence_ids)
            if not valid:
                raise LLMError(f"LLM 输出包含无效的 Evidence ID: {invalid_ids}")
            
            battlecard_actions.append(action)
        
        task.decision_pack = DecisionPack(
            positioning=positioning_actions,
            mvp_priorities=mvp_actions,
            pricing_insights=pricing_actions,
            battlecard=battlecard_actions,
            summary=result.get("summary", "决策包已生成"),
        )
        _add_event(task, "writer", "已通过 LLM 生成带 Evidence ID 的决策包")
        _record_stage_end(task, "writer", start_time)
    
    except LLMNotConfiguredError:
        _add_event(task, "writer", "错误：未配置 LLM_API_KEY，请配置后重试", TaskStatus.failed)
        task.status = TaskStatus.failed
        _record_stage_end(task, "writer", start_time)
    
    except LLMError as e:
        _add_event(task, "writer", f"LLM 调用失败: {str(e)[:100]}", TaskStatus.failed)
        task.status = TaskStatus.failed
        _record_stage_end(task, "writer", start_time)
    
    except Exception as e:
        _add_event(task, "writer", f"决策包生成失败: {str(e)[:100]}", TaskStatus.failed)
        task.status = TaskStatus.failed
        _record_stage_end(task, "writer", start_time)
    
    return {"task": task}


def _build_reviewer_prompt(task: TaskRecord) -> str:
    evidence_list = []
    for evidence in task.evidence:
        evidence_list.append(f"""
Evidence ID: {evidence.id}
维度: {evidence.dimension.value}
主张: {evidence.claim}
引用: {evidence.quote}
""")
    
    actions_list = []
    if task.decision_pack:
        # 包含所有 4 类决策产物
        all_actions = (
            task.decision_pack.positioning + 
            task.decision_pack.mvp_priorities + 
            task.decision_pack.pricing_insights + 
            task.decision_pack.battlecard
        )
        for action in all_actions:
            actions_list.append(f"""
动作标题: {action.title}
维度: {action.dimension.value}
建议: {action.recommendation}
理由: {action.rationale}
引用证据ID: {', '.join(action.evidence_ids)}
""")
    
    evidence_context = "\n".join(evidence_list)
    actions_context = "\n".join(actions_list)
    
    prompt = f"""
你是一个专业的情报复核员，负责检查竞品分析决策包的质量。

## 可用证据
{evidence_context}

## 待复核的决策动作
{actions_context}

## 复核要求
请检查以下几点：
1. 每条建议是否被引用的证据支持？
2. 是否存在过度推断或没有证据支撑的结论？
3. 是否有幻觉风险（编造事实）？
4. 是否应该降级为"待补证据"？
5. pricing_insights 是否引用了定价相关证据？
6. battlecard 是否有合理的竞争策略建议？

## 输出格式
请严格按照以下 JSON 格式输出：

{{
  "score_adjustment": -0.2,
  "hallucination_risk": "low",
  "notes": ["建议1证据支持不足", "建议2存在过度推断"]
}}

说明：
- score_adjustment: 分数调整值，范围 -0.3 到 0.1，负值表示扣分
- hallucination_risk: "low", "medium", "high" 三者之一
- notes: 复核发现的问题列表
"""
    return prompt.strip()


def reviewer(state: WorkflowState) -> WorkflowState:
    task = state["task"]
    start_time = _record_stage_start(task, "reviewer")
    
    if task.status == TaskStatus.failed:
        _add_event(task, "reviewer", "任务已失败，跳过复核", TaskStatus.failed)
        task.review = ReviewScore(
            score=0.0,
            citation_precision=0.0,
            claim_support_rate=0.0,
            hallucination_risk="high",
            notes=["任务已失败，未进行复核"],
        )
        task_store.update(task)
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
    action_ids = []
    dimension_supported = True
    empty_recommendation = False
    
    # 校验 pricing_insights 的 evidence_ids
    pricing_insights_valid = True
    pricing_insights_has_pricing_evidence = True
    pricing_evidence_ids = {ev.id for ev in task.evidence if ev.dimension == EvidenceDimension.pricing}
    
    if task.decision_pack and task.decision_pack.pricing_insights:
        for action in task.decision_pack.pricing_insights:
            # 检查 evidence_ids 是否有效
            for eid in action.evidence_ids:
                if eid not in evidence_by_id:
                    pricing_insights_valid = False
                    break
            
            # 检查 evidence_ids 是否引用了定价维度证据
            if action.evidence_ids:
                has_pricing = any(eid in pricing_evidence_ids for eid in action.evidence_ids)
                if not has_pricing:
                    pricing_insights_has_pricing_evidence = False
    
    # 校验 battlecard 的 evidence_ids
    battlecard_valid = True
    battlecard_has_evidence = True
    
    if task.decision_pack and task.decision_pack.battlecard:
        for action in task.decision_pack.battlecard:
            # 检查 evidence_ids 是否有效
            for eid in action.evidence_ids:
                if eid not in evidence_by_id:
                    battlecard_valid = False
                    break
            
            # 检查是否有证据支撑
            if not action.evidence_ids:
                battlecard_has_evidence = False
    
    if task.decision_pack:
        # 包含所有 4 类决策产物进行校验
        all_actions = (
            task.decision_pack.positioning + 
            task.decision_pack.mvp_priorities + 
            task.decision_pack.pricing_insights + 
            task.decision_pack.battlecard
        )
        for action in all_actions:
            action_ids.extend(action.evidence_ids)
            allowed = support_map[action.dimension]
            cited_dimensions = {
                evidence_by_id[item].dimension
                for item in action.evidence_ids
                if item in evidence_by_id
            }
            dimension_supported = dimension_supported and bool(cited_dimensions & allowed)
            if not action.recommendation.strip():
                empty_recommendation = True
    
    ids_supported = all(item in evidence_by_id for item in action_ids)
    coverage_passed = bool(task.coverage and task.coverage.passed)
    rule_supported = ids_supported and dimension_supported and not empty_recommendation
    
    base_score = 0.88 if rule_supported and coverage_passed else 0.68 if rule_supported else 0.42
    notes = []
    hallucination_risk = "low" if rule_supported and coverage_passed else "medium" if rule_supported else "high"
    
    if ids_supported:
        notes.append("所有决策动作均绑定 Evidence ID")
    else:
        notes.append("存在无法追溯的 Evidence ID")
    
    if dimension_supported:
        notes.append("决策动作引用了匹配维度的 Evidence")
    else:
        notes.append("存在维度不匹配的引用")
    
    if empty_recommendation:
        notes.append("存在空的 recommendation")
    
    if not coverage_passed:
        notes.append("Coverage Gate 未完全达标，输出应作为补证据后的临时建议")
    
    # 检查 pricing_insights 校验结果
    if task.decision_pack and task.decision_pack.pricing_insights:
        if not pricing_insights_valid:
            notes.append("pricing_insights 存在无效的 evidence_ids")
            hallucination_risk = "high"
        if not pricing_insights_has_pricing_evidence:
            notes.append("pricing_insights 未引用定价维度证据，可能存在幻觉")
            hallucination_risk = "high"
    
    # 检查 battlecard 校验结果
    if task.decision_pack and task.decision_pack.battlecard:
        if not battlecard_valid:
            notes.append("battlecard 存在无效的 evidence_ids")
            hallucination_risk = "high"
        if not battlecard_has_evidence:
            notes.append("battlecard 缺少证据支撑，可能存在幻觉风险")
            if hallucination_risk != "high":
                hallucination_risk = "medium"
    
    llm_adjustment = 0.0
    llm_notes = []
    llm_enabled = False
    
    try:
        if task.decision_pack and (task.decision_pack.positioning or task.decision_pack.pricing_insights or task.decision_pack.battlecard):
            prompt = _build_reviewer_prompt(task)
            messages = [
                {"role": "system", "content": "你是一个专业的情报复核员，负责检查竞品分析决策包的质量。"},
                {"role": "user", "content": prompt}
            ]
            
            result = llm_client.chat_completion_json_sync(messages)
            
            llm_adjustment = result.get("score_adjustment", 0.0)
            llm_hallucination = result.get("hallucination_risk", hallucination_risk)
            llm_notes = result.get("notes", [])
            llm_enabled = True
            
            if llm_hallucination == "high":
                hallucination_risk = "high"
            elif llm_hallucination == "medium" and hallucination_risk == "low":
                hallucination_risk = "medium"
            
            notes.extend(llm_notes)
            
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
    task.status = TaskStatus.completed if rule_supported else TaskStatus.failed
    task.rewrite_round += 1
    _add_event(task, "reviewer", "Reviewer 已完成引用核验", task.status)
    task_store.update(task)
    _record_stage_end(task, "reviewer", start_time)
    return {"task": task}


def run_fallback(task: TaskRecord) -> TaskRecord:
    # 初始化 metrics 以便记录各阶段耗时
    if task.metrics is None:
        task.metrics = TaskMetrics(
            total_duration_ms=0,
            stage_durations={},
            evidence_count=0,
            conflict_count=0,
            intervention_count=0,
        )
    state: WorkflowState = {"task": task}
    
    state = planner(state)
    state = research(state)
    state = evidence_normalizer(state)
    state = coverage_gate(state)
    
    task = state["task"]
    if (task.coverage and not task.coverage.passed 
        and len(task.evidence) < task.request.budget.max_sources):
        state = research(state)
        state = evidence_normalizer(state)
        state = coverage_gate(state)
    
    state = conflict_resolver(state)
    state = writer(state)
    state = reviewer(state)
    
    task = state["task"]
    if (task.review and task.review.score is not None 
        and task.review.score < 0.6 
        and task.rewrite_round < 1):
        state = writer(state)
        state = reviewer(state)
    
    task = state["task"]
    _summarize_metrics(task)
    return task


def route_after_reviewer(state: WorkflowState) -> str:
    task = state["task"]
    
    if (task.review and task.review.score is not None 
        and task.review.score < 0.6 
        and task.rewrite_round < 1 
        and task.status != TaskStatus.failed):
        _add_event(task, "reviewer", "Reviewer 评分低于阈值，触发重写")
        return "writer"
    
    return END


def route_after_coverage(state: WorkflowState) -> str:
    task = state["task"]
    
    from app.services.search_adapter import search_adapter
    
    if (task.coverage and not task.coverage.passed 
        and task.research_round < 1 
        and len(task.evidence) < task.request.budget.max_sources
        and search_adapter.config.is_configured):
        return "research"
    
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
    graph.add_conditional_edges("coverage_gate", route_after_coverage, {"research": "research", "conflict_resolver": "conflict_resolver"})
    graph.add_edge("conflict_resolver", "writer")
    graph.add_edge("writer", "reviewer")
    graph.add_conditional_edges("reviewer", route_after_reviewer, {"writer": "writer", END: END})
    return graph.compile()


def run_competitive_intelligence_workflow(task: TaskRecord) -> TaskRecord:
    import logging
    import sys
    wf_logger = logging.getLogger(__name__)
    # 初始化 metrics 以便记录各阶段耗时
    if task.metrics is None:
        task.metrics = TaskMetrics(
            total_duration_ms=0,
            stage_durations={},
            evidence_count=0,
            conflict_count=0,
            intervention_count=0,
        )
    wf_logger.info(f"WF starting for task {task.id}")
    sys.stdout.flush()
    # 使用 run_fallback 避免 langgraph 在后台线程中的问题
    wf_logger.info(f"WF using fallback for task {task.id}")
    result = run_fallback(task)
    wf_logger.info(f"WF fallback completed for task {task.id}, status={result.status}")
    sys.stdout.flush()
    return result


# 支持强制重跑的有效阶段
VALID_RERUN_STAGES = ["writer", "reviewer"]


def rerun_from_stage(task: TaskRecord, stage: str) -> TaskRecord:
    """从指定阶段重新执行工作流
    
    Args:
        task: 任务记录
        stage: 目标阶段（writer 或 reviewer）
        
    Returns:
        更新后的任务记录
    """
    if stage not in VALID_RERUN_STAGES:
        _add_event(task, "force_rerun", f"无效的阶段: {stage}，有效阶段: {VALID_RERUN_STAGES}", TaskStatus.failed)
        task_store.update(task)
        return task
    
    # 重置任务状态为运行中
    task.status = TaskStatus.running
    _add_event(task, "force_rerun", f"开始从 {stage} 阶段重新执行")
    
    state: WorkflowState = {"task": task}
    
    if stage == "writer":
        # 从 writer 阶段重新执行
        # 清除 writer 和 reviewer 阶段的所有产物
        task.decision_pack = None
        task.review = None
        task.rewrite_round = 0
        task.metrics = None
        
        state = writer(state)
        task = state["task"]
        
        if task.status != TaskStatus.failed:
            state = reviewer(state)
            task = state["task"]
            
            # 检查是否需要重写
            if (task.review and task.review.score is not None 
                and task.review.score < 0.6 
                and task.rewrite_round < 1):
                _add_event(task, "force_rerun", "Reviewer 评分低于阈值，触发重写")
                state = writer(state)
                task = state["task"]
                if task.status != TaskStatus.failed:
                    state = reviewer(state)
                    task = state["task"]
    
    elif stage == "reviewer":
        # 从 reviewer 阶段重新执行
        # 清除之前的审核结果
        task.review = None
        
        state = reviewer(state)
        task = state["task"]
        
        # 检查是否需要重写
        if (task.review and task.review.score is not None 
            and task.review.score < 0.6 
            and task.rewrite_round < 1
            and task.status != TaskStatus.failed):
            _add_event(task, "force_rerun", "Reviewer 评分低于阈值，触发重写")
            state = writer(state)
            task = state["task"]
            if task.status != TaskStatus.failed:
                state = reviewer(state)
                task = state["task"]
    
    _add_event(task, "force_rerun", f"从 {stage} 阶段重新执行完成", task.status)
    task_store.update(task)
    return task
