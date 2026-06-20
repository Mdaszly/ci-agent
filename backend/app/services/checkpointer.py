"""LangGraph Checkpointer 工厂模块。

支持两种后端：
1. MemorySaver（默认，开发/测试环境）
2. PostgresSaver（生产环境，通过 DB_USE_SQLITE=false 启用）

设计目标：
- 懒加载：首次调用时才建立连接
- 降级：PostgreSQL 不可用时自动降级到 MemorySaver
- 线程安全：使用锁保护单例创建
- 可测试：提供 reset_checkpointer() 用于测试
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_checkpointer: Any | None = None
_checkpointer_lock = threading.Lock()
_checkpointer_kind: str = "memory"  # memory | postgres


def _build_postgres_checkpointer():
    """构建 PostgreSQL checkpointer。

    使用同步 psycopg3 连接（LangGraph PostgresSaver 要求同步连接）。
    返回 (checkpointer, kind) 元组；失败返回 (None, None)。
    """
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg_pool import ConnectionPool
    except ImportError:
        logger.info("PostgresSaver 或 psycopg_pool 未安装，跳过 PostgreSQL checkpointer")
        return None, None

    from app.core.config import db_settings

    if db_settings.use_sqlite:
        # SQLite 模式不支持 PostgresSaver
        return None, None

    # 构造同步 DSN（LangGraph PostgresSaver 使用 psycopg3 同步连接）
    dsn = (
        f"host={db_settings.host} port={db_settings.port} "
        f"dbname={db_settings.database} user={db_settings.username} "
        f"password={db_settings.password}"
    )

    try:
        # 使用连接池提升生产稳定性
        pool = ConnectionPool(conninfo=dsn, max_size=20, open=True)
        checkpointer = PostgresSaver(conn=pool)
        # 首次使用时创建必要的表结构
        checkpointer.setup()
        logger.info("PostgreSQL checkpointer 已启用")
        return checkpointer, "postgres"
    except Exception as e:
        logger.warning(f"PostgreSQL checkpointer 初始化失败，降级到 MemorySaver: {e}")
        return None, None


def _build_memory_checkpointer():
    """构建内存 checkpointer（开发/测试环境）。"""
    try:
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver(), "memory"
    except ImportError:
        logger.warning("MemorySaver 不可用，checkpointer 将为 None")
        return None, None


def get_checkpointer() -> Any | None:
    """获取全局 checkpointer 单例。

    首次调用时根据配置决定后端：
    - DB_USE_SQLITE=false → 尝试 PostgreSQL，失败降级到 Memory
    - 其他情况 → Memory

    Returns:
        checkpointer 实例或 None（LangGraph 未安装时）
    """
    global _checkpointer, _checkpointer_kind

    if _checkpointer is not None:
        return _checkpointer

    with _checkpointer_lock:
        if _checkpointer is not None:
            return _checkpointer

        # 先尝试 PostgreSQL（仅当配置为 PG 时）
        try:
            from app.core.config import db_settings

            if not db_settings.use_sqlite:
                cp, kind = _build_postgres_checkpointer()
                if cp is not None:
                    _checkpointer = cp
                    _checkpointer_kind = kind
                    return _checkpointer
        except Exception as e:
            logger.warning(f"读取数据库配置失败: {e}")

        # 降级到 Memory
        cp, kind = _build_memory_checkpointer()
        _checkpointer = cp
        _checkpointer_kind = kind
        return _checkpointer


def get_checkpointer_kind() -> str:
    """返回当前 checkpointer 类型（memory/postgres/none）。"""
    return _checkpointer_kind if _checkpointer is not None else "none"


def reset_checkpointer() -> None:
    """重置全局 checkpointer（仅用于测试）。"""
    global _checkpointer, _checkpointer_kind
    with _checkpointer_lock:
        _checkpointer = None
        _checkpointer_kind = "memory"


def make_thread_config(thread_id: str) -> dict:
    """构造 LangGraph invoke 所需的 thread_id 配置。

    Args:
        thread_id: 会话唯一标识，通常使用 task.id

    Returns:
        config 字典，形如 {"configurable": {"thread_id": "..."}}
    """
    if not thread_id:
        raise ValueError("thread_id 不能为空")
    return {"configurable": {"thread_id": thread_id}}


def get_state_history(graph: Any, thread_id: str) -> list:
    """获取指定 thread 的状态历史（Time Travel 支持）。

    Args:
        graph: 编译后的 LangGraph 图
        thread_id: 会话 ID

    Returns:
        状态快照列表（按时间倒序）
    """
    if graph is None:
        return []
    config = make_thread_config(thread_id)
    try:
        return list(graph.get_state_history(config))
    except Exception as e:
        logger.warning(f"获取状态历史失败: {e}")
        return []


def get_state_snapshot(graph: Any, thread_id: str) -> Any | None:
    """获取指定 thread 的当前状态快照。

    Args:
        graph: 编译后的 LangGraph 图
        thread_id: 会话 ID

    Returns:
        StateSnapshot 或 None
    """
    if graph is None:
        return None
    config = make_thread_config(thread_id)
    try:
        return graph.get_state(config)
    except Exception as e:
        logger.warning(f"获取状态快照失败: {e}")
        return None


def resume_workflow(graph: Any, thread_id: str) -> Any | None:
    """从最后一个 checkpoint 恢复工作流执行。

    Args:
        graph: 编译后的 LangGraph 图
        thread_id: 会话 ID

    Returns:
        执行结果或 None
    """
    if graph is None:
        return None
    config = make_thread_config(thread_id)
    try:
        # 传入 None 表示从最后状态继续
        return graph.invoke(None, config)
    except Exception as e:
        logger.error(f"恢复工作流失败: {e}")
        return None
