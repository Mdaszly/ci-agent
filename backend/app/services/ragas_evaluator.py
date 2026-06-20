"""RAGAS 评估指标模块。

实现 RAGAS（Retrieval Augmented Generation Assessment）标准评估指标：
1. faithfulness（忠实度）：答案是否忠于检索到的上下文
2. answer_relevancy（答案相关性）：答案与问题的相关程度
3. context_precision（上下文精确率）：检索到的上下文中有多少是相关的
4. context_recall（上下文召回率）：相关上下文是否都被检索到

设计原则：
- 轻量级：不依赖 ragas 库，使用 LLM 自行实现
- 可降级：LLM 不可用时返回默认分数
- 可观测：记录每个指标的评估过程
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RAGASResult:
    """RAGAS 评估结果。"""

    faithfulness: float  # 忠实度 [0, 1]
    answer_relevancy: float  # 答案相关性 [0, 1]
    context_precision: float  # 上下文精确率 [0, 1]
    context_recall: float  # 上下文召回率 [0, 1]
    overall_score: float  # 综合分数 [0, 1]
    details: dict  # 详细评估信息


def _call_llm_for_score(system_prompt: str, user_prompt: str) -> float | None:
    """调用 LLM 获取评分。

    降级策略：LLM 不可用时返回 None。
    """
    try:
        from app.services.llm_client import llm_client

        if llm_client is None or not llm_client.is_configured():
            return None

        response = llm_client.chat_completion_sync([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])

        if not response:
            return None

        # 从响应中提取分数
        text = response.strip()
        # 尝试提取 0-1 之间的浮点数
        import re

        match = re.search(r"([01]?\.?\d+)", text)
        if match:
            score = float(match.group(1))
            return max(0.0, min(1.0, score))
        return None
    except Exception as e:
        logger.warning(f"LLM 评分失败: {e}")
        return None


def evaluate_faithfulness(answer: str, contexts: list[str]) -> float:
    """评估忠实度：答案是否忠于检索到的上下文。

    Args:
        answer: 生成的答案
        contexts: 检索到的上下文列表

    Returns:
        忠实度分数 [0, 1]
    """
    if not answer or not contexts:
        return 0.0

    context_text = "\n".join(contexts)
    system_prompt = "你是一个 RAG 系统评估专家。请评估答案是否忠于给定的上下文。"
    user_prompt = f"""请评估以下答案是否完全基于给定的上下文，没有编造信息。
只输出一个 0 到 1 之间的分数，1 表示完全忠于上下文，0 表示完全编造。

上下文：
{context_text}

答案：
{answer}

分数："""

    score = _call_llm_for_score(system_prompt, user_prompt)
    if score is None:
        # 降级：简单关键词匹配
        answer_words = set(answer.lower().split())
        context_words = set(context_text.lower().split())
        if not answer_words:
            return 0.0
        overlap = len(answer_words & context_words) / len(answer_words)
        return min(1.0, overlap)
    return score


def evaluate_answer_relevancy(question: str, answer: str) -> float:
    """评估答案相关性：答案与问题的相关程度。

    Args:
        question: 问题
        answer: 答案

    Returns:
        相关性分数 [0, 1]
    """
    if not question or not answer:
        return 0.0

    system_prompt = "你是一个 RAG 系统评估专家。请评估答案与问题的相关程度。"
    user_prompt = f"""请评估以下答案与问题的相关程度。
只输出一个 0 到 1 之间的分数，1 表示完全相关，0 表示完全不相关。

问题：
{question}

答案：
{answer}

分数："""

    score = _call_llm_for_score(system_prompt, user_prompt)
    if score is None:
        # 降级：关键词重叠
        q_words = set(question.lower().split())
        a_words = set(answer.lower().split())
        if not q_words:
            return 0.0
        overlap = len(q_words & a_words) / len(q_words)
        return min(1.0, overlap)
    return score


def evaluate_context_precision(question: str, contexts: list[str]) -> float:
    """评估上下文精确率：检索到的上下文中有多少是相关的。

    Args:
        question: 问题
        contexts: 检索到的上下文列表

    Returns:
        精确率分数 [0, 1]
    """
    if not contexts:
        return 0.0

    if not question:
        return 0.5  # 无问题时给中间分

    system_prompt = "你是一个 RAG 系统评估专家。请评估检索到的上下文与问题的相关比例。"
    user_prompt = f"""请评估以下检索到的上下文中，有多少与问题相关。
只输出一个 0 到 1 之间的分数，1 表示全部相关，0 表示全部无关。

问题：
{question}

上下文（共 {len(contexts)} 条）：
""" + "\n".join(f"{i+1}. {ctx[:200]}" for i, ctx in enumerate(contexts)) + "\n\n相关比例分数："

    score = _call_llm_for_score(system_prompt, user_prompt)
    if score is None:
        # 降级：关键词匹配
        q_words = set(question.lower().split())
        relevant = 0
        for ctx in contexts:
            ctx_words = set(ctx.lower().split())
            if q_words & ctx_words:
                relevant += 1
        return relevant / len(contexts) if contexts else 0.0
    return score


def evaluate_context_recall(question: str, contexts: list[str], ground_truth: str = "") -> float:
    """评估上下文召回率：相关上下文是否都被检索到。

    Args:
        question: 问题
        contexts: 检索到的上下文列表
        ground_truth: 标准答案（可选，用于判断是否覆盖关键信息）

    Returns:
        召回率分数 [0, 1]
    """
    if not contexts:
        return 0.0

    if ground_truth:
        # 有标准答案时，评估上下文是否覆盖标准答案的关键信息
        system_prompt = "你是一个 RAG 系统评估专家。请评估上下文是否覆盖了标准答案的关键信息。"
        user_prompt = f"""请评估以下上下文是否覆盖了标准答案中的关键信息。
只输出一个 0 到 1 之间的分数，1 表示完全覆盖，0 表示完全未覆盖。

问题：
{question}

标准答案：
{ground_truth}

检索到的上下文：
""" + "\n".join(contexts) + "\n\n覆盖分数："

        score = _call_llm_for_score(system_prompt, user_prompt)
        if score is not None:
            return score

    # 降级：基于上下文数量和长度的启发式
    total_length = sum(len(ctx) for ctx in contexts)
    if total_length > 2000:
        return 0.8
    elif total_length > 500:
        return 0.6
    else:
        return 0.4


def evaluate_ragas(
    question: str,
    answer: str,
    contexts: list[str],
    ground_truth: str = "",
) -> RAGASResult:
    """执行完整的 RAGAS 评估。

    Args:
        question: 问题
        answer: 生成的答案
        contexts: 检索到的上下文列表
        ground_truth: 标准答案（可选）

    Returns:
        RAGASResult 评估结果
    """
    faithfulness = evaluate_faithfulness(answer, contexts)
    answer_relevancy = evaluate_answer_relevancy(question, answer)
    context_precision = evaluate_context_precision(question, contexts)
    context_recall = evaluate_context_recall(question, contexts, ground_truth)

    # 综合分数（加权平均）
    overall = (
        faithfulness * 0.3
        + answer_relevancy * 0.3
        + context_precision * 0.2
        + context_recall * 0.2
    )

    return RAGASResult(
        faithfulness=faithfulness,
        answer_relevancy=answer_relevancy,
        context_precision=context_precision,
        context_recall=context_recall,
        overall_score=overall,
        details={
            "question": question[:200],
            "answer_length": len(answer),
            "contexts_count": len(contexts),
            "has_ground_truth": bool(ground_truth),
        },
    )
