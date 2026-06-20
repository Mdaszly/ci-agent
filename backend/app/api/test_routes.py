"""召回率测试 API 路由。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import AuthSubject, get_auth_subject
from app.services.recall_tester import recall_tester

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tests/recall")


class MemoryItemPayload(BaseModel):
    id: str
    task_id: str
    pack_id: str = "test_pack"
    version: int = 1
    chunk_type: str = "decision"
    stage: str | None = None
    iteration: int = 0
    source_refs: list[str] = Field(default_factory=list)
    summary: str
    embedding_text: str
    payload: dict = Field(default_factory=dict)
    status: str = "approved"


class TestCasePayload(BaseModel):
    case_id: str
    category: str
    bucket: str | None = None
    query: str
    expected_ids: list[str] = Field(default_factory=list)
    expected_relevance: list[int] | None = None
    description: str = ""


class DatasetRequest(BaseModel):
    dataset_id: str
    memory_items: list[MemoryItemPayload]
    test_cases: list[TestCasePayload]
    clear_existing: bool = True
    dataset_version: str | None = None
    baseline_config: dict | None = None
    description: str | None = None


class RunTestRequest(BaseModel):
    dataset_id: str
    modes: list[str] = Field(default_factory=lambda: ["lexical_only", "vector_only", "hybrid"])
    top_k: int = 5
    categories: list[str] | None = None
    detailed: bool = True
    vector_weight: float | None = None
    lexical_weight: float | None = None
    fusion_strategy: str | None = None
    candidate_multiplier: int | None = None
    hnsw_ef_search: int | None = None
    allow_degraded_mode: bool | None = None


@router.post("/dataset")
def load_dataset(request: DatasetRequest) -> dict:
    """加载测试数据集。"""
    try:
        return recall_tester.load_dataset(
            dataset_id=request.dataset_id,
            memory_items=[item.model_dump() for item in request.memory_items],
            test_cases=[tc.model_dump() for tc in request.test_cases],
            clear_existing=request.clear_existing,
            dataset_version=request.dataset_version,
            baseline_config=request.baseline_config,
            description=request.description,
        )
    except Exception as e:
        logger.error("Failed to load dataset: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run")
def run_test(request: RunTestRequest) -> dict:
    """执行召回率测试。"""
    try:
        return recall_tester.run_test(
            dataset_id=request.dataset_id,
            modes=request.modes,
            top_k=request.top_k,
            categories=request.categories,
            detailed=request.detailed,
            vector_weight=request.vector_weight,
            lexical_weight=request.lexical_weight,
            fusion_strategy=request.fusion_strategy,
            candidate_multiplier=request.candidate_multiplier,
            hnsw_ef_search=request.hnsw_ef_search,
            allow_degraded_mode=request.allow_degraded_mode,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Failed to run test: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
def get_history(limit: int = 10, dataset_id: str | None = None) -> dict:
    """查询测试历史。"""
    return recall_tester.get_history(limit=limit, dataset_id=dataset_id)


@router.get("/compare")
def compare_tests(test_a: str, test_b: str, subject: AuthSubject = Depends(get_auth_subject)) -> dict:
    """对比两次测试结果。"""
    try:
        return recall_tester.compare_tests(test_a, test_b)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Failed to compare tests: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/history/{test_id}")
def delete_history(test_id: str, subject: AuthSubject = Depends(get_auth_subject)) -> dict:
    """删除指定测试记录。"""
    try:
        return recall_tester.delete_history(test_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Failed to delete history: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history/{test_id}")
def get_test_result(test_id: str, subject: AuthSubject = Depends(get_auth_subject)) -> dict:
    """查询单个测试结果详情。"""
    try:
        return recall_tester.get_test_result(test_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Failed to get test result: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
