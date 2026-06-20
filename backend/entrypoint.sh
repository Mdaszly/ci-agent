#!/bin/sh
set -e

echo "Starting ci-agent backend..."

# 自动执行数据库迁移（失败时记录日志但继续启动，避免迁移问题阻断服务）
echo "Running database migrations..."
alembic upgrade head || echo "WARNING: Database migration failed, continuing startup..."

# 启动应用前先检查 8000 是否已被占用
exec python -m app.run_backend --host 0.0.0.0 --port 8000
