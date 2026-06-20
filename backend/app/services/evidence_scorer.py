from __future__ import annotations

import logging
import re

from app.models.schemas import Evidence, EvidenceDimension

logger = logging.getLogger(__name__)

# 通用 + 消费品（如手持小风扇）关键词
GENERAL_KEYWORDS = [
    "pricing", "price", "cost", "subscription", "free", "trial",
    "feature", "function", "tool", "review", "feedback", "rating", "user",
    "价格", "定价", "收费", "功能", "特性", "评价", "反馈", "用户",
]

CONSUMER_PRODUCT_KEYWORDS = [
    "风力", "风速", "噪音", "分贝", "续航", "电池", "便携", "重量",
    "usb", "无刷", "档位", "充电", "手持", "风扇", "静音",
    "wind", "speed", "noise", "battery", "portable", "fan",
]

TRUSTED_DOMAINS = [
    "tmall.com", "taobao.com", "jd.com", "xiaohongshu.com", "bilibili.com",
    "resumeworded.com", "kickresume.com", "linkedin.com",
]


class EvidenceScorer:
    def score(self, evidence: Evidence, context: str = "", focus_attributes: list[str] | None = None) -> Evidence:
        evidence.credibility_score = self._calculate_credibility(evidence)
        evidence.relevance_score = self._calculate_relevance(evidence, context, focus_attributes)
        evidence.quality_score = self._calculate_quality(evidence)

        return evidence

    def _calculate_credibility(self, evidence: Evidence) -> float:
        score = 0.5

        if evidence.source_type == "url":
            score += 0.2
            if evidence.source_url:
                url_lower = evidence.source_url.lower()
                if any(domain in url_lower for domain in TRUSTED_DOMAINS):
                    score += 0.1
                if "https" in url_lower:
                    score += 0.05

        if evidence.freshness == "recent":
            score += 0.15
        elif evidence.freshness == "user-provided":
            score += 0.1

        if evidence.license_risk == "low":
            score += 0.1
        elif evidence.license_risk == "high":
            score -= 0.15

        score += evidence.confidence * 0.2

        return min(1.0, max(0.0, score))

    def _calculate_relevance(
        self,
        evidence: Evidence,
        context: str,
        focus_attributes: list[str] | None = None,
    ) -> float:
        score = 0.5

        context_lower = context.lower()
        evidence_text = (evidence.claim + " " + evidence.quote).lower()
        keywords = GENERAL_KEYWORDS + CONSUMER_PRODUCT_KEYWORDS

        matched_keywords = sum(
            1 for kw in keywords if kw in evidence_text and kw in context_lower
        )
        score += matched_keywords * 0.06

        consumer_hits = sum(1 for kw in CONSUMER_PRODUCT_KEYWORDS if kw in evidence_text)
        score += min(consumer_hits * 0.04, 0.16)

        if focus_attributes:
            focus_hits = sum(
                1 for attr in focus_attributes
                if attr.lower() in evidence_text or attr.lower() in context_lower
            )
            score += min(focus_hits * 0.08, 0.24)

        if evidence.dimension == EvidenceDimension.user_feedback:
            score += 0.2 if any(k in context_lower for k in ["反馈", "评价", "review", "feedback"]) else 0.1
        elif evidence.dimension == EvidenceDimension.pricing:
            score += 0.2 if any(k in context_lower for k in ["价格", "定价", "price", "cost"]) else 0.1
        elif evidence.dimension == EvidenceDimension.feature:
            score += 0.2 if any(k in context_lower for k in ["功能", "特性", "feature", "风力", "续航"]) else 0.1

        if len(evidence.quote) > 100:
            score += 0.1

        return min(1.0, max(0.0, score))

    def _calculate_quality(self, evidence: Evidence) -> float:
        score = 0.5

        quote_length = len(evidence.quote)
        if quote_length > 200:
            score += 0.2
        elif quote_length > 50:
            score += 0.1

        claim_length = len(evidence.claim)
        if 30 < claim_length < 150:
            score += 0.15

        if evidence.confidence >= 0.7:
            score += 0.15
        elif evidence.confidence < 0.4:
            score -= 0.1

        if not evidence.untrusted:
            score += 0.1

        special_chars = re.findall(r'[<>{}[\]|\\]', evidence.quote)
        if len(special_chars) > 5:
            score -= 0.1

        return min(1.0, max(0.0, score))


evidence_scorer = EvidenceScorer()
