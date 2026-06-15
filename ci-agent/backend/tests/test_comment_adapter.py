"""评论聚类适配器测试"""
import pytest
from unittest.mock import MagicMock, patch

from app.services.comment_adapter import CommentAdapter
from app.models.schemas import EvidenceDimension, SourceType


class TestCommentAdapter:
    def test_cluster_short_comments_returns_single_evidence(self):
        """评论过短时返回单条 Evidence"""
        adapter = CommentAdapter()
        evidences = adapter.cluster("短评论", "TestCompetitor")
        
        assert len(evidences) == 1
        assert evidences[0].dimension == EvidenceDimension.user_feedback
        assert evidences[0].source_type == SourceType.text

    def test_cluster_empty_comments_returns_single_evidence(self):
        """空评论返回单条 Evidence"""
        adapter = CommentAdapter()
        evidences = adapter.cluster("", "TestCompetitor")
        
        assert len(evidences) == 1
        assert evidences[0].quote == ""

    def test_extract_themes_by_keywords(self):
        """关键词聚类提取主题"""
        adapter = CommentAdapter()
        comments = "用户常抱怨模板同质化、定价偏高、中文场景支持不足，希望获得更具体的求职反馈。很多人反映免费版功能太少，Pro 版 $49 太贵。"
        
        themes = adapter._extract_themes_by_keywords(comments)
        
        assert len(themes) >= 2
        theme_names = [t["theme"] for t in themes]
        assert "定价偏高" in theme_names or "模板同质化" in theme_names or "中文支持不足" in theme_names

    def test_create_evidence_from_themes(self):
        """从主题创建 Evidence"""
        adapter = CommentAdapter()
        themes = [
            {"theme": "模板同质化", "quote": "用户常抱怨模板同质化", "sentiment": "negative"},
            {"theme": "定价偏高", "quote": "定价偏高", "sentiment": "negative"},
        ]
        
        evidences = adapter._create_evidence_from_themes(themes, "TestCompetitor")
        
        assert len(evidences) == 2
        assert all(e.dimension == EvidenceDimension.user_feedback for e in evidences)
        assert all(e.source_type == SourceType.text for e in evidences)
        assert all(e.confidence >= 0.6 for e in evidences)

    @patch("app.services.comment_adapter.LLMClient")
    def test_cluster_with_llm_success(self, mock_llm_class):
        """LLM 成功聚类"""
        mock_llm = MagicMock()
        mock_llm_class.return_value = mock_llm
        
        mock_llm.chat_completion_json_sync.return_value = {
            "themes": [
                {"theme": "模板同质化", "quote": "模板千篇一律", "sentiment": "negative"},
                {"theme": "定价偏高", "quote": "价格太贵", "sentiment": "negative"},
            ]
        }
        
        adapter = CommentAdapter()
        comments = "用户常抱怨模板千篇一律，价格太贵，希望有更多选择。"
        evidences = adapter.cluster(comments, "TestCompetitor")
        
        assert len(evidences) == 2
        assert all(e.dimension == EvidenceDimension.user_feedback for e in evidences)

    @patch("app.services.comment_adapter.LLMClient")
    def test_cluster_llm_not_configured_fallback_to_keywords(self, mock_llm_class):
        """LLM 未配置时 fallback 到关键词聚类"""
        from app.services.llm import LLMNotConfiguredError
        
        mock_llm = MagicMock()
        mock_llm_class.return_value = mock_llm
        mock_llm.chat_completion_json_sync.side_effect = LLMNotConfiguredError("LLM API key not configured")
        
        adapter = CommentAdapter()
        comments = "用户常抱怨模板同质化、定价偏高、中文场景支持不足。"
        evidences = adapter.cluster(comments, "TestCompetitor")
        
        assert len(evidences) >= 1
        assert all(e.dimension == EvidenceDimension.user_feedback for e in evidences)

    @patch("app.services.comment_adapter.LLMClient")
    def test_cluster_llm_error_fallback_to_keywords(self, mock_llm_class):
        """LLM 调用失败时 fallback 到关键词聚类"""
        from app.services.llm import LLMError
        
        mock_llm = MagicMock()
        mock_llm_class.return_value = mock_llm
        mock_llm.chat_completion_json_sync.side_effect = LLMError("LLM API error")
        
        adapter = CommentAdapter()
        comments = "用户常抱怨模板同质化、定价偏高、中文场景支持不足。"
        evidences = adapter.cluster(comments, "TestCompetitor")
        
        assert len(evidences) >= 1
        assert all(e.dimension == EvidenceDimension.user_feedback for e in evidences)

    def test_cluster_no_matching_keywords_returns_generic_theme(self):
        """无匹配关键词时返回通用主题"""
        adapter = CommentAdapter()
        comments = "这是一条普通的评论，没有特定关键词。"
        
        themes = adapter._extract_themes_by_keywords(comments)
        
        assert len(themes) == 1
        assert themes[0]["theme"] == "用户反馈"