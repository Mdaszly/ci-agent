"""查询改写服务 - HyDE 与 Multi-Query 扩展。

提升 RAG 召回率的查询增强技术：

1. HyDE (Hypothetical Document Embeddings):
   先用 LLM 根据查询生成一个"假设性答案文档"，再用该文档的 embedding
   去检索。因为假设文档与真实答案在语义空间更接近，比直接用短查询检索
   召回率更高。适合查询短、模糊的场景。

2. Multi-Query Expansion:
   用 LLM 将原始查询改写为多个语义等价的变体查询，分别检索后合并结果。
   适合查询存在同义词、行业术语差异的场景。

设计要点：
- 同步接口为主（decision_memory 在 LangGraph 同步上下文中执行）
- 降级策略：LLM 不可用时返回原始查询
- 可配置：通过 QueryRewriteConfig 控制策略和参数
"""
from __future__ import annotations

import json
import logging
from typing import Literal

from app.services.llm import LLMError, LLMNotConfiguredError, llm_client
from app.services.llm_utils import strip_code_fence

logger = logging.getLogger(__name__)

RewriteStrategy = Literal["none", "hyde", "multi_query"]


def _build_hyde_prompt(query: str) -> list[dict[str, str]]:
    """构建 HyDE prompt - 生成假设性答案文档。

    HyDE 的核心假设：LLM 生成的答案即使事实有误，其"语言风格"和"语义结构"
    仍与真实答案接近，因此用假设文档做 embedding 检索比用原始短查询更有效。
    """
    system_msg = (
        "你是一个竞品分析专家。请根据用户的查询，生成一段可能的答案文档。"
        "这段文档应该像真实分析报告的一个片段，包含相关的事实陈述和分析结论。"
        "不要回答「我不知道」，请基于你的知识给出最可能的答案。\n\n"
        "要求：\n"
        "- 长度 100-200 字\n"
        "- 包含具体的产品名、维度、数据点\n"
        "- 使用分析报告的正式语体\n"
        "- 直接输出文档内容，不要加前缀说明"
    )
    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": query},
    ]


def _build_multi_query_prompt(query: str, n: int = 3) -> list[dict[str, str]]:
    """构建 Multi-Query prompt - 生成查询变体。"""
    system_msg = (
        f"你是一个搜索查询优化专家。请将用户的查询改写为 {n} 个语义等价但表达不同的变体查询。"
        "变体查询应该：\n"
        "- 使用不同的关键词和同义词\n"
        "- 覆盖不同的表达方式（正式/口语/行业术语）\n"
        "- 保持与原始查询相同的搜索意图\n\n"
        f"请以 JSON 数组格式返回 {n} 个查询字符串，例如：[\"查询1\", \"查询2\", \"查询3\"]\n"
        "只返回 JSON，不要其他文字。"
    )
    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": f"原始查询：{query}"},
    ]


def rewrite_query(
    query: str,
    *,
    strategy: RewriteStrategy = "none",
) -> str:
    """根据策略改写查询，返回用于 embedding 的文本。

    Args:
        query: 原始查询
        strategy: 改写策略
            - "none": 不改写，返回原始查询
            - "hyde": 生成假设文档，返回假设文档文本
            - "multi_query": 生成多个变体，返回拼接后的文本

    Returns:
        用于 embedding 的查询文本。LLM 不可用时降级返回原始查询。
    """
    if strategy == "none" or not query.strip():
        return query

    try:
        if strategy == "hyde":
            return _hyde_rewrite(query)
        elif strategy == "multi_query":
            return _multi_query_rewrite(query)
        else:
            logger.warning("Unknown query rewrite strategy: %s", strategy)
            return query
    except (LLMNotConfiguredError, LLMError) as e:
        logger.warning("Query rewrite skipped (LLM unavailable): %s", e)
        return query
    except Exception as e:
        logger.error("Query rewrite failed: %s", e, exc_info=True)
        return query


def _hyde_rewrite(query: str) -> str:
    """HyDE 改写：生成假设性答案文档。"""
    messages = _build_hyde_prompt(query)
    content = llm_client.chat_completion_sync(messages)
    content = content.strip()

    if not content:
        logger.warning("HyDE generated empty document, falling back to original query")
        return query

    logger.debug("HyDE rewrite: query=%r -> doc=%r", query[:50], content[:50])
    return content


def _multi_query_rewrite(query: str, n: int = 3) -> str:
    """Multi-Query 改写：生成多个变体并拼接。"""
    messages = _build_multi_query_prompt(query, n)
    content = llm_client.chat_completion_sync(messages)
    content = content.strip()

    # 处理 markdown code fence
    content = strip_code_fence(content)

    try:
        variants = json.loads(content)
        if isinstance(variants, list) and all(isinstance(v, str) for v in variants):
            # 拼接原始查询和所有变体，用空格分隔
            combined = query + " " + " ".join(variants)
            logger.debug(
                "Multi-query rewrite: query=%r -> %d variants", query[:50], len(variants)
            )
            return combined
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Multi-query parse failed: %s", e)

    logger.warning("Multi-query rewrite failed, falling back to original query")
    return query
