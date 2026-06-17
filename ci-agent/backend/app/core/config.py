from __future__ import annotations

import os
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8",
        env_prefix="LLM_", 
        case_sensitive=False
    )
    
    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen-plus"
    timeout_seconds: int = 60
    max_retries: int = 2
    temperature: float = 0.2
    provider: str = "dashscope"
    
    @property
    def is_configured(self) -> bool:
        return bool(self.api_key.strip())


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


llm_settings = LLMConfig()
db_settings = DatabaseConfig()
search_settings = SearchConfig()
