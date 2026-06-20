"""Bad Case 持久化测试。

验证 DB 优先 + 内存缓存双写策略：
- create_bad_case: DB 写入 + 内存缓存更新
- get_bad_case: 内存优先，未命中回查 DB
- update_status / mark_fixed: DB 更新 + 内存同步
- rebuild_cache_from_db: 从 DB 全量重建缓存

使用内存 SQLite 数据库，每个测试函数独立 session。
"""
from __future__ import annotations

import os

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret-key-for-unit-tests-32chars")

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.db.models import Base, BadCaseDB
from app.models.schemas import BadCase, BadCaseType, BadCaseStatus, BadCaseSeverity
from app.services.bad_case import BadCaseManager


@pytest_asyncio.fixture
async def db_session():
    """创建内存 SQLite 数据库 session，每个测试独立"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def fresh_manager():
    """每个测试使用全新的 BadCaseManager 实例（重置单例）"""
    BadCaseManager._instance = None
    manager = BadCaseManager()
    yield manager
    BadCaseManager._instance = None


class TestCreateBadCase:
    """create_bad_case 持久化测试"""

    @pytest.mark.asyncio
    async def test_create_returns_bad_case_with_id(
        self, db_session: AsyncSession, fresh_manager: BadCaseManager
    ) -> None:
        bc = await fresh_manager.create_bad_case(
            type=BadCaseType.hallucination,
            description="测试幻觉案例",
            session=db_session,
            task_id="task-001",
            severity=BadCaseSeverity.high,
            context={"key": "value"},
            metrics={"score": 0.3},
        )
        assert bc.id is not None
        assert bc.type == BadCaseType.hallucination
        assert bc.task_id == "task-001"
        assert bc.severity == BadCaseSeverity.high
        assert bc.status == BadCaseStatus.pending

    @pytest.mark.asyncio
    async def test_create_persists_to_db(
        self, db_session: AsyncSession, fresh_manager: BadCaseManager
    ) -> None:
        bc = await fresh_manager.create_bad_case(
            type=BadCaseType.low_quality,
            description="DB 持久化验证",
            session=db_session,
        )
        # 直接从 DB 查询验证
        from sqlalchemy import select

        result = await db_session.execute(select(BadCaseDB).where(BadCaseDB.id == bc.id))
        db_obj = result.scalar_one_or_none()
        assert db_obj is not None
        assert db_obj.description == "DB 持久化验证"
        assert db_obj.type == BadCaseType.low_quality.value

    @pytest.mark.asyncio
    async def test_create_updates_memory_cache(
        self, db_session: AsyncSession, fresh_manager: BadCaseManager
    ) -> None:
        bc = await fresh_manager.create_bad_case(
            type=BadCaseType.coverage_gap,
            description="缓存更新验证",
            session=db_session,
        )
        # 验证内存缓存中有该记录
        assert bc.id in fresh_manager._bad_cases
        assert fresh_manager._bad_cases[bc.id].description == "缓存更新验证"

    @pytest.mark.asyncio
    async def test_create_updates_indices(
        self, db_session: AsyncSession, fresh_manager: BadCaseManager
    ) -> None:
        bc = await fresh_manager.create_bad_case(
            type=BadCaseType.evidence_conflict,
            description="索引更新验证",
            session=db_session,
            task_id="task-index-test",
        )
        # 验证 type 索引
        assert bc.id in fresh_manager._type_index[BadCaseType.evidence_conflict.value]
        # 验证 status 索引
        assert bc.id in fresh_manager._status_index[BadCaseStatus.pending.value]
        # 验证 task 索引
        assert bc.id in fresh_manager._task_index["task-index-test"]


class TestGetBadCase:
    """get_bad_case 读取测试"""

    @pytest.mark.asyncio
    async def test_get_from_cache_hit(
        self, db_session: AsyncSession, fresh_manager: BadCaseManager
    ) -> None:
        bc = await fresh_manager.create_bad_case(
            type=BadCaseType.hallucination,
            description="缓存命中测试",
            session=db_session,
        )
        # 第二次查询应从缓存命中
        result = await fresh_manager.get_bad_case(bc.id, db_session)
        assert result is not None
        assert result.id == bc.id
        assert result.description == "缓存命中测试"

    @pytest.mark.asyncio
    async def test_get_from_db_on_cache_miss(
        self, db_session: AsyncSession, fresh_manager: BadCaseManager
    ) -> None:
        bc = await fresh_manager.create_bad_case(
            type=BadCaseType.hallucination,
            description="缓存未命中回查 DB",
            session=db_session,
        )
        # 清空缓存模拟未命中
        fresh_manager._bad_cases.clear()
        fresh_manager._task_index.clear()
        fresh_manager._type_index.clear()
        fresh_manager._status_index.clear()

        # 查询应回查 DB
        result = await fresh_manager.get_bad_case(bc.id, db_session)
        assert result is not None
        assert result.id == bc.id
        assert result.description == "缓存未命中回查 DB"
        # 验证已回填缓存
        assert bc.id in fresh_manager._bad_cases

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(
        self, db_session: AsyncSession, fresh_manager: BadCaseManager
    ) -> None:
        result = await fresh_manager.get_bad_case("nonexistent-id", db_session)
        assert result is None


class TestUpdateStatus:
    """update_status 持久化测试"""

    @pytest.mark.asyncio
    async def test_update_status_persists_to_db(
        self, db_session: AsyncSession, fresh_manager: BadCaseManager
    ) -> None:
        bc = await fresh_manager.create_bad_case(
            type=BadCaseType.hallucination,
            description="状态更新测试",
            session=db_session,
        )
        await fresh_manager.update_status(bc.id, BadCaseStatus.analyzed, db_session)

        # 从 DB 验证
        from sqlalchemy import select

        result = await db_session.execute(select(BadCaseDB).where(BadCaseDB.id == bc.id))
        db_obj = result.scalar_one()
        assert db_obj.status == BadCaseStatus.analyzed.value

    @pytest.mark.asyncio
    async def test_update_status_syncs_memory(
        self, db_session: AsyncSession, fresh_manager: BadCaseManager
    ) -> None:
        bc = await fresh_manager.create_bad_case(
            type=BadCaseType.hallucination,
            description="内存同步测试",
            session=db_session,
        )
        updated = await fresh_manager.update_status(bc.id, BadCaseStatus.fixed, db_session)
        assert updated is not None
        assert updated.status == BadCaseStatus.fixed
        # 验证内存索引已更新
        assert bc.id in fresh_manager._status_index[BadCaseStatus.fixed.value]
        assert bc.id not in fresh_manager._status_index[BadCaseStatus.pending.value]

    @pytest.mark.asyncio
    async def test_update_status_nonexistent_returns_none(
        self, db_session: AsyncSession, fresh_manager: BadCaseManager
    ) -> None:
        result = await fresh_manager.update_status("nonexistent", BadCaseStatus.fixed, db_session)
        assert result is None


class TestMarkFixed:
    """mark_fixed 持久化测试"""

    @pytest.mark.asyncio
    async def test_mark_fixed_persists_to_db(
        self, db_session: AsyncSession, fresh_manager: BadCaseManager
    ) -> None:
        bc = await fresh_manager.create_bad_case(
            type=BadCaseType.low_quality,
            description="标记修复测试",
            session=db_session,
        )
        await fresh_manager.mark_fixed(bc.id, "tester", db_session)

        from sqlalchemy import select

        result = await db_session.execute(select(BadCaseDB).where(BadCaseDB.id == bc.id))
        db_obj = result.scalar_one()
        assert db_obj.status == BadCaseStatus.fixed.value
        assert db_obj.fixed_by == "tester"

    @pytest.mark.asyncio
    async def test_mark_fixed_syncs_memory(
        self, db_session: AsyncSession, fresh_manager: BadCaseManager
    ) -> None:
        bc = await fresh_manager.create_bad_case(
            type=BadCaseType.low_quality,
            description="标记修复内存同步",
            session=db_session,
        )
        updated = await fresh_manager.mark_fixed(bc.id, "admin", db_session)
        assert updated is not None
        assert updated.status == BadCaseStatus.fixed
        assert updated.fixed_by == "admin"


class TestRebuildCache:
    """rebuild_cache_from_db 缓存重建测试"""

    @pytest.mark.asyncio
    async def test_rebuild_loads_all_records(
        self, db_session: AsyncSession, fresh_manager: BadCaseManager
    ) -> None:
        # 创建 3 条记录
        for i in range(3):
            await fresh_manager.create_bad_case(
                type=BadCaseType.hallucination,
                description=f"重建测试 {i}",
                session=db_session,
            )

        # 清空缓存
        fresh_manager._bad_cases.clear()
        fresh_manager._task_index.clear()
        fresh_manager._type_index.clear()
        fresh_manager._status_index.clear()
        assert len(fresh_manager._bad_cases) == 0

        # 从 DB 重建
        count = await fresh_manager.rebuild_cache_from_db(db_session)
        assert count == 3
        assert len(fresh_manager._bad_cases) == 3

    @pytest.mark.asyncio
    async def test_rebuild_empty_db_returns_zero(
        self, db_session: AsyncSession, fresh_manager: BadCaseManager
    ) -> None:
        count = await fresh_manager.rebuild_cache_from_db(db_session)
        assert count == 0
        assert len(fresh_manager._bad_cases) == 0

    @pytest.mark.asyncio
    async def test_rebuild_restores_indices(
        self, db_session: AsyncSession, fresh_manager: BadCaseManager
    ) -> None:
        # 创建带 task_id 的记录
        await fresh_manager.create_bad_case(
            type=BadCaseType.evidence_conflict,
            description="索引恢复测试",
            session=db_session,
            task_id="task-rebuild-001",
        )

        # 清空并重建
        fresh_manager._bad_cases.clear()
        fresh_manager._task_index.clear()
        fresh_manager._type_index.clear()
        fresh_manager._status_index.clear()

        await fresh_manager.rebuild_cache_from_db(db_session)

        # 验证索引已恢复
        assert len(fresh_manager._type_index[BadCaseType.evidence_conflict.value]) == 1
        assert len(fresh_manager._status_index[BadCaseStatus.pending.value]) == 1
        assert len(fresh_manager._task_index["task-rebuild-001"]) == 1


class TestDbToDomainConversion:
    """_db_to_domain 转换方法测试"""

    @pytest.mark.asyncio
    async def test_conversion_preserves_all_fields(
        self, db_session: AsyncSession, fresh_manager: BadCaseManager
    ) -> None:
        bc = await fresh_manager.create_bad_case(
            type=BadCaseType.hallucination,
            description="转换测试",
            session=db_session,
            task_id="task-convert",
            severity=BadCaseSeverity.critical,
            context={"a": 1},
            metrics={"b": 2},
        )
        await fresh_manager.update_analysis(bc.id, "分析结果", db_session, "修复计划")

        # 清空缓存后重新查询，触发 _db_to_domain
        fresh_manager._bad_cases.clear()
        fresh_manager._task_index.clear()
        fresh_manager._type_index.clear()
        fresh_manager._status_index.clear()

        result = await fresh_manager.get_bad_case(bc.id, db_session)
        assert result is not None
        assert result.type == BadCaseType.hallucination
        assert result.severity == BadCaseSeverity.critical
        assert result.status == BadCaseStatus.analyzed  # update_analysis 自动转为 analyzed
        assert result.description == "转换测试"
        assert result.task_id == "task-convert"
        assert result.context == {"a": 1}
        assert result.metrics == {"b": 2}
        assert result.analysis == "分析结果"
        assert result.fix_plan == "修复计划"
