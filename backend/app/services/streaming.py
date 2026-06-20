"""LangGraph Streaming 模块。

实现 LangGraph 的流式输出，支持三种流式模式：
1. values：每次状态变化时发送完整状态
2. updates：每个节点更新时发送增量更新
3. events：详细事件流（LLM token 级别）

设计原则：
- 兼容性：自动适配不同 LangGraph 版本
- 降级：streaming 不可用时降级到 invoke
- SSE 友好：输出格式兼容 Server-Sent Events
"""

from __future__ import annotations

import logging
from typing import Any, Iterator

logger = logging.getLogger(__name__)


def stream_workflow(
    graph: Any,
    input_data: dict,
    config: dict,
    stream_mode: str = "updates",
) -> Iterator[dict]:
    """流式执行工作流。

    Args:
        graph: 编译后的 LangGraph 图
        input_data: 输入数据
        config: 配置（包含 thread_id）
        stream_mode: 流式模式
            - "values": 每次状态变化发送完整状态
            - "updates": 每个节点更新发送增量
            - "events": 详细事件流

    Yields:
        流式数据块
    """
    if graph is None:
        logger.warning("graph 为 None，无法流式执行")
        return

    try:
        # 尝试使用 stream 方法
        for chunk in graph.stream(input_data, config=config, stream_mode=stream_mode):
            yield chunk
    except AttributeError:
        # stream 方法不可用，降级到 invoke
        logger.info("stream 方法不可用，降级到 invoke")
        try:
            result = graph.invoke(input_data, config=config)
            yield {"type": "result", "data": result}
        except Exception as e:
            yield {"type": "error", "error": str(e)}
    except Exception as e:
        logger.error(f"流式执行失败: {e}")
        yield {"type": "error", "error": str(e)}


def stream_workflow_events(
    graph: Any,
    input_data: dict,
    config: dict,
    version: str = "v2",
) -> Iterator[dict]:
    """流式执行工作流（事件模式）。

    提供更详细的事件流，包括 LLM token 级别的输出。

    Args:
        graph: 编译后的 LangGraph 图
        input_data: 输入数据
        config: 配置
        version: 事件版本（v1/v2）

    Yields:
        事件字典
    """
    if graph is None:
        return

    try:
        for event in graph.stream_events(input_data, config=config, version=version):
            yield event
    except AttributeError:
        logger.info("stream_events 不可用，降级到 stream")
        yield from stream_workflow(graph, input_data, config, stream_mode="updates")
    except Exception as e:
        logger.error(f"事件流执行失败: {e}")
        yield {"type": "error", "error": str(e)}


def format_sse_event(event_type: str, data: Any) -> str:
    """格式化为 SSE 事件字符串。

    Args:
        event_type: 事件类型
        data: 事件数据

    Returns:
        SSE 格式字符串
    """
    import json

    try:
        if isinstance(data, (dict, list)):
            data_str = json.dumps(data, ensure_ascii=False, default=str)
        else:
            data_str = str(data)
    except Exception:
        data_str = str(data)

    return f"event: {event_type}\ndata: {data_str}\n\n"


def stream_workflow_sse(
    graph: Any,
    input_data: dict,
    config: dict,
    stream_mode: str = "updates",
) -> Iterator[str]:
    """流式执行工作流并输出 SSE 格式。

    用于 FastAPI 的 StreamingResponse。

    Args:
        graph: 编译后的 LangGraph 图
        input_data: 输入数据
        config: 配置
        stream_mode: 流式模式

    Yields:
        SSE 格式的字符串
    """
    yield format_sse_event("start", {"config": config})

    try:
        for chunk in stream_workflow(graph, input_data, config, stream_mode):
            yield format_sse_event("chunk", chunk)
    except Exception as e:
        yield format_sse_event("error", {"error": str(e)})
        return

    yield format_sse_event("done", {})
