from __future__ import annotations

import os
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8",
        env_prefix="LLM_", 
        case_sensitive=False,
        extra="ignore"
    )
    
    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen-plus"
    timeout_seconds: int = 60
    max_retries: int = 2
    temperature: float = 0.2
    provider: str = "dashscope"
    
    # OpenAI 兼容配置
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o"
    
    @property
    def is_configured(self) -> bool:
        return bool(self.api_key.strip())
    
    @property
    def effective_api_key(self) -> str:
        """根据 provider 返回有效的 API Key"""
        if self.provider == "openai":
            return self.openai_api_key or self.api_key
        return self.api_key
    
    @property
    def effective_base_url(self) -> str:
        """根据 provider 返回有效的 Base URL"""
        if self.provider == "openai":
            return self.openai_base_url or self.base_url
        return self.base_url
    
    @property
    def effective_model(self) -> str:
        """根据 provider 返回有效的模型名称"""
        if self.provider == "openai":
            return self.openai_model or self.model
        return self.model


class DatabaseConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="DB_",
        case_sensitive=False,
        extra="ignore"
    )
    
    host: str = "localhost"
    port: int = 5432
    username: str = "postgres"
    password: str = "postgres"
    database: str = "ci_agent"
    use_sqlite: bool = True
    
    @property
    def url(self) -> str:
        if self.use_sqlite:
            return "sqlite+aiosqlite:///./data/ci_agent.db"
        return f"postgresql+asyncpg://{self.username}:{self.password}@{self.host}:{self.port}/{self.database}"


class SearchConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="SEARCH_",
        case_sensitive=False,
        extra="ignore"
    )
    
    api_key: str = ""
    provider: str = "serpapi"
    max_results: int = 5
    
    @property
    def is_configured(self) -> bool:
        return bool(self.api_key.strip())


class EmbeddingConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="EMBEDDING_",
        case_sensitive=False,
        extra="ignore"
    )

    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "text-embedding-v4"
    dimensions: int = 1024
    timeout_seconds: int = 30
    max_retries: int = 2
    batch_size: int = 10

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key.strip())


class MemoryConfig(BaseSettings):
    """决策记忆与混合检索的配置项。

    通过 MEMORY_ 前缀的环境变量覆盖默认值，例如：
      MEMORY_VECTOR_WEIGHT=0.8
      MEMORY_LEXICAL_WEIGHT=0.2
      MEMORY_RECALL_TOP_K=10
      MEMORY_HNSW_M=24
      MEMORY_FUSION_STRATEGY=rrf
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MEMORY_",
        case_sensitive=False,
        extra="ignore",
    )

    # 混合检索权重：final_score = vector * vector_weight + lexical * lexical_weight
    vector_weight: float = 0.7
    lexical_weight: float = 0.3
    fusion_strategy: str = "weighted"

    # 召回数量与候选倍数：每路召回 top_k * candidate_multiplier 条，融合后截断到 top_k
    recall_top_k: int = 5
    candidate_multiplier: int = 2

    # HNSW 索引参数：m=每层最大连接数，ef_construction=构建时搜索宽度，ef_search=查询时搜索宽度
    hnsw_m: int = 16
    hnsw_ef_construction: int = 64
    hnsw_ef_search: int = 40

    # 降级模式配置：当搜索API不可用时是否允许继续执行
    allow_degraded_mode: bool = True

    # ============ RAG 检索增强配置 ============

    # Rerank 重排序：对融合后的候选结果用 LLM 精排
    rerank_enabled: bool = False
    rerank_top_k: int = 5  # rerank 后返回的数量

    # 查询改写策略：none(不改写) / hyde(假设文档) / multi_query(多查询变体)
    query_rewrite_strategy: str = "none"

    # 相似度阈值过滤：低于此分数的结果被剔除（0.0 = 不过滤）
    min_similarity_threshold: float = 0.0

    # Embedding 缓存：避免相同文本重复调用 embedding API
    embedding_cache_enabled: bool = True
    embedding_cache_size: int = 1000  # LRU 缓存最大条目数


class AuthConfig(BaseSettings):
    """认证授权配置。

    通过 AUTH_ 前缀的环境变量覆盖默认值。
    生产环境必须设置 AUTH_JWT_SECRET（建议使用 `openssl rand -hex 32` 生成）。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="AUTH_",
        case_sensitive=False,
        extra="ignore",
    )

    # JWT 配置
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # API Key 配置
    api_key_prefix: str = "ci_"
    api_key_length: int = 32  # 明文 key 的随机部分长度

    # 认证开关：紧急情况下可设为 true 临时关闭认证（仅限调试）
    disabled: bool = False

    # 默认管理员配置（首次启动时自动创建）
    default_admin_username: str = "admin"
    default_admin_password: str = ""  # 留空则不创建默认管理员
    default_tenant_id: str = "default"

    @property
    def is_configured(self) -> bool:
        """生产环境必须配置 jwt_secret"""
        return bool(self.jwt_secret.strip())


class RateLimitConfig(BaseSettings):
    """速率限制配置。

    通过 RATE_LIMIT_ 前缀的环境变量覆盖默认值。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="RATE_LIMIT_",
        case_sensitive=False,
        extra="ignore",
    )

    # 默认限流：所有受保护接口
    default: str = "60/minute"
    # 任务创建：更严格
    task_create: str = "10/minute"
    # 文件上传：最严格
    upload: str = "20/minute"
    # 认证接口：防止暴力破解
    auth: str = "10/minute"


llm_settings = LLMConfig()
db_settings = DatabaseConfig()
search_settings = SearchConfig()
embedding_settings = EmbeddingConfig()
memory_settings = MemoryConfig()
auth_settings = AuthConfig()
rate_limit_settings = RateLimitConfig()
