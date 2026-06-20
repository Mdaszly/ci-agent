"""add auth and bad_case tables

Revision ID: 002
Revises: 001
Create Date: 2026-06-19

新增表：
- users：JWT 认证用户
- api_keys：API Key 认证
- bad_cases：坏案例持久化
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # users 表
    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False, comment="租户ID"),
        sa.Column("username", sa.String(), nullable=False, comment="用户名"),
        sa.Column("password_hash", sa.String(), nullable=False, comment="bcrypt 哈希"),
        sa.Column("role", sa.String(), nullable=False, server_default="user", comment="角色"),
        sa.Column("is_active", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])
    op.create_index("ix_users_username", "users", ["username"])

    # api_keys 表
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False, comment="租户ID"),
        sa.Column("key_hash", sa.String(), nullable=False, comment="SHA256 哈希"),
        sa.Column("key_prefix", sa.String(), nullable=False, comment="前 8 位"),
        sa.Column("name", sa.String(), nullable=False, comment="名称"),
        sa.Column("scopes", sa.JSON(), server_default=sa.text("'[]'")),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("is_revoked", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
    )
    op.create_index("ix_api_keys_tenant_id", "api_keys", ["tenant_id"])
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"])

    # bad_cases 表
    op.create_table(
        "bad_cases",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=True, comment="关联任务ID"),
        sa.Column("type", sa.String(), nullable=False, comment="案例类型"),
        sa.Column("severity", sa.String(), nullable=False, comment="严重度"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending", comment="状态"),
        sa.Column("description", sa.Text(), nullable=False, comment="描述"),
        sa.Column("context", sa.JSON(), server_default=sa.text("'{}'")),
        sa.Column("metrics", sa.JSON(), server_default=sa.text("'{}'")),
        sa.Column("analysis", sa.Text(), nullable=True),
        sa.Column("fix_plan", sa.Text(), nullable=True),
        sa.Column("fixed_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bad_cases_task_id", "bad_cases", ["task_id"])
    op.create_index("ix_bad_cases_type", "bad_cases", ["type"])
    op.create_index("ix_bad_cases_status", "bad_cases", ["status"])


def downgrade() -> None:
    op.drop_index("ix_bad_cases_status", table_name="bad_cases")
    op.drop_index("ix_bad_cases_type", table_name="bad_cases")
    op.drop_index("ix_bad_cases_task_id", table_name="bad_cases")
    op.drop_table("bad_cases")

    op.drop_index("ix_api_keys_key_hash", table_name="api_keys")
    op.drop_index("ix_api_keys_tenant_id", table_name="api_keys")
    op.drop_table("api_keys")

    op.drop_index("ix_users_username", table_name="users")
    op.drop_index("ix_users_tenant_id", table_name="users")
    op.drop_table("users")
