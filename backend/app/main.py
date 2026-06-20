from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import logging
import os

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from app.api.routes import router
from app.api.auth_routes import router as auth_router
from app.api.test_routes import router as test_router
from app.api.bad_case_routes import router as bad_case_router
from app.api.agent_routes import router as agent_router
from app.core.config import llm_settings, search_settings
from app.core.limiter import limiter
from app.db.session import AsyncSessionLocal, init_db, is_pgvector_enabled
from app.services.workflow_observability import get_runtime_snapshot, get_task_snapshot, render_prometheus_metrics

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        await init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.warning(f"Database initialization failed (may need manual setup): {e}")

    # 从 DB 重建 bad_case 内存缓存
    try:
        from app.db.session import AsyncSessionLocal
        from app.services.bad_case import bad_case_manager
        async with AsyncSessionLocal() as session:
            count = await bad_case_manager.rebuild_cache_from_db(session)
            if count > 0:
                logger.info("Bad case cache rebuilt from DB: %d records", count)
    except Exception as e:
        logger.warning(f"Bad case cache rebuild failed: {e}")

    yield


app = FastAPI(
    title="Competitive Intelligence Agent API",
    version="0.1.0",
    description="Evidence-first multi-agent workflow for competitive intelligence decisions.",
    lifespan=lifespan,
)

# CORS 配置：按环境分离，生产环境从环境变量读取白名单
# 注意：CORS middleware 必须在 rate limiter 之后添加，这样它会最先执行
is_dev = os.getenv("ENVIRONMENT", "development").lower() == "development"
if is_dev:
    # 开发环境：允许本地前端端口，不使用通配符 + credentials 的危险组合
    allow_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ]
    logger.info("Running in development mode - CORS restricted to local frontend ports")
else:
    # 生产环境：从 CORS_ALLOWED_ORIGINS 环境变量读取（逗号分隔）
    cors_env = os.getenv("CORS_ALLOWED_ORIGINS", "")
    allow_origins = [origin.strip() for origin in cors_env.split(",") if origin.strip()]
    if not allow_origins:
        logger.warning("CORS_ALLOWED_ORIGINS not set in production - no origins allowed")
    else:
        logger.info("Running in production mode - CORS allowed origins: %s", allow_origins)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册速率限制器（在 CORS 之后添加，这样 CORS 会先执行）
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(router)
app.include_router(auth_router)
app.include_router(test_router)
app.include_router(bad_case_router)
app.include_router(agent_router)


async def _probe_database() -> dict[str, object]:
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:  # pragma: no cover - readiness only
        return {"status": "error", "error": str(exc)}


@app.get("/health")
def health_check() -> dict[str, object]:
    """兼容性健康检查端点。"""
    return {"status": "ok", "mode": "live"}


@app.get("/health/live")
def liveness() -> dict[str, object]:
    """liveness 探针：进程存活即可。"""
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness() -> dict[str, object]:
    """readiness 探针：DB 可用且基础服务配置满足执行条件。"""
    db_probe = await _probe_database()
    readiness_status = "ok" if db_probe["status"] == "ok" else "degraded"
    payload = {
        "status": readiness_status,
        "database": db_probe,
        "llm": {
            "configured": bool(llm_settings.effective_api_key.strip()),
            "provider": llm_settings.provider,
            "model": llm_settings.effective_model,
        },
        "search": {
            "configured": search_settings.is_configured,
            "provider": search_settings.provider,
        },
        "pgvector": {
            "enabled": is_pgvector_enabled(),
        },
    }
    if db_probe["status"] != "ok":
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=payload)
    return payload


@app.get("/metrics")
def metrics() -> str:
    """Prometheus 指标暴露端点。"""
    return render_prometheus_metrics()


@app.get("/debug/runtime")
def debug_runtime() -> dict:
    """运行时诊断快照。"""
    return get_runtime_snapshot()


@app.get("/debug/tasks/{task_id}")
def debug_task(task_id: str) -> dict:
    """单任务运行轨迹与统计信息。"""
    return get_task_snapshot(task_id)


