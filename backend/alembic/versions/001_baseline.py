"""baseline: 创建初始 4 张表（tasks/evidence/results/decision_memory_items）

Revision ID: 001
Revises:
Create Date: 2026-06-19

注意：
- 对于新部署：执行 `alembic upgrade head` 创建所有表
- 对于已有数据库：执行 `alembic stamp head` 标记当前版本，不实际执行迁移
- pgvector 的 embedding 列由 init_db() 动态添加，不在本基线迁移中
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # tasks 表
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("product_goal", sa.Text(), nullable=False, comment="产品目标"),
        sa.Column("competitors", sa.JSON(), nullable=False, comment="竞品列表"),
        sa.Column("urls", sa.JSON(), nullable=False, comment="URL列表"),
        sa.Column("comments", sa.Text(), nullable=True, comment="用户评论"),
        sa.Column("analysis_profile", sa.JSON(), nullable=True, comment="分析策略与权重配置"),
        sa.Column("image_names", sa.JSON(), nullable=False, comment="图片文件名列表"),
        sa.Column(
            "status",
            sa.Enum("queued", "running", "completed", "cancelled", "failed", name="taskstatus"),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("claims", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("conflicts", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("events", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("decision_history", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("memory_state", sa.JSON(), nullable=True),
        sa.Column("review", sa.JSON(), nullable=True),
        sa.Column("coverage", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_created_at", "tasks", ["created_at"])

    # evidence 表
    op.create_table(
        "evidence",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False, comment="关联任务ID"),
        sa.Column("source_type", sa.String(), nullable=False, comment="证据来源类型: url/text/image"),
        sa.Column("source_url", sa.Text(), nullable=True, comment="来源URL"),
        sa.Column("competitor", sa.String(), nullable=False, comment="竞品名称"),
        sa.Column("dimension", sa.String(), nullable=False, comment="维度"),
        sa.Column("claim", sa.Text(), nullable=False, comment="从原文提取的核心观点"),
        sa.Column("quote", sa.Text(), nullable=False, comment="原文引用"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column("freshness", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("media_ref", sa.String(), nullable=True),
        sa.Column("untrusted", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("content_hash", sa.String(), nullable=False, comment="内容哈希去重"),
        sa.Column("license_risk", sa.String(), nullable=False, server_default="medium"),
        sa.Column("credibility_score", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column("relevance_score", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column("quality_score", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evidence_task_id", "evidence", ["task_id"])
    op.create_index("ix_evidence_competitor", "evidence", ["competitor"])
    op.create_index("ix_evidence_dimension", "evidence", ["dimension"])
    op.create_index("ix_evidence_content_hash", "evidence", ["content_hash"])

    # results 表
    op.create_table(
        "results",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False, unique=True, comment="关联任务ID"),
        sa.Column("positioning", sa.JSON(), nullable=False, comment="定位建议列表"),
        sa.Column("mvp_priorities", sa.JSON(), nullable=False, comment="MVP优先级列表"),
        sa.Column("pricing_insights", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("battlecard", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("summary", sa.Text(), nullable=False, comment="决策摘要"),
        sa.Column("review_score", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column("citation_precision", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column("claim_support_rate", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column("hallucination_risk", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("review_notes", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("budget_usage", sa.JSON(), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_results_task_id", "results", ["task_id"])
    op.create_index("ix_results_generated_at", "results", ["generated_at"])

    # decision_memory_items 表
    op.create_table(
        "decision_memory_items",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False, comment="关联任务ID"),
        sa.Column("pack_id", sa.String(), nullable=False, comment="关联决策包ID"),
        sa.Column("version", sa.Integer(), nullable=False, comment="决策包版本"),
        sa.Column("chunk_type", sa.String(), nullable=False, comment="块类型"),
        sa.Column("stage", sa.String(), nullable=True, comment="产生该块的工作流阶段"),
        sa.Column("iteration", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("source_refs", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("summary", sa.String(length=1200), nullable=False, comment="摘要"),
        sa.Column("embedding_text", sa.Text(), nullable=False, comment="检索文本"),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_memory_task_version", "decision_memory_items", ["task_id", "version"])
    op.create_index("idx_memory_chunk_type", "decision_memory_items", ["chunk_type"])


def downgrade() -> None:
    op.drop_index("idx_memory_chunk_type", table_name="decision_memory_items")
    op.drop_table("decision_memory_items")
    op.drop_index("ix_results_generated_at", table_name="results")
    op.drop_index("ix_results_task_id", table_name="results")
    op.drop_table("results")
    op.drop_index("ix_evidence_content_hash", table_name="evidence")
    op.drop_index("ix_evidence_dimension", table_name="evidence")
    op.drop_index("ix_evidence_competitor", table_name="evidence")
    op.drop_index("ix_evidence_task_id", table_name="evidence")
    op.drop_table("evidence")
    op.drop_index("ix_tasks_created_at", table_name="tasks")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_table("tasks")
    # 删除 enum 类型（PostgreSQL）
    op.execute("DROP TYPE IF EXISTS taskstatus")
