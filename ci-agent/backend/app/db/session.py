from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import db_settings
from app.db.models import Base

logger = logging.getLogger(__name__)
logger.debug("Creating async database engine for compose startup")

if db_settings.use_sqlite:
    sqlite_path = Path(db_settings.url.removeprefix("sqlite+aiosqlite:///"))
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(db_settings.url, echo=True)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if db_settings.use_sqlite:
            await conn.run_sync(_sync_sqlite_schema)


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