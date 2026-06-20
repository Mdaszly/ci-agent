"""召回率测试指标计算函数。"""

from __future__ import annotations

import math


def recall_at_k(retrieved_ids: list[str], expected_ids: list[str], k: int) -> float:
    """Recall@K: 前 K 个结果中包含相关文档的比例。"""
    if not expected_ids:
        return 0.0
    retrieved_set = set(retrieved_ids[:k])
    relevant_hit = len(retrieved_set & set(expected_ids))
    return relevant_hit / len(expected_ids)


def precision_at_k(retrieved_ids: list[str], expected_ids: list[str], k: int) -> float:
    """Precision@K: 前 K 个结果中相关文档的占比。"""
    if k == 0:
        return 0.0
    retrieved_set = set(retrieved_ids[:k])
    relevant_hit = len(retrieved_set & set(expected_ids))
    return relevant_hit / k


def mrr(retrieved_ids: list[str], expected_ids: list[str]) -> float:
    """MRR: 第一个相关文档的排名倒数的均值。"""
    if not expected_ids:
        return 0.0
    expected_set = set(expected_ids)
    for idx, rid in enumerate(retrieved_ids, start=1):
        if rid in expected_set:
            return 1.0 / idx
    return 0.0


def dcg_at_k(relevances: list[float], k: int) -> float:
    """DCG@K: 折损累积增益。"""
    return sum(rel / math.log2(i + 2) for i, rel in enumerate(relevances[:k]))


def ndcg_at_k(retrieved_ids: list[str], expected_ids: list[str], k: int) -> float:
    """NDCG@K: 归一化折损累积增益。"""
    expected_set = set(expected_ids)
    relevances = [1.0 if rid in expected_set else 0.0 for rid in retrieved_ids[:k]]
    dcg = dcg_at_k(relevances, k)
    ideal_relevances = [1.0] * min(len(expected_ids), k)
    idcg = dcg_at_k(ideal_relevances, k)
    return dcg / idcg if idcg > 0 else 0.0


def f1_at_k(retrieved_ids: list[str], expected_ids: list[str], k: int) -> float:
    """F1@K: 精确率和召回率的调和平均。"""
    p = precision_at_k(retrieved_ids, expected_ids, k)
    r = recall_at_k(retrieved_ids, expected_ids, k)
    if p + r == 0:
        return 0.0
    return 2 * p * r / (p + r)


def compute_all_metrics(retrieved_ids: list[str], expected_ids: list[str], k: int) -> dict[str, float]:
    """一次性计算所有指标。"""
    return {
        f"recall@{k}": recall_at_k(retrieved_ids, expected_ids, k),
        f"precision@{k}": precision_at_k(retrieved_ids, expected_ids, k),
        "mrr": mrr(retrieved_ids, expected_ids),
        f"ndcg@{k}": ndcg_at_k(retrieved_ids, expected_ids, k),
        f"f1@{k}": f1_at_k(retrieved_ids, expected_ids, k),
    }


def compute_graded_metrics(
    retrieved_ids: list[str],
    expected_ids: list[str],
    expected_relevance: list[int],
    k: int,
) -> dict[str, float]:
    """计算分级相关性指标。
    
    支持多级相关性评分（如 1-5 级），比二元相关性更精确地评估检索质量。
    
    Args:
        retrieved_ids: 检索到的文档 ID 列表
        expected_ids: 预期相关的文档 ID 列表
        expected_relevance: 对应 expected_ids 的相关性评分列表（如 [3, 2, 1]）
        k: 评估的截断位置
    
    Returns:
        包含分级指标的字典
    """
    if not expected_ids or len(expected_ids) != len(expected_relevance):
        return compute_all_metrics(retrieved_ids, expected_ids, k)
    
    # 构建相关性映射
    relevance_map = dict(zip(expected_ids, expected_relevance))
    
    # 计算分级 NDCG
    graded_relevances = [relevance_map.get(rid, 0) for rid in retrieved_ids[:k]]
    graded_dcg = dcg_at_k(graded_relevances, k)
    # 理想排序：按相关性降序排列
    ideal_relevances = sorted(expected_relevance, reverse=True)[:k]
    graded_idcg = dcg_at_k(ideal_relevances, k)
    graded_ndcg = graded_dcg / graded_idcg if graded_idcg > 0 else 0.0
    
    # 计算分级 MRR（考虑相关性等级）
    graded_mrr = 0.0
    for idx, rid in enumerate(retrieved_ids, start=1):
        if rid in relevance_map:
            graded_mrr = relevance_map[rid] / idx
            break
    
    # 计算加权召回率（考虑相关性权重）
    total_relevance = sum(expected_relevance)
    if total_relevance > 0:
        retrieved_relevance = sum(relevance_map.get(rid, 0) for rid in retrieved_ids[:k])
        weighted_recall = retrieved_relevance / total_relevance
    else:
        weighted_recall = 0.0
    
    # 保留基础指标便于对比
    base_metrics = compute_all_metrics(retrieved_ids, expected_ids, k)
    
    return {
        **base_metrics,
        f"graded_ndcg@{k}": round(graded_ndcg, 4),
        "graded_mrr": round(graded_mrr, 4),
        f"weighted_recall@{k}": round(weighted_recall, 4),
    }
