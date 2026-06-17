from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import JSON, Column, DateTime, Enum as SQLAlchemyEnum, Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class TaskStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    cancelled = "cancelled"
    failed = "failed"


class TaskDB(Base):
    """任务主表 - 存储分析任务的核心信息"""
    __tablename__ = "tasks"
    
    id = Column(String, primary_key=True)
    product_goal = Column(Text, nullable=False, comment="产品目标")
    competitors = Column(JSON, nullable=False, comment="竞品列表")
    urls = Column(JSON, nullable=False, comment="URL列表")
    comments = Column(Text, comment="用户评论")
    analysis_profile = Column(JSON, comment="分析策略与权重配置")
    image_names = Column(JSON, nullable=False, comment="图片文件名列表")
    status = Column(SQLAlchemyEnum(TaskStatus), nullable=False, default=TaskStatus.queued)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    
    # 中间状态（JSON存储，调试用）
    claims = Column(JSON, nullable=False, default=list, comment="从证据中提取的观点")
    conflicts = Column(JSON, nullable=False, default=list, comment="证据之间的矛盾")
    events = Column(JSON, nullable=False, default=list, comment="任务执行日志")
    decision_history = Column(JSON, nullable=False, default=list, comment="决策包历史版本")
    memory_state = Column(JSON, comment="工作流回流状态")
    review = Column(JSON, comment="当前复核结果")
    
    # 覆盖检查结果
    coverage = Column(JSON, comment="维度覆盖检查结果")
    
    __table_args__ = (
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_created_at", "created_at"),
    )


class EvidenceDB(Base):
    """证据表 - 存储抓取到的竞品信息"""
    __tablename__ = "evidence"
    
    id = Column(String, primary_key=True)
    task_id = Column(String, nullable=False, comment="关联任务ID")
    source_type = Column(String, nullable=False, comment="证据来源类型: url/text/image")
    source_url = Column(Text, comment="来源URL")
    competitor = Column(String, nullable=False, comment="竞品名称")
    dimension = Column(String, nullable=False, comment="维度: feature/pricing/positioning/user_feedback")
    claim = Column(Text, nullable=False, comment="从原文提取的核心观点")
    quote = Column(Text, nullable=False, comment="原文引用")
    confidence = Column(Float, nullable=False, default=0.5, comment="可信度评分")
    freshness = Column(String, nullable=False, default="unknown", comment="数据时效性")
    media_ref = Column(String, comment="媒体引用")
    untrusted = Column(Integer, nullable=False, default=1, comment="是否可信")
    content_hash = Column(String, nullable=False, comment="内容哈希去重")
    license_risk = Column(String, nullable=False, default="medium", comment="版权风险")
    
    # 细粒度评分
    credibility_score = Column(Float, nullable=False, default=0.5, comment="可信度评分")
    relevance_score = Column(Float, nullable=False, default=0.5, comment="相关性评分")
    quality_score = Column(Float, nullable=False, default=0.5, comment="质量评分")
    
    __table_args__ = (
        Index("ix_evidence_task_id", "task_id"),
        Index("ix_evidence_competitor", "competitor"),
        Index("ix_evidence_dimension", "dimension"),
        Index("ix_evidence_content_hash", "content_hash"),
    )


class ResultDB(Base):
    """结果表 - 存储最终的决策包和复核评分"""
    __tablename__ = "results"
    
    id = Column(String, primary_key=True)
    task_id = Column(String, nullable=False, unique=True, comment="关联任务ID")
    
    # 决策包（核心输出）
    positioning = Column(JSON, nullable=False, comment="定位建议列表")
    mvp_priorities = Column(JSON, nullable=False, comment="MVP优先级列表")
    summary = Column(Text, nullable=False, comment="决策摘要")
    
    # 复核评分
    review_score = Column(Float, nullable=False, default=0.0, comment="综合评分")
    citation_precision = Column(Float, nullable=False, default=0.0, comment="引用精度")
    claim_support_rate = Column(Float, nullable=False, default=0.0, comment="主张支持率")
    hallucination_risk = Column(String, nullable=False, default="unknown", comment="幻觉风险")
    review_notes = Column(JSON, nullable=False, default=list, comment="复核备注")
    
    # 成本估算
    budget_usage = Column(JSON, comment="预算使用情况")
    
    generated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    
    __table_args__ = (
        Index("ix_results_task_id", "task_id"),
        Index("ix_results_generated_at", "generated_at"),
    )
