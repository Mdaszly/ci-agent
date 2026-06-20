"""RAG 检索增强模块测试。

覆盖：
- reranker.py: LLM 重排序（正常/降级/解析失败）
- query_rewriter.py: HyDE/Multi-Query 查询改写（正常/降级）
- embedding.py: LRU 缓存（命中/失效/淘汰/统计）
- decision_memory.py: 集成后的检索流程（阈值过滤/rerank/查询改写）
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret-key-for-unit-tests-32chars")


# ========== Reranker 测试 ==========


class TestReranker:
    """reranker.py 测试"""

    def test_rerank_disabled_returns_original(self) -> None:
        """enabled=False 时返回原始候选的前 top_k"""
        from app.services.reranker import rerank

        candidates = [("doc1", 0.9), ("doc2", 0.8), ("doc3", 0.7)]
        result = rerank("query", candidates, top_k=2, enabled=False)
        assert len(result) == 2
        assert result[0][0] == "doc1"
        assert result[1][0] == "doc2"

    def test_rerank_empty_candidates(self) -> None:
        """空候选列表返回空"""
        from app.services.reranker import rerank

        result = rerank("query", [], top_k=5, enabled=True)
        assert result == []

    def test_rerank_single_candidate(self) -> None:
        """单个候选跳过 rerank，直接返回"""
        from app.services.reranker import rerank

        candidates = [("doc1", 0.9)]
        result = rerank("query", candidates, top_k=5, enabled=True)
        assert len(result) == 1
        assert result[0][0] == "doc1"

    @patch("app.services.reranker.llm_client")
    def test_rerank_success_reorders(self, mock_llm: MagicMock) -> None:
        """LLM 返回评分后正确重排序（评分归一化到 [0,1]）"""
        from app.services.reranker import rerank

        # LLM 返回评分：doc2 最高，doc1 次之，doc3 最低
        mock_llm.chat_completion_sync.return_value = json.dumps([
            {"index": 0, "score": 5.0},
            {"index": 1, "score": 9.0},
            {"index": 2, "score": 2.0},
        ])

        candidates = [("doc1", 0.9), ("doc2", 0.8), ("doc3", 0.7)]
        result = rerank("query", candidates, top_k=3, enabled=True)

        assert len(result) == 3
        # doc2 应排第一（score=9.0/10=0.9）
        assert result[0][0] == "doc2"
        assert result[0][1] == 0.9
        # doc1 应排第二（score=5.0/10=0.5）
        assert result[1][0] == "doc1"
        assert result[1][1] == 0.5
        # doc3 应排第三（score=2.0/10=0.2）
        assert result[2][0] == "doc3"
        assert result[2][1] == 0.2

    @patch("app.services.reranker.llm_client")
    def test_rerank_llm_error_falls_back(self, mock_llm: MagicMock) -> None:
        """LLM 异常时降级返回原始排序"""
        from app.services.reranker import rerank
        from app.services.llm import LLMError

        mock_llm.chat_completion_sync.side_effect = LLMError("API unavailable")

        candidates = [("doc1", 0.9), ("doc2", 0.8), ("doc3", 0.7)]
        result = rerank("query", candidates, top_k=2, enabled=True)

        assert len(result) == 2
        assert result[0][0] == "doc1"  # 原始排序

    @patch("app.services.reranker.llm_client")
    def test_rerank_invalid_json_falls_back(self, mock_llm: MagicMock) -> None:
        """LLM 返回无效 JSON 时降级"""
        from app.services.reranker import rerank

        mock_llm.chat_completion_sync.return_value = "not a json"

        candidates = [("doc1", 0.9), ("doc2", 0.8)]
        result = rerank("query", candidates, top_k=2, enabled=True)

        assert len(result) == 2
        assert result[0][0] == "doc1"  # 原始排序

    @patch("app.services.reranker.llm_client")
    def test_rerank_markdown_fence(self, mock_llm: MagicMock) -> None:
        """LLM 返回带 markdown code fence 的 JSON"""
        from app.services.reranker import rerank

        mock_llm.chat_completion_sync.return_value = (
            "```json\n"
            '[{"index": 0, "score": 8.0}, {"index": 1, "score": 3.0}]\n'
            "```"
        )

        candidates = [("doc1", 0.9), ("doc2", 0.8)]
        result = rerank("query", candidates, top_k=2, enabled=True)

        assert len(result) == 2
        assert result[0][0] == "doc1"  # score=8.0/10=0.8 最高
        assert result[0][1] == 0.8

    @patch("app.services.reranker.llm_client")
    def test_rerank_top_k_limit(self, mock_llm: MagicMock) -> None:
        """rerank 后正确截断到 top_k"""
        from app.services.reranker import rerank

        mock_llm.chat_completion_sync.return_value = json.dumps([
            {"index": 0, "score": 3.0},
            {"index": 1, "score": 9.0},
            {"index": 2, "score": 7.0},
            {"index": 3, "score": 5.0},
        ])

        candidates = [("doc1", 0.9), ("doc2", 0.8), ("doc3", 0.7), ("doc4", 0.6)]
        result = rerank("query", candidates, top_k=2, enabled=True)

        assert len(result) == 2
        assert result[0][0] == "doc2"  # score=9.0
        assert result[1][0] == "doc3"  # score=7.0


# ========== Query Rewriter 测试 ==========


class TestQueryRewriter:
    """query_rewriter.py 测试"""

    def test_rewrite_none_strategy(self) -> None:
        """strategy='none' 返回原始查询"""
        from app.services.query_rewriter import rewrite_query

        result = rewrite_query("竞品分析", strategy="none")
        assert result == "竞品分析"

    def test_rewrite_empty_query(self) -> None:
        """空查询返回空"""
        from app.services.query_rewriter import rewrite_query

        result = rewrite_query("", strategy="hyde")
        assert result == ""

    @patch("app.services.query_rewriter.llm_client")
    def test_rewrite_hyde_success(self, mock_llm: MagicMock) -> None:
        """HyDE 生成假设文档"""
        from app.services.query_rewriter import rewrite_query

        mock_llm.chat_completion_sync.return_value = "根据分析，产品A在定价策略上采用了渗透定价..."

        result = rewrite_query("产品A的定价策略", strategy="hyde")
        assert "定价" in result
        assert result != "产品A的定价策略"  # 应该被改写

    @patch("app.services.query_rewriter.llm_client")
    def test_rewrite_hyde_empty_response_falls_back(self, mock_llm: MagicMock) -> None:
        """HyDE 返回空文档时降级"""
        from app.services.query_rewriter import rewrite_query

        mock_llm.chat_completion_sync.return_value = ""

        result = rewrite_query("竞品分析", strategy="hyde")
        assert result == "竞品分析"  # 降级返回原始查询

    @patch("app.services.query_rewriter.llm_client")
    def test_rewrite_hyde_llm_error_falls_back(self, mock_llm: MagicMock) -> None:
        """HyDE LLM 异常时降级"""
        from app.services.query_rewriter import rewrite_query
        from app.services.llm import LLMError

        mock_llm.chat_completion_sync.side_effect = LLMError("API unavailable")

        result = rewrite_query("竞品分析", strategy="hyde")
        assert result == "竞品分析"

    @patch("app.services.query_rewriter.llm_client")
    def test_rewrite_multi_query_success(self, mock_llm: MagicMock) -> None:
        """Multi-Query 生成多个变体并拼接"""
        from app.services.query_rewriter import rewrite_query

        mock_llm.chat_completion_sync.return_value = json.dumps([
            "竞争对手分析报告",
            "市场竞品对比研究",
            "行业竞争格局分析",
        ])

        result = rewrite_query("竞品分析", strategy="multi_query")
        assert "竞品分析" in result  # 包含原始查询
        assert "竞争对手分析报告" in result  # 包含变体

    @patch("app.services.query_rewriter.llm_client")
    def test_rewrite_multi_query_invalid_json_falls_back(self, mock_llm: MagicMock) -> None:
        """Multi-Query 返回无效 JSON 时降级"""
        from app.services.query_rewriter import rewrite_query

        mock_llm.chat_completion_sync.return_value = "not json"

        result = rewrite_query("竞品分析", strategy="multi_query")
        assert result == "竞品分析"

    def test_rewrite_unknown_strategy(self) -> None:
        """未知策略返回原始查询"""
        from app.services.query_rewriter import rewrite_query

        result = rewrite_query("竞品分析", strategy="unknown")
        assert result == "竞品分析"


# ========== Embedding 缓存测试 ==========


class TestEmbeddingCache:
    """embedding.py 缓存测试"""

    def test_cache_hit(self) -> None:
        """相同文本第二次调用命中缓存"""
        from app.services.embedding import _EmbeddingCache

        cache = _EmbeddingCache(max_size=10)
        cache.put("hello", "text-embedding-v4", 1024, [0.1, 0.2, 0.3])

        result = cache.get("hello", "text-embedding-v4", 1024)
        assert result == [0.1, 0.2, 0.3]

        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 0

    def test_cache_miss(self) -> None:
        """未缓存的文本返回 None"""
        from app.services.embedding import _EmbeddingCache

        cache = _EmbeddingCache(max_size=10)
        result = cache.get("hello", "text-embedding-v4", 1024)
        assert result is None

        stats = cache.stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 1

    def test_cache_different_model(self) -> None:
        """不同模型不命中缓存"""
        from app.services.embedding import _EmbeddingCache

        cache = _EmbeddingCache(max_size=10)
        cache.put("hello", "model-a", 1024, [0.1, 0.2])

        result = cache.get("hello", "model-b", 1024)
        assert result is None

    def test_cache_different_dimensions(self) -> None:
        """不同维度不命中缓存"""
        from app.services.embedding import _EmbeddingCache

        cache = _EmbeddingCache(max_size=10)
        cache.put("hello", "text-embedding-v4", 1024, [0.1, 0.2])

        result = cache.get("hello", "text-embedding-v4", 512)
        assert result is None

    def test_cache_lru_eviction(self) -> None:
        """超过 max_size 时淘汰最久未使用"""
        from app.services.embedding import _EmbeddingCache

        cache = _EmbeddingCache(max_size=3)
        cache.put("doc1", "model", 1024, [1.0])
        cache.put("doc2", "model", 1024, [2.0])
        cache.put("doc3", "model", 1024, [3.0])

        # 访问 doc1，使其成为最近使用
        cache.get("doc1", "model", 1024)

        # 添加 doc4，应淘汰 doc2（最久未使用）
        cache.put("doc4", "model", 1024, [4.0])

        assert cache.get("doc1", "model", 1024) == [1.0]  # 仍在
        assert cache.get("doc2", "model", 1024) is None  # 被淘汰
        assert cache.get("doc3", "model", 1024) == [3.0]  # 仍在
        assert cache.get("doc4", "model", 1024) == [4.0]  # 仍在

    def test_cache_clear(self) -> None:
        """清空缓存"""
        from app.services.embedding import _EmbeddingCache

        cache = _EmbeddingCache(max_size=10)
        cache.put("hello", "model", 1024, [0.1])
        cache.clear()

        # clear 后 get 会是 miss
        assert cache.get("hello", "model", 1024) is None
        stats = cache.stats()
        assert stats["size"] == 0
        assert stats["hits"] == 0

    def test_cache_stats(self) -> None:
        """缓存统计正确"""
        from app.services.embedding import _EmbeddingCache

        cache = _EmbeddingCache(max_size=10)
        cache.put("a", "model", 1024, [1.0])
        cache.put("b", "model", 1024, [2.0])

        cache.get("a", "model", 1024)  # hit
        cache.get("a", "model", 1024)  # hit
        cache.get("c", "model", 1024)  # miss

        stats = cache.stats()
        assert stats["size"] == 2
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["max_size"] == 10

    @patch("app.services.embedding.embedding_client")
    def test_embed_sync_uses_cache(self, mock_client: MagicMock) -> None:
        """embed_sync 第二次调用命中缓存，不调用 HTTP"""
        from app.services.embedding import EmbeddingClient, _EmbeddingCache

        client = EmbeddingClient()
        client._cache = _EmbeddingCache(max_size=10)

        # 第一次调用：mock HTTP 返回
        with patch.object(client, "is_available", return_value=True), \
             patch.object(client, "_sync_client") as mock_sync:
            mock_response = MagicMock()
            mock_response.json.return_value = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
            mock_response.raise_for_status = MagicMock()
            mock_sync.post.return_value = mock_response

            result1 = client.embed_sync("test text")
            assert result1 == [0.1, 0.2, 0.3]
            assert mock_sync.post.call_count == 1

            # 第二次调用：应命中缓存，不调用 HTTP
            result2 = client.embed_sync("test text")
            assert result2 == [0.1, 0.2, 0.3]
            assert mock_sync.post.call_count == 1  # 仍然只调用了 1 次

            stats = client.cache_stats()
            assert stats["hits"] == 1


# ========== 集成测试：search_decision_memory 阈值过滤 ==========


class TestSearchThresholdFilter:
    """search_decision_memory 阈值过滤集成测试"""

    def test_threshold_filters_low_score_results(self) -> None:
        """阈值过滤剔除低分结果"""
        from app.services import decision_memory
        from app.core.config import memory_settings

        # 临时设置阈值
        original_threshold = memory_settings.min_similarity_threshold
        original_rerank = memory_settings.rerank_enabled
        original_rewrite = memory_settings.query_rewrite_strategy
        memory_settings.min_similarity_threshold = 0.5
        memory_settings.rerank_enabled = False
        memory_settings.query_rewrite_strategy = "none"

        try:
            # mock _lexical_search 返回低分结果
            mock_item = MagicMock()
            mock_item.id = "test-1"
            mock_item.summary = "test"

            with patch.object(decision_memory, "_lexical_search", return_value=[(mock_item, 0.3)]), \
                 patch.object(decision_memory, "_vector_search", return_value=[]):
                results, meta = decision_memory.search_decision_memory(
                    "query", return_meta=True
                )

                # 0.3 < 0.5 阈值，应被过滤
                assert len(results) == 0
                assert meta["threshold_filtered"] == 1
        finally:
            memory_settings.min_similarity_threshold = original_threshold
            memory_settings.rerank_enabled = original_rerank
            memory_settings.query_rewrite_strategy = original_rewrite

    def test_threshold_zero_keeps_all(self) -> None:
        """阈值为 0 时不过滤"""
        from app.services import decision_memory
        from app.core.config import memory_settings

        original_threshold = memory_settings.min_similarity_threshold
        original_rerank = memory_settings.rerank_enabled
        original_rewrite = memory_settings.query_rewrite_strategy
        memory_settings.min_similarity_threshold = 0.0
        memory_settings.rerank_enabled = False
        memory_settings.query_rewrite_strategy = "none"

        try:
            mock_item = MagicMock()
            mock_item.id = "test-1"
            mock_item.summary = "test"

            with patch.object(decision_memory, "_lexical_search", return_value=[(mock_item, 0.1)]), \
                 patch.object(decision_memory, "_vector_search", return_value=[]):
                results, meta = decision_memory.search_decision_memory(
                    "query", return_meta=True
                )

                assert len(results) == 1
                assert meta["threshold_filtered"] == 0
        finally:
            memory_settings.min_similarity_threshold = original_threshold
            memory_settings.rerank_enabled = original_rerank
            memory_settings.query_rewrite_strategy = original_rewrite

    def test_meta_includes_rag_enhancement_info(self) -> None:
        """meta 包含 RAG 增强信息"""
        from app.services import decision_memory
        from app.core.config import memory_settings

        original_rewrite = memory_settings.query_rewrite_strategy
        original_rerank = memory_settings.rerank_enabled
        memory_settings.query_rewrite_strategy = "none"
        memory_settings.rerank_enabled = False

        try:
            with patch.object(decision_memory, "_lexical_search", return_value=[]), \
                 patch.object(decision_memory, "_vector_search", return_value=[]):
                _, meta = decision_memory.search_decision_memory(
                    "query", return_meta=True
                )

                assert "query_rewrite_strategy" in meta
                assert "query_rewritten" in meta
                assert "threshold_filtered" in meta
                assert "rerank_applied" in meta
                assert meta["query_rewrite_strategy"] == "none"
                assert meta["query_rewritten"] is False
                assert meta["rerank_applied"] is False
        finally:
            memory_settings.query_rewrite_strategy = original_rewrite
            memory_settings.rerank_enabled = original_rerank
