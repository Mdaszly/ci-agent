from __future__ import annotations

import html
import logging
import re
from typing import Optional, Tuple
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup, SoupStrainer

logger = logging.getLogger(__name__)


class URLAdapterError(Exception):
    pass


class URLAdapter:
    def __init__(self, timeout: int = 30, max_retries: int = 2):
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            max_redirects=5,
        )
    
    def close(self) -> None:
        self._client.close()
    
    def fetch(self, url: str) -> Tuple[str, str, str]:
        # 从 URL 解析 hostname 用于 Host header
        parsed = urlparse(url)
        host = parsed.hostname or ""

        for attempt in range(self.max_retries + 1):
            try:
                headers = self._get_headers(host)
                response = self._client.get(url, headers=headers)
                response.raise_for_status()
                
                content_type = response.headers.get("content-type", "").lower()
                if "text/html" not in content_type:
                    raise URLAdapterError(f"不支持的内容类型: {content_type}")
                
                return self._parse_html(response.text)
            
            except httpx.HTTPStatusError as e:
                if attempt < self.max_retries:
                    logger.warning(f"HTTP错误 {e.response.status_code}, 重试中 {attempt + 1}/{self.max_retries}")
                    continue
                raise URLAdapterError(f"HTTP请求失败: {e.response.status_code}") from e
            
            except httpx.TimeoutException:
                if attempt < self.max_retries:
                    logger.warning(f"请求超时, 重试中 {attempt + 1}/{self.max_retries}")
                    continue
                raise URLAdapterError(f"请求超时 ({self.timeout}s)")
            
            except Exception as e:
                if attempt < self.max_retries:
                    logger.warning(f"请求失败: {e}, 重试中 {attempt + 1}/{self.max_retries}")
                    continue
                raise URLAdapterError(f"请求失败: {str(e)}") from e
        
        raise URLAdapterError("重试次数已用完")
    
    def _get_headers(self, host: str) -> dict[str, str]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Cache-Control": "max-age=0",
        }
        # 添加 Host header 防护 DNS rebinding 攻击
        if host:
            headers["Host"] = host
        return headers
    
    def _parse_html(self, html: str) -> Tuple[str, str, str]:
        strainer = SoupStrainer(["title", "h1", "h2", "article", "main", "body"])
        soup = BeautifulSoup(html, "lxml", parse_only=strainer)
        
        title = self._extract_title(soup)
        text = self._extract_text(soup)
        price_info = self._extract_price(text)
        
        return title, text, price_info
    
    def _extract_title(self, soup: BeautifulSoup) -> str:
        if soup.title:
            return soup.title.string.strip() if soup.title.string else ""
        
        for tag in ["h1", "h2"]:
            element = soup.find(tag)
            if element:
                return element.get_text(strip=True)
        
        return ""
    
    def _extract_text(self, soup: BeautifulSoup) -> str:
        text_parts = []
        
        for tag in ["article", "main", "div[role=main]", "body"]:
            element = soup.select_one(tag) if "[" in tag else soup.find(tag)
            if element:
                text_parts.append(element.get_text(separator="\n", strip=True))
        
        if not text_parts:
            body = soup.find("body")
            if body:
                text_parts.append(body.get_text(separator="\n", strip=True))
        
        full_text = "\n\n".join(text_parts)
        # 转义 HTML 特殊字符，防止 XSS
        escaped_text = html.escape(full_text)
        return self._clean_text(escaped_text)
    
    def _extract_price(self, text: str) -> str:
        price_patterns = [
            r"\$?\s?\d{1,4}(?:[.,]\d{1,2})?(?:\s*(?:USD|dollars?|元))?",
            r"(?:\$|¥|€|£)\s?\d{1,4}(?:[.,]\d{1,2})?",
            r"(?:pricing|price|订阅|套餐)\s*[:：]\s*\$?\s?\d{1,4}",
        ]
        
        prices = []
        for pattern in price_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            prices.extend(matches)
        
        return "; ".join(set(prices))[:500] if prices else ""
    
    def _clean_text(self, text: str) -> str:
        lines = text.split("\n")
        cleaned = []
        
        for line in lines:
            line = line.strip()
            if len(line) > 3:
                cleaned.append(line)
        
        return "\n".join(cleaned)[:8000]


url_adapter = URLAdapter()
