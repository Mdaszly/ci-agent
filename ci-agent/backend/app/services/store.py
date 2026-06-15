from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, text

logger = logging.getLogger(__name__)

from app.db.models import EvidenceDB, ResultDB, TaskDB, TaskStatus
from app.db.session import AsyncSessionLocal
from app.models.schemas import (
    BudgetUsage,
    Claim,
    Conflict,
    CoverageGateResult,
    DecisionAction,
    DecisionPack,
    Evidence,
    EvidenceDimension,
    ReviewScore,
    TaskEvent,
    TaskRecord,
)


def _run_async(coro):
    """在同步或异步上下文中安全执行协程。"""
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            return asyncio.create_task(coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


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
            decision_pack = DecisionPack(
                id=db_result.id,
                positioning=positioning,
                mvp_priorities=mvp_priorities,
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

        # 重建 request 对象
        from app.models.schemas import TaskCreateRequest
        request = TaskCreateRequest(
            product_goal=db_task.product_goal,
            competitors=db_task.competitors,
            urls=db_task.urls,
            comments=db_task.comments,
            image_names=db_task.image_names,
        )

        return TaskRecord(
            id=db_task.id,
            request=request,
            status=db_task.status,
            evidence=evidence,
            claims=claims,
            conflicts=conflicts,
            coverage=coverage,
            decision_pack=decision_pack,
            review=review,
            budget_usage=budget_usage,
            events=events,
            created_at=db_task.created_at,
            updated_at=db_task.updated_at,
        )


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
                        urls=[str(u) for u in task.request.urls],
                        comments=task.request.comments,
                        image_names=task.request.image_names,
                        status=task.status,
                        created_at=task.created_at,
                        updated_at=task.updated_at,
                        claims=[],
                        conflicts=[],
                        events=[],
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
                        if not db_result:
                            positioning_data = [
                                {
                                    "title": a.title,
                                    "dimension": a.dimension.value,
                                    "recommendation": a.recommendation,
                                    "rationale": a.rationale,
                                    "evidence_ids": a.evidence_ids,
                                    "priority": a.priority,
                                }
                                for a in task.decision_pack.positioning
                            ]
                            mvp_data = [
                                {
                                    "title": a.title,
                                    "dimension": a.dimension.value,
                                    "recommendation": a.recommendation,
                                    "rationale": a.rationale,
                                    "evidence_ids": a.evidence_ids,
                                    "priority": a.priority,
                                }
                                for a in task.decision_pack.mvp_priorities
                            ]
                            db_result = ResultDB(
                                id=task.decision_pack.id,
                                task_id=task.id,
                                positioning=positioning_data,
                                mvp_priorities=mvp_data,
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


task_store = InMemoryTaskStore()
