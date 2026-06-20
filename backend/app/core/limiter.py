"""速率限制器模块。

独立模块化 limiter 实例，避免 main.py 与路由模块之间的循环导入。
所有需要应用 @limiter.limit 装饰器的模块都应从此处导入 limiter。
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

# 基于客户端 IP 的速率限制器实例
limiter = Limiter(key_func=get_remote_address)
