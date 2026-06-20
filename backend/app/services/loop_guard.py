"""Agent 死循环三层防御模块。

实现字节真题要求的"三层防御"：
1. 运行时硬限制（max_iterations）
2. 状态重复检测（状态哈希）
3. 语义相似度阻断（embedding 相似度）

设计原则：
- 无状态：每个 guard 实例对应一个 thread_id 的执行会话
- 可降级：embedding 服务不可用时降级到仅哈希检测
- 可观测：每次阻断都记录原因
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class LoopGuardConfig:
    """死循环防御配置。"""

    # 第一层：运行时硬限制
    max_iterations: int = 25  # 与 LangGraph recursion_limit 对齐
    # 第二层：状态重复检测
    max_state_repeats: int = 3  # 同一状态哈希允许出现的最大次数
    state_history_size: int = 10  # 保留最近 N 个状态哈希
    # 第三层：语义相似度阻断
    semantic_block_enabled: bool = True
    semantic_similarity_threshold: float = 0.95  # 相似度高于此值视为重复
    semantic_history_size: int = 5  # 保留最近 N 个状态的 embedding


@dataclass
class LoopBlockResult:
    """阻断检测结果。"""

    blocked: bool
    layer: str = ""  # hard_limit | state_repeat | semantic_block
    reason: str = ""
    iteration: int = 0
    current_hash: str = ""
    repeat_count: int = 0
    similarity: float = 0.0


class LoopGuard:
    """三层死循环防御。

    使用方式：
        guard = LoopGuard(thread_id="task-123")
        for step in range(max_steps):
            result = guard.check(state_dict, state_text_for_embedding)
            if result.blocked:
                logger.warning(f"死循环阻断: {result.layer} - {result.reason}")
                break
            # ... 执行节点逻辑
    """

    def __init__(self, thread_id: str, config: LoopGuardConfig | None = None):
        self.thread_id = thread_id
        self.config = config or LoopGuardConfig()
        self._iteration = 0
        self._state_hashes: deque[str] = deque(maxlen=self.config.state_history_size)
        self._hash_counts: dict[str, int] = {}
        self._semantic_embeddings: deque[list[float]] = deque(maxlen=self.config.semantic_history_size)
        self._lock = threading.Lock()

    def _compute_state_hash(self, state: Any) -> str:
        """计算状态的哈希值（用于重复检测）。

        使用 repr + sha256，避免不可哈希的嵌套结构。
        """
        try:
            # 尝试 model_dump（Pydantic）
            if hasattr(state, "model_dump"):
                state = state.model_dump(mode="json")
            # 尝试 dict 转换
            if hasattr(state, "__dict__"):
                state = state.__dict__
            content = repr(sorted(state.items())) if isinstance(state, dict) else repr(state)
        except Exception:
            content = str(state)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def _compute_semantic_similarity(self, text: str) -> float:
        """计算当前文本与历史文本的最大余弦相似度。

        降级策略：embedding 服务不可用时返回 0.0（不阻断）。
        """
        if not self.config.semantic_block_enabled or not text.strip():
            return 0.0

        try:
            from app.services.embedding import embedding_client

            if embedding_client is None or not embedding_client.is_configured():
                return 0.0

            current_emb = embedding_client.embed_sync(text)
            if current_emb is None:
                return 0.0

            max_sim = 0.0
            for hist_emb in self._semantic_embeddings:
                sim = _cosine_similarity(current_emb, hist_emb)
                if sim > max_sim:
                    max_sim = sim
            return max_sim
        except Exception as e:
            logger.debug(f"语义相似度计算失败，跳过: {e}")
            return 0.0

    def check(self, state: Any, state_text: str = "") -> LoopBlockResult:
        """执行三层防御检查。

        Args:
            state: 当前状态（用于哈希检测）
            state_text: 状态的文本表示（用于语义相似度检测）

        Returns:
            LoopBlockResult：如果 blocked=True 则应终止循环
        """
        with self._lock:
            self._iteration += 1
            current_hash = self._compute_state_hash(state)

            # 第一层：运行时硬限制
            if self._iteration > self.config.max_iterations:
                return LoopBlockResult(
                    blocked=True,
                    layer="hard_limit",
                    reason=f"超过最大迭代次数 {self.config.max_iterations}",
                    iteration=self._iteration,
                    current_hash=current_hash,
                )

            # 第二层：状态重复检测
            self._state_hashes.append(current_hash)
            self._hash_counts[current_hash] = self._hash_counts.get(current_hash, 0) + 1
            repeat_count = self._hash_counts[current_hash]
            if repeat_count > self.config.max_state_repeats:
                return LoopBlockResult(
                    blocked=True,
                    layer="state_repeat",
                    reason=f"状态哈希 {current_hash} 重复出现 {repeat_count} 次，超过阈值 {self.config.max_state_repeats}",
                    iteration=self._iteration,
                    current_hash=current_hash,
                    repeat_count=repeat_count,
                )

            # 第三层：语义相似度阻断
            similarity = 0.0
            if self.config.semantic_block_enabled and state_text:
                similarity = self._compute_semantic_similarity(state_text)
                if similarity > self.config.semantic_similarity_threshold:
                    # 记录当前 embedding 用于后续比较
                    try:
                        from app.services.embedding import embedding_client

                        if embedding_client and embedding_client.is_configured():
                            emb = embedding_client.embed_sync(state_text)
                            if emb is not None:
                                self._semantic_embeddings.append(emb)
                    except Exception:
                        pass
                    return LoopBlockResult(
                        blocked=True,
                        layer="semantic_block",
                        reason=f"语义相似度 {similarity:.3f} 超过阈值 {self.config.semantic_similarity_threshold}",
                        iteration=self._iteration,
                        current_hash=current_hash,
                        similarity=similarity,
                    )

                # 未阻断，记录 embedding
                try:
                    from app.services.embedding import embedding_client

                    if embedding_client and embedding_client.is_configured():
                        emb = embedding_client.embed_sync(state_text)
                        if emb is not None:
                            self._semantic_embeddings.append(emb)
                except Exception:
                    pass

            return LoopBlockResult(
                blocked=False,
                iteration=self._iteration,
                current_hash=current_hash,
                repeat_count=repeat_count,
                similarity=similarity,
            )

    def reset(self) -> None:
        """重置 guard 状态（用于测试或重新开始会话）。"""
        with self._lock:
            self._iteration = 0
            self._state_hashes.clear()
            self._hash_counts.clear()
            self._semantic_embeddings.clear()

    @property
    def iteration(self) -> int:
        return self._iteration


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度。"""
    if len(a) != len(b) or len(a) == 0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ============ 全局 Guard 注册表（按 thread_id 管理） ============

_guards: dict[str, LoopGuard] = {}
_guards_lock = threading.Lock()


def get_loop_guard(thread_id: str, config: LoopGuardConfig | None = None) -> LoopGuard:
    """获取或创建指定 thread 的 LoopGuard。

    Args:
        thread_id: 会话 ID（通常为 task.id）
        config: 可选配置，仅首次创建时生效

    Returns:
        LoopGuard 实例
    """
    with _guards_lock:
        if thread_id not in _guards:
            _guards[thread_id] = LoopGuard(thread_id, config)
        return _guards[thread_id]


def reset_loop_guard(thread_id: str) -> None:
    """重置指定 thread 的 LoopGuard（任务完成后调用）。"""
    with _guards_lock:
        if thread_id in _guards:
            _guards[thread_id].reset()
            del _guards[thread_id]


def clear_all_guards() -> None:
    """清空所有 guard（仅用于测试）。"""
    with _guards_lock:
        _guards.clear()
