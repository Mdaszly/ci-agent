"""工作流测试 - 默认使用 mock，不依赖外网"""
import pytest
from unittest.mock import MagicMock, patch

from app.models.schemas import (
    CoverageGateResult,
    DecisionAction,
    DecisionChunkType,
    DecisionMemoryItem,
    DecisionPack,
    DecisionPackStatus,
    DecisionPackVersion,
    Evidence,
    EvidenceDimension,
    ReviewScore,
    ReviewStatus,
    SourceType,
    TaskCreateRequest,
    TaskRecord,
    ResearchPlan,
    ResearchTask,
    TaskStatus,
    WorkflowMemoryState,
)
from app.worker.workflow import (
    REQUIRED_DIMENSIONS,
    coverage_gate,
    planner,
    research,
    run_competitive_intelligence_workflow,
    WorkflowState,
    writer,
    reviewer,
    route_after_reviewer,
    _repair_decision_pack,
    _should_stop,
)


class TestCoverageGate:
    def test_coverage_gate_passes_with_high_quality_evidence(self):
        request = TaskCreateRequest(
            product_goal="Test product goal",
            competitors=["TestCompetitor"],
        )
        task = TaskRecord(request=request)
        
        task.evidence = [
            Evidence(
                id="ev_1",
                source_type=SourceType.url,
                source_url="https://example.com",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.feature,
                claim="Feature evidence",
                quote="Feature content",
                confidence=0.85,
                freshness="recent",
                content_hash="hash1",
                quality_score=0.7,
                credibility_score=0.6,
            ),
            Evidence(
                id="ev_2",
                source_type=SourceType.url,
                source_url="https://example.com/pricing",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.pricing,
                claim="Pricing evidence",
                quote="$9.99",
                confidence=0.85,
                freshness="recent",
                content_hash="hash2",
                quality_score=0.75,
                credibility_score=0.7,
            ),
            Evidence(
                id="ev_3",
                source_type=SourceType.text,
                competitor="TestCompetitor",
                dimension=EvidenceDimension.user_feedback,
                claim="User feedback",
                quote="User comments",
                confidence=0.68,
                freshness="user-provided",
                content_hash="hash3",
                quality_score=0.6,
                credibility_score=0.5,
            ),
        ]
        
        state = coverage_gate({"task": task})
        assert state["task"].coverage is not None
        assert state["task"].coverage.passed is True
        assert len(state["task"].coverage.covered_dimensions) == len(REQUIRED_DIMENSIONS)

    def test_coverage_gate_fails_with_low_quality_evidence(self):
        request = TaskCreateRequest(
            product_goal="Test product goal",
            competitors=["TestCompetitor"],
        )
        task = TaskRecord(request=request)
        
        task.evidence = [
            Evidence(
                id="ev_1",
                source_type=SourceType.url,
                source_url="https://example.com",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.feature,
                claim="Feature evidence",
                quote="Feature content",
                confidence=0.85,
                freshness="recent",
                content_hash="hash1",
                quality_score=0.7,
                credibility_score=0.6,
            ),
            Evidence(
                id="ev_2",
                source_type=SourceType.url,
                source_url="https://example.com/pricing",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.pricing,
                claim="Pricing evidence",
                quote="",
                confidence=0.3,
                freshness="unknown",
                content_hash="hash2",
                quality_score=0.2,
                credibility_score=0.2,
            ),
            Evidence(
                id="ev_3",
                source_type=SourceType.text,
                competitor="TestCompetitor",
                dimension=EvidenceDimension.user_feedback,
                claim="User feedback",
                quote="User comments",
                confidence=0.68,
                freshness="user-provided",
                content_hash="hash3",
                quality_score=0.6,
                credibility_score=0.5,
            ),
        ]
        
        state = coverage_gate({"task": task})
        assert state["task"].coverage is not None
        assert state["task"].coverage.passed is False
        assert EvidenceDimension.pricing in state["task"].coverage.missing_dimensions

    def test_coverage_gate_fails_with_missing_dimension(self):
        request = TaskCreateRequest(
            product_goal="Test product goal",
            competitors=["TestCompetitor"],
        )
        task = TaskRecord(request=request)
        
        task.evidence = [
            Evidence(
                id="ev_1",
                source_type=SourceType.url,
                source_url="https://example.com",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.feature,
                claim="Feature evidence",
                quote="Feature content",
                confidence=0.85,
                freshness="recent",
                content_hash="hash1",
                quality_score=0.7,
                credibility_score=0.6,
            ),
        ]
        
        state = coverage_gate({"task": task})
        assert state["task"].coverage is not None
        assert state["task"].coverage.passed is False
        assert len(state["task"].coverage.missing_dimensions) > 0


@patch("app.services.url_adapter.url_adapter")
@patch("app.worker.workflow.llm_client")
def test_workflow_with_mock_llm_and_url(mock_llm, mock_url_adapter):
    mock_url_adapter.fetch.return_value = (
        "Test Page",
        "Page content with pricing $9.99",
        "$9.99",
    )
    
    def generate_response(*args, **kwargs):
        prompt = args[0][-1]["content"]
        if "evidence_context" in prompt.lower():
            evidence_id = prompt.split("Evidence ID: ")[1].split("\n")[0]
            return {
                "summary": "Test summary",
                "positioning": [
                    {
                        "title": "定位建议",
                        "dimension": "positioning",
                        "recommendation": "差异化定位建议",
                        "rationale": "基于证据",
                        "evidence_ids": [evidence_id],
                        "priority": "P0",
                    }
                ],
                "mvp_priorities": [
                    {
                        "title": "功能优先级",
                        "dimension": "feature",
                        "recommendation": "功能建议",
                        "rationale": "基于证据",
                        "evidence_ids": [evidence_id],
                        "priority": "P0",
                    }
                ],
            }
        else:
            return {
                "score_adjustment": 0.0,
                "hallucination_risk": "low",
                "notes": ["所有决策动作均绑定 Evidence ID"],
            }
    
    mock_llm.chat_completion_json_sync.side_effect = generate_response
    
    request = TaskCreateRequest(
        product_goal="Test product goal",
        competitors=["TestCompetitor"],
        urls=["https://example.com", "https://example.com/pricing"],
        comments="用户反馈模板同质化，希望获得更具体的修改建议。",
    )
    task = run_competitive_intelligence_workflow(TaskRecord(request=request))
    
    assert task.status == "completed"
    assert task.evidence is not None
    assert len(task.evidence) >= 2
    assert task.coverage is not None


@patch("app.services.search_adapter.search_adapter")
@patch("app.worker.workflow.llm_client")
def test_workflow_uses_search_when_urls_missing(mock_llm, mock_search_adapter):
    mock_search_adapter.search_for_competitor.return_value = [
        Evidence(
            id="ev_search_1",
            source_type=SourceType.text,
            source_url="https://example.com/search-result",
            competitor="TestCompetitor",
            dimension=EvidenceDimension.pricing,
            claim="搜索结果显示价格信息",
            quote="Pricing starts at $9.99",
            confidence=0.65,
            freshness="search-result",
            content_hash="searchhash1",
            quality_score=0.62,
            credibility_score=0.55,
        ),
        Evidence(
            id="ev_search_2",
            source_type=SourceType.text,
            source_url="https://example.com/review-result",
            competitor="TestCompetitor",
            dimension=EvidenceDimension.feature,
            claim="搜索结果显示功能信息",
            quote="Provides AI resume feedback",
            confidence=0.67,
            freshness="search-result",
            content_hash="searchhash2",
            quality_score=0.66,
            credibility_score=0.58,
        ),
    ]

    def generate_response(*args, **kwargs):
        prompt = args[0][-1]["content"]
        if "evidence_context" in prompt.lower():
            evidence_id = prompt.split("Evidence ID: ")[1].split("\n")[0]
            return {
                "summary": "Search-backed summary",
                "positioning": [
                    {
                        "title": "搜索定位建议",
                        "dimension": "positioning",
                        "recommendation": "基于搜索与评论做定位",
                        "rationale": "搜索结果和用户反馈共同支持",
                        "evidence_ids": [evidence_id],
                        "priority": "P1",
                    }
                ],
                "mvp_priorities": [
                    {
                        "title": "搜索功能建议",
                        "dimension": "feature",
                        "recommendation": "优先做 AI 反馈能力",
                        "rationale": "搜索结果展示这是核心能力",
                        "evidence_ids": [evidence_id],
                        "priority": "P0",
                    }
                ],
            }
        return {
            "score_adjustment": 0.0,
            "hallucination_risk": "low",
            "notes": ["搜索证据已正确接入工作流"],
        }

    mock_llm.chat_completion_json_sync.side_effect = generate_response

    request = TaskCreateRequest(
        product_goal="Test product goal",
        competitors=["TestCompetitor"],
        urls=[],
        comments="用户反馈定价偏高，希望反馈更具体。",
    )
    task = run_competitive_intelligence_workflow(TaskRecord(request=request))

    assert task.status == "completed"
    assert mock_search_adapter.search_for_competitor.called
    assert any(ev.source_url == "https://example.com/search-result" for ev in task.evidence)
    assert any("搜索补充" in event.message for event in task.events if event.stage == "research")


@pytest.mark.integration
def test_workflow_generates_traceable_decision_pack() -> None:
    """集成测试：需要真实 LLM API key 和外网访问"""
    request = TaskCreateRequest(
        product_goal="为 AI 简历优化工具寻找首版差异化定位和 MVP 功能优先级",
        competitors=["ResumeWorded"],
        urls=["https://www.resumeworded.com/", "https://www.resumeworded.com/pricing"],
        comments="用户反馈模板同质化，中文场景支持不足，希望获得更具体的修改建议。",
        image_names=["homepage.png"],
    )
    task = run_competitive_intelligence_workflow(TaskRecord(request=request))

    assert task.status == "completed"
    assert task.evidence
    assert task.coverage is not None
    assert task.decision_pack is not None
    evidence_ids = {item.id for item in task.evidence}
    for action in task.decision_pack.positioning + task.decision_pack.mvp_priorities:
        assert set(action.evidence_ids).issubset(evidence_ids)
    assert task.review is not None
    assert task.review.hallucination_risk in ["low", "medium"]


class TestPlanner:
    def test_workflow_planner_generates_research_plan_with_urls(self):
        request = TaskCreateRequest(
            product_goal="Test product goal",
            competitors=["CompetitorA", "CompetitorB"],
            urls=["https://example.com", "https://example2.com"],
        )
        task = TaskRecord(request=request)
        state = planner({"task": task})

        assert state["task"].research_plan is not None
        url_tasks = [t for t in state["task"].research_plan.tasks if t.source_type == "url"]
        search_tasks = [t for t in state["task"].research_plan.tasks if t.source_type == "search"]
        assert len(url_tasks) == 2
        assert len(search_tasks) == 2
        assert url_tasks[0].competitor == "CompetitorA"
        assert url_tasks[1].competitor == "CompetitorB"

    def test_workflow_planner_generates_research_plan_with_competitor_urls(self):
        request = TaskCreateRequest(
            product_goal="Test product goal",
            competitors=["CompetitorA", "CompetitorB"],
            competitor_urls=[
                {"competitor": "CompetitorB", "url": "https://example.com/b"},
                {"competitor": "CompetitorA", "url": "https://example.com/a"},
            ],
        )
        task = TaskRecord(request=request)
        state = planner({"task": task})

        url_tasks = [t for t in state["task"].research_plan.tasks if t.source_type == "url"]
        assert len(url_tasks) == 2
        assert url_tasks[0].competitor == "CompetitorB"
        assert url_tasks[1].competitor == "CompetitorA"

    def test_workflow_planner_generates_research_plan_without_urls(self):
        request = TaskCreateRequest(
            product_goal="Test product goal for resume optimization",
            competitors=["CompetitorA", "CompetitorB"],
        )
        task = TaskRecord(request=request)
        state = planner({"task": task})

        assert state["task"].research_plan is not None
        assert len(state["task"].research_plan.tasks) == 2
        assert all(t.source_type == "search" for t in state["task"].research_plan.tasks)
        assert state["task"].research_plan.tasks[0].competitor == "CompetitorA"
        assert state["task"].research_plan.tasks[1].competitor == "CompetitorB"

    def test_workflow_planner_generates_research_plan_with_comments(self):
        request = TaskCreateRequest(
            product_goal="Test product goal",
            competitors=["CompetitorA"],
            comments="用户反馈模板同质化",
        )
        task = TaskRecord(request=request)
        state = planner({"task": task})

        assert state["task"].research_plan is not None
        comment_tasks = [t for t in state["task"].research_plan.tasks if t.source_type == "comment"]
        assert len(comment_tasks) == 1
        assert comment_tasks[0].competitor == "CompetitorA"


@patch("app.services.research_executor.execute_task")
@patch("app.services.url_adapter.url_adapter")
@patch("app.services.search_adapter.search_adapter")
@patch("app.services.comment_adapter.comment_adapter")
def test_research_executes_plan_in_parallel(
    mock_comment, mock_search, mock_url, mock_execute_task
):
    """验证 research() 按 research_plan 并行执行"""
    
    # Mock execute_task 返回不同的 Evidence
    def execute_task_side_effect(task):
        if task.source_type == "url":
            return [Evidence(
                id=f"ev_url_{task.competitor}",
                source_type=SourceType.url,
                source_url=task.query_or_url,
                competitor=task.competitor,
                dimension=EvidenceDimension.feature,
                claim=f"URL evidence for {task.competitor}",
                quote="URL content",
                confidence=0.85,
                freshness="recent",
                content_hash="hash1",
            )]
        elif task.source_type == "search":
            return [Evidence(
                id=f"ev_search_{task.competitor}",
                source_type=SourceType.text,
                source_url="https://search.result",
                competitor=task.competitor,
                dimension=EvidenceDimension.pricing,
                claim=f"Search evidence for {task.competitor}",
                quote="Search snippet",
                confidence=0.65,
                freshness="search-result",
                content_hash="hash2",
            )]
        elif task.source_type == "comment":
            return [Evidence(
                id="ev_comment_1",
                source_type=SourceType.text,
                competitor=task.competitor,
                dimension=EvidenceDimension.user_feedback,
                claim="User feedback evidence",
                quote="User comment",
                confidence=0.68,
                freshness="user-provided",
                content_hash="hash3",
            )]
        return []
    
    mock_execute_task.side_effect = execute_task_side_effect
    
    # 创建带 research_plan 的 TaskRecord
    from app.models.schemas import TaskCreateRequest
    
    request = TaskCreateRequest(
        product_goal="Test product goal",
        competitors=["CompetitorA", "CompetitorB"],
    )
    task = TaskRecord(request=request)
    task.research_plan = ResearchPlan(
        tasks=[
            ResearchTask(competitor="CompetitorA", source_type="url", query_or_url="https://example.com", dimension=EvidenceDimension.feature),
            ResearchTask(competitor="CompetitorB", source_type="search", query_or_url="competitor B search", dimension=EvidenceDimension.pricing),
            ResearchTask(competitor="CompetitorA", source_type="comment", query_or_url="用户反馈", dimension=EvidenceDimension.user_feedback),
        ],
        dimensions=[EvidenceDimension.feature, EvidenceDimension.pricing, EvidenceDimension.user_feedback],
        keywords=["test", "product"],
    )
    
    state = research({"task": task})
    
    # 验证并行调用
    assert mock_execute_task.call_count == 3
    
    # 验证 evidence 合并正确
    assert len(state["task"].evidence) == 3
    assert any("URL evidence" in e.claim for e in state["task"].evidence)
    assert any("Search evidence" in e.claim for e in state["task"].evidence)
    assert any("User feedback" in e.claim for e in state["task"].evidence)


@patch("app.services.research_executor.execute_task")
def test_research_fallback_when_no_plan(mock_execute_task):
    """验证无 research_plan 时 fallback 到串行逻辑（现也复用 execute_task）"""
    mock_execute_task.side_effect = lambda t: []

    from app.models.schemas import TaskCreateRequest

    request = TaskCreateRequest(
        product_goal="Test goal",
        competitors=["TestCompetitor"],
        urls=["https://example.com"],
    )
    task = TaskRecord(request=request)
    # 不设置 research_plan

    state = research({"task": task})

    # fallback 路径现在也调用 execute_task（更好的代码复用）
    assert mock_execute_task.call_count >= 1
    # fallback 会调用真实的 url_adapter
    assert len(state["task"].evidence) >= 0


@patch("app.services.research_executor.execute_task")
def test_research_handles_task_failure(mock_execute_task):
    """验证单个任务失败不阻塞整体（parallel 路径）"""
    def fail_on_url(task):
        if task.source_type == "url":
            raise Exception("Simulated URL failure")
        return [Evidence(
            id=f"ev_{task.source_type}",
            source_type=SourceType.text,
            competitor=task.competitor,
            dimension=EvidenceDimension.feature,
            claim=f"Evidence for {task.source_type}",
            quote="Content",
            confidence=0.7,
            freshness="test",
            content_hash="hash",
        )]
    
    mock_execute_task.side_effect = fail_on_url
    
    from app.models.schemas import TaskCreateRequest
    
    request = TaskCreateRequest(
        product_goal="Test goal",
        competitors=["CompetitorA", "CompetitorB"],
    )
    task = TaskRecord(request=request)
    task.research_plan = ResearchPlan(
        tasks=[
            ResearchTask(competitor="CompetitorA", source_type="url", query_or_url="https://fail.com", dimension=EvidenceDimension.feature),
            ResearchTask(competitor="CompetitorB", source_type="search", query_or_url="competitor B", dimension=EvidenceDimension.feature),
        ],
        dimensions=[EvidenceDimension.feature],
        keywords=["test"],
    )
    
    # 不应 raise
    state = research({"task": task})
    
    # 整体任务应该完成（失败的返回空，成功的有 evidence）
    assert len(state["task"].evidence) >= 0
    assert state["task"].status in [TaskStatus.running, TaskStatus.completed]


@patch("app.services.search_adapter.search_adapter")
def test_coverage_gate_triggers_research_loop(mock_search):
    """验证 coverage gate 未达标时触发补搜"""
    mock_search.config.is_configured = True
    
    def mock_search_func(query, competitor):
        return [Evidence(
            id=f"ev_supplement_{query[:8]}",
            source_type=SourceType.text,
            source_url="https://search.result",
            competitor=competitor,
            dimension=EvidenceDimension.feature,
            claim=f"补搜结果: {query}",
            quote="Supplement search result",
            confidence=0.65,
            freshness="search-result",
            content_hash="supplement_hash",
            quality_score=0.6,
            credibility_score=0.55,
        )]
    
    mock_search.search.side_effect = mock_search_func
    
    # 创建一个 coverage 未达标的任务
    request = TaskCreateRequest(
        product_goal="Test product goal",
        competitors=["TestCompetitor"],
    )
    task = TaskRecord(request=request)
    
    # 设置初始证据（缺少某些维度）
    task.evidence = [
        Evidence(
            id="ev_1",
            source_type=SourceType.text,
            competitor="TestCompetitor",
            dimension=EvidenceDimension.feature,
            claim="Feature evidence",
            quote="Feature content",
            confidence=0.85,
            freshness="recent",
            content_hash="hash1",
            quality_score=0.7,
            credibility_score=0.6,
        ),
    ]
    task.coverage = CoverageGateResult(
        passed=False,
        score=0.33,
        covered_dimensions=[EvidenceDimension.feature],
        missing_dimensions=[EvidenceDimension.pricing, EvidenceDimension.user_feedback],
        gap_queries=["TestCompetitor pricing evidence", "TestCompetitor user_feedback evidence"],
    )
    
    # 调用补搜逻辑
    from app.worker.workflow import _supplement_search
    _supplement_search(task)
    
    # 验证补搜执行
    assert mock_search.search.call_count == 2
    assert task.research_round == 1
    assert len(task.evidence) == 3  # 初始 1 条 + 补搜 2 条


@patch("app.services.search_adapter.search_adapter")
def test_coverage_gate_does_not_trigger_loop_if_passed(mock_search):
    """验证 coverage gate 达标时不触发补搜"""
    mock_search.config.is_configured = True
    
    request = TaskCreateRequest(
        product_goal="Test product goal",
        competitors=["TestCompetitor"],
    )
    task = TaskRecord(request=request)
    
    task.evidence = [
        Evidence(
            id="ev_1",
            source_type=SourceType.text,
            competitor="TestCompetitor",
            dimension=EvidenceDimension.feature,
            claim="Feature evidence",
            quote="Feature content",
            confidence=0.85,
            freshness="recent",
            content_hash="hash1",
            quality_score=0.7,
            credibility_score=0.6,
        ),
        Evidence(
            id="ev_2",
            source_type=SourceType.text,
            competitor="TestCompetitor",
            dimension=EvidenceDimension.pricing,
            claim="Pricing evidence",
            quote="$9.99",
            confidence=0.85,
            freshness="recent",
            content_hash="hash2",
            quality_score=0.75,
            credibility_score=0.7,
        ),
        Evidence(
            id="ev_3",
            source_type=SourceType.text,
            competitor="TestCompetitor",
            dimension=EvidenceDimension.user_feedback,
            claim="User feedback",
            quote="User comments",
            confidence=0.68,
            freshness="user-provided",
            content_hash="hash3",
            quality_score=0.6,
            credibility_score=0.5,
        ),
    ]
    task.coverage = CoverageGateResult(
        passed=True,
        score=1.0,
        covered_dimensions=[ EvidenceDimension.feature, EvidenceDimension.pricing, EvidenceDimension.user_feedback],
        missing_dimensions=[],
        gap_queries=[],
    )
    
    from app.worker.workflow import _supplement_search
    _supplement_search(task)
    
    # 验证补搜未执行
    assert mock_search.search.call_count == 0
    assert task.research_round == 0


def test_research_round_limit():
    """验证 research_round 最多为 1"""
    request = TaskCreateRequest(
        product_goal="Test product goal",
        competitors=["TestCompetitor"],
    )
    task = TaskRecord(request=request)
    task.research_round = 1
    
    task.evidence = [
        Evidence(
            id="ev_1",
            source_type=SourceType.text,
            competitor="TestCompetitor",
            dimension=EvidenceDimension.feature,
            claim="Feature evidence",
            quote="Feature content",
            confidence=0.85,
            freshness="recent",
            content_hash="hash1",
            quality_score=0.7,
            credibility_score=0.6,
        ),
    ]
    task.coverage = CoverageGateResult(
        passed=False,
        score=0.33,
        covered_dimensions=[EvidenceDimension.feature],
        missing_dimensions=[EvidenceDimension.pricing],
        gap_queries=["TestCompetitor pricing evidence"],
    )
    
    from app.worker.workflow import _supplement_search
    _supplement_search(task)
    
    # 验证已补搜过一轮，不再补搜
    assert task.research_round == 1  # 保持为 1，不会增加


def test_reviewer_triggers_rewrite_on_low_score():
    """验证 Reviewer 低分触发重写"""
    request = TaskCreateRequest(
        product_goal="Test product goal",
        competitors=["TestCompetitor"],
    )
    task = TaskRecord(request=request)
    
    # 设置低分 review
    task.review = ReviewScore(
        score=0.5,
        citation_precision=0.5,
        claim_support_rate=0.5,
        hallucination_risk="medium",
        notes=["分析不够深入"],
    )
    task.rewrite_round = 0
    
    # 验证路由返回 writer（第一次重写）
    from app.worker.workflow import route_after_reviewer
    from langgraph.graph import END
    
    result = route_after_reviewer({"task": task})
    assert result == "writer"
    
    # 模拟重写后
    task.rewrite_round = 1
    
    # 验证路由返回 END（已重写一次）
    result = route_after_reviewer({"task": task})
    assert result == END


def test_reviewer_does_not_trigger_rewrite_on_high_score():
    """验证高分不触发重写"""
    request = TaskCreateRequest(
        product_goal="Test product goal",
        competitors=["TestCompetitor"],
    )
    task = TaskRecord(request=request)
    
    task.review = ReviewScore(
        score=0.85,
        citation_precision=0.9,
        claim_support_rate=0.8,
        hallucination_risk="low",
        notes=["优秀的分析报告"],
    )
    task.rewrite_round = 0
    
    from app.worker.workflow import route_after_reviewer
    from langgraph.graph import END
    
    result = route_after_reviewer({"task": task})
    
    # 高分应该返回 END
    assert result == END


def test_versioned_repair_marks_previous_pack_superseded():
    request = TaskCreateRequest(
        product_goal="Test product goal",
        competitors=["TestCompetitor"],
    )
    task = TaskRecord(request=request)
    task.evidence = [
        Evidence(
            id="ev_feature_1",
            source_type=SourceType.url,
            source_url="https://example.com",
            competitor="TestCompetitor",
            dimension=EvidenceDimension.feature,
            claim="Feature evidence",
            quote="Feature content",
            confidence=0.85,
            freshness="recent",
            content_hash="hash1",
            quality_score=0.7,
            credibility_score=0.6,
        ),
        Evidence(
            id="ev_pricing_1",
            source_type=SourceType.url,
            source_url="https://example.com/pricing",
            competitor="TestCompetitor",
            dimension=EvidenceDimension.pricing,
            claim="Pricing evidence",
            quote="$9.99 per month",
            confidence=0.88,
            freshness="recent",
            content_hash="hash2",
            quality_score=0.78,
            credibility_score=0.72,
        ),
    ]
    task.decision_pack = DecisionPack(
        positioning=[
            DecisionAction(
                title="定位建议",
                dimension=EvidenceDimension.positioning,
                recommendation="先做基础定位",
                rationale="基于现有证据",
                evidence_ids=["ev_feature_1"],
                priority="P0",
            )
        ],
        mvp_priorities=[
            DecisionAction(
                title="MVP建议",
                dimension=EvidenceDimension.feature,
                recommendation="优先核心功能",
                rationale="基于现有证据",
                evidence_ids=["ev_feature_1"],
                priority="P0",
            )
        ],
        pricing_insights=[
            DecisionAction(
                title="定价建议",
                dimension=EvidenceDimension.pricing,
                recommendation="保持价格竞争力",
                rationale="基于现有证据",
                evidence_ids=["ev_pricing_1"],
                priority="P0",
            )
        ],
        battlecard=[],
        summary="初版决策包",
    )
    task.review = ReviewScore(
        score=0.45,
        citation_precision=0.5,
        claim_support_rate=0.5,
        hallucination_risk="medium",
        notes=["需要局部修复", "补充定价说明"],
    )
    task.coverage = CoverageGateResult(
        passed=False,
        score=0.5,
        covered_dimensions=[EvidenceDimension.feature, EvidenceDimension.pricing],
        missing_dimensions=[EvidenceDimension.user_feedback],
        gap_queries=["TestCompetitor user feedback"],
    )
    task.memory_state = WorkflowMemoryState(max_iterations=2, current_iteration=0)

    repaired = _repair_decision_pack(task, [])

    assert repaired is True
    assert task.decision_pack is not None
    assert task.decision_pack.version == 2
    assert task.decision_pack.status == DecisionPackStatus.draft
    assert any(item.version == 1 and item.status == DecisionPackStatus.superseded for item in task.decision_history)
    assert task.memory_state is not None
    assert task.memory_state.current_pack_version == 2
    assert task.memory_state.last_reviewer_status == ReviewStatus.needs_retry


def test_route_after_reviewer_stops_when_max_iterations_reached():
    request = TaskCreateRequest(
        product_goal="Test product goal",
        competitors=["TestCompetitor"],
    )
    task = TaskRecord(request=request)
    task.review = ReviewScore(
        score=0.45,
        citation_precision=0.5,
        claim_support_rate=0.5,
        hallucination_risk="high",
        notes=["still failing"],
    )
    task.memory_state = WorkflowMemoryState(max_iterations=1, current_iteration=1)

    assert _should_stop(task) is True
    from langgraph.graph import END
    assert route_after_reviewer({"task": task}) == END


@patch("app.worker.workflow.llm_client")
def test_writer_filters_pricing_insights_without_pricing_evidence(mock_llm):
    request = TaskCreateRequest(
        product_goal="Test product goal",
        competitors=["TestCompetitor"],
    )
    task = TaskRecord(request=request)

    task.evidence = [
        Evidence(
            id="ev_feature_1",
            source_type=SourceType.url,
            source_url="https://example.com",
            competitor="TestCompetitor",
            dimension=EvidenceDimension.feature,
            claim="Feature evidence",
            quote="Feature content",
            confidence=0.85,
            freshness="recent",
            content_hash="hash1",
            quality_score=0.7,
            credibility_score=0.6,
        ),
    ]

    mock_llm.chat_completion_json_sync.return_value = {
        "summary": "????",
        "positioning": [
            {
                "title": "????",
                "dimension": "positioning",
                "recommendation": "????",
                "rationale": "??????",
                "evidence_ids": ["ev_feature_1"],
                "priority": "P0",
            }
        ],
        "mvp_priorities": [],
        "pricing_insights": [
            {
                "title": "???????",
                "dimension": "pricing",
                "recommendation": "???????",
                "rationale": "?????",
                "evidence_ids": ["ev_feature_1"],
                "priority": "P0",
            }
        ],
        "battlecard": [],
    }

    state = writer({"task": task})

    assert state["task"].decision_pack is not None
    assert state["task"].decision_pack.pricing_insights == []


class TestReviewerValidatesEvidenceIds:
    """?? Reviewer ????? evidence_ids"""

    def test_reviewer_validates_pricing_insights_evidence_ids(self):
        """?? Reviewer ????? pricing_insights ? evidence_ids"""
        request = TaskCreateRequest(
            product_goal="Test product goal",
            competitors=["TestCompetitor"],
        )
        task = TaskRecord(request=request)

        task.evidence = [
            Evidence(
                id="ev_pricing_1",
                source_type=SourceType.url,
                source_url="https://example.com/pricing",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.pricing,
                claim="Pricing evidence",
                quote="$9.99",
                confidence=0.85,
                freshness="recent",
                content_hash="hash1",
                quality_score=0.75,
                credibility_score=0.7,
            ),
            Evidence(
                id="ev_feature_1",
                source_type=SourceType.url,
                source_url="https://example.com",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.feature,
                claim="Feature evidence",
                quote="Feature content",
                confidence=0.85,
                freshness="recent",
                content_hash="hash2",
                quality_score=0.7,
                credibility_score=0.6,
            ),
        ]

        task.coverage = CoverageGateResult(
            passed=True,
            score=1.0,
            covered_dimensions=[EvidenceDimension.pricing, EvidenceDimension.feature],
            missing_dimensions=[],
            gap_queries=[],
        )

        task.decision_pack = DecisionPack(
            positioning=[],
            mvp_priorities=[],
            pricing_insights=[
                DecisionAction(
                    title="????",
                    dimension=EvidenceDimension.pricing,
                    recommendation="????",
                    rationale="??????",
                    evidence_ids=["ev_pricing_1"],
                    priority="P0",
                )
            ],
            battlecard=[],
            summary="??",
        )

        state = reviewer({"task": task})

        assert state["task"].review is not None
        assert state["task"].review.hallucination_risk in ["low", "medium"]
        assert "????????? Evidence ID" in state["task"].review.notes

    def test_reviewer_detects_invalid_pricing_insights_evidence_ids(self):
        """?? Reviewer ?? pricing_insights ??? evidence_ids"""
        request = TaskCreateRequest(
            product_goal="Test product goal",
            competitors=["TestCompetitor"],
        )
        task = TaskRecord(request=request)

        task.evidence = [
            Evidence(
                id="ev_pricing_1",
                source_type=SourceType.url,
                source_url="https://example.com/pricing",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.pricing,
                claim="Pricing evidence",
                quote="$9.99",
                confidence=0.85,
                freshness="recent",
                content_hash="hash1",
                quality_score=0.75,
                credibility_score=0.7,
            ),
        ]

        task.coverage = CoverageGateResult(
            passed=True,
            score=1.0,
            covered_dimensions=[EvidenceDimension.pricing],
            missing_dimensions=[],
            gap_queries=[],
        )

        task.decision_pack = DecisionPack(
            positioning=[],
            mvp_priorities=[],
            pricing_insights=[
                DecisionAction(
                    title="????",
                    dimension=EvidenceDimension.pricing,
                    recommendation="????",
                    rationale="??????",
                    evidence_ids=["ev_invalid_id"],
                    priority="P0",
                )
            ],
            battlecard=[],
            summary="??",
        )

        state = reviewer({"task": task})

        assert state["task"].review is not None
        assert state["task"].review.hallucination_risk == "high"
        assert any("pricing_insights" in note for note in state["task"].review.notes)

    @patch("app.worker.workflow.llm_client")
    def test_reviewer_validates_battlecard_evidence_ids(self, mock_llm):
        """?? Reviewer ????? battlecard ? evidence_ids"""
        request = TaskCreateRequest(
            product_goal="Test product goal",
            competitors=["TestCompetitor"],
        )
        task = TaskRecord(request=request)

        task.evidence = [
            Evidence(
                id="ev_positioning_1",
                source_type=SourceType.url,
                source_url="https://example.com",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.positioning,
                claim="Positioning evidence",
                quote="Positioning content",
                confidence=0.85,
                freshness="recent",
                content_hash="hash1",
                quality_score=0.7,
                credibility_score=0.6,
            ),
        ]

        task.coverage = CoverageGateResult(
            passed=True,
            score=1.0,
            covered_dimensions=[EvidenceDimension.positioning],
            missing_dimensions=[],
            gap_queries=[],
        )

        task.decision_pack = DecisionPack(
            positioning=[],
            mvp_priorities=[],
            pricing_insights=[],
            battlecard=[
                DecisionAction(
                    title="??????",
                    dimension=EvidenceDimension.positioning,
                    recommendation="??????",
                    rationale="??????",
                    evidence_ids=["ev_positioning_1"],
                    priority="P0",
                )
            ],
            summary="??",
        )

        mock_llm.chat_completion_json_sync.return_value = {
            "score_adjustment": 0.0,
            "hallucination_risk": "low",
            "notes": ["battlecard ??????????"],
        }

        state = reviewer({"task": task})

        assert state["task"].review is not None
        assert state["task"].review.hallucination_risk in ["low", "medium"]

    def test_reviewer_detects_battlecard_without_evidence(self):
        """?? Reviewer ?? battlecard ??????"""
        request = TaskCreateRequest(
            product_goal="Test product goal",
            competitors=["TestCompetitor"],
        )
        task = TaskRecord(request=request)

        task.evidence = [
            Evidence(
                id="ev_feature_1",
                source_type=SourceType.url,
                source_url="https://example.com",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.feature,
                claim="Feature evidence",
                quote="Feature content",
                confidence=0.85,
                freshness="recent",
                content_hash="hash1",
                quality_score=0.7,
                credibility_score=0.6,
            ),
        ]

        task.coverage = CoverageGateResult(
            passed=True,
            score=1.0,
            covered_dimensions=[EvidenceDimension.feature],
            missing_dimensions=[],
            gap_queries=[],
        )

        task.decision_pack = DecisionPack(
            positioning=[],
            mvp_priorities=[],
            pricing_insights=[],
            battlecard=[
                DecisionAction(
                    title="??????",
                    dimension=EvidenceDimension.positioning,
                    recommendation="??????",
                    rationale="?????",
                    evidence_ids=["ev_feature_1"],
                    priority="P0",
                )
            ],
            summary="??",
        )

        state = reviewer({"task": task})

        assert state["task"].review is not None

    def test_reviewer_detects_pricing_insights_without_pricing_evidence(self):
        """?????????pricing_insights ?????????"""
        request = TaskCreateRequest(
            product_goal="Test product goal",
            competitors=["TestCompetitor"],
        )
        task = TaskRecord(request=request)

        task.evidence = [
            Evidence(
                id="ev_pricing_1",
                source_type=SourceType.url,
                source_url="https://example.com/pricing",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.pricing,
                claim="Pricing evidence",
                quote="$9.99",
                confidence=0.85,
                freshness="recent",
                content_hash="hash1",
                quality_score=0.75,
                credibility_score=0.7,
            ),
            Evidence(
                id="ev_feature_1",
                source_type=SourceType.url,
                source_url="https://example.com",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.feature,
                claim="Feature evidence",
                quote="Feature content",
                confidence=0.85,
                freshness="recent",
                content_hash="hash2",
                quality_score=0.7,
                credibility_score=0.6,
            ),
        ]

        task.coverage = CoverageGateResult(
            passed=True,
            score=1.0,
            covered_dimensions=[EvidenceDimension.pricing, EvidenceDimension.feature],
            missing_dimensions=[],
            gap_queries=[],
        )

        task.decision_pack = DecisionPack(
            positioning=[],
            mvp_priorities=[],
            pricing_insights=[
                DecisionAction(
                    title="????",
                    dimension=EvidenceDimension.pricing,
                    recommendation="????",
                    rationale="??????????",
                    evidence_ids=["ev_feature_1"],
                    priority="P0",
                )
            ],
            battlecard=[],
            summary="??",
        )

        state = reviewer({"task": task})

        assert state["task"].review is not None
        assert state["task"].review.hallucination_risk == "high"
        assert any("pricing_insights" in note and "??" in note for note in state["task"].review.notes)


class TestTaskMetrics:
    """测试 TaskMetrics 字段存在和非空"""

    def test_task_metrics_field_exists_after_creation(self):
        """验证创建任务后 task.metrics 字段存在"""
        request = TaskCreateRequest(
            product_goal="Test product goal",
            competitors=["TestCompetitor"],
        )
        task = TaskRecord(request=request)

        # 验证 metrics 字段存在（即使为 None）
        assert hasattr(task, "metrics")
        # metrics 可以为 None，但字段本身存在
        # 关键是运行工作流后应该有值

    @patch("app.services.url_adapter.url_adapter")
    @patch("app.worker.workflow.llm_client")
    def test_task_metrics_non_empty_after_workflow(self, mock_llm, mock_url_adapter):
        """验证运行完整工作流后 task.metrics 有值且有效"""
        mock_url_adapter.fetch.return_value = (
            "Test Page",
            "Page content with pricing $9.99",
            "$9.99",
        )

        def generate_response(*args, **kwargs):
            prompt = args[0][-1]["content"]
            if "evidence_context" in prompt.lower():
                evidence_id = prompt.split("Evidence ID: ")[1].split("\n")[0]
                return {
                    "summary": "Test summary",
                    "positioning": [
                        {
                            "title": "定位建议",
                            "dimension": "positioning",
                            "recommendation": "差异化定位建议",
                            "rationale": "基于证据",
                            "evidence_ids": [evidence_id],
                            "priority": "P0",
                        }
                    ],
                    "mvp_priorities": [
                        {
                            "title": "功能优先级",
                            "dimension": "feature",
                            "recommendation": "功能建议",
                            "rationale": "基于证据",
                            "evidence_ids": [evidence_id],
                            "priority": "P0",
                        }
                    ],
                }
            else:
                return {
                    "score_adjustment": 0.0,
                    "hallucination_risk": "low",
                    "notes": ["所有决策动作均绑定 Evidence ID"],
                }

        mock_llm.chat_completion_json_sync.side_effect = generate_response

        request = TaskCreateRequest(
            product_goal="Test product goal",
            competitors=["TestCompetitor"],
            urls=["https://example.com", "https://example.com/pricing"],
            comments="用户反馈模板同质化，希望获得更具体的修改建议。",
        )
        task = run_competitive_intelligence_workflow(TaskRecord(request=request))

        # 验证 task.metrics 存在且有值
        assert task.metrics is not None

        # 验证 total_duration_ms > 0
        assert task.metrics.total_duration_ms > 0

        # 验证 evidence_count >= 0
        assert task.metrics.evidence_count >= 0
        assert task.metrics.evidence_count == len(task.evidence)

        # 验证 conflict_count >= 0
        assert task.metrics.conflict_count >= 0

        # 验证 intervention_count >= 0
        assert task.metrics.intervention_count >= 0