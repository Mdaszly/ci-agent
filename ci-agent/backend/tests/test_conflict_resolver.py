"""冲突检测测试"""
import pytest

from app.models.schemas import (
    Claim,
    Conflict,
    Evidence,
    EvidenceDimension,
    SourceType,
    TaskCreateRequest,
    TaskRecord,
)
from app.worker.workflow import (
    _detect_conflicts,
    _is_conflicting,
    conflict_resolver,
    WorkflowState,
)


class TestConflictDetection:
    def test_no_conflict_single_claim(self):
        """单个 Claim 无冲突"""
        evidence = [
            Evidence(
                id="ev_1",
                source_type=SourceType.url,
                source_url="https://example.com",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.pricing,
                claim="价格 $9.99",
                quote="$9.99",
                confidence=0.85,
                freshness="recent",
                content_hash="hash1",
                credibility_score=0.7,
            )
        ]
        claims = [
            Claim(
                id="claim_1",
                statement="价格 $9.99",
                dimension=EvidenceDimension.pricing,
                competitor="TestCompetitor",
                evidence_ids=["ev_1"],
                confidence=0.85,
            )
        ]
        
        conflicts = _detect_conflicts(evidence, claims)
        assert len(conflicts) == 0

    def test_conflict_price_mismatch(self):
        """价格数字不一致产生冲突"""
        evidence = [
            Evidence(
                id="ev_1",
                source_type=SourceType.url,
                source_url="https://example.com/pricing",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.pricing,
                claim="价格 $9.99",
                quote="$9.99",
                confidence=0.85,
                freshness="recent",
                content_hash="hash1",
                credibility_score=0.7,
            ),
            Evidence(
                id="ev_2",
                source_type=SourceType.url,
                source_url="https://example.com/pricing",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.pricing,
                claim="价格 $49",
                quote="$49",
                confidence=0.85,
                freshness="recent",
                content_hash="hash2",
                credibility_score=0.5,
            )
        ]
        claims = [
            Claim(
                id="claim_1",
                statement="价格 $9.99",
                dimension=EvidenceDimension.pricing,
                competitor="TestCompetitor",
                evidence_ids=["ev_1"],
                confidence=0.85,
            ),
            Claim(
                id="claim_2",
                statement="价格 $49",
                dimension=EvidenceDimension.pricing,
                competitor="TestCompetitor",
                evidence_ids=["ev_2"],
                confidence=0.85,
            )
        ]
        
        conflicts = _detect_conflicts(evidence, claims)
        assert len(conflicts) == 1
        assert conflicts[0].claim_ids[0] == "claim_1"  # credibility 更高的被保留

    def test_conflict_fetch_success_vs_failure(self):
        """抓取成功 vs 抓取失败产生冲突"""
        evidence = [
            Evidence(
                id="ev_1",
                source_type=SourceType.url,
                source_url="https://example.com",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.feature,
                claim="功能信息",
                quote="功能内容",
                confidence=0.85,
                freshness="recent",
                content_hash="hash1",
                credibility_score=0.7,
            ),
            Evidence(
                id="ev_2",
                source_type=SourceType.url,
                source_url="https://example.com",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.feature,
                claim="页面抓取失败",
                quote="URL: https://example.com (抓取失败)",
                confidence=0.3,
                freshness="unknown",
                content_hash="hash2",
                credibility_score=0.3,
            )
        ]
        claims = [
            Claim(
                id="claim_1",
                statement="功能信息",
                dimension=EvidenceDimension.feature,
                competitor="TestCompetitor",
                evidence_ids=["ev_1"],
                confidence=0.85,
            ),
            Claim(
                id="claim_2",
                statement="页面抓取失败",
                dimension=EvidenceDimension.feature,
                competitor="TestCompetitor",
                evidence_ids=["ev_2"],
                confidence=0.3,
            )
        ]
        
        conflicts = _detect_conflicts(evidence, claims)
        assert len(conflicts) == 1

    def test_no_conflict_different_competitors(self):
        """不同竞品的 Claim 无冲突"""
        evidence = [
            Evidence(
                id="ev_1",
                source_type=SourceType.url,
                source_url="https://a.com",
                competitor="CompetitorA",
                dimension=EvidenceDimension.pricing,
                claim="价格 $9.99",
                quote="$9.99",
                confidence=0.85,
                freshness="recent",
                content_hash="hash1",
                credibility_score=0.7,
            ),
            Evidence(
                id="ev_2",
                source_type=SourceType.url,
                source_url="https://b.com",
                competitor="CompetitorB",
                dimension=EvidenceDimension.pricing,
                claim="价格 $49",
                quote="$49",
                confidence=0.85,
                freshness="recent",
                content_hash="hash2",
                credibility_score=0.5,
            )
        ]
        claims = [
            Claim(
                id="claim_1",
                statement="价格 $9.99",
                dimension=EvidenceDimension.pricing,
                competitor="CompetitorA",
                evidence_ids=["ev_1"],
                confidence=0.85,
            ),
            Claim(
                id="claim_2",
                statement="价格 $49",
                dimension=EvidenceDimension.pricing,
                competitor="CompetitorB",
                evidence_ids=["ev_2"],
                confidence=0.85,
            )
        ]
        
        conflicts = _detect_conflicts(evidence, claims)
        assert len(conflicts) == 0

    def test_no_conflict_different_dimensions(self):
        """不同维度的 Claim 无冲突"""
        evidence = [
            Evidence(
                id="ev_1",
                source_type=SourceType.url,
                source_url="https://example.com",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.pricing,
                claim="价格 $9.99",
                quote="$9.99",
                confidence=0.85,
                freshness="recent",
                content_hash="hash1",
                credibility_score=0.7,
            ),
            Evidence(
                id="ev_2",
                source_type=SourceType.text,
                competitor="TestCompetitor",
                dimension=EvidenceDimension.user_feedback,
                claim="用户反馈定价偏高",
                quote="定价偏高",
                confidence=0.68,
                freshness="user-provided",
                content_hash="hash2",
                credibility_score=0.5,
            )
        ]
        claims = [
            Claim(
                id="claim_1",
                statement="价格 $9.99",
                dimension=EvidenceDimension.pricing,
                competitor="TestCompetitor",
                evidence_ids=["ev_1"],
                confidence=0.85,
            ),
            Claim(
                id="claim_2",
                statement="用户反馈定价偏高",
                dimension=EvidenceDimension.user_feedback,
                competitor="TestCompetitor",
                evidence_ids=["ev_2"],
                confidence=0.68,
            )
        ]
        
        conflicts = _detect_conflicts(evidence, claims)
        assert len(conflicts) == 0

    def test_conflict_resolver_marks_rejected_evidence(self):
        """冲突裁决标记被否决的证据"""
        request = TaskCreateRequest(
            product_goal="Test product goal",
            competitors=["TestCompetitor"],
        )
        task = TaskRecord(request=request)
        
        task.evidence = [
            Evidence(
                id="ev_1",
                source_type=SourceType.url,
                source_url="https://example.com",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.pricing,
                claim="价格 $9.99",
                quote="$9.99",
                confidence=0.85,
                freshness="recent",
                content_hash="hash1",
                credibility_score=0.7,
            ),
            Evidence(
                id="ev_2",
                source_type=SourceType.url,
                source_url="https://example.com",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.pricing,
                claim="价格 $49",
                quote="$49",
                confidence=0.85,
                freshness="recent",
                content_hash="hash2",
                credibility_score=0.5,
            )
        ]
        task.claims = [
            Claim(
                id="claim_1",
                statement="价格 $9.99",
                dimension=EvidenceDimension.pricing,
                competitor="TestCompetitor",
                evidence_ids=["ev_1"],
                confidence=0.85,
            ),
            Claim(
                id="claim_2",
                statement="价格 $49",
                dimension=EvidenceDimension.pricing,
                competitor="TestCompetitor",
                evidence_ids=["ev_2"],
                confidence=0.85,
            )
        ]
        
        state = conflict_resolver({"task": task})
        
        assert len(state["task"].conflicts) == 1
        # 检查被否决的证据被标记
        rejected_ev = [ev for ev in state["task"].evidence if "已被冲突裁决否决" in ev.quote]
        assert len(rejected_ev) == 1

    def test_is_conflicting_same_price(self):
        """相同价格不冲突"""
        claim_a = Claim(
            id="claim_1",
            statement="价格 $9.99",
            dimension=EvidenceDimension.pricing,
            competitor="TestCompetitor",
            evidence_ids=["ev_1"],
            confidence=0.85,
        )
        claim_b = Claim(
            id="claim_2",
            statement="价格 $9.99",
            dimension=EvidenceDimension.pricing,
            competitor="TestCompetitor",
            evidence_ids=["ev_2"],
            confidence=0.85,
        )
        evidence = [
            Evidence(
                id="ev_1",
                source_type=SourceType.url,
                source_url="https://example.com",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.pricing,
                claim="价格 $9.99",
                quote="$9.99",
                confidence=0.85,
                freshness="recent",
                content_hash="hash1",
                credibility_score=0.7,
            ),
            Evidence(
                id="ev_2",
                source_type=SourceType.url,
                source_url="https://example.com",
                competitor="TestCompetitor",
                dimension=EvidenceDimension.pricing,
                claim="价格 $9.99",
                quote="$9.99",
                confidence=0.85,
                freshness="recent",
                content_hash="hash2",
                credibility_score=0.5,
            )
        ]
        
        assert not _is_conflicting(claim_a, claim_b, evidence)