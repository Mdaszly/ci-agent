from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import db_settings, embedding_settings, memory_settings
from app.db.models import Base

logger = logging.getLogger(__name__)
logger.debug("Creating async database engine for compose startup")

if db_settings.use_sqlite:
    sqlite_path = Path(db_settings.url.removeprefix("sqlite+aiosqlite:///"))
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(db_settings.url, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

_pgvector_enabled: bool = False


async def get_db_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    global _pgvector_enabled
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if db_settings.use_sqlite:
            await conn.run_sync(_sync_sqlite_schema)

    await _create_default_user()

    if not db_settings.use_sqlite:
        try:
            async with engine.begin() as conn:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                dim = embedding_settings.dimensions
                await conn.execute(text(
                    f"ALTER TABLE decision_memory_items "
                    f"ADD COLUMN IF NOT EXISTS embedding vector({dim})"
                ))
                await conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS idx_memory_embedding "
                    "ON decision_memory_items USING hnsw (embedding vector_cosine_ops) "
                    f"WITH (m = {memory_settings.hnsw_m}, ef_construction = {memory_settings.hnsw_ef_construction})"
                ))
            _pgvector_enabled = True
            logger.info("pgvector extension enabled with HNSW index (dim=%d)", dim)
        except Exception as e:
            _pgvector_enabled = False
            logger.warning("pgvector initialization failed, vector search disabled: %s", e)


def is_pgvector_enabled() -> bool:
    return _pgvector_enabled


def _sync_sqlite_schema(sync_conn) -> None:
    inspector = inspect(sync_conn)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_columns:
                continue
            column_type = column.type.compile(dialect=sync_conn.dialect)
            sync_conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}'))
            logger.warning("Added missing SQLite column %s.%s", table.name, column.name)


async def _create_default_user() -> None:
    from app.db.models import UserDB
    from app.core.auth import hash_password
    from app.models.schemas import new_id

    async with AsyncSessionLocal() as session:
        # 创建或更新管理员账号
        result = await session.execute(text("SELECT id FROM users WHERE username = 'admin'"))
        existing_admin = result.scalar_one_or_none()
        if existing_admin is None:
            admin_user = UserDB(
                id=new_id("user"),
                tenant_id="default-tenant",
                username="admin",
                password_hash=hash_password("admin"),
                role="admin",
                is_active=1,
            )
            session.add(admin_user)
            logger.info("Created default admin user: username='admin', password='admin'")
        else:
            await session.execute(
                text("UPDATE users SET password_hash = :hash, role = 'admin', is_active = 1 WHERE username = 'admin'"),
                {"hash": hash_password("admin")}
            )
            logger.info("Updated admin user password: username='admin', password='admin'")

        # 创建或更新普通用户账号
        result = await session.execute(text("SELECT id FROM users WHERE username = 'user'"))
        existing_user = result.scalar_one_or_none()
        if existing_user is None:
            user_user = UserDB(
                id=new_id("user"),
                tenant_id="default-tenant",
                username="user",
                password_hash=hash_password("user"),
                role="user",
                is_active=1,
            )
            session.add(user_user)
            logger.info("Created default user: username='user', password='user'")
        else:
            await session.execute(
                text("UPDATE users SET password_hash = :hash, role = 'user', is_active = 1 WHERE username = 'user'"),
                {"hash": hash_password("user")}
            )
            logger.info("Updated user password: username='user', password='user'")

        await session.commit()