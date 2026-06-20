from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, text

logger = logging.getLogger(__name__)

from app.db.models import EvidenceDB, ResultDB, TaskDB, TaskStatus
from app.db.session import AsyncSessionLocal
from app.models.schemas import (
    AnalysisProfile,
    BudgetUsage,
    Claim,
    CompetitorUrl,
    Conflict,
    CoverageGateResult,
    DecisionAction,
    DecisionPack,
    DecisionPackVersion,
    Evidence,
    EvidenceDimension,
    ResearchPlan,
    TaskCreateRequest,
    TaskEvent,
    TaskMetrics,
    TaskRecord,
    ReviewScore,
    WorkflowMemoryState,
    build_task_observable_id,
)


def _run_async(coro):
    """在同步或异步上下文中安全执行协程。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}
    done = threading.Event()

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except Exception as exc:  # pragma: no cover - 透传到调用方
            result["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    done.wait()

    if "error" in result:
        raise result["error"]
    return result.get("value")


def _coerce_score(value: Any, default: float = 0.5) -> float:
    """把旧数据或异常值统一收敛为可渲染的分数。"""
    try:
        if value is None:
            return default
        score = float(value)
        if math.isnan(score) or math.isinf(score):
            return default
        return min(1.0, max(0.0, score))
    except (TypeError, ValueError):
        return default


def _serialize_urls(request: TaskCreateRequest) -> list:
    if request.competitor_urls:
        return [{"competitor": item.competitor, "url": str(item.url)} for item in request.competitor_urls]
    return [str(url) for url in request.urls]


def _deserialize_request(db_task: TaskDB) -> TaskCreateRequest:
    raw_urls = db_task.urls or []
    profile_data = db_task.analysis_profile or {}
    analysis_profile = AnalysisProfile(**profile_data) if profile_data else AnalysisProfile()
    if raw_urls and isinstance(raw_urls[0], dict):
        competitor_urls = [CompetitorUrl(competitor=item["competitor"], url=item["url"]) for item in raw_urls]
        return TaskCreateRequest(
            product_goal=db_task.product_goal,
            competitors=db_task.competitors,
            competitor_urls=competitor_urls,
            comments=db_task.comments,
            image_names=db_task.image_names,
            analysis_profile=analysis_profile,
        )
    return TaskCreateRequest(
        product_goal=db_task.product_goal,
        competitors=db_task.competitors,
        urls=raw_urls,
        comments=db_task.comments,
        image_names=db_task.image_names,
        analysis_profile=analysis_profile,
    )


async def _db_task_to_pydantic(db_task: TaskDB) -> TaskRecord:
    """从数据库记录转换为 Pydantic 模型"""
    async with AsyncSessionLocal() as session:
        # 获取证据
        stmt = select(EvidenceDB).filter(EvidenceDB.task_id == db_task.id)
        result = await session.execute(stmt)
        db_evidence = result.scalars().all()
        evidence = [
            Evidence(
                id=ev.id,
                source_type=ev.source_type,
                source_url=ev.source_url,
                competitor=ev.competitor,
                dimension=ev.dimension,
                claim=ev.claim,
                quote=ev.quote,
                confidence=ev.confidence,
                freshness=ev.freshness,
                media_ref=ev.media_ref,
                untrusted=bool(ev.untrusted),
                content_hash=ev.content_hash,
                license_risk=ev.license_risk,
                credibility_score=_coerce_score(ev.credibility_score),
                relevance_score=_coerce_score(ev.relevance_score),
                quality_score=_coerce_score(ev.quality_score),
            )
            for ev in db_evidence
        ]

        # 从 TaskDB 的 JSON 字段读取 claims, conflicts, events
        claims = [Claim(**c) for c in (db_task.claims or [])]
        conflicts = [Conflict(**c) for c in (db_task.conflicts or [])]
        events = [TaskEvent(**e) for e in (db_task.events or [])]

        # 从 coverage JSON 字段读取
        coverage = None
        if db_task.coverage:
            cov_data = db_task.coverage
            coverage = CoverageGateResult(
                passed=cov_data.get("passed", False),
                score=cov_data.get("score", 0.0),
                covered_dimensions=[
                    EvidenceDimension(d) for d in cov_data.get("covered_dimensions", [])
                ],
                missing_dimensions=[
                    EvidenceDimension(d) for d in cov_data.get("missing_dimensions", [])
                ],
                gap_queries=cov_data.get("gap_queries", []),
            )

        decision_history = [DecisionPackVersion(**item) for item in (db_task.decision_history or [])]
        memory_state = WorkflowMemoryState(**db_task.memory_state) if db_task.memory_state else None
        metrics = TaskMetrics(**db_task.metrics) if db_task.metrics else None
        research_plan = ResearchPlan(**db_task.research_plan) if db_task.research_plan else None
        review = ReviewScore(**db_task.review) if db_task.review else None
        _current_version_meta = max(decision_history, key=lambda item: item.version, default=None)

        # 从 ResultDB 读取决策包和复核评分
        stmt = select(ResultDB).filter(ResultDB.task_id == db_task.id)
        result = await session.execute(stmt)
        db_result = result.scalar_one_or_none()
        decision_pack = None
        review = None
        budget_usage = None

        if db_result:
            # 决策包
            positioning = [DecisionAction(**a) for a in (db_result.positioning or [])]
            mvp_priorities = [DecisionAction(**a) for a in (db_result.mvp_priorities or [])]
            pricing_insights = [DecisionAction(**a) for a in (getattr(db_result, 'pricing_insights', None) or [])]
            battlecard = [DecisionAction(**a) for a in (getattr(db_result, 'battlecard', None) or [])]
            decision_pack = DecisionPack(
                id=db_result.id,
                positioning=positioning,
                mvp_priorities=mvp_priorities,
                pricing_insights=pricing_insights,
                battlecard=battlecard,
                summary=db_result.summary,
                generated_at=db_result.generated_at,
            )

            # 复核评分
            review = ReviewScore(
                score=db_result.review_score,
                citation_precision=db_result.citation_precision,
                claim_support_rate=db_result.claim_support_rate,
                hallucination_risk=db_result.hallucination_risk,
                notes=db_result.review_notes or [],
            )

            # 预算使用
            if db_result.budget_usage:
                budget_usage = BudgetUsage(**db_result.budget_usage)

        request = _deserialize_request(db_task)

        task = TaskRecord(
            id=db_task.id,
            request=request,
            status=db_task.status,
            evidence=evidence,
            claims=claims,
            conflicts=conflicts,
            coverage=coverage,
            decision_pack=decision_pack,
            decision_history=decision_history,
            memory_state=memory_state,
            review=review,
            budget_usage=budget_usage,
            research_plan=research_plan,
            metrics=metrics,
            run_id=db_task.run_id,
            thread_id=db_task.thread_id,
            request_id=db_task.request_id,
            last_error_stage=db_task.last_error_stage,
            last_error_message=db_task.last_error_message,
            last_error_traceback=db_task.last_error_traceback,
            last_checkpoint_id=db_task.last_checkpoint_id,
            events=events,
            created_at=db_task.created_at,
            updated_at=db_task.updated_at,
            research_round=db_task.research_round or 0,
            rewrite_round=db_task.rewrite_round or 0,
        )
        task.observable_id = build_task_observable_id(task)
        return task


class InMemoryTaskStore:
    """简化的任务存储 - 支持内存和数据库两种模式"""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._use_db = self._check_db_connection()

    def _check_db_connection(self) -> bool:
        try:
            async def test_conn():
                async with AsyncSessionLocal() as session:
                    await session.execute(text("SELECT 1"))
                    if session.bind and session.bind.dialect.name == "sqlite":
                        result = await session.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('tasks', 'evidence', 'results')"))
                        tables = {row[0] for row in result.fetchall()}
                        return {"tasks", "evidence", "results"}.issubset(tables)
                return True
            return self._run_async(test_conn())
        except Exception as e:
            logger.warning(f"Database connection failed, falling back to memory storage: {e}")
            return False

    def _run_async(self, coro):
        return globals()['_run_async'](coro)

    def create(self, task: TaskRecord) -> TaskRecord:
        """创建新任务"""
        if self._use_db:
            async def _create():
                async with AsyncSessionLocal() as session:
                    db_task = TaskDB(
                        id=task.id,
                        product_goal=task.request.product_goal,
                        competitors=[str(c) for c in task.request.competitors],
                        urls=_serialize_urls(task.request),
                        comments=task.request.comments,
                        analysis_profile=task.request.analysis_profile.model_dump(mode="json"),
                        image_names=task.request.image_names,
                        status=task.status,
                        created_at=task.created_at,
                        updated_at=task.updated_at,
                        claims=[],
                        conflicts=[],
                        events=[],
                        decision_history=[item.model_dump(mode="json") for item in task.decision_history],
                        memory_state=task.memory_state.model_dump(mode="json") if task.memory_state else None,
                        review=task.review.model_dump(mode="json") if task.review else None,
                        metrics=task.metrics.model_dump(mode="json") if task.metrics else None,
                        research_plan=task.research_plan.model_dump(mode="json") if task.research_plan else None,
                        research_round=task.research_round,
                        rewrite_round=task.rewrite_round,
                        run_id=task.run_id,
                        thread_id=task.thread_id,
                        request_id=task.request_id,
                        last_error_stage=task.last_error_stage,
                        last_error_message=task.last_error_message,
                        last_error_traceback=task.last_error_traceback,
                        last_checkpoint_id=task.last_checkpoint_id,
                    )
                    session.add(db_task)
                    await session.commit()

            self._run_async(_create())

        self._tasks[task.id] = task
        return task

    def get(self, task_id: str) -> TaskRecord | None:
        """获取任务"""
        # 先从内存缓存获取
        if task_id in self._tasks:
            return self._tasks[task_id]

        # 从数据库获取
        if self._use_db:
            async def _get():
                async with AsyncSessionLocal() as session:
                    db_task = await session.get(TaskDB, task_id)
                    if db_task:
                        return await _db_task_to_pydantic(db_task)
                    return None

            task = self._run_async(_get())
            if task:
                self._tasks[task_id] = task
            return task

        return None

    def list(self) -> list[TaskRecord]:
        """列出任务，优先返回缓存，必要时从数据库补全。"""
        tasks = list(self._tasks.values())
        if not self._use_db:
            return sorted(tasks, key=lambda task: task.created_at, reverse=True)

        async def _list():
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(TaskDB))
                db_tasks = result.scalars().all()
                return [await _db_task_to_pydantic(db_task) for db_task in db_tasks]

        db_tasks = self._run_async(_list()) or []
        merged: dict[str, TaskRecord] = {task.id: task for task in tasks}
        for task in db_tasks:
            merged[task.id] = task
            self._tasks[task.id] = task
        return sorted(merged.values(), key=lambda task: task.created_at, reverse=True)

    def update(self, task: TaskRecord) -> TaskRecord:
        """更新任务"""
        task.updated_at = datetime.now(timezone.utc)

        if self._use_db:
            async def _update():
                async with AsyncSessionLocal() as session:
                    db_task = await session.get(TaskDB, task.id)
                    if db_task:
                        db_task.status = task.status
                        db_task.updated_at = task.updated_at

                        # 更新 JSON 字段
                        db_task.claims = [
                            {
                                "id": c.id,
                                "statement": c.statement,
                                "dimension": c.dimension.value,
                                "competitor": c.competitor,
                                "evidence_ids": c.evidence_ids,
                                "confidence": c.confidence,
                                "conflict_ids": c.conflict_ids,
                            }
                            for c in task.claims
                        ]
                        db_task.conflicts = [
                            {
                                "id": c.id,
                                "claim_ids": c.claim_ids,
                                "resolution": c.resolution,
                                "rationale": c.rationale,
                                "confidence": c.confidence,
                            }
                            for c in task.conflicts
                        ]
                        db_task.events = [
                            {
                                "id": e.id,
                                "task_id": e.task_id,
                                "stage": e.stage,
                                "message": e.message,
                                "status": e.status.value,
                                "created_at": e.created_at.isoformat() if e.created_at else None,
                            }
                            for e in task.events
                        ]

                        db_task.decision_history = [item.model_dump(mode="json") for item in task.decision_history]
                        db_task.memory_state = task.memory_state.model_dump(mode="json") if task.memory_state else None
                        db_task.review = task.review.model_dump(mode="json") if task.review else None
                        db_task.metrics = task.metrics.model_dump(mode="json") if task.metrics else None
                        db_task.research_plan = task.research_plan.model_dump(mode="json") if task.research_plan else None
                        db_task.research_round = task.research_round
                        db_task.rewrite_round = task.rewrite_round
                        db_task.run_id = task.run_id
                        db_task.thread_id = task.thread_id
                        db_task.request_id = task.request_id
                        db_task.last_error_stage = task.last_error_stage
                        db_task.last_error_message = task.last_error_message
                        db_task.last_error_traceback = task.last_error_traceback
                        db_task.last_checkpoint_id = task.last_checkpoint_id

                        # 更新 coverage
                        if task.coverage:
                            db_task.coverage = {
                                "passed": task.coverage.passed,
                                "score": task.coverage.score,
                                "covered_dimensions": [d.value for d in task.coverage.covered_dimensions],
                                "missing_dimensions": [d.value for d in task.coverage.missing_dimensions],
                                "gap_queries": task.coverage.gap_queries,
                            }

                    # 更新证据
                    for ev in task.evidence:
                        db_ev = await session.get(EvidenceDB, ev.id)
                        if not db_ev:
                            db_ev = EvidenceDB(
                                id=ev.id,
                                task_id=task.id,
                                source_type=ev.source_type,
                                source_url=ev.source_url,
                                competitor=ev.competitor,
                                dimension=ev.dimension,
                                claim=ev.claim,
                                quote=ev.quote,
                                confidence=ev.confidence,
                                freshness=ev.freshness,
                                media_ref=ev.media_ref,
                                untrusted=1 if ev.untrusted else 0,
                                content_hash=ev.content_hash,
                                license_risk=ev.license_risk,
                                credibility_score=ev.credibility_score,
                                relevance_score=ev.relevance_score,
                                quality_score=ev.quality_score,
                            )
                            session.add(db_ev)
                        else:
                            db_ev.claim = ev.claim
                            db_ev.quote = ev.quote
                            db_ev.confidence = ev.confidence
                            db_ev.credibility_score = ev.credibility_score
                            db_ev.relevance_score = ev.relevance_score
                            db_ev.quality_score = ev.quality_score

                    # 更新决策包和复核评分（存储在 ResultDB）
                    if task.decision_pack:
                        stmt = select(ResultDB).filter(ResultDB.task_id == task.id)
                        result = await session.execute(stmt)
                        db_result = result.scalar_one_or_none()

                        def _serialize_action(a):
                            return {
                                "title": a.title,
                                "dimension": a.dimension.value,
                                "recommendation": a.recommendation,
                                "rationale": a.rationale,
                                "evidence_ids": a.evidence_ids,
                                "priority": a.priority,
                            }

                        positioning_data = [_serialize_action(a) for a in task.decision_pack.positioning]
                        mvp_data = [_serialize_action(a) for a in task.decision_pack.mvp_priorities]
                        pricing_data = [_serialize_action(a) for a in task.decision_pack.pricing_insights]
                        battlecard_data = [_serialize_action(a) for a in task.decision_pack.battlecard]

                        if not db_result:
                            db_result = ResultDB(
                                id=task.decision_pack.id,
                                task_id=task.id,
                                positioning=positioning_data,
                                mvp_priorities=mvp_data,
                                pricing_insights=pricing_data,
                                battlecard=battlecard_data,
                                summary=task.decision_pack.summary,
                                review_score=task.review.score if task.review else 0.0,
                                citation_precision=task.review.citation_precision if task.review else 0.0,
                                claim_support_rate=task.review.claim_support_rate if task.review else 0.0,
                                hallucination_risk=task.review.hallucination_risk if task.review else "unknown",
                                review_notes=task.review.notes if task.review else [],
                                budget_usage=(
                                    {
                                        "estimated_sources": task.budget_usage.estimated_sources,
                                        "estimated_tokens": task.budget_usage.estimated_tokens,
                                        "estimated_cost_usd": task.budget_usage.estimated_cost_usd,
                                        "within_budget": task.budget_usage.within_budget,
                                    }
                                    if task.budget_usage
                                    else None
                                ),
                                generated_at=task.decision_pack.generated_at,
                            )
                            session.add(db_result)
                        else:
                            # 更新已存在的决策包数据
                            db_result.id = task.decision_pack.id
                            db_result.positioning = positioning_data
                            db_result.mvp_priorities = mvp_data
                            db_result.pricing_insights = pricing_data
                            db_result.battlecard = battlecard_data
                            db_result.summary = task.decision_pack.summary
                            db_result.generated_at = task.decision_pack.generated_at
                            if task.review:
                                db_result.review_score = task.review.score
                                db_result.citation_precision = task.review.citation_precision
                                db_result.claim_support_rate = task.review.claim_support_rate
                                db_result.hallucination_risk = task.review.hallucination_risk
                                db_result.review_notes = task.review.notes

                    await session.commit()

            self._run_async(_update())

        self._tasks[task.id] = task
        return task

    def append_event(
        self,
        task: TaskRecord,
        stage: str,
        message: str,
        status: TaskStatus = TaskStatus.running,
    ) -> TaskEvent:
        """追加任务事件"""
        event = TaskEvent(task_id=task.id, stage=stage, message=message, status=status)
        task.events.append(event)
        self.update(task)
        return event

    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        message: str | None = None,
        stage: str = "task",
    ) -> TaskRecord | None:
        task = self.get(task_id)
        if task is None:
            return None
        task.status = status
        if message:
            self.append_event(task, stage, message, status)
        else:
            self.update(task)
        return task

    def cancel(self, task_id: str, reason: str = "用户主动停止分析") -> TaskRecord | None:
        task = self.get(task_id)
        if task is None:
            return None
        if task.status in (TaskStatus.completed, TaskStatus.failed, TaskStatus.cancelled):
            return task
        task.status = TaskStatus.cancelled
        self.append_event(task, "cancelled", reason, TaskStatus.cancelled)
        return task


task_store = InMemoryTaskStore()
