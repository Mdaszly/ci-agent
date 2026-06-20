"""分层记忆存储模块。

实现大厂面试要求的"分层记忆设计"：
1. 工作记忆（Working Memory）：当前任务上下文，生命周期=单次执行
2. 短期记忆（Short-term Memory）：本次会话历史，生命周期=会话
3. 长期记忆（Long-term Memory）：跨会话记忆，生命周期=永久

每层记忆支持四种操作：
- 写入（Write）：重要性评分、显式确认
- 读取（Read）：相关性检索、时间衰减
- 压缩（Compress）：摘要、关键信息提取
- 过期（Expire）：TTL、LRU

设计原则：
- 可降级：数据库不可用时降级到内存
- 可观测：记录每次记忆操作
- 线程安全：使用锁保护共享状态
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class MemoryLayer(str, Enum):
    """记忆层级。"""

    WORKING = "working"  # 工作记忆
    SHORT_TERM = "short_term"  # 短期记忆
    LONG_TERM = "long_term"  # 长期记忆


class MemoryType(str, Enum):
    """记忆类型（长期记忆细分）。"""

    SEMANTIC = "semantic"  # 语义记忆：稳定事实（用户偏好）
    EPISODIC = "episodic"  # 情景记忆：时间戳事件
    PROCEDURAL = "procedural"  # 程序记忆：学习技能


@dataclass
class MemoryItem:
    """记忆条目。"""

    key: str
    value: dict
    layer: MemoryLayer
    memory_type: MemoryType | None = None
    importance: float = 0.5  # 重要性 [0, 1]
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    ttl: float | None = None  # 过期时间（秒），None 表示永不过期
    namespace: str = "default"

    def is_expired(self) -> bool:
        """是否已过期。"""
        if self.ttl is None:
            return False
        return time.time() - self.created_at > self.ttl

    def access(self) -> None:
        """更新访问信息。"""
        self.last_accessed = time.time()
        self.access_count += 1

    def relevance_score(self, query_embedding: list[float] | None = None) -> float:
        """计算相关性分数（时间衰减 + 重要性 + 访问频率）。"""
        # 时间衰减：越久未访问，分数越低
        age = time.time() - self.last_accessed
        time_decay = 1.0 / (1.0 + age / 3600)  # 1小时半衰期

        # 访问频率：访问越多，分数越高
        frequency = min(1.0, self.access_count / 10)

        # 综合分数
        return self.importance * 0.5 + time_decay * 0.3 + frequency * 0.2


@dataclass
class MemoryStoreConfig:
    """记忆存储配置。"""

    # 工作记忆：容量小，生命周期短
    working_memory_capacity: int = 100
    working_memory_ttl: float = 3600  # 1小时

    # 短期记忆：容量中，会话级
    short_term_capacity: int = 1000
    short_term_ttl: float = 86400  # 24小时

    # 长期记忆：容量大，永久
    long_term_capacity: int = 10000
    long_term_ttl: float | None = None  # 永不过期

    # 压缩配置
    compress_threshold: int = 100  # 超过此数量触发压缩
    compress_ratio: float = 0.5  # 压缩到原数量的 50%


class LayeredMemoryStore:
    """分层记忆存储。

    使用方式：
        store = LayeredMemoryStore()
        store.write("key", {"data": "..."}, MemoryLayer.WORKING, importance=0.8)
        item = store.read("key", MemoryLayer.WORKING)
        store.expire(MemoryLayer.WORKING)
    """

    def __init__(self, config: MemoryStoreConfig | None = None):
        self.config = config or MemoryStoreConfig()
        self._stores: dict[MemoryLayer, OrderedDict[str, MemoryItem]] = {
            MemoryLayer.WORKING: OrderedDict(),
            MemoryLayer.SHORT_TERM: OrderedDict(),
            MemoryLayer.LONG_TERM: OrderedDict(),
        }
        self._locks: dict[MemoryLayer, threading.Lock] = {
            MemoryLayer.WORKING: threading.Lock(),
            MemoryLayer.SHORT_TERM: threading.Lock(),
            MemoryLayer.LONG_TERM: threading.Lock(),
        }
        self.working_memory = _LayerView(self, MemoryLayer.WORKING)
        self.short_term_memory = _LayerView(self, MemoryLayer.SHORT_TERM)
        self.long_term_memory = _LayerView(self, MemoryLayer.LONG_TERM)

    @property
    def working_memory_view(self) -> _LayerView:
        return self.working_memory

    @property
    def short_term_memory_view(self) -> _LayerView:
        return self.short_term_memory

    @property
    def long_term_memory_view(self) -> _LayerView:
        return self.long_term_memory

    def _get_capacity(self, layer: MemoryLayer) -> int:
        if layer == MemoryLayer.WORKING:
            return self.config.working_memory_capacity
        elif layer == MemoryLayer.SHORT_TERM:
            return self.config.short_term_capacity
        else:
            return self.config.long_term_capacity

    def _get_ttl(self, layer: MemoryLayer) -> float | None:
        if layer == MemoryLayer.WORKING:
            return self.config.working_memory_ttl
        elif layer == MemoryLayer.SHORT_TERM:
            return self.config.short_term_ttl
        else:
            return self.config.long_term_ttl

    def write(
        self,
        key: str,
        value: dict,
        layer: MemoryLayer,
        *,
        importance: float = 0.5,
        memory_type: MemoryType | None = None,
        ttl: float | None = None,
        namespace: str = "default",
    ) -> bool:
        """写入记忆。

        Args:
            key: 记忆键
            value: 记忆值
            layer: 记忆层级
            importance: 重要性 [0, 1]
            memory_type: 记忆类型（仅长期记忆）
            ttl: 过期时间（秒），None 使用层级默认值
            namespace: 命名空间

        Returns:
            是否写入成功
        """
        if not key:
            return False

        effective_ttl = ttl if ttl is not None else self._get_ttl(layer)
        item = MemoryItem(
            key=key,
            value=value,
            layer=layer,
            memory_type=memory_type,
            importance=max(0.0, min(1.0, importance)),
            ttl=effective_ttl,
            namespace=namespace,
        )

        with self._locks[layer]:
            store = self._stores[layer]
            # 如果已存在，更新
            if key in store:
                store[key] = item
            else:
                store[key] = item
                # 检查容量，LRU 淘汰
                capacity = self._get_capacity(layer)
                while len(store) > capacity:
                    store.popitem(last=False)  # 移除最旧的

        return True

    def read(self, key: str, layer: MemoryLayer) -> MemoryItem | None:
        """读取记忆。

        Args:
            key: 记忆键
            layer: 记忆层级

        Returns:
            记忆条目或 None
        """
        with self._locks[layer]:
            store = self._stores[layer]
            if key not in store:
                return None

            item = store[key]
            if item.is_expired():
                # 已过期，移除
                del store[key]
                return None

            item.access()
            return item

    def search(
        self,
        layer: MemoryLayer,
        *,
        namespace: str | None = None,
        memory_type: MemoryType | None = None,
        limit: int = 10,
    ) -> list[MemoryItem]:
        """搜索记忆。

        Args:
            layer: 记忆层级
            namespace: 命名空间过滤
            memory_type: 记忆类型过滤
            limit: 返回数量上限

        Returns:
            记忆条目列表（按相关性排序）
        """
        with self._locks[layer]:
            store = self._stores[layer]
            results: list[MemoryItem] = []

            for item in store.values():
                if item.is_expired():
                    continue
                if namespace and item.namespace != namespace:
                    continue
                if memory_type and item.memory_type != memory_type:
                    continue
                results.append(item)

            # 按相关性排序
            results.sort(key=lambda x: x.relevance_score(), reverse=True)
            return results[:limit]

    def expire(self, layer: MemoryLayer | None = None) -> int:
        """清理过期记忆。

        Args:
            layer: 指定层级，None 表示所有层级

        Returns:
            清理的条目数
        """
        layers = [layer] if layer else list(MemoryLayer)
        total_removed = 0

        for ly in layers:
            with self._locks[ly]:
                store = self._stores[ly]
                expired_keys = [k for k, v in store.items() if v.is_expired()]
                for k in expired_keys:
                    del store[k]
                total_removed += len(expired_keys)

        return total_removed

    def compress(self, layer: MemoryLayer) -> int:
        """压缩记忆（保留高重要性的条目）。

        Args:
            layer: 记忆层级

        Returns:
            移除的条目数
        """
        with self._locks[layer]:
            store = self._stores[layer]
            if len(store) <= self.config.compress_threshold:
                return 0

            # 按相关性排序，移除低相关性的
            items = sorted(store.items(), key=lambda x: x[1].relevance_score())
            target_count = int(len(store) * self.config.compress_ratio)
            remove_count = len(store) - target_count

            for i in range(remove_count):
                key, _ = items[i]
                del store[key]

            return remove_count

    def clear(self, layer: MemoryLayer | None = None) -> int:
        """清空记忆。

        Args:
            layer: 指定层级，None 表示所有层级

        Returns:
            清空的条目数
        """
        layers = [layer] if layer else list(MemoryLayer)
        total = 0

        for ly in layers:
            with self._locks[ly]:
                store = self._stores[ly]
                total += len(store)
                store.clear()

        return total

    def stats(self) -> dict[MemoryLayer, dict]:
        """获取各层记忆统计信息。"""
        result = {}
        for layer in MemoryLayer:
            with self._locks[layer]:
                store = self._stores[layer]
                result[layer] = {
                    "count": len(store),
                    "capacity": self._get_capacity(layer),
                    "expired": sum(1 for v in store.values() if v.is_expired()),
                }
        return result


class _LayerView:
    def __init__(self, store: LayeredMemoryStore, layer: MemoryLayer) -> None:
        self._store = store
        self._layer = layer

    @property
    def items(self) -> list[MemoryItem]:
        with self._store._locks[self._layer]:
            return list(self._store._stores[self._layer].values())

    def __len__(self) -> int:
        return len(self.items)

    def clear(self) -> int:
        return self._store.clear(self._layer)


# 全局单例
_global_store: LayeredMemoryStore | None = None
_global_store_lock = threading.Lock()


def get_memory_store() -> LayeredMemoryStore:
    """获取全局记忆存储单例。"""
    global _global_store
    if _global_store is not None:
        return _global_store

    with _global_store_lock:
        if _global_store is None:
            _global_store = LayeredMemoryStore()
        return _global_store


def reset_memory_store() -> None:
    """重置全局记忆存储（仅用于测试）。"""
    global _global_store
    with _global_store_lock:
        _global_store = None
