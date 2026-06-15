"""研究任务执行器 - 按 source_type 分发到对应 Adapter"""
from __future__ import annotations

import logging
from typing import List

from app.models.schemas import Evidence, EvidenceDimension, ResearchTask, SourceType
from app.services.url_adapter import url_adapter, URLAdapterError

logger = logging.getLogger(__name__)


def execute_task(task: ResearchTask) -> List[Evidence]:
    """
    根据 ResearchTask.source_type 分发到对应 Adapter 执行
    
    Args:
        task: ResearchTask 任务
        
    Returns:
        Evidence 列表
    """
    try:
        if task.source_type == "url":
            return _execute_url_task(task)
        elif task.source_type == "search":
            return _execute_search_task(task)
        elif task.source_type == "comment":
            return _execute_comment_task(task)
        elif task.source_type == "image":
            return _execute_image_task(task)
        else:
            logger.warning(f"未知的 source_type: {task.source_type}")
            return []
    except Exception as e:
        logger.warning(f"执行 ResearchTask 失败 {task.source_type}: {e}")
        return []


def _execute_url_task(task: ResearchTask) -> List[Evidence]:
    """执行 URL 采集任务"""
    try:
        title, content, price_info = url_adapter.fetch(task.query_or_url)
        
        lower_url = task.query_or_url.lower()
        dimension = (
            EvidenceDimension.pricing
            if "price" in lower_url or "pricing" in lower_url
            else EvidenceDimension.feature
        )
        
        if content:
            claim_text = f"{task.competitor} 的页面标题为 '{title}'" if title else f"{task.competitor} 页面包含相关信息"
            if price_info:
                claim_text += f"，价格信息：{price_info}"
            
            return [
                Evidence(
                    source_type=SourceType.url,
                    source_url=task.query_or_url,
                    competitor=task.competitor,
                    dimension=dimension,
                    claim=claim_text,
                    quote=content[:500] if content else f"来自 URL 输入：{task.query_or_url}",
                    confidence=0.85,
                    freshness="recent",
                    content_hash=_hash_text(content[:200] if content else task.query_or_url),
                    license_risk="low",
                )
            ]
        else:
            return [
                Evidence(
                    source_type=SourceType.url,
                    source_url=task.query_or_url,
                    competitor=task.competitor,
                    dimension=dimension,
                    claim=f"{task.competitor} 在公开页面展示了与 {dimension.value} 相关的信息",
                    quote=f"来自 URL 输入：{task.query_or_url}",
                    confidence=0.72,
                    freshness="unknown",
                    content_hash=_hash_text(task.query_or_url),
                    license_risk="low",
                )
            ]
    except URLAdapterError as e:
        logger.warning(f"URL抓取失败 {task.query_or_url}: {e}")
        return [
            Evidence(
                source_type=SourceType.url,
                source_url=task.query_or_url,
                competitor=task.competitor,
                dimension=EvidenceDimension.feature,
                claim=f"{task.competitor} 页面抓取失败: {str(e)}",
                quote=f"URL: {task.query_or_url} (抓取失败)",
                confidence=0.3,
                freshness="unknown",
                content_hash=_hash_text(task.query_or_url),
                license_risk="low",
            )
        ]


def _execute_search_task(task: ResearchTask) -> List[Evidence]:
    """执行搜索任务"""
    from app.services.search_adapter import search_adapter
    
    evidences = search_adapter.search_for_competitor(task.competitor, task.query_or_url)
    return evidences


def _execute_comment_task(task: ResearchTask) -> List[Evidence]:
    """执行评论聚类任务"""
    from app.services.comment_adapter import comment_adapter
    
    evidences = comment_adapter.cluster(task.query_or_url, task.competitor)
    return evidences


def _execute_image_task(task: ResearchTask) -> List[Evidence]:
    """执行图片任务"""
    import hashlib
    
    return [
        Evidence(
            source_type=SourceType.image,
            competitor=task.competitor,
            dimension=EvidenceDimension.positioning,
            claim="截图输入可用于识别竞品的视觉定位和核心卖点",
            quote=f"用户上传截图：{task.query_or_url}",
            confidence=0.58,
            freshness="user-provided",
            media_ref=task.query_or_url,
            content_hash=hashlib.sha256(task.query_or_url.encode()).hexdigest(),
            license_risk="medium",
        )
    ]


def _hash_text(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
