"""搜索适配器测试"""
import pytest
from unittest.mock import MagicMock, patch

from app.services.search_adapter import SearchAdapter, SearchAdapterError
from app.models.schemas import EvidenceDimension, SourceType


class TestSearchAdapter:
    def test_search_not_configured_returns_empty(self):
        """未配置 API key 时返回空列表"""
        with patch("app.services.search_adapter.settings") as mock_settings:
            mock_settings.is_configured = False
            
            adapter = SearchAdapter()
            results = adapter.search("test query", "TestCompetitor")
            
            assert results == []

    @patch("app.services.search_adapter.httpx.Client")
    def test_search_serpapi_success(self, mock_client_class):
        """SerpAPI 搜索成功"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "organic_results": [
                {"title": "Result 1", "snippet": "Snippet 1", "link": "https://example.com/1"},
                {"title": "Result 2", "snippet": "Snippet 2", "link": "https://example.com/2"},
            ]
        }
        mock_response.raise_for_status.return_value = None
        mock_client.get.return_value = mock_response
        
        with patch("app.services.search_adapter.settings") as mock_settings:
            mock_settings.is_configured = True
            mock_settings.provider = "serpapi"
            mock_settings.api_key = "test-key"
            mock_settings.max_results = 5
            
            adapter = SearchAdapter()
            results = adapter.search("test query", "TestCompetitor")
            
            assert len(results) == 2
            assert all(r.source_type == SourceType.text for r in results)
            assert all(r.dimension == EvidenceDimension.feature for r in results)

    @patch("app.services.search_adapter.httpx.Client")
    def test_search_pricing_query_sets_dimension(self, mock_client_class):
        """价格查询设置 pricing 维度"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "organic_results": [
                {"title": "Pricing", "snippet": "$9.99", "link": "https://example.com/pricing"},
            ]
        }
        mock_response.raise_for_status.return_value = None
        mock_client.get.return_value = mock_response
        
        with patch("app.services.search_adapter.settings") as mock_settings:
            mock_settings.is_configured = True
            mock_settings.provider = "serpapi"
            mock_settings.api_key = "test-key"
            mock_settings.max_results = 5
            
            adapter = SearchAdapter()
            results = adapter.search("competitor pricing", "TestCompetitor")
            
            assert len(results) == 1
            assert results[0].dimension == EvidenceDimension.pricing

    @patch("app.services.search_adapter.httpx.Client")
    def test_search_error_returns_empty(self, mock_client_class):
        """搜索失败返回空列表"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        from httpx import HTTPStatusError
        mock_client.get.side_effect = HTTPStatusError(
            "Error",
            request=MagicMock(),
            response=MagicMock(status_code=500)
        )
        
        with patch("app.services.search_adapter.settings") as mock_settings:
            mock_settings.is_configured = True
            mock_settings.provider = "serpapi"
            mock_settings.api_key = "test-key"
            mock_settings.max_results = 5
            
            adapter = SearchAdapter()
            results = adapter.search("test query", "TestCompetitor")
            
            assert results == []

    def test_create_evidence_from_results(self):
        """从搜索结果创建 Evidence"""
        adapter = SearchAdapter()
        results = [
            {"title": "Result 1", "snippet": "Snippet 1", "link": "https://example.com/1"},
            {"title": "Result 2", "snippet": "Snippet 2", "link": "https://example.com/2"},
        ]
        
        evidences = adapter._create_evidence_from_results(results, "TestCompetitor", "test query")
        
        assert len(evidences) == 2
        assert all(e.confidence == 0.65 for e in evidences)
        assert all(e.credibility_score == 0.5 for e in evidences)

    def test_create_evidence_skips_empty_snippet(self):
        """空 snippet 被跳过"""
        adapter = SearchAdapter()
        results = [
            {"title": "Result 1", "snippet": "", "link": "https://example.com/1"},
            {"title": "Result 2", "snippet": "Snippet 2", "link": "https://example.com/2"},
        ]
        
        evidences = adapter._create_evidence_from_results(results, "TestCompetitor", "test query")
        
        assert len(evidences) == 1

    def test_search_for_competitor_not_configured(self):
        """未配置时自动搜索返回空"""
        with patch("app.services.search_adapter.settings") as mock_settings:
            mock_settings.is_configured = False
            
            adapter = SearchAdapter()
            results = adapter.search_for_competitor("TestCompetitor", "test goal")
            
            assert results == []

    @patch("app.services.search_adapter.httpx.Client")
    def test_search_for_competitor_executes_queries(self, mock_client_class):
        """自动搜索执行多个查询"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "organic_results": [
                {"title": "Result", "snippet": "Snippet", "link": "https://example.com"},
            ]
        }
        mock_response.raise_for_status.return_value = None
        mock_client.get.return_value = mock_response
        
        with patch("app.services.search_adapter.settings") as mock_settings:
            mock_settings.is_configured = True
            mock_settings.provider = "serpapi"
            mock_settings.api_key = "test-key"
            mock_settings.max_results = 5
            
            adapter = SearchAdapter()
            results = adapter.search_for_competitor("TestCompetitor", "test goal")
            
            assert len(results) >= 1
            assert mock_client.get.call_count >= 1