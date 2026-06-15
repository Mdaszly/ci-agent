"""搜索适配器 - 补充搜索 Evidence"""
from __future__ import annotations

import hashlib
import html
import logging
from typing import List, Dict, Any

import httpx

from app.core.config import search_settings as settings
from app.models.schemas import Evidence, EvidenceDimension, SourceType

logger = logging.getLogger(__name__)


class SearchAdapterError(Exception):
    pass


class SearchAdapter:
    """搜索适配器"""

    # 搜索结果默认置信度配置
    DEFAULT_CONFIDENCE = 0.65
    DEFAULT_CREDIBILITY = 0.5
    DEFAULT_QUALITY = 0.55
    # quote 截断长度
    QUOTE_MAX_LENGTH = 500
    # content_hash 长度
    HASH_PREFIX_LENGTH = 16

    def __init__(self):
        self.config = settings
        self._client: httpx.Client | None = None
    
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=httpx.Timeout(30))
        return self._client
    
    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "SearchAdapter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def search(self, query: str, competitor: str) -> List[Evidence]:
        """
        执行搜索并生成 Evidence
        
        Args:
            query: 搜索查询
            competitor: 竞品名称
            
        Returns:
            搜索结果 Evidence 列表
        """
        if not self.config.is_configured:
            logger.info("Search API key 未配置，跳过搜索")
            return []
        
        try:
            results = self._execute_search(query)
            return self._create_evidence_from_results(results, competitor, query)
        except Exception as e:
            logger.warning(f"搜索失败: {e}")
            return []
    
    def _execute_search(self, query: str) -> List[Dict[str, Any]]:
        """执行搜索 API 调用"""
        if self.config.provider == "serpapi":
            return self._search_serpapi(query)
        else:
            logger.warning(f"未支持的搜索提供商: {self.config.provider}")
            return []
    
    def _search_serpapi(self, query: str) -> List[Dict[str, Any]]:
        """调用 SerpAPI"""
        url = "https://serpapi.com/search"
        params = {
            "q": query,
            "num": self.config.max_results,
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
        }

        try:
            response = self.client.get(url, params=params, headers=headers)
            response.raise_for_status()
            
            data = response.json()
            results = []
            
            # 提取有机搜索结果
            for item in data.get("organic_results", [])[:self.config.max_results]:
                results.append({
                    "title": item.get("title", ""),
                    "snippet": item.get("snippet", ""),
                    "link": item.get("link", ""),
                })
            
            return results
        except httpx.HTTPStatusError as e:
            raise SearchAdapterError(f"SerpAPI HTTP error: {e.response.status_code}")
        except httpx.TimeoutException:
            raise SearchAdapterError("SerpAPI request timed out")
        except Exception as e:
            raise SearchAdapterError(f"SerpAPI error: {str(e)}")
    
    # 维度关键词映射（按优先级排序）
    DIMENSION_KEYWORDS = {
        EvidenceDimension.pricing: [
            "price", "pricing", "cost", "fee", "subscription", "plan",
            "价格", "收费", "套餐", "订阅", "付费", "费用", "人民币", "美元",
            "dollar", "yen", "€", "¥", "$",
        ],
        EvidenceDimension.user_feedback: [
            "review", "reviews", "feedback", "comment", "rating", "testimonial",
            "评论", "反馈", "评价", "用户", "体验", "口碑",
        ],
        EvidenceDimension.positioning: [
            "positioning", "brand", "differentiation", "unique",
            "定位", "品牌", "差异化", "独特", "优势",
        ],
        EvidenceDimension.risk: [
            "risk", "risk", "issue", "problem", "concern", "complaint",
            "风险", "问题", "隐患", "缺点", "劣势", "不足",
        ],
    }

    def _infer_dimension(self, query: str) -> EvidenceDimension:
        """根据查询关键词推断维度"""
        query_lower = query.lower()

        for dimension, keywords in self.DIMENSION_KEYWORDS.items():
            for keyword in keywords:
                if keyword in query_lower:
                    return dimension

        return EvidenceDimension.feature

    def _create_evidence_from_results(
        self, results: List[Dict[str, Any]], competitor: str, query: str
    ) -> List[Evidence]:
        """从搜索结果创建 Evidence"""
        evidences = []

        # 根据查询推断维度
        dimension = self._infer_dimension(query)

        for result in results:
            title = result.get("title", "")
            snippet = result.get("snippet", "")
            link = result.get("link", "")

            if not snippet:
                continue

            evidence = Evidence(
                source_type=SourceType.text,
                source_url=link,
                competitor=competitor,
                dimension=dimension,
                claim=f"搜索结果显示: {html.escape(title)}",
                quote=html.escape(snippet[: self.QUOTE_MAX_LENGTH]),
                confidence=self.DEFAULT_CONFIDENCE,
                freshness="search-result",
                content_hash=hashlib.sha256(snippet.encode()).hexdigest()[: self.HASH_PREFIX_LENGTH],
                credibility_score=self.DEFAULT_CREDIBILITY,
                quality_score=self.DEFAULT_QUALITY,
            )
            evidences.append(evidence)

        return evidences
    
    def search_for_competitor(self, competitor: str, product_goal: str) -> List[Evidence]:
        """
        为竞品自动搜索补充 Evidence
        
        Args:
            competitor: 竞品名称
            product_goal: 产品目标
            
        Returns:
            搜索结果 Evidence 列表
        """
        if not self.config.is_configured:
            return []
        
        # 构建搜索查询
        queries = [
            f"{competitor} pricing",
            f"{competitor} features",
            f"{competitor} reviews",
        ]
        
        evidences = []
        for query in queries[:2]:  # 限制搜索次数
            results = self.search(query, competitor)
            evidences.extend(results)
        
        return evidences


search_adapter = SearchAdapter()