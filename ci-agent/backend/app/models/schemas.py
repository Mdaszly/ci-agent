from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class SourceType(str, Enum):
    url = "url"
    text = "text"
    image = "image"
    video = "video"
    document = "document"


class EvidenceDimension(str, Enum):
    feature = "feature"
    pricing = "pricing"
    positioning = "positioning"
    user_feedback = "user_feedback"
    risk = "risk"


class TaskStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    cancelled = "cancelled"
    failed = "failed"


class TaskBudget(BaseModel):
    max_sources: int = Field(default=8, ge=1, le=30)
    max_tokens: int = Field(default=12000, ge=1000, le=100000)
    max_cost_usd: float = Field(default=1.0, ge=0.05, le=20)
    timeout_seconds: int = Field(default=90, ge=10, le=600)


class BudgetUsage(BaseModel):
    estimated_sources: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    within_budget: bool


class InputAsset(BaseModel):
    source_type: SourceType
    value: str = Field(min_length=1, max_length=12000)
    label: str | None = Field(default=None, max_length=120)


class CompetitorUrl(BaseModel):
    competitor: str = Field(min_length=1, max_length=120)
    url: HttpUrl


class AnalysisStrategy(str, Enum):
    cost_leadership = "cost_leadership"
    performance = "performance"
    hybrid = "hybrid"
    custom = "custom"


STRATEGY_LABELS: dict[AnalysisStrategy, str] = {
    AnalysisStrategy.cost_leadership: "定价优势",
    AnalysisStrategy.performance: "产品力优势",
    AnalysisStrategy.hybrid: "性价比导向",
    AnalysisStrategy.custom: "自定义权重",
}

DEFAULT_DIMENSION_WEIGHTS: dict[str, float] = {
    EvidenceDimension.feature.value: 0.30,
    EvidenceDimension.pricing.value: 0.25,
    EvidenceDimension.user_feedback.value: 0.25,
    EvidenceDimension.positioning.value: 0.10,
    EvidenceDimension.risk.value: 0.10,
}

STRATEGY_PRESETS: dict[AnalysisStrategy, dict[str, object]] = {
    AnalysisStrategy.cost_leadership: {
        "weights": {
            EvidenceDimension.feature.value: 0.20,
            EvidenceDimension.pricing.value: 0.40,
            EvidenceDimension.user_feedback.value: 0.25,
            EvidenceDimension.positioning.value: 0.10,
            EvidenceDimension.risk.value: 0.05,
        },
        "mandatory": [
            EvidenceDimension.pricing,
            EvidenceDimension.feature,
            EvidenceDimension.user_feedback,
        ],
    },
    AnalysisStrategy.performance: {
        "weights": {
            EvidenceDimension.feature.value: 0.35,
            EvidenceDimension.pricing.value: 0.15,
            EvidenceDimension.user_feedback.value: 0.30,
            EvidenceDimension.positioning.value: 0.10,
            EvidenceDimension.risk.value: 0.10,
        },
        "mandatory": [
            EvidenceDimension.feature,
            EvidenceDimension.user_feedback,
        ],
    },
    AnalysisStrategy.hybrid: {
        "weights": DEFAULT_DIMENSION_WEIGHTS.copy(),
        "mandatory": [
            EvidenceDimension.feature,
            EvidenceDimension.pricing,
            EvidenceDimension.user_feedback,
        ],
    },
    AnalysisStrategy.custom: {
        "weights": DEFAULT_DIMENSION_WEIGHTS.copy(),
        "mandatory": [
            EvidenceDimension.feature,
            EvidenceDimension.pricing,
            EvidenceDimension.user_feedback,
        ],
    },
}


class AnalysisProfile(BaseModel):
    strategy: AnalysisStrategy = AnalysisStrategy.hybrid
    dimension_weights: dict[str, float] = Field(default_factory=lambda: DEFAULT_DIMENSION_WEIGHTS.copy())
    focus_attributes: list[str] = Field(default_factory=list, max_length=8)
    our_product_hints: str | None = Field(default=None, max_length=500)

    @field_validator("focus_attributes")
    @classmethod
    def normalize_focus_attributes(cls, attributes: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in attributes:
            token = item.strip()
            if token and token not in seen:
                cleaned.append(token)
                seen.add(token)
        return cleaned[:8]

    @model_validator(mode="after")
    def validate_dimension_weights(self) -> AnalysisProfile:
        if self.strategy != AnalysisStrategy.custom:
            return self

        normalized: dict[str, float] = {}
        for dimension in EvidenceDimension:
            value = self.dimension_weights.get(dimension.value, 0.0)
            float_value = float(value)
            
            if float_value > 1.0:
                float_value = float_value / 100.0
            
            normalized[dimension.value] = min(1.0, max(0.0, float_value))

        total = sum(normalized.values())
        if total <= 0:
            raise ValueError("自定义权重总和必须大于 0")
        self.dimension_weights = {key: round(value / total, 4) for key, value in normalized.items()}
        return self

    def resolved_weights(self) -> dict[str, float]:
        if self.strategy == AnalysisStrategy.custom:
            return self.dimension_weights.copy()
        preset = STRATEGY_PRESETS[self.strategy]
        return dict(preset["weights"])  # type: ignore[arg-type]

    def mandatory_dimensions(self) -> list[EvidenceDimension]:
        if self.strategy == AnalysisStrategy.custom:
            weights = self.resolved_weights()
            ranked = sorted(weights.items(), key=lambda item: item[1], reverse=True)
            top = [EvidenceDimension(key) for key, weight in ranked[:3] if weight > 0]
            return top or [
                EvidenceDimension.feature,
                EvidenceDimension.pricing,
                EvidenceDimension.user_feedback,
            ]
        preset = STRATEGY_PRESETS[self.strategy]
        return list(preset["mandatory"])  # type: ignore[arg-type]

    def strategy_label(self) -> str:
        return STRATEGY_LABELS[self.strategy]


class TaskCreateRequest(BaseModel):
    product_goal: str = Field(min_length=8, max_length=800)
    competitors: list[str] = Field(min_length=1, max_length=3)
    urls: list[HttpUrl] = Field(default_factory=list, max_length=5)
    competitor_urls: list[CompetitorUrl] = Field(default_factory=list, max_length=5)
    comments: str | None = Field(default=None, max_length=10000)
    image_names: list[str] = Field(default_factory=list, max_length=3)
    analysis_profile: AnalysisProfile = Field(default_factory=AnalysisProfile)
    budget: TaskBudget = Field(default_factory=TaskBudget)

    @field_validator("competitors")
    @classmethod
    def normalize_competitors(cls, competitors: list[str]) -> list[str]:
        cleaned = [item.strip() for item in competitors if item.strip()]
        if not cleaned:
            raise ValueError("至少需要一个竞品名称")
        return cleaned

    def get_url_bindings(self) -> list[tuple[str, str]]:
        """返回 (竞品名, URL) 绑定列表；优先使用 competitor_urls。"""
        if self.competitor_urls:
            return [(item.competitor.strip(), str(item.url)) for item in self.competitor_urls]
        return [
            (self.competitors[index % len(self.competitors)], str(url))
            for index, url in enumerate(self.urls)
        ]

    def count_sources(self) -> int:
        return len(self.get_url_bindings()) + (1 if self.comments else 0) + len(self.image_names)

    @model_validator(mode="after")
    def validate_competitor_url_bindings(self) -> TaskCreateRequest:
        normalized = {item.strip() for item in self.competitors}
        for binding in self.competitor_urls:
            if binding.competitor.strip() not in normalized:
                raise ValueError(f"URL 绑定的竞品 '{binding.competitor}' 不在竞品列表中")
        return self


class Evidence(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ev"))
    source_type: SourceType
    source_url: str | None = None
    competitor: str
    dimension: EvidenceDimension
    claim: str
    quote: str
    confidence: float = Field(ge=0, le=1)
    freshness: str
    media_ref: str | None = None
    untrusted: bool = True
    content_hash: str
    license_risk: Literal["low", "medium", "high"] = "medium"
    credibility_score: float = Field(default=0.5, ge=0, le=1)
    relevance_score: float = Field(default=0.5, ge=0, le=1)
    quality_score: float = Field(default=0.5, ge=0, le=1)


class Claim(BaseModel):
    id: str = Field(default_factory=lambda: new_id("claim"))
    statement: str
    dimension: EvidenceDimension
    competitor: str
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    conflict_ids: list[str] = Field(default_factory=list)


class Conflict(BaseModel):
    id: str = Field(default_factory=lambda: new_id("conflict"))
    claim_ids: list[str] = Field(min_length=2)
    resolution: str
    rationale: str
    confidence: float = Field(ge=0, le=1)


class CoverageGateResult(BaseModel):
    passed: bool
    score: float = Field(ge=0, le=1)
    covered_dimensions: list[EvidenceDimension]
    missing_dimensions: list[EvidenceDimension]
    gap_queries: list[str]


class DecisionAction(BaseModel):
    title: str
    dimension: EvidenceDimension
    recommendation: str
    rationale: str
    evidence_ids: list[str] = Field(min_length=1)
    priority: Literal["P0", "P1", "P2"]


class DecisionPackStatus(str, Enum):
    draft = "draft"
    approved = "approved"
    rejected = "rejected"
    superseded = "superseded"


class DecisionChunkType(str, Enum):
    decision = "decision"
    evidence = "evidence"
    conflict = "conflict"
    repair = "repair"
    reviewer_feedback = "reviewer_feedback"


class DecisionPackVersion(BaseModel):
    pack_id: str = Field(default_factory=lambda: new_id("pack"))
    version: int = Field(ge=1)
    parent_pack_id: str | None = None
    superseded_by: str | None = None
    status: DecisionPackStatus = DecisionPackStatus.draft
    task_id: str | None = None
    stage: str | None = None
    iteration: int = Field(default=0, ge=0)
    source_refs: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "medium"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DecisionMemoryItem(BaseModel):
    id: str = Field(default_factory=lambda: new_id("mem"))
    task_id: str
    pack_id: str
    version: int = Field(ge=1)
    chunk_type: DecisionChunkType
    stage: str | None = None
    iteration: int = Field(default=0, ge=0)
    source_refs: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1, max_length=1200)
    embedding_text: str = Field(min_length=1, max_length=8000)
    payload: dict[str, object] = Field(default_factory=dict)
    status: DecisionPackStatus = DecisionPackStatus.draft
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DecisionPack(BaseModel):
    id: str = Field(default_factory=lambda: new_id("decision"))
    positioning: list[DecisionAction]
    mvp_priorities: list[DecisionAction]
    pricing_insights: list[DecisionAction] = Field(default_factory=list)
    battlecard: list[DecisionAction] = Field(default_factory=list)
    summary: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version_meta: DecisionPackVersion | None = None
    source_refs: list[str] = Field(default_factory=list)
    version: int = Field(default=1, ge=1)
    parent_pack_id: str | None = None
    superseded_by: str | None = None
    status: DecisionPackStatus = DecisionPackStatus.draft


class ReviewStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    needs_retry = "needs_retry"


class WorkflowMemoryState(BaseModel):
    current_pack_version: int = Field(default=1, ge=1)
    max_iterations: int = Field(default=3, ge=1, le=10)
    current_iteration: int = Field(default=0, ge=0)
    latest_memory_ids: list[str] = Field(default_factory=list)
    last_recall_count: int = Field(default=0, ge=0)
    last_recall_summary: str | None = None
    last_reviewer_status: ReviewStatus = ReviewStatus.pending
    retry_reason: str | None = None


class ReviewScore(BaseModel):
    score: float = Field(ge=0, le=1)
    citation_precision: float = Field(ge=0, le=1)
    claim_support_rate: float = Field(ge=0, le=1)
    hallucination_risk: Literal["low", "medium", "high"]
    notes: list[str]


class TaskEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("event"))
    task_id: str
    stage: str
    message: str
    status: TaskStatus
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResearchTask(BaseModel):
    competitor: str
    source_type: Literal["url", "search", "comment", "image"]
    query_or_url: str
    dimension: EvidenceDimension
    priority: int = 1


class ResearchPlan(BaseModel):
    tasks: list[ResearchTask]
    dimensions: list[EvidenceDimension]
    keywords: list[str]


class TaskMetrics(BaseModel):
    total_duration_ms: int = Field(ge=0, description="任务总耗时，毫秒")
    stage_durations: dict[str, int] = Field(default_factory=dict, description="各阶段耗时，毫秒")
    evidence_count: int = Field(ge=0, description="证据数量")
    conflict_count: int = Field(ge=0, description="冲突数量")
    intervention_count: int = Field(ge=0, description="干预次数")


class TaskRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    request: TaskCreateRequest
    status: TaskStatus = TaskStatus.queued
    evidence: list[Evidence] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    coverage: CoverageGateResult | None = None
    decision_pack: DecisionPack | None = None
    decision_history: list[DecisionPackVersion] = Field(default_factory=list)
    memory_state: WorkflowMemoryState | None = None
    review: ReviewScore | None = None
    budget_usage: BudgetUsage | None = None
    research_plan: ResearchPlan | None = None
    events: list[TaskEvent] = Field(default_factory=list)
    metrics: TaskMetrics | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    research_round: int = 0
    rewrite_round: int = 0


class InterventionRequest(BaseModel):
    target: Literal["task", "evidence", "decision"]
    target_id: str
    action: Literal["approve", "reject", "revise", "force_rerun"]
    reason: str = Field(min_length=4, max_length=500)
    stage: Literal["writer", "reviewer"] | None = Field(default=None, description="强制重跑的目标阶段，仅当 action=force_rerun 时有效")


class ErrorResponse(BaseModel):
    detail: str
