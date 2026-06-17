from __future__ import annotations

import logging
import re
import threading
from collections import defaultdict
from typing import Iterable, Sequence

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

logger = logging.getLogger(__name__)

_MEMORY_LOCK = threading.RLock()
_MEMORY_INDEX: dict[str, list[DecisionMemoryItem]] = defaultdict(list)


_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")
_WS_PATTERN = re.compile(r"\s+")


def _normalize_text(text: str) -> str:
    return _WS_PATTERN.sub(" ", text).strip().lower()


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_PATTERN.findall(text)}


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
    """把决策包及其修复材料拆分为可检索的记忆块。"""
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
    """写入或更新记忆块。"""
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
    return inserted


def search_decision_memory(
    query: str,
    *,
    task_id: str | None = None,
    top_k: int = 5,
    chunk_types: Sequence[DecisionChunkType] | None = None,
    stage: str | None = None,
    version: int | None = None,
    include_superseded: bool = False,
) -> list[tuple[DecisionMemoryItem, float]]:
    """基于词面重叠的记忆召回。"""
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


def clear_task_memory(task_id: str) -> None:
    with _MEMORY_LOCK:
        _MEMORY_INDEX.pop(task_id, None)