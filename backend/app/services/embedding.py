from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict
from typing import Optional

import httpx

from app.core.config import embedding_settings, llm_settings, memory_settings

logger = logging.getLogger(__name__)


class EmbeddingError(Exception):
    pass


class _EmbeddingCache:
    """基于 OrderedDict 的线程安全 LRU 缓存。

    缓存 key 为 (text_hash, model, dimensions) 的组合，确保模型或维度
    变更后不会返回旧缓存。缓存命中时直接返回向量，避免重复调用 API。
    """

    def __init__(self, max_size: int = 1000):
        self._max_size = max_size
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def _make_key(self, text: str, model: str, dimensions: int) -> str:
        """生成缓存 key：基于文本哈希 + 模型 + 维度"""
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        return f"{model}:{dimensions}:{text_hash}"

    def get(self, text: str, model: str, dimensions: int) -> Optional[list[float]]:
        key = self._make_key(text, model, dimensions)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._hits += 1
                return self._cache[key]
            self._misses += 1
            return None

    def put(self, text: str, model: str, dimensions: int, embedding: list[float]) -> None:
        key = self._make_key(text, model, dimensions)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            else:
                if len(self._cache) >= self._max_size:
                    self._cache.popitem(last=False)  # 淘汰最久未使用
            self._cache[key] = embedding

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "hits": self._hits,
                "misses": self._misses,
            }


class EmbeddingClient:
    """调用 DashScope/OpenAI 兼容 embedding API。

    通过 httpx 直调 HTTP API（不依赖 openai/dashscope SDK），支持异步和同步两种调用方式。
    默认对接阿里云百炼 DashScope（base_url 指向 compatible-mode/v1），也可通过修改环境变量切换到 OpenAI。

    内置 LRU 缓存：相同文本+模型+维度的请求直接返回缓存结果，避免重复 API 调用。
    """

    def __init__(self):
        self.config = embedding_settings
        self._async_client: Optional[httpx.AsyncClient] = None
        self._sync_client: Optional[httpx.Client] = None
        self._cache = _EmbeddingCache(max_size=memory_settings.embedding_cache_size)

    @property
    def _cache_enabled(self) -> bool:
        return memory_settings.embedding_cache_enabled

    @property
    def _api_key(self) -> str:
        """获取 API Key，优先用 EMBEDDING_API_KEY，为空时回退到 LLM_API_KEY。

        这种回退机制让用户只需配置一个 LLM_API_KEY 就能同时使用 LLM 和 Embedding 服务，
        避免在 DashScope 场景下重复配置相同的密钥。
        """
        if self.config.api_key.strip():
            return self.config.api_key.strip()
        return llm_settings.api_key.strip()

    @property
    def _base_url(self) -> str:
        return self.config.base_url.rstrip("/")

    def is_available(self) -> bool:
        return bool(self._api_key)

    def clear_cache(self) -> None:
        """清空 embedding 缓存"""
        self._cache.clear()

    def cache_stats(self) -> dict[str, int]:
        """返回缓存统计信息"""
        return self._cache.stats()

    @property
    def async_client(self) -> httpx.AsyncClient:
        if self._async_client is None:
            timeout = httpx.Timeout(self.config.timeout_seconds)
            self._async_client = httpx.AsyncClient(
                timeout=timeout,
                headers={"Content-Type": "application/json"},
            )
        return self._async_client

    @property
    def sync_client(self) -> httpx.Client:
        if self._sync_client is None:
            timeout = httpx.Timeout(self.config.timeout_seconds)
            self._sync_client = httpx.Client(
                timeout=timeout,
                headers={"Content-Type": "application/json"},
            )
        return self._sync_client

    async def close(self) -> None:
        if self._async_client is not None:
            await self._async_client.aclose()
        if self._sync_client is not None:
            self._sync_client.close()

    def _build_request(self, text: str) -> dict:
        return {
            "model": self.config.model,
            "input": text,
            "dimensions": self.config.dimensions,
            "encoding_format": "float",
        }

    def _build_batch_request(self, texts: list[str]) -> dict:
        return {
            "model": self.config.model,
            "input": texts,
            "dimensions": self.config.dimensions,
            "encoding_format": "float",
        }

    async def embed(self, text: str) -> list[float]:
        """异步生成单条文本的 embedding 向量。

        重试机制：遇到超时或网络异常时重试，最多 max_retries+1 次。
        HTTP 状态码错误（如 401/429）不重试，直接抛出 EmbeddingError。

        缓存：若 embedding_cache_enabled=True，相同文本直接返回缓存结果。

        Args:
            text: 待向量化的文本

        Returns:
            embedding 向量（list[float]，维度由 EMBEDDING_DIMENSIONS 控制）

        Raises:
            EmbeddingError: API Key 未配置、请求超时、HTTP 错误或响应格式异常
        """
        # 缓存检查
        if self._cache_enabled:
            cached = self._cache.get(text, self.config.model, self.config.dimensions)
            if cached is not None:
                return cached

        if not self.is_available():
            raise EmbeddingError("Embedding API key not configured")

        url = f"{self._base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        request_data = self._build_request(text)

        for attempt in range(self.config.max_retries + 1):
            try:
                response = await self.async_client.post(url, headers=headers, json=request_data)
                response.raise_for_status()
                data = response.json()
                if "data" in data and data["data"]:
                    result = data["data"][0]["embedding"]
                    # 写入缓存
                    if self._cache_enabled:
                        self._cache.put(text, self.config.model, self.config.dimensions, result)
                    return result
                raise EmbeddingError(f"Unexpected response format: {data}")
            except httpx.TimeoutException:
                if attempt < self.config.max_retries:
                    logger.warning(f"Embedding request timed out, retrying {attempt + 1}/{self.config.max_retries}")
                    continue
                raise EmbeddingError(f"Embedding request timed out after {self.config.timeout_seconds}s")
            except httpx.HTTPStatusError as e:
                error_detail = self._extract_error_detail(e.response)
                logger.error(f"Embedding API error: {error_detail}")
                raise EmbeddingError(f"Embedding API error: {error_detail}")
            except Exception as e:
                if attempt < self.config.max_retries:
                    logger.warning(f"Embedding request failed, retrying {attempt + 1}/{self.config.max_retries}: {e}")
                    continue
                raise EmbeddingError(f"Embedding request failed: {str(e)}")

        raise EmbeddingError("Max retries exceeded")

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """异步批量生成 embedding 向量。

        缓存：若 embedding_cache_enabled=True，先检查缓存，仅对未命中的文本
        发起 API 请求，并将结果回填缓存。
        """
        if not self.is_available():
            raise EmbeddingError("Embedding API key not configured")

        results: list[list[float] | None] = [None] * len(texts)
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        # 缓存检查：分离已缓存和未缓存的文本
        if self._cache_enabled:
            for idx, text in enumerate(texts):
                cached = self._cache.get(text, self.config.model, self.config.dimensions)
                if cached is not None:
                    results[idx] = cached
                else:
                    uncached_indices.append(idx)
                    uncached_texts.append(text)
        else:
            uncached_indices = list(range(len(texts)))
            uncached_texts = list(texts)

        if not uncached_texts:
            return results  # type: ignore[return-value]

        batch_size = self.config.batch_size
        fetched: list[list[float]] = []

        for i in range(0, len(uncached_texts), batch_size):
            batch = uncached_texts[i : i + batch_size]
            url = f"{self._base_url}/embeddings"
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
            request_data = self._build_batch_request(batch)

            for attempt in range(self.config.max_retries + 1):
                try:
                    response = await self.async_client.post(url, headers=headers, json=request_data)
                    response.raise_for_status()
                    data = response.json()
                    if "data" in data and data["data"]:
                        batch_results = [item["embedding"] for item in sorted(data["data"], key=lambda x: x.get("index", 0))]
                        fetched.extend(batch_results)
                        break
                    raise EmbeddingError(f"Unexpected response format: {data}")
                except httpx.TimeoutException:
                    if attempt < self.config.max_retries:
                        logger.warning(f"Embedding batch request timed out, retrying {attempt + 1}/{self.config.max_retries}")
                        continue
                    raise EmbeddingError(f"Embedding batch request timed out after {self.config.timeout_seconds}s")
                except httpx.HTTPStatusError as e:
                    error_detail = self._extract_error_detail(e.response)
                    logger.error(f"Embedding batch API error: {error_detail}")
                    raise EmbeddingError(f"Embedding batch API error: {error_detail}")
                except Exception as e:
                    if attempt < self.config.max_retries:
                        logger.warning(f"Embedding batch request failed, retrying {attempt + 1}/{self.config.max_retries}: {e}")
                        continue
                    raise EmbeddingError(f"Embedding batch request failed: {str(e)}")

        # 回填缓存并写入结果
        for idx, text, emb in zip(uncached_indices, uncached_texts, fetched):
            results[idx] = emb
            if self._cache_enabled:
                self._cache.put(text, self.config.model, self.config.dimensions, emb)

        return results  # type: ignore[return-value]

    def embed_sync(self, text: str) -> list[float]:
        """同步生成单条文本的 embedding 向量。

        供 LangGraph 工作流的同步上下文调用（如 _persist_memory_items_sync、
        _vector_search）。重试机制与 embed() 一致。

        缓存：若 embedding_cache_enabled=True，相同文本直接返回缓存结果。

        Args:
            text: 待向量化的文本

        Returns:
            embedding 向量

        Raises:
            EmbeddingError: API Key 未配置、请求超时、HTTP 错误或响应格式异常
        """
        # 缓存检查
        if self._cache_enabled:
            cached = self._cache.get(text, self.config.model, self.config.dimensions)
            if cached is not None:
                return cached

        if not self.is_available():
            raise EmbeddingError("Embedding API key not configured")

        url = f"{self._base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        request_data = self._build_request(text)

        for attempt in range(self.config.max_retries + 1):
            try:
                response = self.sync_client.post(url, headers=headers, json=request_data)
                response.raise_for_status()
                data = response.json()
                if "data" in data and data["data"]:
                    result = data["data"][0]["embedding"]
                    # 写入缓存
                    if self._cache_enabled:
                        self._cache.put(text, self.config.model, self.config.dimensions, result)
                    return result
                raise EmbeddingError(f"Unexpected response format: {data}")
            except httpx.TimeoutException:
                if attempt < self.config.max_retries:
                    logger.warning(f"Embedding sync request timed out, retrying {attempt + 1}/{self.config.max_retries}")
                    continue
                raise EmbeddingError(f"Embedding sync request timed out after {self.config.timeout_seconds}s")
            except httpx.HTTPStatusError as e:
                error_detail = self._extract_error_detail(e.response)
                logger.error(f"Embedding sync API error: {error_detail}")
                raise EmbeddingError(f"Embedding sync API error: {error_detail}")
            except Exception as e:
                if attempt < self.config.max_retries:
                    logger.warning(f"Embedding sync request failed, retrying {attempt + 1}/{self.config.max_retries}: {e}")
                    continue
                raise EmbeddingError(f"Embedding sync request failed: {str(e)}")

        raise EmbeddingError("Max retries exceeded")

    def _extract_error_detail(self, response: httpx.Response) -> str:
        """从 DashScope/OpenAI 的错误响应中提取可读的错误信息。

        DashScope 错误响应格式：{"error": {"message": "...", "code": "..."}}
        OpenAI 错误响应格式：{"error": {"message": "...", "type": "..."}}
        若响应体不是 JSON 或不含 error 字段，返回原始文本前 200 字符。
        """
        try:
            data = response.json()
            if "error" in data:
                error = data["error"]
                if isinstance(error, dict):
                    return error.get("message", str(error))
                return str(error)
        except Exception:
            pass
        return response.text[:200]


embedding_client = EmbeddingClient()
