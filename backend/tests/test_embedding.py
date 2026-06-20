"""Embedding 客户端测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services.embedding import EmbeddingClient, EmbeddingError


def _make_mock_response(json_data: dict, status_code: int = 200) -> httpx.Response:
    request = httpx.Request("POST", "https://example.com/embeddings")
    response = httpx.Response(status_code=status_code, json=json_data, request=request)
    return response


class TestEmbeddingClient:
    """EmbeddingClient 测试。"""

    def test_is_available_false_when_no_api_key(self):
        client = EmbeddingClient()
        with patch.object(client.config, "api_key", ""):
            with patch("app.services.embedding.llm_settings") as mock_llm:
                mock_llm.api_key = ""
                assert client.is_available() is False

    def test_is_available_true_when_api_key_set(self):
        client = EmbeddingClient()
        with patch.object(client.config, "api_key", "test_key"):
            assert client.is_available() is True

    def test_embed_sync_raises_when_not_available(self):
        client = EmbeddingClient()
        with patch.object(client, "is_available", return_value=False):
            with pytest.raises(EmbeddingError, match="not configured"):
                client.embed_sync("test")

    def test_embed_sync_success(self):
        client = EmbeddingClient()
        mock_response = _make_mock_response({
            "data": [{"embedding": [0.1, 0.2, 0.3], "index": 0, "object": "embedding"}],
            "model": "text-embedding-v4",
            "object": "list",
            "usage": {"prompt_tokens": 5},
        })

        mock_httpx_client = MagicMock()
        mock_httpx_client.post = MagicMock(return_value=mock_response)

        with patch.object(client, "is_available", return_value=True):
            client._sync_client = mock_httpx_client
            result = client.embed_sync("test text")
            assert result == [0.1, 0.2, 0.3]
            mock_httpx_client.post.assert_called_once()

    def test_embed_sync_retries_on_timeout(self):
        client = EmbeddingClient()
        mock_response = _make_mock_response({
            "data": [{"embedding": [0.5], "index": 0, "object": "embedding"}],
        })

        call_count = [0]

        def mock_post(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise httpx.TimeoutException("timeout")
            return mock_response

        mock_httpx_client = MagicMock()
        mock_httpx_client.post = mock_post

        with patch.object(client, "is_available", return_value=True):
            client._sync_client = mock_httpx_client
            result = client.embed_sync("test")
            assert result == [0.5]
            assert call_count[0] == 2  # 第一次失败，第二次成功

    def test_embed_batch_success(self):
        """测试批量 embedding（需要 async）。"""
        import asyncio

        client = EmbeddingClient()
        mock_response = _make_mock_response({
            "data": [
                {"embedding": [0.1, 0.2], "index": 0, "object": "embedding"},
                {"embedding": [0.3, 0.4], "index": 1, "object": "embedding"},
            ],
            "model": "text-embedding-v4",
            "object": "list",
        })

        mock_async_client = MagicMock()
        mock_async_client.post = MagicMock(return_value=_async_wrapper(mock_response))

        async def run():
            with patch.object(client, "is_available", return_value=True):
                client._async_client = mock_async_client
                result = await client.embed_batch(["text1", "text2"])
                assert len(result) == 2
                assert result[0] == [0.1, 0.2]
                assert result[1] == [0.3, 0.4]

        asyncio.run(run())


class _AsyncMockResponse:
    """模拟 httpx 异步响应。"""

    def __init__(self, response: httpx.Response):
        self._response = response

    def raise_for_status(self):
        if self._response.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=None, response=self._response
            )

    def json(self):
        return self._response.json()


def _async_wrapper(response: httpx.Response):
    """返回一个 awaitable 的 mock。"""

    async def _async():
        return _AsyncMockResponse(response)

    return _async()
