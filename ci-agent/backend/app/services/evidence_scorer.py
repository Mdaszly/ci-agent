from __future__ import annotations

import logging
import re

from app.models.schemas import Evidence, EvidenceDimension

logger = logging.getLogger(__name__)


class EvidenceScorer:
    def score(self, evidence: Evidence, context: str = "") -> Evidence:
        evidence.credibility_score = self._calculate_credibility(evidence)
        evidence.relevance_score = self._calculate_relevance(evidence, context)
        evidence.quality_score = self._calculate_quality(evidence)
        
        return evidence
    
    def _calculate_credibility(self, evidence: Evidence) -> float:
        score = 0.5
        
        if evidence.source_type == "url":
            score += 0.2
            if evidence.source_url:
                if any(domain in evidence.source_url.lower() for domain in ["resumeworded.com", "kickresume.com", "linkedin.com"]):
                    score += 0.1
                if "https" in evidence.source_url:
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
    
    def _calculate_relevance(self, evidence: Evidence, context: str) -> float:
        score = 0.5
        
        context_lower = context.lower()
        evidence_text = (evidence.claim + " " + evidence.quote).lower()
        
        keywords = ["pricing", "price", "cost", "subscription", "free", "trial",
                    "feature", "function", "tool", "AI", "resume", "CV",
                    "review", "feedback", "rating", "user"]
        
        matched_keywords = sum(1 for kw in keywords if kw in evidence_text and kw in context_lower)
        score += matched_keywords * 0.08
        
        if evidence.dimension == EvidenceDimension.user_feedback:
            score += 0.2 if "feedback" in context_lower or "review" in context_lower else 0.1
        elif evidence.dimension == EvidenceDimension.pricing:
            score += 0.2 if "price" in context_lower or "cost" in context_lower else 0.1
        elif evidence.dimension == EvidenceDimension.feature:
            score += 0.2 if "feature" in context_lower or "function" in context_lower else 0.1
        
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
