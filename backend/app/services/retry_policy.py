"""LangGraph RetryPolicy 封装模块。

为 LLM 调用节点和外部 API 调用节点提供统一的重试策略。

设计原则：
- 兼容性：自动适配不同 LangGraph 版本的 RetryPolicy API
- 可配置：通过环境变量配置重试参数
- 降级：RetryPolicy 不可用时返回 None，不影响原有逻辑
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 缓存 RetryPolicy 可用性
_retry_policy_available: bool | None = None


def _check_retry_policy_available() -> bool:
    """检查 LangGraph RetryPolicy 是否可用。"""
    global _retry_policy_available
    if _retry_policy_available is not None:
        return _retry_policy_available
    try:
        from langgraph.types import RetryPolicy  # noqa: F401

        _retry_policy_available = True
    except ImportError:
        try:
            # 兼容旧版本路径
            from langgraph.graph.types import RetryPolicy  # noqa: F401

            _retry_policy_available = True
        except ImportError:
            _retry_policy_available = False
            logger.info("LangGraph RetryPolicy 不可用，重试策略将降级为节点内 try-catch")
    return _retry_policy_available


def make_llm_retry_policy() -> Any | None:
    """为 LLM 调用节点创建重试策略。

    配置：
    - max_attempts: 3（含首次）
    - initial_interval: 1.0s
    - backoff_factor: 2.0（指数退避）
    - max_interval: 30.0s
    - jitter: True（避免惊群）
    - retry_on: [ConnectionError, TimeoutError]

    Returns:
        RetryPolicy 实例或 None（不可用时）
    """
    if not _check_retry_policy_available():
        return None

    try:
        from langgraph.types import RetryPolicy

        return RetryPolicy(
            max_attempts=3,
            initial_interval=1.0,
            backoff_factor=2.0,
            max_interval=30.0,
            jitter=True,
            retry_on=[ConnectionError, TimeoutError, OSError],
        )
    except Exception as e:
        logger.warning(f"创建 LLM RetryPolicy 失败: {e}")
        return None


def make_api_retry_policy() -> Any | None:
    """为外部 API 调用节点创建重试策略（更激进）。

    配置：
    - max_attempts: 5
    - initial_interval: 0.5s
    - backoff_factor: 2.0
    - max_interval: 60.0s

    Returns:
        RetryPolicy 实例或 None
    """
    if not _check_retry_policy_available():
        return None

    try:
        from langgraph.types import RetryPolicy

        return RetryPolicy(
            max_attempts=5,
            initial_interval=0.5,
            backoff_factor=2.0,
            max_interval=60.0,
            jitter=True,
            retry_on=[ConnectionError, TimeoutError, OSError],
        )
    except Exception as e:
        logger.warning(f"创建 API RetryPolicy 失败: {e}")
        return None


def get_node_retry_policies() -> dict[str, Any]:
    """获取各节点的 RetryPolicy 配置字典。

    用于 build_graph() 时批量应用：
        retry_policies = get_node_retry_policies()
        for node_name, policy in retry_policies.items():
            graph.add_node(node_name, node_func, retry_policy=policy)

    Returns:
        {node_name: RetryPolicy} 字典，不可用时为空字典
    """
    llm_policy = make_llm_retry_policy()
    api_policy = make_api_retry_policy()

    policies: dict[str, Any] = {}

    # LLM 密集型节点
    if llm_policy is not None:
        policies["planner"] = llm_policy
        policies["writer"] = llm_policy
        policies["reviewer"] = llm_policy
        policies["repair"] = llm_policy

    # API 密集型节点
    if api_policy is not None:
        policies["research"] = api_policy

    return policies
