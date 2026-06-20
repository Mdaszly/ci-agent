"""Bad Case 管理器 - DB 优先 + 内存缓存双写策略。

写操作: DB 优先，DB 成功后才更新内存缓存；DB 失败则抛异常，内存不更新。
读操作: 内存优先（缓存命中），未命中回查 DB 并回填缓存。
缓存重建: 应用启动时或按需从 DB 全量加载到内存。
"""
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
from collections import defaultdict

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BadCaseDB
from app.models.schemas import BadCase, BadCaseType, BadCaseStatus, BadCaseSeverity


class BadCaseManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._bad_cases: Dict[str, BadCase] = {}
            cls._instance._task_index: Dict[str, List[str]] = defaultdict(list)
            cls._instance._type_index: Dict[str, List[str]] = defaultdict(list)
            cls._instance._status_index: Dict[str, List[str]] = defaultdict(list)
        return cls._instance

    # ========== 内部辅助方法 ==========

    def _db_to_domain(self, db_obj: BadCaseDB) -> BadCase:
        """将 BadCaseDB 转换为 BadCase 领域对象"""
        return BadCase(
            id=db_obj.id,
            task_id=db_obj.task_id,
            type=BadCaseType(db_obj.type),
            severity=BadCaseSeverity(db_obj.severity),
            status=BadCaseStatus(db_obj.status),
            description=db_obj.description,
            context=db_obj.context or {},
            metrics=db_obj.metrics or {},
            analysis=db_obj.analysis,
            fix_plan=db_obj.fix_plan,
            fixed_by=db_obj.fixed_by,
            created_at=db_obj.created_at,
            updated_at=db_obj.updated_at,
        )

    def _index_bad_case(self, bad_case: BadCase) -> None:
        """将领域对象加入内存索引"""
        self._bad_cases[bad_case.id] = bad_case
        if bad_case.task_id:
            self._task_index[bad_case.task_id].append(bad_case.id)
        self._type_index[bad_case.type.value].append(bad_case.id)
        self._status_index[bad_case.status.value].append(bad_case.id)

    def _reindex_status(self, bad_case_id: str, old_status: BadCaseStatus, new_status: BadCaseStatus) -> None:
        """更新内存中的 status 索引"""
        if bad_case_id in self._status_index.get(old_status.value, []):
            self._status_index[old_status.value].remove(bad_case_id)
        self._status_index[new_status.value].append(bad_case_id)

    # ========== 写操作（DB 优先） ==========

    async def create_bad_case(
        self,
        type: BadCaseType,
        description: str,
        session: AsyncSession,
        task_id: Optional[str] = None,
        severity: BadCaseSeverity = BadCaseSeverity.medium,
        context: Optional[dict] = None,
        metrics: Optional[dict] = None,
    ) -> BadCase:
        # 1. 构造领域对象（生成 id）
        bad_case = BadCase(
            type=type,
            severity=severity,
            description=description,
            task_id=task_id,
            context=context or {},
            metrics=metrics or {},
        )

        # 2. 构造 DB 对象（使用相同 id）
        db_obj = BadCaseDB(
            id=bad_case.id,
            task_id=bad_case.task_id,
            type=bad_case.type.value,
            severity=bad_case.severity.value,
            status=bad_case.status.value,
            description=bad_case.description,
            context=bad_case.context,
            metrics=bad_case.metrics,
            analysis=bad_case.analysis,
            fix_plan=bad_case.fix_plan,
            fixed_by=bad_case.fixed_by,
        )

        # 3. DB 写入（主）
        session.add(db_obj)
        await session.commit()
        await session.refresh(db_obj)

        # 4. 回填 DB 生成的时间戳到领域对象
        bad_case.created_at = db_obj.created_at
        bad_case.updated_at = db_obj.updated_at

        # 5. 更新内存缓存
        self._index_bad_case(bad_case)

        return bad_case

    async def update_status(
        self, bad_case_id: str, status: BadCaseStatus, session: AsyncSession
    ) -> Optional[BadCase]:
        # 1. DB 更新（主）
        result = await session.execute(
            select(BadCaseDB).where(BadCaseDB.id == bad_case_id)
        )
        db_obj = result.scalar_one_or_none()
        if db_obj is None:
            return None

        old_status_value = db_obj.status
        db_obj.status = status.value
        db_obj.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(db_obj)

        # 2. 内存同步
        bad_case = self._bad_cases.get(bad_case_id)
        if bad_case is not None:
            old_status = bad_case.status
            bad_case.status = status
            bad_case.updated_at = db_obj.updated_at
            self._reindex_status(bad_case_id, old_status, status)
        else:
            # 缓存未命中，从 DB 重建该条目
            bad_case = self._db_to_domain(db_obj)
            self._index_bad_case(bad_case)

        return bad_case

    async def update_analysis(
        self,
        bad_case_id: str,
        analysis: str,
        session: AsyncSession,
        fix_plan: Optional[str] = None,
    ) -> Optional[BadCase]:
        # 1. DB 更新（主）
        result = await session.execute(
            select(BadCaseDB).where(BadCaseDB.id == bad_case_id)
        )
        db_obj = result.scalar_one_or_none()
        if db_obj is None:
            return None

        old_status_value = db_obj.status
        db_obj.analysis = analysis
        if fix_plan:
            db_obj.fix_plan = fix_plan
        # 若当前为 pending，自动转为 analyzed
        new_status = None
        if db_obj.status == BadCaseStatus.pending.value:
            db_obj.status = BadCaseStatus.analyzed.value
            new_status = BadCaseStatus.analyzed
        db_obj.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(db_obj)

        # 2. 内存同步
        bad_case = self._bad_cases.get(bad_case_id)
        if bad_case is not None:
            old_status = bad_case.status
            bad_case.analysis = analysis
            if fix_plan:
                bad_case.fix_plan = fix_plan
            if new_status is not None:
                bad_case.status = new_status
                self._reindex_status(bad_case_id, old_status, new_status)
            bad_case.updated_at = db_obj.updated_at
        else:
            bad_case = self._db_to_domain(db_obj)
            self._index_bad_case(bad_case)

        return bad_case

    async def mark_fixed(
        self, bad_case_id: str, fixed_by: str, session: AsyncSession
    ) -> Optional[BadCase]:
        # 1. DB 更新（主）
        result = await session.execute(
            select(BadCaseDB).where(BadCaseDB.id == bad_case_id)
        )
        db_obj = result.scalar_one_or_none()
        if db_obj is None:
            return None

        db_obj.status = BadCaseStatus.fixed.value
        db_obj.fixed_by = fixed_by
        db_obj.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(db_obj)

        # 2. 内存同步
        bad_case = self._bad_cases.get(bad_case_id)
        if bad_case is not None:
            old_status = bad_case.status
            bad_case.status = BadCaseStatus.fixed
            bad_case.fixed_by = fixed_by
            bad_case.updated_at = db_obj.updated_at
            self._reindex_status(bad_case_id, old_status, BadCaseStatus.fixed)
        else:
            bad_case = self._db_to_domain(db_obj)
            self._index_bad_case(bad_case)

        return bad_case

    # ========== 读操作（内存优先，未命中回查 DB） ==========

    async def get_bad_case(
        self, bad_case_id: str, session: AsyncSession
    ) -> Optional[BadCase]:
        # 内存优先
        if bad_case_id in self._bad_cases:
            return self._bad_cases[bad_case_id]

        # 回查 DB
        result = await session.execute(
            select(BadCaseDB).where(BadCaseDB.id == bad_case_id)
        )
        db_obj = result.scalar_one_or_none()
        if db_obj is None:
            return None

        bad_case = self._db_to_domain(db_obj)
        self._index_bad_case(bad_case)  # 回填缓存
        return bad_case

    async def get_by_task(
        self, task_id: str, session: AsyncSession
    ) -> List[BadCase]:
        # 内存优先
        if task_id in self._task_index:
            return [
                self._bad_cases.get(id_)
                for id_ in self._task_index.get(task_id, [])
                if id_ in self._bad_cases
            ]

        # 回查 DB
        result = await session.execute(
            select(BadCaseDB).where(BadCaseDB.task_id == task_id)
        )
        bad_cases = []
        for db_obj in result.scalars():
            bad_case = self._db_to_domain(db_obj)
            if bad_case.id not in self._bad_cases:
                self._index_bad_case(bad_case)
            bad_cases.append(bad_case)
        return bad_cases

    async def get_by_type(
        self, type: BadCaseType, session: AsyncSession
    ) -> List[BadCase]:
        # 内存优先
        if type.value in self._type_index and self._type_index[type.value]:
            return [
                self._bad_cases.get(id_)
                for id_ in self._type_index.get(type.value, [])
                if id_ in self._bad_cases
            ]

        # 回查 DB
        result = await session.execute(
            select(BadCaseDB).where(BadCaseDB.type == type.value)
        )
        bad_cases = []
        for db_obj in result.scalars():
            bad_case = self._db_to_domain(db_obj)
            if bad_case.id not in self._bad_cases:
                self._index_bad_case(bad_case)
            bad_cases.append(bad_case)
        return bad_cases

    async def get_by_status(
        self, status: BadCaseStatus, session: AsyncSession
    ) -> List[BadCase]:
        # 内存优先
        if status.value in self._status_index and self._status_index[status.value]:
            return [
                self._bad_cases.get(id_)
                for id_ in self._status_index.get(status.value, [])
                if id_ in self._bad_cases
            ]

        # 回查 DB
        result = await session.execute(
            select(BadCaseDB).where(BadCaseDB.status == status.value)
        )
        bad_cases = []
        for db_obj in result.scalars():
            bad_case = self._db_to_domain(db_obj)
            if bad_case.id not in self._bad_cases:
                self._index_bad_case(bad_case)
            bad_cases.append(bad_case)
        return bad_cases

    # ========== 纯内存操作（无需 DB） ==========

    def get_summary(self) -> dict:
        """汇总统计 - 纯内存操作"""
        summary = {
            "total": len(self._bad_cases),
            "by_status": {},
            "by_type": {},
            "by_severity": {},
        }

        for status in BadCaseStatus:
            summary["by_status"][status.value] = len(self._status_index.get(status.value, []))

        for type_ in BadCaseType:
            summary["by_type"][type_.value] = len(self._type_index.get(type_.value, []))

        severity_counts = defaultdict(int)
        for bc in self._bad_cases.values():
            severity_counts[bc.severity.value] += 1
        summary["by_severity"] = dict(severity_counts)

        return summary

    async def get_regression_candidates(self, session: AsyncSession) -> List[BadCase]:
        return await self.get_by_status(BadCaseStatus.fixed, session)

    # ========== 检测与缓存重建 ==========

    async def detect_bad_cases_from_task(
        self, task: dict, session: AsyncSession
    ) -> List[BadCase]:
        bad_cases = []

        if task.get("review"):
            review = task["review"]
            score = review.get("score", 0)
            risk = review.get("hallucination_risk", "low")

            if risk == "high":
                bad_cases.append(await self.create_bad_case(
                    type=BadCaseType.hallucination,
                    description=f"高幻觉风险检测：评分={score}, 风险等级={risk}",
                    session=session,
                    task_id=task.get("id"),
                    severity=BadCaseSeverity.high,
                    context={"task": task.get("id"), "review": review},
                    metrics={"score": score, "risk": risk},
                ))

            if score < 0.5:
                bad_cases.append(await self.create_bad_case(
                    type=BadCaseType.low_quality,
                    description=f"决策包质量过低：评分={score}",
                    session=session,
                    task_id=task.get("id"),
                    severity=BadCaseSeverity.medium,
                    context={"task": task.get("id"), "review": review},
                    metrics={"score": score},
                ))

        if task.get("coverage"):
            coverage = task["coverage"]
            if not coverage.get("passed", True):
                missing = coverage.get("missing_dimensions", [])
                bad_cases.append(await self.create_bad_case(
                    type=BadCaseType.coverage_gap,
                    description=f"证据覆盖不足：缺失维度={missing}",
                    session=session,
                    task_id=task.get("id"),
                    severity=BadCaseSeverity.medium,
                    context={"task": task.get("id"), "coverage": coverage},
                    metrics={"score": coverage.get("score", 0), "missing_dimensions": len(missing)},
                ))

        if task.get("conflicts") and len(task["conflicts"]) > 0:
            bad_cases.append(await self.create_bad_case(
                type=BadCaseType.evidence_conflict,
                description=f"存在证据冲突：冲突数={len(task['conflicts'])}",
                session=session,
                task_id=task.get("id"),
                severity=BadCaseSeverity.high,
                context={"task": task.get("id"), "conflicts": task["conflicts"]},
                metrics={"conflict_count": len(task["conflicts"])},
            ))

        return bad_cases

    async def rebuild_cache_from_db(self, session: AsyncSession) -> int:
        """从 DB 全量重建内存缓存（应用启动时调用）。

        返回重建的记录数。
        """
        # 清空现有缓存
        self._bad_cases.clear()
        self._task_index.clear()
        self._type_index.clear()
        self._status_index.clear()

        result = await session.execute(select(BadCaseDB))
        count = 0
        for db_obj in result.scalars():
            bad_case = self._db_to_domain(db_obj)
            self._index_bad_case(bad_case)
            count += 1
        return count


bad_case_manager = BadCaseManager()
