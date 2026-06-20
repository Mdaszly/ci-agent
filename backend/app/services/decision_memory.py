from __future__ import annotations

import json
import logging
import re
import threading
from collections import defaultdict
from typing import Iterable, Sequence

from sqlalchemy import text

from app.core.config import db_settings, memory_settings
from app.db.session import is_pgvector_enabled
from app.models.schemas import (
    Conflict,
    DecisionAction,
    DecisionChunkType,
    DecisionMemoryItem,
    DecisionPack,
    DecisionPackStatus,
    Evidence,
    ReviewScore,
)
from app.services.embedding import embedding_client

logger = logging.getLogger(__name__)

_MEMORY_LOCK = threading.RLock()
_MEMORY_INDEX: dict[str, list[DecisionMemoryItem]] = defaultdict(list)
# numpy 内存向量降级方案：{item_id: (item, embedding_vector)}
_MEMORY_VECTORS: dict[str, tuple[DecisionMemoryItem, list[float]]] = {}

# 同步 Engine 单例：pgvector 查询需要在同步上下文中执行，懒加载避免重复创建连接池
_sync_engine = None


def _get_sync_engine():
    """获取同步 Engine 单例。

    pgvector 的向量检索和持久化都在同步上下文中执行（因为 LangGraph 工作流
    在召回/写入记忆时是同步调用），这里复用同一个 Engine 避免每次查询都
    创建新连接池导致资源泄漏。
    """
    global _sync_engine
    if _sync_engine is None:
        from sqlalchemy import create_engine
        from sqlalchemy.engine import make_url

        # 从异步 URL 构造同步 URL（asyncpg -> psycopg2），使用 make_url 更稳健
        async_url = make_url(db_settings.url)
        sync_url = async_url.set(drivername="postgresql+psycopg2").render_as_string(hide_password=False)
        _sync_engine = create_engine(
            sync_url,
            pool_pre_ping=True,  # 连接前检查有效性，避免使用已断开的连接
            pool_size=5,
            max_overflow=10,
        )
    return _sync_engine


_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")
_WS_PATTERN = re.compile(r"\s+")


def _normalize_text(text: str) -> str:
    return _WS_PATTERN.sub(" ", text).strip().lower()


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_PATTERN.findall(text)}


def _get_embedding_client():
    """返回可用的 embedding 客户端，未配置则返回 None"""
    if embedding_client.is_available():
        return embedding_client
    return None


def _decision_actions_text(actions: Sequence[DecisionAction]) -> str:
    if not actions:
        return "无"
    return "\n".join(
        f"- [{action.priority}] {action.title} | {action.dimension.value} | {action.recommendation} | {action.rationale}"
        for action in actions
    )


def _build_decision_chunk_text(decision_pack: DecisionPack) -> str:
    sections = [
        f"决策包ID: {decision_pack.id}",
        f"版本: {decision_pack.version}",
        f"状态: {decision_pack.status.value}",
        f"摘要: {decision_pack.summary}",
        "定位建议:\n" + _decision_actions_text(decision_pack.positioning),
        "MVP优先级:\n" + _decision_actions_text(decision_pack.mvp_priorities),
    ]
    if decision_pack.pricing_insights:
        sections.append("定价洞察:\n" + _decision_actions_text(decision_pack.pricing_insights))
    if decision_pack.battlecard:
        sections.append("Battlecard:\n" + _decision_actions_text(decision_pack.battlecard))
    if decision_pack.source_refs:
        sections.append("来源引用:\n- " + "\n- ".join(decision_pack.source_refs))
    return "\n\n".join(sections)


def _build_evidence_chunk_text(evidence: Evidence) -> str:
    return "\n".join(
        [
            f"证据ID: {evidence.id}",
            f"竞品: {evidence.competitor}",
            f"维度: {evidence.dimension.value}",
            f"观点: {evidence.claim}",
            f"引用: {evidence.quote}",
            f"置信度: {evidence.confidence:.2f}",
            f"时效: {evidence.freshness}",
            f"可信度评分: {evidence.credibility_score:.2f}",
            f"相关性评分: {evidence.relevance_score:.2f}",
            f"质量评分: {evidence.quality_score:.2f}",
        ]
    )


def _build_conflict_chunk_text(conflict: Conflict) -> str:
    return "\n".join(
        [
            f"冲突ID: {conflict.id}",
            f"观点ID列表: {', '.join(conflict.claim_ids)}",
            f"解决方式: {conflict.resolution}",
            f"理由: {conflict.rationale}",
            f"置信度: {conflict.confidence:.2f}",
        ]
    )


def _build_review_chunk_text(review: ReviewScore) -> str:
    notes = "\n- ".join(review.notes) if review.notes else "无"
    return "\n".join(
        [
            f"复核评分: {review.score:.2f}",
            f"引用精度: {review.citation_precision:.2f}",
            f"主张支持率: {review.claim_support_rate:.2f}",
            f"幻觉风险: {review.hallucination_risk}",
            f"复核备注:\n- {notes}",
        ]
    )


def _source_ref_overlap(query_refs: Sequence[str] | None, item_refs: Sequence[str]) -> float:
    if not query_refs or not item_refs:
        return 0.0
    query_set = {_normalize_text(ref) for ref in query_refs if ref}
    item_set = {_normalize_text(ref) for ref in item_refs if ref}
    if not query_set or not item_set:
        return 0.0
    return len(query_set & item_set) / max(len(query_set), 1)


def _chunk_payload_base(
    task_id: str,
    pack_id: str,
    version: int,
    chunk_type: DecisionChunkType,
    *,
    stage: str | None,
    iteration: int,
    source_refs: Sequence[str] | None,
) -> dict[str, object]:
    return {
        "task_id": task_id,
        "pack_id": pack_id,
        "version": version,
        "chunk_type": chunk_type.value,
        "stage": stage,
        "iteration": iteration,
        "source_refs": list(source_refs or []),
    }


def build_memory_chunks(
    task_id: str,
    decision_pack: DecisionPack,
    *,
    evidence: Sequence[Evidence] = (),
    conflicts: Sequence[Conflict] = (),
    review: ReviewScore | None = None,
    repair_summary: str | None = None,
    repair_notes: Sequence[str] = (),
    stage: str | None = None,
    iteration: int = 0,
    source_refs: Sequence[str] | None = None,
) -> list[DecisionMemoryItem]:
    """把决策包及其修复材料拆分为可检索的记忆块。

    拆分策略：一个决策包会生成多个不同 chunk_type 的记忆块，每块独立
    向量化，便于召回时按类型过滤。生成规则：
    - decision：必有，决策包主体（定位建议、MVP 优先级、定价洞察等）
    - evidence：每条证据生成一块，含竞品、维度、观点、引用、评分
    - conflict：每个冲突生成一块，含解决方式和理由
    - repair：仅当 repair_summary 或 repair_notes 非空时生成
    - reviewer_feedback：仅当 review 非空时生成

    Args:
        task_id: 关联任务 ID
        decision_pack: 决策包主体
        evidence: 证据列表，每条生成一个 evidence 块
        conflicts: 冲突列表，每条生成一个 conflict 块
        review: 复核评分，非空时生成 reviewer_feedback 块
        repair_summary: 修复摘要，非空时生成 repair 块
        repair_notes: 修复备注列表
        stage: 产生该块的工作流阶段（writer/reviewer/repair）
        iteration: 回流迭代轮次，用于版本演进追踪
        source_refs: 来源引用列表，默认取 decision_pack.source_refs

    Returns:
        拆分后的记忆块列表，至少包含一个 decision 块
    """
    pack_id = decision_pack.id
    version = decision_pack.version
    shared_refs = list(source_refs or decision_pack.source_refs or [])
    items: list[DecisionMemoryItem] = []

    decision_text = _build_decision_chunk_text(decision_pack)
    items.append(
        DecisionMemoryItem(
            task_id=task_id,
            pack_id=pack_id,
            version=version,
            chunk_type=DecisionChunkType.decision,
            stage=stage,
            iteration=iteration,
            source_refs=shared_refs,
            summary=decision_pack.summary,
            embedding_text=decision_text,
            payload={
                **_chunk_payload_base(task_id, pack_id, version, DecisionChunkType.decision, stage=stage, iteration=iteration, source_refs=shared_refs),
                "decision_ids": {
                    "positioning": [action.dimension.value for action in decision_pack.positioning],
                    "mvp_priorities": [action.dimension.value for action in decision_pack.mvp_priorities],
                },
            },
            status=decision_pack.status,
        )
    )

    for ev in evidence:
        items.append(
            DecisionMemoryItem(
                task_id=task_id,
                pack_id=pack_id,
                version=version,
                chunk_type=DecisionChunkType.evidence,
                stage=stage,
                iteration=iteration,
                source_refs=[ev.source_url] if ev.source_url else shared_refs,
                summary=ev.claim,
                embedding_text=_build_evidence_chunk_text(ev),
                payload={
                    **_chunk_payload_base(task_id, pack_id, version, DecisionChunkType.evidence, stage=stage, iteration=iteration, source_refs=shared_refs),
                    "evidence_id": ev.id,
                    "competitor": ev.competitor,
                    "dimension": ev.dimension.value,
                    "content_hash": ev.content_hash,
                },
                status=decision_pack.status,
            )
        )

    for conflict in conflicts:
        items.append(
            DecisionMemoryItem(
                task_id=task_id,
                pack_id=pack_id,
                version=version,
                chunk_type=DecisionChunkType.conflict,
                stage=stage,
                iteration=iteration,
                source_refs=shared_refs,
                summary=conflict.resolution,
                embedding_text=_build_conflict_chunk_text(conflict),
                payload={
                    **_chunk_payload_base(task_id, pack_id, version, DecisionChunkType.conflict, stage=stage, iteration=iteration, source_refs=shared_refs),
                    "conflict_id": conflict.id,
                    "claim_ids": list(conflict.claim_ids),
                },
                status=decision_pack.status,
            )
        )

    if repair_summary or repair_notes:
        repair_text = "\n".join(
            [
                f"修复摘要: {repair_summary or '无'}",
                f"修复备注:\n- {'\n- '.join(repair_notes) if repair_notes else '无'}",
            ]
        )
        items.append(
            DecisionMemoryItem(
                task_id=task_id,
                pack_id=pack_id,
                version=version,
                chunk_type=DecisionChunkType.repair,
                stage=stage,
                iteration=iteration,
                source_refs=shared_refs,
                summary=repair_summary or "修复补丁",
                embedding_text=repair_text,
                payload={
                    **_chunk_payload_base(task_id, pack_id, version, DecisionChunkType.repair, stage=stage, iteration=iteration, source_refs=shared_refs),
                    "repair_summary": repair_summary,
                    "repair_notes": list(repair_notes),
                },
                status=DecisionPackStatus.approved,
            )
        )

    if review is not None:
        review_notes = list(review.notes)
        items.append(
            DecisionMemoryItem(
                task_id=task_id,
                pack_id=pack_id,
                version=version,
                chunk_type=DecisionChunkType.reviewer_feedback,
                stage=stage,
                iteration=iteration,
                source_refs=shared_refs,
                summary=f"复核评分 {review.score:.2f}",
                embedding_text=_build_review_chunk_text(review),
                payload={
                    **_chunk_payload_base(task_id, pack_id, version, DecisionChunkType.reviewer_feedback, stage=stage, iteration=iteration, source_refs=shared_refs),
                    "review_score": review.score,
                    "citation_precision": review.citation_precision,
                    "claim_support_rate": review.claim_support_rate,
                    "hallucination_risk": review.hallucination_risk,
                    "notes": review_notes,
                },
                status=DecisionPackStatus.approved,
            )
        )

    return items


def upsert_decision_memory(items: Sequence[DecisionMemoryItem]) -> list[DecisionMemoryItem]:
    """写入或更新记忆块（内存 + pgvector 双写）。"""
    inserted: list[DecisionMemoryItem] = []
    with _MEMORY_LOCK:
        for item in items:
            bucket = _MEMORY_INDEX[item.task_id]
            existing_index = next((idx for idx, current in enumerate(bucket) if current.id == item.id), None)
            if existing_index is None:
                bucket.append(item)
            else:
                bucket[existing_index] = item
            inserted.append(item)

    # 持久化（不阻塞主流程，失败不影响内存写入）
    try:
        _persist_memory_items_sync(inserted)
    except Exception as e:
        logger.warning("Memory persistence failed (non-blocking): %s", e)

    return inserted


def _persist_memory_items_sync(items: list[DecisionMemoryItem]) -> None:
    """将记忆块持久化到 pgvector（含 embedding），失败时降级到 numpy 内存向量。

    三级降级策略：
    1. SQLite 模式（DB_USE_SQLITE=true）→ 直接走 numpy 内存向量
    2. pgvector 未启用（初始化失败）→ 走 numpy 内存向量
    3. pgvector 可用 → 生成 embedding 后写入 PostgreSQL

    使用同步 Engine（_get_sync_engine）而非异步引擎，因为本函数在
    LangGraph 工作流的同步上下文中被调用。embedding 逐条生成（非批量），
    牺牲少量性能换取更稳健的错误隔离。
    """
    if db_settings.use_sqlite:
        _persist_to_memory_vectors(items)
        return

    if not is_pgvector_enabled():
        _persist_to_memory_vectors(items)
        return

    client = _get_embedding_client()
    if client is None:
        return  # embedding 未配置，跳过

    # 逐条生成 embedding（批量接口可选，逐条更稳健）
    embeddings: list[list[float]] = []
    for item in items:
        embeddings.append(client.embed_sync(item.embedding_text))

    # 写入 PostgreSQL（同步），复用模块级 Engine 单例
    sync_engine = _get_sync_engine()
    try:
        with sync_engine.begin() as conn:
            for item, emb in zip(items, embeddings):
                conn.execute(
                    text(
                        "INSERT INTO decision_memory_items "
                        "(id, task_id, pack_id, version, chunk_type, stage, iteration, "
                        "source_refs, summary, embedding_text, payload, status, embedding) "
                        "VALUES (:id, :task_id, :pack_id, :version, :chunk_type, :stage, :iteration, "
                        ":source_refs, :summary, :embedding_text, :payload, :status, :embedding) "
                        "ON CONFLICT (id) DO UPDATE SET "
                        "summary = EXCLUDED.summary, embedding_text = EXCLUDED.embedding_text, "
                        "payload = EXCLUDED.payload, status = EXCLUDED.status, "
                        "embedding = EXCLUDED.embedding"
                    ),
                    {
                        "id": item.id,
                        "task_id": item.task_id,
                        "pack_id": item.pack_id,
                        "version": item.version,
                        "chunk_type": item.chunk_type.value,
                        "stage": item.stage,
                        "iteration": item.iteration,
                        "source_refs": json.dumps(item.source_refs, ensure_ascii=False),
                        "summary": item.summary,
                        "embedding_text": item.embedding_text,
                        "payload": json.dumps(item.payload, ensure_ascii=False),
                        "status": item.status.value,
                        "embedding": emb,
                    },
                )
    finally:
        # Engine 是单例，不 dispose；仅确保连接归还连接池
        pass


def _persist_to_memory_vectors(items: list[DecisionMemoryItem]) -> None:
    """numpy 内存向量降级方案：embed 后存内存字典。"""
    client = _get_embedding_client()
    if client is None:
        return

    for item in items:
        try:
            emb = client.embed_sync(item.embedding_text)
            with _MEMORY_LOCK:
                _MEMORY_VECTORS[item.id] = (item, emb)
        except Exception as e:
            logger.warning("Embedding failed for item %s: %s", item.id, e)


def _lexical_search(
    query: str,
    *,
    task_id: str | None = None,
    top_k: int = 5,
    chunk_types: Sequence[DecisionChunkType] | None = None,
    stage: str | None = None,
    version: int | None = None,
    include_superseded: bool = False,
) -> list[tuple[DecisionMemoryItem, float]]:
    """词面重叠检索（保留原有评分算法）。"""
    normalized_query = _normalize_text(query)
    query_tokens = _tokenize(normalized_query)
    allowed_types = set(chunk_types) if chunk_types else None

    with _MEMORY_LOCK:
        if task_id is not None:
            candidates = list(_MEMORY_INDEX.get(task_id, []))
        else:
            candidates = [item for bucket in _MEMORY_INDEX.values() for item in bucket]

    scored: list[tuple[DecisionMemoryItem, float]] = []
    for item in candidates:
        if not include_superseded and item.status == DecisionPackStatus.superseded:
            continue
        if allowed_types is not None and item.chunk_type not in allowed_types:
            continue
        if stage is not None and item.stage != stage:
            continue
        if version is not None and item.version != version:
            continue

        text = _normalize_text(f"{item.summary} {item.embedding_text} {item.payload}")
        item_tokens = _tokenize(text)
        overlap = len(query_tokens & item_tokens)
        lexical = overlap / max(len(query_tokens), 1)
        direct_match = 1.0 if normalized_query and normalized_query in text else 0.0
        task_boost = 0.2 if task_id is not None and item.task_id == task_id else 0.0
        type_boost = 0.08 if item.chunk_type == DecisionChunkType.decision else 0.0
        stage_boost = 0.05 if stage is not None and item.stage == stage else 0.0
        version_boost = 0.05 if version is not None and item.version == version else 0.0
        source_ref_boost = min(0.12, _source_ref_overlap([normalized_query], item.source_refs) * 0.12)
        recent_boost = 0.03 if item.chunk_type in {DecisionChunkType.repair, DecisionChunkType.reviewer_feedback} else 0.0
        score = min(
            1.0,
            lexical * 0.58
            + direct_match * 0.24
            + task_boost
            + type_boost
            + stage_boost
            + version_boost
            + source_ref_boost
            + recent_boost,
        )
        if score <= 0:
            continue
        scored.append((item, score))

    scored.sort(key=lambda pair: (pair[1], pair[0].version, pair[0].iteration, pair[0].created_at), reverse=True)
    return scored[:top_k]


def _row_to_memory_item(row) -> DecisionMemoryItem:
    """将数据库行转换为 DecisionMemoryItem。"""
    source_refs = row.source_refs
    if not isinstance(source_refs, list):
        source_refs = json.loads(source_refs or "[]")
    payload = row.payload
    if not isinstance(payload, dict):
        payload = json.loads(payload or "{}")
    return DecisionMemoryItem(
        id=row.id,
        task_id=row.task_id,
        pack_id=row.pack_id,
        version=row.version,
        chunk_type=DecisionChunkType(row.chunk_type),
        stage=row.stage,
        iteration=row.iteration,
        source_refs=source_refs,
        summary=row.summary,
        embedding_text=row.embedding_text,
        payload=payload,
        status=DecisionPackStatus(row.status),
        created_at=row.created_at,
    )


def _vector_search(
    query: str,
    *,
    task_id: str | None = None,
    top_k: int = 10,
    chunk_types: Sequence[DecisionChunkType] | None = None,
    stage: str | None = None,
    version: int | None = None,
    include_superseded: bool = False,
) -> list[tuple[DecisionMemoryItem, float]]:
    """pgvector 向量检索，失败时降级到 numpy 内存向量。

    查询流程：
    1. 生成查询向量（embed_sync）
    2. 构造 SQL：使用 <=> 操作符计算余弦距离，1 - distance 转为相似度
    3. 按 task_id/chunk_types/stage/version 过滤，按距离升序排序
    4. 返回 (item, similarity) 列表

    降级触发条件：
    - SQLite 模式或 pgvector 未启用 → 直接走 numpy 内存向量
    - 查询过程中任何异常（网络、SQL 错误等）→ 走 numpy 内存向量

    Args:
        query: 查询文本
        task_id: 限定任务范围，None 表示跨任务检索
        top_k: 返回数量
        chunk_types: 限定块类型，None 表示所有类型
        stage: 限定工作流阶段
        version: 限定决策包版本
        include_superseded: 是否包含已废弃的记忆块

    Returns:
        (DecisionMemoryItem, similarity) 列表，similarity 范围 [0, 1]
    """
    if db_settings.use_sqlite or not is_pgvector_enabled():
        return _numpy_vector_search(
            query,
            task_id=task_id,
            top_k=top_k,
            chunk_types=chunk_types,
            stage=stage,
            version=version,
            include_superseded=include_superseded,
        )

    client = _get_embedding_client()
    if client is None:
        return []

    try:
        query_emb = client.embed_sync(query)

        sql = (
            "SELECT id, task_id, pack_id, version, chunk_type, stage, iteration, "
            "source_refs, summary, embedding_text, payload, status, created_at, "
            "1 - (embedding <=> :query_emb) AS similarity "
            "FROM decision_memory_items WHERE 1=1"
        )
        params: dict[str, object] = {"query_emb": str(query_emb)}

        if task_id:
            sql += " AND task_id = :task_id"
            params["task_id"] = task_id
        if not include_superseded:
            sql += " AND status != 'superseded'"
        if chunk_types:
            types = [ct.value for ct in chunk_types]
            sql += " AND chunk_type = ANY(:chunk_types)"
            params["chunk_types"] = types
        if stage:
            sql += " AND stage = :stage"
            params["stage"] = stage
        if version:
            sql += " AND version = :version"
            params["version"] = version

        sql += " ORDER BY embedding <=> :query_emb LIMIT :top_k"
        params["top_k"] = top_k

        # 复用模块级 Engine 单例执行查询
        sync_engine = _get_sync_engine()
        with sync_engine.begin() as conn:
            conn.execute(text("SET LOCAL hnsw.ef_search = :ef_search"), {"ef_search": memory_settings.hnsw_ef_search})
            rows = conn.execute(text(sql), params).fetchall()

        results: list[tuple[DecisionMemoryItem, float]] = []
        for row in rows:
            item = _row_to_memory_item(row)
            results.append((item, float(row.similarity)))
        return results
    except Exception as e:
        logger.warning("Vector search failed, degrading to numpy: %s", e)
        return _numpy_vector_search(
            query,
            task_id=task_id,
            top_k=top_k,
            chunk_types=chunk_types,
            stage=stage,
            version=version,
            include_superseded=include_superseded,
        )


def _numpy_vector_search(
    query: str,
    *,
    task_id: str | None = None,
    top_k: int = 10,
    chunk_types: Sequence[DecisionChunkType] | None = None,
    stage: str | None = None,
    version: int | None = None,
    include_superseded: bool = False,
) -> list[tuple[DecisionMemoryItem, float]]:
    """numpy 内存向量检索降级方案。

    原理：对查询向量和候选向量都做 L2 归一化（除以模长），归一化后的
    点积等价于余弦相似度。相比 pgvector 的 <=> 操作符，这种方式不依赖
    数据库扩展，但需要把所有向量加载到内存，适合小规模数据或降级场景。

    数据来源：_MEMORY_VECTORS 字典，在 _persist_to_memory_vectors 中写入。
    """
    client = _get_embedding_client()
    if client is None:
        return []

    try:
        import numpy as np

        query_emb = np.array(client.embed_sync(query))
        query_norm = np.linalg.norm(query_emb)
        if query_norm == 0:
            return []
        query_emb = query_emb / query_norm

        allowed_types = set(chunk_types) if chunk_types else None
        scored: list[tuple[DecisionMemoryItem, float]] = []
        with _MEMORY_LOCK:
            candidates = list(_MEMORY_VECTORS.values())

        for item, emb in candidates:
            if not include_superseded and item.status == DecisionPackStatus.superseded:
                continue
            if allowed_types is not None and item.chunk_type not in allowed_types:
                continue
            if stage is not None and item.stage != stage:
                continue
            if version is not None and item.version != version:
                continue
            if task_id is not None and item.task_id != task_id:
                continue

            vec = np.array(emb)
            vec_norm = np.linalg.norm(vec)
            if vec_norm == 0:
                continue
            similarity = float(np.dot(query_emb, vec / vec_norm))
            scored.append((item, similarity))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]
    except Exception as e:
        logger.warning("numpy vector search failed: %s", e)
        return []


def _normalize_fusion_results(
    results: list[tuple[DecisionMemoryItem, float]],
) -> tuple[dict[str, float], dict[str, DecisionMemoryItem]]:
    if not results:
        return {}, {}

    max_score = max(score for _, score in results) or 1.0
    scores = {item.id: score / max_score for item, score in results}
    item_map = {item.id: item for item, _ in results}
    return scores, item_map


def _hybrid_fusion(
    lexical_results: list[tuple[DecisionMemoryItem, float]],
    vector_results: list[tuple[DecisionMemoryItem, float]],
    top_k: int,
) -> list[tuple[DecisionMemoryItem, float]]:
    """混合排序：final_score = vector * vector_weight + lexical * lexical_weight。

    当前项目默认使用加权融合，因为它保留了可解释的分值梯度，方便后续在
    召回测试里做阈值分析和人工调参。融合前先做 max 归一化，避免不同后端
    的分值尺度不一致把排序结果拉偏。
    """
    vector_weight = memory_settings.vector_weight
    lexical_weight = memory_settings.lexical_weight

    lex_scores, lex_map = _normalize_fusion_results(lexical_results)
    vec_scores, vec_map = _normalize_fusion_results(vector_results)

    all_ids = set(lex_scores.keys()) | set(vec_scores.keys())
    item_map = {**lex_map, **vec_map}

    hybrid_scores: dict[str, float] = {}
    for item_id in all_ids:
        v_score = vec_scores.get(item_id, 0.0)
        l_score = lex_scores.get(item_id, 0.0)
        hybrid_scores[item_id] = v_score * vector_weight + l_score * lexical_weight

    sorted_ids = sorted(hybrid_scores.items(), key=lambda x: x[1], reverse=True)
    return [(item_map[item_id], score) for item_id, score in sorted_ids[:top_k]]


def _rrf_fusion(
    lexical_results: list[tuple[DecisionMemoryItem, float]],
    vector_results: list[tuple[DecisionMemoryItem, float]],
    top_k: int,
    *,
    k: int = 60,
) -> list[tuple[DecisionMemoryItem, float]]:
    """基于 Reciprocal Rank Fusion 的稳健融合。

    RRF 不依赖原始分值尺度，只看排名，因此更适合把词面召回和语义召回合并。
    这个策略在后端差异较大、分值分布不稳定的时候比线性加权更抗噪，但代价是
    结果分数只表示相对优先级，不再适合直接当置信度解释。
    """
    _, lex_map = _normalize_fusion_results(lexical_results)
    _, vec_map = _normalize_fusion_results(vector_results)

    lex_ranks = {item.id: rank for rank, (item, _) in enumerate(lexical_results, start=1)}
    vec_ranks = {item.id: rank for rank, (item, _) in enumerate(vector_results, start=1)}
    item_map = {**lex_map, **vec_map}

    all_ids = set(lex_ranks.keys()) | set(vec_ranks.keys())
    fused_scores: dict[str, float] = {}
    for item_id in all_ids:
        score = 0.0
        if item_id in lex_ranks:
            score += 1.0 / (k + lex_ranks[item_id])
        if item_id in vec_ranks:
            score += 1.0 / (k + vec_ranks[item_id])
        fused_scores[item_id] = score

    sorted_ids = sorted(fused_scores.items(), key=lambda pair: pair[1], reverse=True)
    return [(item_map[item_id], score) for item_id, score in sorted_ids[:top_k]]


def search_decision_memory(
    query: str,
    *,
    task_id: str | None = None,
    top_k: int = 5,
    chunk_types: Sequence[DecisionChunkType] | None = None,
    stage: str | None = None,
    version: int | None = None,
    include_superseded: bool = False,
    return_meta: bool = False,
) -> list[tuple[DecisionMemoryItem, float]] | tuple[list[tuple[DecisionMemoryItem, float]], dict[str, object]]:
    """Hybrid RAG 混合检索：向量检索 + 词面检索 + 查询改写 + 阈值过滤 + Rerank。

    增强流程：
    1. 查询改写（可选）：HyDE 生成假设文档或 Multi-Query 扩展，用于向量检索
    2. 词面检索（_lexical_search）：基于 token 重叠的精确匹配，召回 candidate_k 条
    3. 向量检索（_vector_search）：基于 embedding 的语义相似度，召回 candidate_k 条
    4. 融合策略（weighted / rrf）并截断到 top_k
    5. 相似度阈值过滤：剔除低于 min_similarity_threshold 的结果
    6. Rerank（可选）：LLM 精排，返回 rerank_top_k 条

    降级策略：若向量检索无结果（pgvector 不可用或 embedding 未配置），
    直接返回词面检索结果的前 top_k 条。
    """
    candidate_k = top_k * memory_settings.candidate_multiplier  # 每路多召回，混合后截断
    fusion_strategy = (memory_settings.fusion_strategy or "weighted").strip().lower()

    # ===== 步骤 1: 查询改写（仅影响向量检索）=====
    rewrite_strategy = memory_settings.query_rewrite_strategy
    vector_query = query
    if rewrite_strategy and rewrite_strategy != "none":
        try:
            from app.services.query_rewriter import rewrite_query

            vector_query = rewrite_query(query, strategy=rewrite_strategy)
            logger.debug("Query rewritten: strategy=%s, original=%r, rewritten=%r", rewrite_strategy, query[:50], vector_query[:50])
        except Exception as e:
            logger.warning("Query rewrite failed, using original query: %s", e)

    # ===== 步骤 2: 词面检索（使用原始 query）=====
    lexical_results = _lexical_search(
        query,
        task_id=task_id,
        top_k=candidate_k,
        chunk_types=chunk_types,
        stage=stage,
        version=version,
        include_superseded=include_superseded,
    )

    # ===== 步骤 3: 向量检索（使用改写后的 query）=====
    vector_results = _vector_search(
        vector_query,
        task_id=task_id,
        top_k=candidate_k,
        chunk_types=chunk_types,
        stage=stage,
        version=version,
        include_superseded=include_superseded,
    )

    meta: dict[str, object] = {
        "candidate_k": candidate_k,
        "requested_top_k": top_k,
        "requested_strategy": fusion_strategy,
        "used_strategy": "lexical_only" if not vector_results else fusion_strategy,
        "lexical_count": len(lexical_results),
        "vector_count": len(vector_results),
        "degraded": False,
        "degraded_reason": None,
        "query_rewrite_strategy": rewrite_strategy,
        "query_rewritten": vector_query != query,
        "threshold_filtered": 0,
        "rerank_applied": False,
    }

    # ===== 步骤 4: 融合 =====
    if vector_results:
        if fusion_strategy == "rrf":
            results = _rrf_fusion(lexical_results, vector_results, top_k)
        else:
            if fusion_strategy not in {"weighted", "rrf"}:
                logger.warning(
                    "Unknown memory fusion strategy '%s', falling back to weighted",
                    memory_settings.fusion_strategy,
                )
            results = _hybrid_fusion(lexical_results, vector_results, top_k)
    else:
        meta["degraded"] = True
        meta["degraded_reason"] = "vector_empty"
        meta["used_strategy"] = "lexical_only"
        if not memory_settings.allow_degraded_mode:
            logger.warning("Hybrid search degraded mode is disabled; returning empty results")
            meta["degraded_reason"] = "degraded_disabled"
            results = []
        else:
            results = lexical_results[:top_k]

    # ===== 步骤 5: 相似度阈值过滤 =====
    threshold = memory_settings.min_similarity_threshold
    if threshold > 0.0 and results:
        before_count = len(results)
        results = [(item, score) for item, score in results if score >= threshold]
        filtered_count = before_count - len(results)
        meta["threshold_filtered"] = filtered_count
        if filtered_count > 0:
            logger.debug("Threshold filter: removed %d results below %.3f", filtered_count, threshold)

    # ===== 步骤 6: Rerank（可选）=====
    if memory_settings.rerank_enabled and results:
        try:
            from app.services.reranker import rerank

            def _doc_extractor(item: DecisionMemoryItem) -> str:
                return item.summary or item.embedding_text or ""

            rerank_top_k = max(memory_settings.rerank_top_k, top_k)
            results = rerank(
                query,
                results,
                doc_extractor=_doc_extractor,
                top_k=rerank_top_k,
                enabled=True,
            )
            meta["rerank_applied"] = True
            meta["rerank_top_k"] = rerank_top_k
            logger.debug("Rerank applied: returned %d results", len(results))
        except Exception as e:
            logger.warning("Rerank failed, returning pre-rerank results: %s", e)
            meta["rerank_applied"] = False
            meta["rerank_error"] = str(e)

    return (results, meta) if return_meta else results


def mark_superseded(
    task_id: str,
    *,
    pack_id: str | None = None,
    version: int | None = None,
) -> int:
    """将指定任务下的记忆块标记为已废弃。"""
    count = 0
    with _MEMORY_LOCK:
        bucket = _MEMORY_INDEX.get(task_id, [])
        for item in bucket:
            if pack_id is not None and item.pack_id != pack_id:
                continue
            if version is not None and item.version != version:
                continue
            if item.status != DecisionPackStatus.superseded:
                item.status = DecisionPackStatus.superseded
                count += 1
    return count


def get_task_memory_items(task_id: str) -> list[DecisionMemoryItem]:
    with _MEMORY_LOCK:
        items = list(_MEMORY_INDEX.get(task_id, []))
    return sorted(items, key=lambda item: (item.version, item.iteration, item.created_at), reverse=True)


def get_all_memory_items() -> list[DecisionMemoryItem]:
    with _MEMORY_LOCK:
        items = [item for bucket in _MEMORY_INDEX.values() for item in bucket]
    return sorted(items, key=lambda item: (item.task_id, item.version, item.iteration, item.created_at), reverse=True)


def clear_task_memory(task_id: str) -> None:
    with _MEMORY_LOCK:
        _MEMORY_INDEX.pop(task_id, None)
        # 清理 numpy 向量缓存中属于该 task 的项
        to_remove = [item_id for item_id, (item, _) in _MEMORY_VECTORS.items() if item.task_id == task_id]
        for item_id in to_remove:
            _MEMORY_VECTORS.pop(item_id, None)