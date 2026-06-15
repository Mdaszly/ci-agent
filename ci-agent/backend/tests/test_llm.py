"""LLM 客户端测试"""
import pytest
from unittest.mock import MagicMock, patch

from app.services.llm import LLMClient, LLMError, LLMNotConfiguredError


class TestLLMClient:
    """测试 LLM 客户端功能"""

    def test_not_configured_raises_error(self):
        """未配置 API key 时应抛出 LLMNotConfiguredError"""
        with patch('app.services.llm.settings') as mock_settings:
            mock_settings.is_configured = False
            mock_settings.api_key = None
            
            client = LLMClient()
            
            with pytest.raises(LLMNotConfiguredError):
                client.chat_completion_sync([{"role": "user", "content": "hello"}])

    def test_parse_json_with_code_fence(self):
        """测试解析带 markdown code fence 的 JSON"""
        client = LLMClient()
        
        # 测试带 json fence 的情况
        content = """```json
{"key": "value"}
```"""
        result = client._parse_json(content)
        assert result == {"key": "value"}
        
        # 测试带普通 fence 的情况
        content = """```
{"key": "value"}
```"""
        result = client._parse_json(content)
        assert result == {"key": "value"}
        
        # 测试不带 fence 的情况
        content = '{"key": "value"}'
        result = client._parse_json(content)
        assert result == {"key": "value"}

    def test_parse_json_invalid_raises_error(self):
        """无效 JSON 应抛出 LLMError"""
        client = LLMClient()
        
        with pytest.raises(LLMError):
            client._parse_json("not valid json")

    @patch('app.services.llm.httpx.Client')
    def test_sync_client_success(self, mock_client_class):
        """测试同步调用成功"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"result": "test"}'}}]
        }
        mock_client.post.return_value = mock_response
        
        with patch('app.services.llm.settings') as mock_settings:
            mock_settings.is_configured = True
            mock_settings.api_key = "test-key"
            mock_settings.base_url = "https://api.example.com"
            mock_settings.timeout_seconds = 30
            mock_settings.max_retries = 2
            
            client = LLMClient()
            result = client.chat_completion_json_sync([{"role": "user", "content": "test"}])
            
            assert result == {"result": "test"}
            mock_client.post.assert_called_once()

    @patch('app.services.llm.httpx.Client')
    def test_sync_client_timeout(self, mock_client_class):
        """测试超时重试"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        from httpx import TimeoutException, RequestError
        mock_client.post.side_effect = [
            TimeoutException("timeout", request=MagicMock()),
            TimeoutException("timeout", request=MagicMock()),
            TimeoutException("timeout", request=MagicMock()),
        ]
        
        with patch('app.services.llm.settings') as mock_settings:
            mock_settings.is_configured = True
            mock_settings.api_key = "test-key"
            mock_settings.base_url = "https://api.example.com"
            mock_settings.timeout_seconds = 30
            mock_settings.max_retries = 2
            
            client = LLMClient()
            
            with pytest.raises(LLMError, match="timed out"):
                client.chat_completion_json_sync([{"role": "user", "content": "test"}])
            
            assert mock_client.post.call_count == 3
