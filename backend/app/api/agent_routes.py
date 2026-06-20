"""Agent 工程化增强模块的 API 端点"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.auth import get_auth_subject
from app.services.checkpointer import get_checkpointer, get_checkpointer_kind
from app.services.memory_store import get_memory_store

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.get("/checkpointer")
def get_checkpointer_info():
    """获取 Checkpointer 信息"""
    checkpointer = get_checkpointer()
    kind = get_checkpointer_kind()
    if checkpointer is None:
        return {
            "kind": "none",
            "available": False,
        }

    return {
        "kind": getattr(checkpointer, "kind", kind if kind != "none" else checkpointer.__class__.__name__),
        "available": True,
    }


@router.get("/memory/stats")
def get_memory_stats():
    """获取分层记忆统计信息"""
    memory_store = get_memory_store()
    return {
        "working": {
            "count": memory_store.working_memory.count(),
            "capacity": memory_store.working_memory.capacity,
            "expired": 0,
        },
        "short_term": {
            "count": memory_store.short_term_memory.count(),
            "capacity": memory_store.short_term_memory.capacity,
            "expired": 0,
        },
        "long_term": {
            "count": memory_store.long_term_memory.count(),
            "capacity": memory_store.long_term_memory.capacity,
            "expired": 0,
        },
    }
