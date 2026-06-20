from __future__ import annotations

import asyncio
import os

import pytest

# 测试环境默认关闭认证，避免每个接口都依赖 JWT/API Key
os.environ.setdefault("AUTH_DISABLED", "true")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DB_USE_SQLITE", "true")

from app.core.auth import AuthSubject, get_auth_subject
from app.db.session import init_db
from app.services import decision_memory as dm

asyncio.run(init_db())

from app.main import app


@pytest.fixture(autouse=True)
def _reset_decision_memory() -> None:
    dm._MEMORY_INDEX.clear()
    dm._MEMORY_VECTORS.clear()
    yield
    dm._MEMORY_INDEX.clear()
    dm._MEMORY_VECTORS.clear()


app.dependency_overrides[get_auth_subject] = lambda: AuthSubject(
    user_id="test-user",
    tenant_id="default",
    role="admin",
    auth_method="disabled",
)
