"""Reranker 服务 - 基于 LLM 的第二阶段精排。

在混合检索（向量+词面）融合后，对 top-N 候选结果使用 LLM 进行重排序，
提升最终返回结果的精确度。相比第一阶段的召回（追求覆盖率），reranker
追求精确率，通过 LLM 理解 query 与 document 的语义相关性给出细粒度评分。

设计要点：
1. 同步接口为主 - decision_memory 检索流程在 LangGraph 同步上下文中执行
2. 批量打分 - 一次 LLM 调用处理所有候选，减少 API 延迟和成本
3. 降级策略 - LLM 不可用时返回原始排序，不阻断检索流程
4. 可配置 - 通过 RerankConfig 控制是否启用、候选数量、温度等
"""
from __future__ import annotations

import json
import logging
from typing import Sequence, TypeVar

from app.services.llm import LLMError, LLMNotConfiguredError, llm_client
from app.services.llm_utils import strip_code_fence

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _truncate(text: str, max_len: int = 200) -> str:
    """截断文本到指定长度，避免 prompt 过长"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _build_rerank_prompt(
    query: str,
    candidates: list[tuple[T, float]],
    doc_extractor,
) -> list[dict[str, str]]:
    """构建 rerank prompt。

    Args:
        query: 用户查询
        candidates: 候选列表 [(item, score)]
        doc_extractor: 从 item 提取文档文本的函数

    Returns:
        LLM messages 列表
    """
    doc_list = []
    for idx, (item, _) in enumerate(candidates):
        doc_text = doc_extractor(item)
        doc_list.append(f"[{idx}] {_truncate(doc_text, 300)}")

    docs_block = "\n".join(doc_list)

    system_msg = (
        "你是一个专业的信息检索重排序专家。给定用户查询和一组候选文档，"
        "你需要评估每个文档与查询的语义相关性，并给出 0-10 的相关性评分。\n\n"
        "评分标准：\n"
        "- 9-10: 完全匹配，文档直接回答了查询\n"
        "- 7-8: 高度相关，文档包含查询所需的关键信息\n"
        "- 5-6: 部分相关，文档包含部分相关信息\n"
        "- 3-4: 弱相关，文档主题相关但信息不直接\n"
        "- 0-2: 不相关，文档与查询无关\n\n"
        "请以 JSON 数组格式返回评分，每个元素为 {\"index\": <序号>, \"score\": <分数>}。"
        "只返回 JSON，不要其他文字。"
    )

    user_msg = f"查询：{query}\n\n候选文档：\n{docs_block}\n\n请对每个文档评分（0-10）："

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def rerank(
    query: str,
    candidates: list[tuple[T, float]],
    *,
    doc_extractor=None,
    top_k: int = 5,
    enabled: bool = True,
) -> list[tuple[T, float]]:
    """对检索候选结果进行 LLM 重排序。

    Args:
        query: 用户查询文本
        candidates: 候选结果列表 [(item, original_score)]
        doc_extractor: 从 item 提取文档文本的可调用对象，默认使用 str(item)
        top_k: 重排序后返回的数量
        enabled: 是否启用 rerank，False 时直接返回原始候选的前 top_k

    Returns:
        重排序后的结果列表 [(item, new_score)]，长度 <= top_k
    """
    if not enabled or not candidates:
        return candidates[:top_k]

    if doc_extractor is None:
        doc_extractor = lambda item: str(item)  # noqa: E731

    # 候选数量过少时跳过 rerank
    if len(candidates) <= 1:
        return candidates[:top_k]

    try:
        messages = _build_rerank_prompt(query, candidates, doc_extractor)
        content = llm_client.chat_completion_sync(messages)

        # 解析 LLM 返回的评分
        scores = _parse_rerank_scores(content, len(candidates))

        if scores is None:
            logger.warning("Rerank score parsing failed, returning original order")
            return candidates[:top_k]

        # 用 LLM 评分重新排序（归一化到 [0,1] 保持与相似度分数尺度一致）
        reranked = []
        for idx, (item, _) in enumerate(candidates):
            new_score = scores.get(idx, 0.0) / 10.0
            reranked.append((item, new_score))

        reranked.sort(key=lambda x: x[1], reverse=True)
        result = reranked[:top_k]

        logger.debug(
            "Rerank completed: %d candidates -> top %d, score range [%.1f, %.1f]",
            len(candidates),
            len(result),
            result[-1][1] if result else 0,
            result[0][1] if result else 0,
        )
        return result

    except (LLMNotConfiguredError, LLMError) as e:
        logger.warning("Rerank skipped due to LLM error: %s", e)
        return candidates[:top_k]
    except Exception as e:
        logger.error("Rerank failed unexpectedly: %s", e, exc_info=True)
        return candidates[:top_k]


def _parse_rerank_scores(content: str, candidate_count: int) -> dict[int, float] | None:
    """解析 LLM 返回的评分 JSON。

    Args:
        content: LLM 返回的文本
        candidate_count: 候选数量，用于校验

    Returns:
        {index: score} 字典，解析失败返回 None
    """
    try:
        content = content.strip()

        # 处理 markdown code fence
        content = strip_code_fence(content)

        data = json.loads(content)

        if not isinstance(data, list):
            logger.warning("Rerank response is not a list: %s", type(data))
            return None

        scores: dict[int, float] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            score = item.get("score")
            if idx is None or score is None:
                continue
            idx = int(idx)
            score = float(score)
            if 0 <= idx < candidate_count and 0.0 <= score <= 10.0:
                scores[idx] = score

        if len(scores) == 0:
            logger.warning("No valid scores parsed from rerank response")
            return None

        return scores

    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning("Failed to parse rerank scores: %s", e)
        return None
