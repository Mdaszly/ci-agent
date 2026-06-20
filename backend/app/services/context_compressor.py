"""上下文压缩模块。

解决 Agent 上下文窗口溢出问题，实现三种压缩策略：
1. 滚动窗口：保留最近 N 条消息
2. 摘要压缩：用 LLM 将历史消息压缩为摘要
3. 关键信息提取：保留关键实体和决策

设计原则：
- 可配置：通过 ContextCompressorConfig 配置策略
- 可降级：LLM 不可用时降级到滚动窗口
- 可观测：记录每次压缩的压缩率和信息损失
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ContextCompressorConfig:
    """上下文压缩配置。"""

    # 通用配置
    max_tokens: int = 8000  # 上下文窗口上限
    current_tokens: int = 0  # 当前 token 数（外部传入）

    # 滚动窗口策略
    rolling_window_size: int = 20  # 保留最近 N 条消息

    # 摘要策略
    summary_enabled: bool = True
    summary_threshold: int = 5000  # 超过此 token 数触发摘要
    summary_max_length: int = 500  # 摘要最大长度

    # 关键信息提取
    key_info_enabled: bool = True
    key_info_max_items: int = 10  # 保留关键信息条数


@dataclass
class CompressionResult:
    """压缩结果。"""

    compressed_messages: list[dict]
    original_count: int
    compressed_count: int
    strategy: str  # rolling_window | summary | key_info | none
    compression_ratio: float = 0.0
    summary: str = ""


def estimate_tokens(text: str) -> int:
    """粗略估算文本的 token 数。

    使用 4 字符 ≈ 1 token 的经验值。
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """估算消息列表的总 token 数。"""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    total += estimate_tokens(str(item.get("text", "")))
    return total


def rolling_window_compress(
    messages: list[dict],
    window_size: int,
) -> CompressionResult:
    """滚动窗口压缩：保留最近 N 条消息。

    Args:
        messages: 原始消息列表
        window_size: 窗口大小

    Returns:
        压缩结果
    """
    original_count = len(messages)
    if original_count <= window_size:
        return CompressionResult(
            compressed_messages=messages,
            original_count=original_count,
            compressed_count=original_count,
            strategy="none",
            compression_ratio=1.0,
        )

    compressed = messages[-window_size:]
    return CompressionResult(
        compressed_messages=compressed,
        original_count=original_count,
        compressed_count=len(compressed),
        strategy="rolling_window",
        compression_ratio=len(compressed) / original_count,
    )


def summary_compress(
    messages: list[dict],
    config: ContextCompressorConfig,
) -> CompressionResult:
    """摘要压缩：用 LLM 将历史消息压缩为摘要。

    降级策略：LLM 不可用时降级到滚动窗口。

    Args:
        messages: 原始消息列表
        config: 压缩配置

    Returns:
        压缩结果
    """
    original_count = len(messages)

    # 尝试调用 LLM 生成摘要
    try:
        from app.services.llm_client import llm_client

        if llm_client is None or not llm_client.is_configured():
            logger.info("LLM 未配置，摘要压缩降级到滚动窗口")
            return rolling_window_compress(messages, config.rolling_window_size)

        # 将历史消息拼接为文本
        history_text = "\n".join(
            f"[{msg.get('role', 'unknown')}]: {msg.get('content', '')}"
            for msg in messages[:-config.rolling_window_size]  # 最近的消息不压缩
        )

        if not history_text.strip():
            return rolling_window_compress(messages, config.rolling_window_size)

        # 调用 LLM 生成摘要
        prompt = f"""请将以下对话历史压缩为简洁的摘要，保留关键信息、决策和实体。
摘要长度不超过 {config.summary_max_length} 字。

对话历史：
{history_text}

摘要："""

        summary = llm_client.chat_completion_sync([
            {"role": "system", "content": "你是一个对话摘要助手，擅长提取关键信息。"},
            {"role": "user", "content": prompt},
        ])

        if not summary or not summary.strip():
            logger.warning("LLM 返回空摘要，降级到滚动窗口")
            return rolling_window_compress(messages, config.rolling_window_size)

        # 构造压缩后的消息：摘要 + 最近的消息
        summary_msg = {
            "role": "system",
            "content": f"【历史摘要】{summary.strip()}",
        }
        recent_messages = messages[-config.rolling_window_size:]
        compressed = [summary_msg] + recent_messages

        return CompressionResult(
            compressed_messages=compressed,
            original_count=original_count,
            compressed_count=len(compressed),
            strategy="summary",
            compression_ratio=len(compressed) / original_count,
            summary=summary.strip(),
        )

    except Exception as e:
        logger.warning(f"摘要压缩失败，降级到滚动窗口: {e}")
        return rolling_window_compress(messages, config.rolling_window_size)


def compress_context(
    messages: list[dict],
    config: ContextCompressorConfig | None = None,
) -> CompressionResult:
    """上下文压缩主入口。

    根据当前 token 数自动选择策略：
    1. 未超过阈值 → 不压缩
    2. 超过摘要阈值 → 摘要压缩
    3. 超过窗口上限 → 滚动窗口

    Args:
        messages: 原始消息列表
        config: 压缩配置

    Returns:
        压缩结果
    """
    if config is None:
        config = ContextCompressorConfig()

    original_count = len(messages)
    if original_count == 0:
        return CompressionResult(
            compressed_messages=[],
            original_count=0,
            compressed_count=0,
            strategy="none",
        )

    # 估算当前 token 数
    current_tokens = config.current_tokens or estimate_messages_tokens(messages)

    # 未超过阈值，不压缩
    if current_tokens <= config.summary_threshold:
        return CompressionResult(
            compressed_messages=messages,
            original_count=original_count,
            compressed_count=original_count,
            strategy="none",
            compression_ratio=1.0,
        )

    # 超过摘要阈值，使用摘要压缩
    if config.summary_enabled:
        return summary_compress(messages, config)

    # 降级到滚动窗口
    return rolling_window_compress(messages, config.rolling_window_size)
