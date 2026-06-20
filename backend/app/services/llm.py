from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import llm_settings as settings

logger = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class LLMNotConfiguredError(LLMError):
    pass


class LLMClient:
    """LLM 客户端，支持多 Provider 切换。
    
    当前支持的 Provider：
    - dashscope: 阿里云百炼（默认）
    - openai: OpenAI API 兼容接口
    
    Provider 通过 LLM_PROVIDER 环境变量配置，不同 Provider 使用独立的
    API Key、Base URL 和 Model 配置，实现无缝切换。
    """
    
    def __init__(self):
        self.config = settings
        self._async_client: Optional[httpx.AsyncClient] = None
        self._sync_client: Optional[httpx.Client] = None
    
    @property
    def provider(self) -> str:
        """当前 Provider 名称"""
        return self.config.provider.lower()
    
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
    
    def _build_request(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """构建请求体，根据 Provider 适配格式"""
        request_data = {
            "model": self.config.effective_model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": 4096,
        }
        
        # 根据 Provider 添加特定参数
        if self.provider == "dashscope":
            # DashScope 特定配置
            pass
        
        return request_data
    
    def _get_api_headers(self) -> Dict[str, str]:
        """根据 Provider 返回认证头"""
        api_key = self.config.effective_api_key
        
        headers = {
            "Content-Type": "application/json",
        }
        
        if self.provider == "dashscope":
            # DashScope 使用 Bearer 认证
            headers["Authorization"] = f"Bearer {api_key}"
        elif self.provider == "openai":
            # OpenAI 使用 Bearer 认证
            headers["Authorization"] = f"Bearer {api_key}"
        
        return headers
    
    def _get_endpoint_url(self) -> str:
        """根据 Provider 返回 API 端点 URL"""
        base_url = self.config.effective_base_url
        return f"{base_url}/chat/completions"
    
    def _check_configured(self) -> None:
        """检查当前 Provider 是否已配置"""
        if not self.config.effective_api_key.strip():
            raise LLMNotConfiguredError(
                f"LLM API key not configured for provider '{self.provider}'"
            )
    
    async def chat_completion(self, messages: List[Dict[str, str]]) -> str:
        """异步调用 LLM"""
        self._check_configured()
        return await self._chat_completion_async(messages)
    
    def chat_completion_sync(self, messages: List[Dict[str, str]]) -> str:
        """同步调用 LLM"""
        self._check_configured()
        return self._chat_completion_sync_impl(messages)
    
    async def _chat_completion_async(self, messages: List[Dict[str, str]]) -> str:
        """异步实现，支持多 Provider"""
        request_data = self._build_request(messages)
        url = self._get_endpoint_url()
        headers = self._get_api_headers()
        
        logger.debug(f"LLM request to {self.provider}: {self.config.effective_model}")
        
        for attempt in range(self.config.max_retries + 1):
            try:
                response = await self.async_client.post(url, headers=headers, json=request_data)
                response.raise_for_status()
                
                data = response.json()
                if "choices" in data and data["choices"]:
                    return data["choices"][0]["message"]["content"]
                
                raise LLMError(f"Unexpected response format: {data}")
            
            except httpx.TimeoutException:
                if attempt < self.config.max_retries:
                    logger.warning(f"LLM request timed out, retrying {attempt + 1}/{self.config.max_retries}")
                    continue
                raise LLMError(f"LLM request timed out after {self.config.timeout_seconds}s")
            
            except httpx.HTTPStatusError as e:
                error_detail = self._extract_error_detail(e.response)
                logger.error(f"LLM API error ({self.provider}): {error_detail}")
                raise LLMError(f"LLM API error: {error_detail}")
            
            except Exception as e:
                if attempt < self.config.max_retries:
                    logger.warning(f"LLM request failed, retrying {attempt + 1}/{self.config.max_retries}: {e}")
                    continue
                raise LLMError(f"LLM request failed: {str(e)}")
        
        raise LLMError("Max retries exceeded")
    
    def _chat_completion_sync_impl(self, messages: List[Dict[str, str]]) -> str:
        """同步实现，支持多 Provider"""
        request_data = self._build_request(messages)
        url = self._get_endpoint_url()
        headers = self._get_api_headers()
        
        logger.debug(f"LLM sync request to {self.provider}: {self.config.effective_model}")
        
        for attempt in range(self.config.max_retries + 1):
            try:
                response = self.sync_client.post(url, headers=headers, json=request_data)
                response.raise_for_status()
                
                data = response.json()
                if "choices" in data and data["choices"]:
                    return data["choices"][0]["message"]["content"]
                
                raise LLMError(f"Unexpected response format: {data}")
            
            except httpx.TimeoutException:
                if attempt < self.config.max_retries:
                    logger.warning(f"LLM request timed out, retrying {attempt + 1}/{self.config.max_retries}")
                    continue
                raise LLMError(f"LLM request timed out after {self.config.timeout_seconds}s")
            
            except httpx.HTTPStatusError as e:
                error_detail = self._extract_error_detail(e.response)
                logger.error(f"LLM API error ({self.provider}): {error_detail}")
                raise LLMError(f"LLM API error: {error_detail}")
            
            except Exception as e:
                if attempt < self.config.max_retries:
                    logger.warning(f"LLM request failed, retrying {attempt + 1}/{self.config.max_retries}: {e}")
                    continue
                raise LLMError(f"LLM request failed: {str(e)}")
        
        raise LLMError("Max retries exceeded")
    
    async def chat_completion_json(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """异步调用并返回 JSON"""
        content = await self.chat_completion(messages)
        return self._parse_json(content)
    
    def chat_completion_json_sync(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """同步调用并返回 JSON"""
        content = self.chat_completion_sync(messages)
        return self._parse_json(content)
    
    def _parse_json(self, content: str) -> Dict[str, Any]:
        """解析 JSON，处理可能的 markdown code fence"""
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]
        
        if content.endswith("```"):
            content = content[:-3]
        
        content = content.strip()
        
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            raise LLMError(f"Failed to parse LLM response as JSON: {content[:200]}...")
    
    def _extract_error_detail(self, response: httpx.Response) -> str:
        """提取错误详情，适配不同 Provider 的响应格式"""
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


llm_client = LLMClient()