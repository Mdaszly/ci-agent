"""速率限制测试。

验证 slowapi 限流装饰器在关键端点上正确生效：
- POST /api/auth/login（auth 限流：10/minute）
- POST /api/tasks（task_create 限流：10/minute）
- POST /api/uploads/file（upload 限流：20/minute）

使用 FastAPI TestClient 发送连续请求，验证第 N+1 次返回 429。
每个测试创建独立的 Limiter 实例避免测试间互相影响。
"""
from __future__ import annotations

import os

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret-key-for-unit-tests-32chars")
os.environ.setdefault("AUTH_DISABLED", "true")

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.config import rate_limit_settings


def _build_test_app() -> tuple[FastAPI, Limiter]:
    """构建一个最小化测试 app，包含限流装饰器的端点。

    返回 (app, limiter)，每个调用创建独立的 limiter 实例。
    """
    limiter = Limiter(key_func=get_remote_address)

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    @app.post("/api/auth/login")
    @limiter.limit(rate_limit_settings.auth)
    async def login(request: Request):
        return {"status": "ok"}

    @app.post("/api/tasks")
    @limiter.limit(rate_limit_settings.task_create)
    async def create_task(request: Request):
        return {"status": "ok"}

    @app.post("/api/uploads/file")
    @limiter.limit(rate_limit_settings.upload)
    async def upload_file(request: Request):
        return {"status": "ok"}

    return app, limiter


@pytest.fixture
def client():
    """TestClient fixture，每个测试函数独立 app 和 limiter"""
    app, _ = _build_test_app()
    return TestClient(app)


class TestRateLimitConfig:
    """验证 RateLimitConfig 配置值正确加载"""

    def test_auth_limit_value(self) -> None:
        assert rate_limit_settings.auth == "10/minute"

    def test_task_create_limit_value(self) -> None:
        assert rate_limit_settings.task_create == "10/minute"

    def test_upload_limit_value(self) -> None:
        assert rate_limit_settings.upload == "20/minute"

    def test_default_limit_value(self) -> None:
        assert rate_limit_settings.default == "60/minute"


class TestLoginRateLimit:
    """登录端点限流测试（10/minute）"""

    def test_login_under_limit_returns_200(self, client: TestClient) -> None:
        """前 10 次请求应返回 200"""
        for i in range(10):
            resp = client.post("/api/auth/login")
            assert resp.status_code == 200, f"Request {i+1} should succeed"

    def test_login_exceeds_limit_returns_429(self, client: TestClient) -> None:
        """第 11 次请求应返回 429"""
        for i in range(10):
            client.post("/api/auth/login")
        resp = client.post("/api/auth/login")
        assert resp.status_code == 429


class TestTaskCreateRateLimit:
    """任务创建端点限流测试（10/minute）"""

    def test_task_create_exceeds_limit_returns_429(self, client: TestClient) -> None:
        """第 11 次请求应返回 429"""
        for i in range(10):
            resp = client.post("/api/tasks")
            assert resp.status_code == 200, f"Request {i+1} should succeed"
        resp = client.post("/api/tasks")
        assert resp.status_code == 429


class TestUploadRateLimit:
    """上传端点限流测试（20/minute）"""

    def test_upload_under_limit_returns_200(self, client: TestClient) -> None:
        """前 20 次请求应返回 200"""
        for i in range(20):
            resp = client.post("/api/uploads/file")
            assert resp.status_code == 200, f"Request {i+1} should succeed"

    def test_upload_exceeds_limit_returns_429(self, client: TestClient) -> None:
        """第 21 次请求应返回 429"""
        for i in range(20):
            client.post("/api/uploads/file")
        resp = client.post("/api/uploads/file")
        assert resp.status_code == 429


class TestRateLimitIsolation:
    """验证不同端点的限流相互独立"""

    def test_different_endpoints_have_independent_limits(self, client: TestClient) -> None:
        """登录 10 次 + 任务创建 10 次都不应触发对方的限流"""
        # 登录 10 次（达到 auth 限流上限）
        for _ in range(10):
            resp = client.post("/api/auth/login")
            assert resp.status_code == 200

        # 任务创建 10 次（达到 task_create 限流上限，但不应受 auth 限流影响）
        for _ in range(10):
            resp = client.post("/api/tasks")
            assert resp.status_code == 200

        # 登录第 11 次应被限流
        resp = client.post("/api/auth/login")
        assert resp.status_code == 429

        # 任务创建第 11 次也应被限流
        resp = client.post("/api/tasks")
        assert resp.status_code == 429
