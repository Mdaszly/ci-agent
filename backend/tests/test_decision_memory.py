"""决策记忆 Hybrid RAG 检索测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.models.schemas import DecisionChunkType, DecisionMemoryItem, DecisionPackStatus
from app.services import decision_memory as dm
from app.services.decision_memory import (
    _hybrid_fusion,
    _lexical_search,
    _rrf_fusion,
    clear_task_memory,
    search_decision_memory,
    upsert_decision_memory,
)


@pytest.fixture(autouse=True)
def _clear_memory():
    """每个测试前后清空内存。"""
    clear_task_memory("test_task")
    clear_task_memory("test_task_2")
    dm._MEMORY_VECTORS.clear()
    yield
    clear_task_memory("test_task")
    clear_task_memory("test_task_2")
    dm._MEMORY_VECTORS.clear()


def _make_item(
    item_id: str,
    summary: str,
    embedding_text: str,
    *,
    task_id: str = "test_task",
    chunk_type: DecisionChunkType = DecisionChunkType.decision,
    status: DecisionPackStatus = DecisionPackStatus.approved,
) -> DecisionMemoryItem:
    return DecisionMemoryItem(
        id=item_id,
        task_id=task_id,
        pack_id="test_pack",
        version=1,
        chunk_type=chunk_type,
        stage="writer",
        iteration=0,
        source_refs=[],
        summary=summary,
        embedding_text=embedding_text,
        payload={},
        status=status,
    )


class TestLexicalSearch:
    """词面检索测试。"""

    def test_exact_keyword_match(self):
        item = _make_item("m1", "几素手持风扇风力强劲", "几素 风力强劲 噪音略大")
        upsert_decision_memory([item])

        results = _lexical_search("几素 风力", top_k=5)
        assert len(results) == 1
        assert results[0][0].id == "m1"
        assert results[0][1] > 0

    def test_no_match_returns_empty(self):
        item = _make_item("m1", "蓝牙耳机", "蓝牙耳机 音质好")
        upsert_decision_memory([item])

        results = _lexical_search("手持风扇", top_k=5)
        for _, score in results:
            assert score < 0.1

    def test_top_k_limit(self):
        items = [_make_item(f"m{i}", f"测试项目{i}", f"测试 关键词{i}") for i in range(5)]
        upsert_decision_memory(items)

        results = _lexical_search("测试", top_k=3)
        assert len(results) <= 3

    def test_filter_by_chunk_type(self):
        item1 = _make_item("m1", "决策", "决策内容", chunk_type=DecisionChunkType.decision)
        item2 = _make_item("m2", "证据", "证据内容", chunk_type=DecisionChunkType.evidence)
        upsert_decision_memory([item1, item2])

        results = _lexical_search("内容", top_k=5, chunk_types=[DecisionChunkType.decision])
        ids = [r[0].id for r in results]
        assert "m1" in ids
        assert "m2" not in ids

    def test_exclude_superseded(self):
        item1 = _make_item("m1", "活跃记忆", "活跃内容", status=DecisionPackStatus.approved)
        item2 = _make_item("m2", "废弃记忆", "废弃内容", status=DecisionPackStatus.superseded)
        upsert_decision_memory([item1, item2])

        results = _lexical_search("内容", top_k=5, include_superseded=False)
        ids = [r[0].id for r in results]
        assert "m1" in ids
        assert "m2" not in ids


class TestHybridFusion:
    """混合排序测试。"""

    def test_empty_results(self):
        assert _hybrid_fusion([], [], top_k=5) == []

    def test_only_lexical_results(self):
        item = _make_item("m1", "测试", "测试内容")
        result = _hybrid_fusion([(item, 0.8)], [], top_k=5)
        assert len(result) == 1
        assert result[0][0].id == "m1"

    def test_only_vector_results(self):
        item = _make_item("m1", "测试", "测试内容")
        result = _hybrid_fusion([], [(item, 0.9)], top_k=5)
        assert len(result) == 1
        assert result[0][0].id == "m1"

    def test_merge_and_rank(self):
        item1 = _make_item("m1", "项目1", "内容1")
        item2 = _make_item("m2", "项目2", "内容2")
        item3 = _make_item("m3", "项目3", "内容3")

        lexical = [(item1, 0.8), (item2, 0.4)]
        vector = [(item2, 0.9), (item3, 0.7)]

        result = _hybrid_fusion(lexical, vector, top_k=3)
        ids = [r[0].id for r in result]
        assert len(result) == 3
        assert "m2" in ids[:2]

    def test_rrf_fusion_prefers_shared_rank(self):
        item1 = _make_item("m1", "项目1", "内容1")
        item2 = _make_item("m2", "项目2", "内容2")
        item3 = _make_item("m3", "项目3", "内容3")

        lexical = [(item1, 0.9), (item2, 0.8), (item3, 0.1)]
        vector = [(item2, 0.95), (item3, 0.9), (item1, 0.2)]

        result = _rrf_fusion(lexical, vector, top_k=3)
        ids = [r[0].id for r in result]
        assert ids[0] == "m2"
        assert set(ids) == {"m1", "m2", "m3"}


class TestSearchDecisionMemory:
    """search_decision_memory 入口测试。"""

    def test_degrade_to_lexical_when_no_embedding(self):
        item = _make_item("m1", "几素手持风扇", "几素 风力强劲")
        upsert_decision_memory([item])

        with patch.object(dm, "_get_embedding_client", return_value=None):
            results = search_decision_memory("几素", top_k=5)

        assert len(results) >= 1
        assert results[0][0].id == "m1"

    def test_hybrid_with_mock_embedding(self):
        item1 = _make_item("m1", "几素风扇噪音大", "几素 噪音 风力大")
        item2 = _make_item("m2", "铁布衫静音风扇", "铁布衫 静音 风力适中")
        upsert_decision_memory([item1, item2])

        class MockClient:
            def is_available(self):
                return True

            def embed_sync(self, text):
                if "噪音" in text or "声音" in text:
                    return [1.0, 0.0, 0.0]
                if "静音" in text:
                    return [0.0, 1.0, 0.0]
                return [0.5, 0.5, 0.0]

        dm._MEMORY_VECTORS["m1"] = (item1, [1.0, 0.0, 0.0])
        dm._MEMORY_VECTORS["m2"] = (item2, [0.0, 1.0, 0.0])

        with patch.object(dm, "_get_embedding_client", return_value=MockClient()):
            with patch.object(dm, "db_settings") as mock_db:
                mock_db.use_sqlite = True
                results = search_decision_memory("声音太响", top_k=5)

        ids = [r[0].id for r in results]
        assert "m1" in ids

    def test_search_decision_memory_uses_rrf_strategy(self):
        item = _make_item("m1", "几素手持风扇", "几素 风力强劲")
        lexical = [(item, 0.8)]
        vector = [(item, 0.9)]

        with patch.object(dm, "_lexical_search", return_value=lexical), patch.object(
            dm, "_vector_search", return_value=vector
        ), patch.object(dm, "_rrf_fusion", return_value=[(item, 1.0)]), patch.object(
            dm.memory_settings, "fusion_strategy", "rrf"
        ):
            results = search_decision_memory("几素", top_k=5)

        assert results == [(item, 1.0)]

    def test_vector_search_applies_hnsw_ef_search(self):
        item = _make_item("m1", "几素手持风扇", "几素 风力强劲")
        row = SimpleNamespace(
            similarity=0.9,
            id=item.id,
            task_id=item.task_id,
            pack_id=item.pack_id,
            version=item.version,
            chunk_type=item.chunk_type.value,
            stage=item.stage,
            iteration=item.iteration,
            source_refs=item.source_refs,
            summary=item.summary,
            embedding_text=item.embedding_text,
            payload=item.payload,
            status=item.status.value,
            created_at=item.created_at,
        )

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [row]
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = [MagicMock(), mock_result]
        mock_begin_ctx = MagicMock()
        mock_begin_ctx.__enter__.return_value = mock_conn
        mock_begin_ctx.__exit__.return_value = False
        mock_engine = MagicMock()
        mock_engine.begin.return_value = mock_begin_ctx
        mock_client = MagicMock()
        mock_client.embed_sync.return_value = [0.1, 0.2, 0.3]

        with patch.object(dm.db_settings, "use_sqlite", False), patch.object(
            dm, "is_pgvector_enabled", return_value=True
        ), patch.object(dm, "_get_embedding_client", return_value=mock_client), patch.object(
            dm, "_get_sync_engine", return_value=mock_engine
        ):
            results = dm._vector_search("几素", top_k=1)

        assert results[0][0].id == "m1"
        assert mock_conn.execute.call_args_list[0].args[1] == {"ef_search": dm.memory_settings.hnsw_ef_search}


class TestClearTaskMemory:
    """clear_task_memory 测试。"""

    def test_clear_removes_numpy_vectors(self):
        item = _make_item("m1", "测试", "测试内容", task_id="test_task")
        upsert_decision_memory([item])

        dm._MEMORY_VECTORS["m1"] = (item, [0.1, 0.2, 0.3])

        clear_task_memory("test_task")

        assert "test_task" not in dm._MEMORY_INDEX
        assert "m1" not in dm._MEMORY_VECTORS