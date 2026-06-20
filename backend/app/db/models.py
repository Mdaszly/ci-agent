from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import JSON, Column, DateTime, Enum as SQLAlchemyEnum, Float, Index, Integer, String, Text, func
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
    coverage = Column(JSON, comment="覆盖门禁结果")
    
    # 工作流运行元数据与可恢复状态
    metrics = Column(JSON, comment="任务级指标快照")
    research_plan = Column(JSON, comment="研究计划快照")
    research_round = Column(Integer, nullable=False, default=0, comment="研究轮次")
    rewrite_round = Column(Integer, nullable=False, default=0, comment="修复轮次")
    run_id = Column(String, comment="当前工作流运行ID")
    thread_id = Column(String, comment="LangGraph 线程ID")
    request_id = Column(String, comment="请求ID")
    last_error_stage = Column(String, comment="最近错误阶段")
    last_error_message = Column(Text, comment="最近错误信息")
    last_error_traceback = Column(Text, comment="最近错误堆栈")
    last_checkpoint_id = Column(String, comment="最近检查点ID")


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
    pricing_insights = Column(JSON, nullable=False, default=list, comment="定价洞察列表")
    battlecard = Column(JSON, nullable=False, default=list, comment="对战卡列表")
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


class DecisionMemoryDB(Base):
    """决策记忆表 - 存储记忆块及其向量（pgvector 模式下含 embedding 列）"""
    __tablename__ = "decision_memory_items"

    id = Column(String, primary_key=True)
    task_id = Column(String, nullable=False, comment="关联任务ID")
    pack_id = Column(String, nullable=False, comment="关联决策包ID")
    version = Column(Integer, nullable=False, comment="决策包版本")
    chunk_type = Column(String, nullable=False, comment="块类型: decision/evidence/conflict/repair/reviewer_feedback")
    stage = Column(String, nullable=True, comment="产生该块的工作流阶段")
    iteration = Column(Integer, nullable=False, default=0, comment="回流迭代轮次")
    source_refs = Column(JSON, nullable=False, default=list, comment="来源引用列表")
    summary = Column(String(1200), nullable=False, comment="摘要")
    embedding_text = Column(Text, nullable=False, comment="检索文本")
    payload = Column(JSON, nullable=False, default=dict, comment="结构化负载")
    status = Column(String, nullable=False, default="draft", comment="状态: draft/approved/rejected/superseded")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")

    __table_args__ = (
        Index("idx_memory_task_version", "task_id", "version"),
        Index("idx_memory_chunk_type", "chunk_type"),
    )


class UserDB(Base):
    """用户表 - JWT 认证用户"""
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True, comment="租户ID")
    username = Column(String, nullable=False, unique=True, comment="用户名")
    password_hash = Column(String, nullable=False, comment="bcrypt 哈希")
    role = Column(String, nullable=False, default="user", comment="角色: user/admin")
    is_active = Column(Integer, nullable=False, default=1, comment="是否启用")
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class APIKeyDB(Base):
    """API Key 表 - 服务间/脚本调用认证"""
    __tablename__ = "api_keys"

    id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True, comment="租户ID")
    key_hash = Column(String, nullable=False, unique=True, comment="SHA256 哈希")
    key_prefix = Column(String, nullable=False, comment="前 8 位用于展示")
    name = Column(String, nullable=False, comment="用户自定义名称")
    scopes = Column(JSON, default=list, comment="权限范围")
    expires_at = Column(DateTime, nullable=True, comment="过期时间")
    is_revoked = Column(Integer, nullable=False, default=0, comment="是否已撤销")
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    last_used_at = Column(DateTime, nullable=True, comment="最后使用时间")


class BadCaseDB(Base):
    """坏案例表 - 持久化存储检测到的问题案例"""
    __tablename__ = "bad_cases"

    id = Column(String, primary_key=True)
    task_id = Column(String, nullable=True, index=True, comment="关联任务ID")
    type = Column(String, nullable=False, index=True, comment="案例类型")
    severity = Column(String, nullable=False, comment="严重度")
    status = Column(String, nullable=False, default="pending", index=True, comment="状态")
    description = Column(Text, nullable=False, comment="描述")
    context = Column(JSON, default=dict, comment="上下文")
    metrics = Column(JSON, default=dict, comment="指标")
    analysis = Column(Text, nullable=True, comment="分析")
    fix_plan = Column(Text, nullable=True, comment="修复计划")
    fixed_by = Column(String, nullable=True, comment="修复人")
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
