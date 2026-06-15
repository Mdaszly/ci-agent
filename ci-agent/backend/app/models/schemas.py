from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl, field_validator


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


class TaskCreateRequest(BaseModel):
    product_goal: str = Field(min_length=8, max_length=800)
    competitors: list[str] = Field(min_length=1, max_length=3)
    urls: list[HttpUrl] = Field(default_factory=list, max_length=5)
    comments: str | None = Field(default=None, max_length=10000)
    image_names: list[str] = Field(default_factory=list, max_length=3)
    budget: TaskBudget = Field(default_factory=TaskBudget)

    @field_validator("competitors")
    @classmethod
    def normalize_competitors(cls, competitors: list[str]) -> list[str]:
        cleaned = [item.strip() for item in competitors if item.strip()]
        if not cleaned:
            raise ValueError("至少需要一个竞品名称")
        return cleaned


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


class DecisionPack(BaseModel):
    id: str = Field(default_factory=lambda: new_id("decision"))
    positioning: list[DecisionAction]
    mvp_priorities: list[DecisionAction]
    pricing_insights: list[DecisionAction] = Field(default_factory=list)
    battlecard: list[DecisionAction] = Field(default_factory=list)
    summary: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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
