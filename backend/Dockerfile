# Stage 1: builder - 安装依赖
FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml ./
RUN pip install --prefix=/install --no-cache-dir .

# Stage 2: runtime - 最终运行镜像
FROM python:3.12-slim

# 创建非 root 用户
RUN useradd -m -u 1000 appuser

WORKDIR /app

# 从 builder 复制已安装的依赖
COPY --from=builder /install /usr/local

# 复制应用代码
COPY app ./app

# 复制 Alembic 迁移配置和脚本
COPY alembic.ini ./
COPY alembic ./alembic/

# 复制启动脚本
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

# 切换到非 root 用户
USER appuser

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
