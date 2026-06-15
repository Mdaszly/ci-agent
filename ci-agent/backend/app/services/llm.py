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
    def __init__(self):
        self.config = settings
        self._async_client: Optional[httpx.AsyncClient] = None
        self._sync_client: Optional[httpx.Client] = None
    
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
        return {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": 4096,
        }
    
    async def chat_completion(self, messages: List[Dict[str, str]]) -> str:
        """异步调用 LLM"""
        if not self.config.is_configured:
            raise LLMNotConfiguredError("LLM API key not configured")
        
        return await self._chat_completion_async(messages)
    
    def chat_completion_sync(self, messages: List[Dict[str, str]]) -> str:
        """同步调用 LLM"""
        if not self.config.is_configured:
            raise LLMNotConfiguredError("LLM API key not configured")
        
        return self._chat_completion_sync_impl(messages)
    
    async def _chat_completion_async(self, messages: List[Dict[str, str]]) -> str:
        """异步实现"""
        request_data = self._build_request(messages)
        url = f"{self.config.base_url}/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        
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
                logger.error(f"LLM API error: {error_detail}")
                raise LLMError(f"LLM API error: {error_detail}")
            
            except Exception as e:
                if attempt < self.config.max_retries:
                    logger.warning(f"LLM request failed, retrying {attempt + 1}/{self.config.max_retries}: {e}")
                    continue
                raise LLMError(f"LLM request failed: {str(e)}")
        
        raise LLMError("Max retries exceeded")
    
    def _chat_completion_sync_impl(self, messages: List[Dict[str, str]]) -> str:
        """同步实现"""
        request_data = self._build_request(messages)
        url = f"{self.config.base_url}/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        
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
                logger.error(f"LLM API error: {error_detail}")
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
        # 移除可能的 markdown code fence
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
