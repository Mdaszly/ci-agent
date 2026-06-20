"""召回率测试执行器：加载数据集、执行测试、聚合结果。"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from app.core.config import memory_settings
from app.models.schemas import (
    DecisionChunkType,
    DecisionMemoryItem,
    DecisionPackStatus,
)
from app.services.recall_metrics import compute_all_metrics, compute_graded_metrics
from app.services.decision_memory import (
    _lexical_search,
    _vector_search,
    clear_task_memory,
    get_task_memory_items,
    upsert_decision_memory,
)

logger = logging.getLogger(__name__)

TestMode = Literal["lexical_only", "vector_only", "hybrid"]


class RecallTester:
    """召回率测试执行器。"""

    def __init__(self) -> None:
        # dataset_id -> {"memory_items": [...], "test_cases": [...], "loaded_at": "..."}
        self._datasets: dict[str, dict] = {}
        # test_id -> full result
        self._history: list[dict] = []
        # 当前测试的临时配置（用于自定义权重测试）
        self._temp_config: dict | None = None

    @contextmanager
    def _temporary_config(self, **kwargs):
        """临时修改配置，测试完成后恢复。"""
        original = {}
        config_attrs = [
            "vector_weight",
            "lexical_weight",
            "fusion_strategy",
            "candidate_multiplier",
            "hnsw_ef_search",
            "allow_degraded_mode",
        ]
        
        # 保存原始值
        for attr in config_attrs:
            if hasattr(memory_settings, attr):
                original[attr] = getattr(memory_settings, attr)
        
        # 设置临时值
        for key, value in kwargs.items():
            if value is not None and hasattr(memory_settings, key):
                setattr(memory_settings, key, value)
        
        try:
            yield
        finally:
            # 恢复原始值
            for attr, value in original.items():
                setattr(memory_settings, attr, value)

    def _load_benchmark_fixture(self) -> dict | None:
        fixture_path = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "recall_dataset.json"
        if not fixture_path.exists():
            return None
        try:
            with fixture_path.open(encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def ensure_dataset(self, dataset_id: str) -> bool:
        if dataset_id in self._datasets:
            return True

        fixture = self._load_benchmark_fixture()
        if not fixture or fixture.get("dataset_id") != dataset_id:
            return False

        self.load_dataset(
            dataset_id=fixture["dataset_id"],
            memory_items=fixture["memory_items"],
            test_cases=fixture["test_cases"],
            clear_existing=True,
            dataset_version=fixture.get("dataset_version", "v1"),
            baseline_config=fixture.get("baseline_config", {}),
            description=fixture.get("description", ""),
        )
        return True

    def ensure_benchmark_dataset(self) -> bool:
        fixture = self._load_benchmark_fixture()
        if not fixture:
            return False
        dataset_id = fixture.get("dataset_id")
        if not dataset_id:
            return False
        return self.ensure_dataset(dataset_id)

    def load_dataset(
        self,
        dataset_id: str,
        memory_items: list[dict],
        test_cases: list[dict],
        clear_existing: bool = True,
        dataset_version: str | None = None,
        baseline_config: dict | None = None,
        description: str | None = None,
    ) -> dict:
        """加载测试数据集，写入记忆系统。

        流程：
        1. 将 dict 转换为 DecisionMemoryItem（含枚举校验）
        2. clear_existing=True 时按 task_id 分组清理旧数据，避免历史数据干扰测试
        3. 调用 upsert_decision_memory 写入记忆系统（触发 embedding + 持久化）
        4. 缓存数据集元数据到内存，供后续 run_test 使用

        Args:
            dataset_id: 数据集唯一标识
            memory_items: 记忆块列表（dict 格式，字段同 DecisionMemoryItem）
            test_cases: 测试用例列表，每项含 case_id/category/query/expected_ids/expected_relevance
            clear_existing: 是否清理同 task_id 的旧记忆，默认 True

        Returns:
            含 dataset_id/memory_count/test_case_count/loaded_at 的字典
        """
        # 转换 dict -> DecisionMemoryItem
        items: list[DecisionMemoryItem] = []
        for raw in memory_items:
            chunk_type = DecisionChunkType(raw.get("chunk_type", "decision"))
            status = DecisionPackStatus(raw.get("status", "approved"))
            items.append(
                DecisionMemoryItem(
                    id=raw["id"],
                    task_id=raw["task_id"],
                    pack_id=raw.get("pack_id", "test_pack"),
                    version=raw.get("version", 1),
                    chunk_type=chunk_type,
                    stage=raw.get("stage"),
                    iteration=raw.get("iteration", 0),
                    source_refs=raw.get("source_refs", []),
                    summary=raw["summary"],
                    embedding_text=raw["embedding_text"],
                    payload=raw.get("payload", {}),
                    status=status,
                )
            )

        # 清空旧数据（按 task_id 分组清理）
        if clear_existing:
            seen_tasks = {item.task_id for item in items}
            for tid in seen_tasks:
                clear_task_memory(tid)

        # 写入记忆系统（触发 embedding + 持久化）
        upsert_decision_memory(items)

        loaded_at = datetime.now(timezone.utc).isoformat()
        self._datasets[dataset_id] = {
            "memory_items": memory_items,
            "test_cases": test_cases,
            "loaded_at": loaded_at,
            "dataset_version": dataset_version or "v1",
            "baseline_config": baseline_config or {},
            "description": description or "",
        }

        return {
            "dataset_id": dataset_id,
            "memory_count": len(items),
            "test_case_count": len(test_cases),
            "loaded_at": loaded_at,
            "dataset_version": dataset_version or "v1",
            "baseline_config": baseline_config or {},
            "description": description or "",
        }

    def run_test(
        self,
        dataset_id: str,
        modes: list[str],
        top_k: int = 5,
        categories: list[str] | None = None,
        detailed: bool = True,
        vector_weight: float | None = None,
        lexical_weight: float | None = None,
        fusion_strategy: str | None = None,
        candidate_multiplier: int | None = None,
        hnsw_ef_search: int | None = None,
        allow_degraded_mode: bool | None = None,
    ) -> dict:
        """执行召回率测试，支持多模式对比和自定义权重。

        核心流程：
        1. 验证数据集存在
        2. 按类别筛选测试用例（可选）
        3. 应用临时配置（自定义权重和融合策略）
        4. 为每个模式调用 _run_mode 执行测试
        5. 聚合 summary（按模式）和 by_category（按类别+模式）
        6. 合并 details（按 case_id 聚合，支持多模式对比同一查询）
        7. 记录到历史并返回完整结果

        Args:
            dataset_id: 已加载数据集的唯一标识
            modes: 测试模式列表，可选值 ["lexical_only", "vector_only", "hybrid"]
            top_k: 每路检索返回数量，默认 5
            categories: 筛选测试用例的类别列表，None 表示全部
            detailed: 是否返回每条测试用例的详细结果，默认 True
            vector_weight: 自定义向量权重（仅在 fusion_strategy="weighted" 时生效）
            lexical_weight: 自定义词面权重（仅在 fusion_strategy="weighted" 时生效）
            fusion_strategy: 自定义融合策略（weighted/rrf）
            candidate_multiplier: 每路候选倍数，控制 hybrid 融合前的召回宽度
            hnsw_ef_search: 向量检索 ef_search，控制查询宽度
            allow_degraded_mode: 是否允许向量失败后降级到词面结果

        Returns:
            测试结果字典，包含 test_id、config、summary、by_category、details、时间戳
        """
        if dataset_id not in self._datasets:
            if dataset_id == "recall_benchmark_v1" and not self.ensure_benchmark_dataset():
                raise ValueError(f"Dataset {dataset_id} not found")
            if dataset_id not in self._datasets and not self.ensure_dataset(dataset_id):
                raise ValueError(f"Dataset {dataset_id} not found")

        dataset = self._datasets[dataset_id]
        test_cases = dataset["test_cases"]
        baseline_config = dataset.get("baseline_config", {})

        # 按类别筛选
        if categories:
            test_cases = [tc for tc in test_cases if tc.get("category") in categories]

        test_id = f"test_run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        started_at = datetime.now(timezone.utc)
        start_ts = time.time()

        # 准备临时配置
        temp_config_kwargs = {}
        if vector_weight is not None:
            temp_config_kwargs["vector_weight"] = vector_weight
        if lexical_weight is not None:
            temp_config_kwargs["lexical_weight"] = lexical_weight
        if fusion_strategy is not None:
            temp_config_kwargs["fusion_strategy"] = fusion_strategy
        if candidate_multiplier is not None:
            temp_config_kwargs["candidate_multiplier"] = candidate_multiplier
        if hnsw_ef_search is not None:
            temp_config_kwargs["hnsw_ef_search"] = hnsw_ef_search
        if allow_degraded_mode is not None:
            temp_config_kwargs["allow_degraded_mode"] = allow_degraded_mode

        # 按模式执行（应用临时配置）
        summary: dict[str, dict] = {}
        by_category: dict[str, dict] = {}
        details: list[dict] = []

        with self._temporary_config(**temp_config_kwargs):
            for mode in modes:
                mode_summary, mode_by_cat, mode_details = self._run_mode(
                    mode, test_cases, top_k
                )
                summary[mode] = mode_summary
                for cat, cat_metrics in mode_by_cat.items():
                    if cat not in by_category:
                        by_category[cat] = {}
                    by_category[cat][mode] = cat_metrics
                if detailed:
                    for detail in mode_details:
                        # 合并到 details（按 case_id 聚合）
                        existing = next((d for d in details if d["case_id"] == detail["case_id"]), None)
                        if existing is None:
                            details.append(detail)
                        else:
                            existing["results"][mode] = detail["results"][mode]

        completed_at = datetime.now(timezone.utc)
        duration_ms = int((time.time() - start_ts) * 1000)

        primary_mode = "hybrid" if "hybrid" in summary else (modes[0] if modes else None)
        primary_summary = summary.get(primary_mode, {}) if primary_mode else {}
        flat_results: list[dict] = []
        if detailed:
            for detail in details:
                for mode, mode_result in detail.get("results", {}).items():
                    flat_results.append(
                        {
                            "mode": mode,
                            "case_id": detail.get("case_id", ""),
                            "query": detail.get("query", ""),
                            "retrieved_ids": [item.get("id", "") for item in mode_result.get("retrieved", [])],
                            "expected_ids": detail.get("expected_ids", []),
                            "metrics": mode_result.get("metrics", {}),
                        }
                    )

        result = {
            "test_id": test_id,
            "dataset_id": dataset_id,
            "modes": modes,
            "config": {
                "modes": modes,
                "top_k": top_k,
                "total_cases": len(test_cases),
                "vector_weight": vector_weight if vector_weight is not None else memory_settings.vector_weight,
                "lexical_weight": lexical_weight if lexical_weight is not None else memory_settings.lexical_weight,
                "fusion_strategy": fusion_strategy or memory_settings.fusion_strategy,
                "recall_top_k": memory_settings.recall_top_k,
                "candidate_multiplier": memory_settings.candidate_multiplier,
                "hnsw_m": memory_settings.hnsw_m,
                "hnsw_ef_construction": memory_settings.hnsw_ef_construction,
                "hnsw_ef_search": memory_settings.hnsw_ef_search,
                "allow_degraded_mode": memory_settings.allow_degraded_mode,
                "dataset_version": dataset.get("dataset_version", "v1"),
                "baseline_config": baseline_config,
                "description": dataset.get("description", ""),
            },
            "summary": summary,
            "summary_by_mode": summary,
            "by_category": by_category,
            "details": details if detailed else [],
            "results": flat_results if detailed else [],
            "aggregated_metrics": self._normalize_metrics(primary_summary, top_k),
            "primary_mode": primary_mode,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_ms": duration_ms,
        }

        self._history.append(result)
        return result

    def _run_mode(
        self,
        mode: str,
        test_cases: list[dict],
        top_k: int,
    ) -> tuple[dict, dict, list[dict]]:
        """执行单一模式的测试，支持分级相关性评估。"""
        all_metrics: list[dict[str, float]] = []
        latencies: list[float] = []
        by_category: dict[str, list[dict[str, float]]] = {}
        details: list[dict] = []
        degraded_count = 0
        vector_available_count = 0

        for tc in test_cases:
            query = tc["query"]
            expected_ids = tc.get("expected_ids", [])
            expected_relevance = tc.get("expected_relevance", None)
            category = tc.get("bucket") or tc.get("category", "unknown")

            start = time.time()
            retrieved, search_meta = self._search_single_mode(mode, query, top_k)
            latency_ms = (time.time() - start) * 1000
            latencies.append(latency_ms)

            if search_meta:
                if search_meta.get("degraded"):
                    degraded_count += 1
                if search_meta.get("vector_count", 0):
                    vector_available_count += 1

            retrieved_ids = [item.id for item, _ in retrieved]
            
            # 根据是否有分级相关性选择不同的指标计算方式
            if expected_relevance:
                metrics = compute_graded_metrics(retrieved_ids, expected_ids, expected_relevance, top_k)
            else:
                metrics = compute_all_metrics(retrieved_ids, expected_ids, top_k)
            
            all_metrics.append(metrics)

            if category not in by_category:
                by_category[category] = []
            by_category[category].append(metrics)

            # 详细结果
            hit = bool(set(retrieved_ids[:top_k]) & set(expected_ids)) if expected_ids else False
            rank = None
            if hit:
                for idx, rid in enumerate(retrieved_ids[:top_k], start=1):
                    if rid in set(expected_ids):
                        rank = idx
                        break

            normalized_metrics = self._normalize_metrics(metrics, top_k)

            details.append({
                "case_id": tc.get("case_id", ""),
                "category": category,
                "bucket": tc.get("bucket") or category,
                "query": query,
                "expected_ids": expected_ids,
                "expected_relevance": expected_relevance,
                "search_meta": search_meta or {},
                "results": {
                    mode: {
                        "retrieved": [{"id": item.id, "score": round(score, 4)} for item, score in retrieved[:top_k]],
                        "hit": hit,
                        "rank": rank,
                        "metrics": normalized_metrics,
                        "raw_metrics": metrics,
                    }
                },
            })

        # 聚合 summary
        summary = self._aggregate_metrics(all_metrics, latencies)
        if mode == "hybrid" and test_cases:
            summary["degraded_rate"] = round(degraded_count / len(test_cases), 4)
            summary["vector_presence_rate"] = round(vector_available_count / len(test_cases), 4)

        # 聚合 by_category
        mode_by_cat: dict[str, dict[str, float]] = {}
        for cat, cat_metrics_list in by_category.items():
            mode_by_cat[cat] = self._aggregate_metrics(cat_metrics_list, [])

        return summary, mode_by_cat, details

    def _search_single_mode(
        self,
        mode: str,
        query: str,
        top_k: int,
    ) -> tuple[list[tuple[DecisionMemoryItem, float]], dict[str, object]]:
        """按指定模式执行检索。

        三种检索模式：
        - lexical_only: 纯词面检索，基于 token 重叠匹配
        - vector_only: 纯向量检索，基于 embedding 语义相似度
        - hybrid: 混合检索，融合词面和向量结果

        参数说明：
        - include_superseded=True: 测试场景需要召回所有版本的记忆块，包括已废弃的，
          这样可以测试算法对旧版本数据的处理能力

        Args:
            mode: 检索模式，可选值 "lexical_only" | "vector_only" | "hybrid"
            query: 查询文本
            top_k: 返回数量

        Returns:
            (DecisionMemoryItem, score) 列表，score 范围 [0, 1]

        Raises:
            ValueError: 未知模式
        """
        if mode == "lexical_only":
            results = _lexical_search(query, top_k=top_k, include_superseded=True)
            return results, {"mode": mode, "degraded": False, "used_strategy": "lexical_only", "vector_count": 0}
        if mode == "vector_only":
            results = _vector_search(query, top_k=top_k, include_superseded=True)
            return results, {"mode": mode, "degraded": False, "used_strategy": "vector_only", "vector_count": len(results)}
        if mode == "hybrid":
            from app.services.decision_memory import search_decision_memory

            results, meta = search_decision_memory(query, top_k=top_k, include_superseded=True, return_meta=True)
            meta["mode"] = mode
            return results, meta
        raise ValueError(f"Unknown mode: {mode}")

    def _normalize_metrics(self, metrics: dict[str, float], top_k: int) -> dict[str, float]:
        """把原始指标转换成前端展示口径。"""
        return {
            "recall_at_k": round(metrics.get(f"recall@{top_k}", 0.0), 4),
            "precision_at_k": round(metrics.get(f"precision@{top_k}", 0.0), 4),
            "mrr_at_k": round(metrics.get("mrr", 0.0), 4),
            "ndcg_at_k": round(metrics.get(f"ndcg@{top_k}", 0.0), 4),
            "f1_at_k": round(metrics.get(f"f1@{top_k}", 0.0), 4),
            "avg_latency_ms": round(metrics.get("avg_latency_ms", 0.0), 2),
        }

    def _aggregate_metrics(
        self,
        metrics_list: list[dict[str, float]],
        latencies: list[float],
    ) -> dict[str, float]:
        """聚合每条样本的原始指标。"""
        if not metrics_list:
            return {}

        keys = metrics_list[0].keys()
        aggregated: dict[str, float] = {}
        for key in keys:
            values = [m.get(key, 0.0) for m in metrics_list]
            aggregated[key] = round(sum(values) / len(values), 4)
        if latencies:
            aggregated["avg_latency_ms"] = round(sum(latencies) / len(latencies), 2)
        return aggregated

    def get_history(self, limit: int = 10, dataset_id: str | None = None) -> dict:
        """查询测试历史。"""
        history = self._history
        if dataset_id:
            history = [h for h in history if h["dataset_id"] == dataset_id]
        history = history[-limit:]
        # 精简历史记录
        simplified = []
        for h in history:
            hybrid_summary = h["summary"].get("hybrid", {})
            simplified.append({
                "test_id": h["test_id"],
                "dataset_id": h["dataset_id"],
                "dataset_version": h.get("config", {}).get("dataset_version", "v1"),
                "modes": h.get("config", {}).get("modes", []),
                "top_k": h.get("config", {}).get("top_k", 5),
                "hybrid_recall@5": hybrid_summary.get("recall@5", 0.0),
                "hybrid_ndcg@5": hybrid_summary.get("ndcg@5", 0.0),
                "hybrid_mrr": hybrid_summary.get("mrr", 0.0),
                "hybrid_degraded_rate": hybrid_summary.get("degraded_rate", 0.0),
                "hybrid_vector_presence_rate": hybrid_summary.get("vector_presence_rate", 0.0),
                "avg_latency_ms": hybrid_summary.get("avg_latency_ms", 0.0),
                "vector_weight": h.get("config", {}).get("vector_weight"),
                "lexical_weight": h.get("config", {}).get("lexical_weight"),
                "fusion_strategy": h["config"].get("fusion_strategy", "weighted"),
                "baseline_config": h.get("config", {}).get("baseline_config", {}),
                "created_at": h["started_at"],
                "duration_ms": h["duration_ms"],
            })
        return {"history": simplified, "total": len(simplified)}

    def get_test_result(self, test_id: str) -> dict:
        """查询单个测试结果详情。"""
        result = next((h for h in self._history if h["test_id"] == test_id), None)
        if not result:
            raise ValueError(f"Test ID {test_id} not found")
        return result

    def delete_history(self, test_id: str) -> dict:
        """删除指定测试记录。"""
        index = next((i for i, h in enumerate(self._history) if h["test_id"] == test_id), None)
        if index is None:
            raise ValueError(f"Test ID {test_id} not found")
        deleted = self._history.pop(index)
        return {
            "test_id": test_id,
            "deleted": True,
            "summary": {mode: {"recall@5": metrics.get("recall@5", 0.0)} for mode, metrics in deleted.get("summary", {}).items()},
        }

    def compare_tests(self, test_a: str, test_b: str) -> dict:
        """对比两次测试结果。"""
        result_a = next((h for h in self._history if h["test_id"] == test_a), None)
        result_b = next((h for h in self._history if h["test_id"] == test_b), None)
        if not result_a or not result_b:
            raise ValueError("Test ID not found")

        diff: dict[str, dict] = {}
        for mode in set(result_a["summary"].keys()) | set(result_b["summary"].keys()):
            a_metrics = result_a["summary"].get(mode, {})
            b_metrics = result_b["summary"].get(mode, {})
            mode_diff: dict[str, dict] = {}
            for key in set(a_metrics.keys()) | set(b_metrics.keys()):
                a_val = a_metrics.get(key, 0.0)
                b_val = b_metrics.get(key, 0.0)
                mode_diff[key] = {"a": a_val, "b": b_val, "delta": round(b_val - a_val, 4)}
            diff[mode] = mode_diff

        by_category_diff: dict[str, dict] = {}
        for category in set(result_a.get("by_category", {}).keys()) | set(result_b.get("by_category", {}).keys()):
            a_modes = result_a.get("by_category", {}).get(category, {})
            b_modes = result_b.get("by_category", {}).get(category, {})
            category_diff: dict[str, dict] = {}
            for mode in set(a_modes.keys()) | set(b_modes.keys()):
                a_metrics = a_modes.get(mode, {})
                b_metrics = b_modes.get(mode, {})
                mode_diff: dict[str, dict] = {}
                for key in set(a_metrics.keys()) | set(b_metrics.keys()):
                    a_val = a_metrics.get(key, 0.0)
                    b_val = b_metrics.get(key, 0.0)
                    mode_diff[key] = {"a": a_val, "b": b_val, "delta": round(b_val - a_val, 4)}
                category_diff[mode] = mode_diff
            by_category_diff[category] = category_diff

        return {
            "test_a": {"test_id": test_a, "config": result_a["config"], "summary": result_a["summary"]},
            "test_b": {"test_id": test_b, "config": result_b["config"], "summary": result_b["summary"]},
            "diff": diff,
            "by_category_diff": by_category_diff,
        }


# 全局单例
recall_tester = RecallTester()