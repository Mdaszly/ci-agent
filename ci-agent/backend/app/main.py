from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.db.session import init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        await init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.warning(f"Database initialization failed (may need manual setup): {e}")
    yield


app = FastAPI(
    title="Competitive Intelligence Agent API",
    version="0.1.0",
    description="Evidence-first multi-agent workflow for competitive intelligence decisions.",
    lifespan=lifespan,
)

# 开发环境：放宽 CORS 限制
is_dev = os.getenv("ENVIRONMENT", "development").lower() == "development"
if is_dev:
    # 开发环境：允许所有来源
    allow_origins = ["*"]
    logger.info("Running in development mode - CORS restrictions relaxed")
else:
    # 生产环境：严格限制来源
    allow_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}