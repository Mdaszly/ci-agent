"""Human-in-the-loop (HITL) 模块。

实现 LangGraph 的 interrupt 机制，支持在关键节点暂停等待人工批准。

设计原则：
- 可配置：通过环境变量控制是否启用 HITL
- 降级：LangGraph interrupt 不可用时自动跳过
- 幂等：interrupt 前的副作用必须是幂等的
- 可观测：记录每次中断和恢复

使用方式：
1. 在 build_graph() 中指定 interrupt_before 或 interrupt_after
2. 节点内使用 interrupt() 函数动态中断
3. 通过 resume_workflow() 恢复执行
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 检查 interrupt 是否可用
_interrupt_available: bool | None = None


def _check_interrupt_available() -> bool:
    """检查 LangGraph interrupt 函数是否可用。"""
    global _interrupt_available
    if _interrupt_available is not None:
        return _interrupt_available
    try:
        from langgraph.types import interrupt  # noqa: F401

        _interrupt_available = True
    except ImportError:
        _interrupt_available = False
        logger.info("LangGraph interrupt 不可用，HITL 将降级为自动批准")
    return _interrupt_available


def request_approval(approval_message: str, default: bool = True) -> bool:
    """请求人工批准。

    在节点内调用此函数会暂停工作流执行，等待人工通过 resume_workflow() 恢复。

    Args:
        approval_message: 展示给审批人的消息
        default: 自动批准模式下的默认返回值（HITL 不可用时）

    Returns:
        是否批准
    """
    if not _check_interrupt_available():
        logger.info(f"HITL 不可用，自动批准: {approval_message[:50]}...")
        return default

    try:
        from langgraph.types import interrupt

        # interrupt 会暂停执行，恢复时返回 Command(resume=value)
        result = interrupt(approval_message)
        # 恢复时 result 是 Command.resume 的值
        if isinstance(result, bool):
            return result
        if isinstance(result, dict):
            return result.get("approved", default)
        return bool(result)
    except Exception as e:
        logger.warning(f"interrupt 调用失败，使用默认值 {default}: {e}")
        return default


def get_interrupt_nodes() -> list[str]:
    """获取需要中断的节点列表。

    通过环境变量 HITL_INTERRUPT_NODES 配置，默认为空（不中断）。
    示例: HITL_INTERRUPT_NODES=writer,reviewer

    Returns:
        节点名称列表
    """
    import os

    nodes_str = os.environ.get("HITL_INTERRUPT_NODES", "")
    if not nodes_str.strip():
        return []
    return [n.strip() for n in nodes_str.split(",") if n.strip()]


def should_interrupt_before(node_name: str) -> bool:
    """判断是否应在指定节点前中断。"""
    return node_name in get_interrupt_nodes()


def make_interrupt_config() -> dict[str, Any]:
    """构造 build_graph 所需的中断配置。

    Returns:
        包含 interrupt_before 和 interrupt_after 的字典
    """
    interrupt_nodes = get_interrupt_nodes()
    config: dict[str, Any] = {}
    if interrupt_nodes:
        config["interrupt_before"] = interrupt_nodes
    return config


def resume_with_approval(graph: Any, thread_id: str, approved: bool, reason: str = "") -> Any | None:
    """人工批准后恢复工作流执行。

    Args:
        graph: 编译后的 LangGraph 图
        thread_id: 会话 ID
        approved: 是否批准
        reason: 批准/拒绝原因

    Returns:
        执行结果或 None
    """
    if graph is None:
        return None

    try:
        from langgraph.types import Command
    except ImportError:
        logger.warning("Command 不可用，无法恢复 interrupt")
        return None

    from app.services.checkpointer import make_thread_config

    config = make_thread_config(thread_id)
    try:
        # 使用 Command 恢复，传入 resume 值
        resume_command = Command(resume={"approved": approved, "reason": reason})
        return graph.invoke(resume_command, config=config)
    except Exception as e:
        logger.error(f"恢复 interrupt 失败: {e}")
        return None
