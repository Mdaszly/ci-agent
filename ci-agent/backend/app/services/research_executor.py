"""研究任务执行器 - 按 source_type 分发到对应 Adapter"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import List

from app.models.schemas import Evidence, EvidenceDimension, ResearchTask, SourceType
from app.services.url_adapter import url_adapter, URLAdapterError

logger = logging.getLogger(__name__)

FEATURE_KEYWORDS = re.compile(
    r"(风力|风速|噪音|分贝|续航|电池|容量|档位|无刷|便携|重量|充电|静音|"
    r"wind|speed|noise|battery|portable|rpm|mah|db)",
    re.IGNORECASE,
)


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
        if task.source_type == "search":
            return _execute_search_task(task)
        if task.source_type == "comment":
            return _execute_comment_task(task)
        if task.source_type == "image":
            return _execute_image_task(task)
        logger.warning(f"未知的 source_type: {task.source_type}")
        return []
    except Exception as e:
        logger.warning(f"执行 ResearchTask 失败 {task.source_type}: {e}")
        return []


def _execute_url_task(task: ResearchTask) -> List[Evidence]:
    """执行 URL 采集任务，拆分为定位 / 定价 / 特性多条 Evidence"""
    try:
        title, content, price_info = url_adapter.fetch(task.query_or_url)
        evidences: List[Evidence] = []

        if title:
            evidences.append(
                Evidence(
                    source_type=SourceType.url,
                    source_url=task.query_or_url,
                    competitor=task.competitor,
                    dimension=EvidenceDimension.positioning,
                    claim=f"{task.competitor} 产品定位：{title[:80]}",
                    quote=title[:300],
                    confidence=0.82,
                    freshness="recent",
                    content_hash=_hash_text(title),
                    license_risk="low",
                )
            )

        if price_info:
            evidences.append(
                Evidence(
                    source_type=SourceType.url,
                    source_url=task.query_or_url,
                    competitor=task.competitor,
                    dimension=EvidenceDimension.pricing,
                    claim=f"{task.competitor} 价格信息",
                    quote=price_info[:500],
                    confidence=0.88,
                    freshness="recent",
                    content_hash=_hash_text(price_info),
                    license_risk="low",
                )
            )

        if content:
            feature_quote = _extract_feature_snippet(content)
            evidences.append(
                Evidence(
                    source_type=SourceType.url,
                    source_url=task.query_or_url,
                    competitor=task.competitor,
                    dimension=EvidenceDimension.feature,
                    claim=f"{task.competitor} 产品特性与参数信息",
                    quote=feature_quote[:800],
                    confidence=0.85,
                    freshness="recent",
                    content_hash=_hash_text(feature_quote[:200]),
                    license_risk="low",
                )
            )

        if evidences:
            return evidences

        return [
            Evidence(
                source_type=SourceType.url,
                source_url=task.query_or_url,
                competitor=task.competitor,
                dimension=EvidenceDimension.feature,
                claim=f"{task.competitor} 在公开页面展示了相关信息",
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


def _extract_feature_snippet(content: str) -> str:
    """从页面正文中提取含参数关键词的段落"""
    lines = [line.strip() for line in content.split("\n") if line.strip()]
    feature_lines = [line for line in lines if FEATURE_KEYWORDS.search(line)]
    if feature_lines:
        return "\n".join(feature_lines[:8])
    return content[:800]


def _execute_search_task(task: ResearchTask) -> List[Evidence]:
    """执行搜索任务"""
    from app.services.search_adapter import search_adapter

    return search_adapter.search_for_competitor(task.competitor, task.query_or_url)


def _execute_comment_task(task: ResearchTask) -> List[Evidence]:
    """执行评论聚类任务"""
    from app.services.comment_adapter import comment_adapter

    return comment_adapter.cluster(task.query_or_url, task.competitor)


def _execute_image_task(task: ResearchTask) -> List[Evidence]:
    """执行图片任务"""
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
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
