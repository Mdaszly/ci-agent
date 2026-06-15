from __future__ import annotations

import json
import logging
import os
import threading
import time

from fastapi import APIRouter, Header, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from typing import Annotated

from app.core.security import (
    estimate_budget_usage,
    estimate_source_count,
    validate_image_name,
    validate_image_upload,
    validate_file_upload,
    validate_public_url,
)
from app.models.schemas import (
    InterventionRequest,
    TaskCreateRequest,
    TaskRecord,
    TaskStatus,
)
from app.services.store import task_store
from app.worker.workflow import run_competitive_intelligence_workflow, rerun_from_stage, VALID_RERUN_STAGES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# 环境变量控制默认同步模式（测试环境默认同步）
SYNC_MODE_DEFAULT = os.getenv("TASK_SYNC_MODE", "false").lower() == "true"


def _get_task_or_404(task_id: str) -> TaskRecord:
    
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return task


def _run_workflow_in_thread(task_id: str) -> None:
    """后台线程执行 workflow，捕获异常并更新状态"""
    import traceback
    import sys
    
    task = task_store.get(task_id)
    if task is None:
        logger.error(f"Task {task_id} not found in store")
        return

    try:
        logger.info(f"Thread starting workflow for task {task_id}")
        logger.info(f"Task status before workflow: {task.status}")
        sys.stdout.flush()
        task = run_competitive_intelligence_workflow(task)
        logger.info(f"Workflow completed for task {task_id}, status={task.status}")
        task_store.update(task)
        logger.info(f"Store updated for task {task_id}")
    except Exception as exc:
        logger.exception(f"Workflow failed for task {task_id}: {exc}")
        logger.error(f"Exception traceback: {traceback.format_exc()}")
        # 重新获取最新的 task 状态
        task = task_store.get(task_id)
        if task:
            task.status = TaskStatus.failed
            task_store.append_event(task, "failed", f"任务失败：{exc}", TaskStatus.failed)
            task_store.update(task)


@router.post("/tasks", response_model=TaskRecord)
def create_task(
    payload: TaskCreateRequest,
    x_sync_mode: Annotated[str | None, Header(alias="X-Sync-Mode")] = None,
) -> TaskRecord:
    for url in payload.urls:
        validate_public_url(str(url))

    source_count = estimate_source_count(
        url_count=len(payload.urls),
        has_comments=payload.comments is not None,
        image_count=len(payload.image_names),
    )
    if source_count > payload.budget.max_sources:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="输入来源数量超过任务预算",
        )
    estimated_tokens, estimated_cost = estimate_budget_usage(source_count)
    if estimated_tokens > payload.budget.max_tokens or estimated_cost > payload.budget.max_cost_usd:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="预估 token 或成本超过任务预算",
        )
    for image_name in payload.image_names:
        validate_image_name(image_name)

    task = task_store.create(TaskRecord(request=payload))
    task_store.append_event(task, "created", "任务已创建", TaskStatus.queued)

    # 判断是否使用同步模式：Header 优先，其次环境变量
    sync_mode = (x_sync_mode and x_sync_mode.lower() == "true") or SYNC_MODE_DEFAULT

    if sync_mode:
        # 同步模式：直接执行 workflow（用于测试）
        try:
            return run_competitive_intelligence_workflow(task)
        except Exception as exc:
            task.status = TaskStatus.failed
            task_store.append_event(task, "failed", f"任务失败：{exc}", TaskStatus.failed)
            task_store.update(task)
            raise HTTPException(status_code=500, detail="任务执行失败") from exc
    else:
        # 异步模式：启动后台线程执行 workflow，立即返回 queued 状态
        # 先创建 queued 状态的副本用于返回，确保不受线程竞态影响
        queued_task = task.model_copy(deep=True)
        thread = threading.Thread(
            target=_run_workflow_in_thread,
            args=(task.id,),
            daemon=True,
        )
        thread.start()
        return queued_task


@router.post("/uploads/images")
async def validate_image(file: UploadFile) -> dict[str, str]:
    await validate_image_upload(file)
    return {"filename": file.filename or "image", "status": "accepted"}


@router.post("/uploads/files")
async def upload_files(files: list[UploadFile]) -> dict:
    results = []
    for file in files:
        try:
            file_info = await validate_file_upload(file)
            results.append({
                "filename": file_info["filename"],
                "content_type": file_info["content_type"],
                "size": file_info["size"],
                "is_image": file_info["is_image"],
                "status": "success",
                "message": "文件上传成功",
            })
        except HTTPException as exc:
            results.append({
                "filename": file.filename or "unknown",
                "content_type": file.content_type,
                "size": 0,
                "is_image": False,
                "status": "error",
                "message": exc.detail,
            })
        except Exception as exc:
            results.append({
                "filename": file.filename or "unknown",
                "content_type": file.content_type,
                "size": 0,
                "is_image": False,
                "status": "error",
                "message": f"上传失败: {str(exc)}",
            })
    
    success_count = sum(1 for r in results if r["status"] == "success")
    return {
        "success": success_count == len(files),
        "success_count": success_count,
        "total_count": len(files),
        "files": results,
    }


@router.post("/uploads/file")
async def upload_single_file(file: UploadFile) -> dict:
    try:
        file_info = await validate_file_upload(file)
        return {
            "success": True,
            "filename": file_info["filename"],
            "content_type": file_info["content_type"],
            "size": file_info["size"],
            "is_image": file_info["is_image"],
            "message": "文件上传成功",
        }
    except HTTPException as exc:
        raise exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"上传失败: {str(exc)}"
        )


@router.get("/tasks/{task_id}", response_model=TaskRecord)
def get_task(task_id: str) -> TaskRecord:
    return _get_task_or_404(task_id)


@router.get("/tasks/{task_id}/events/stream")
def stream_task_events(task_id: str) -> StreamingResponse:
    """SSE endpoint：实时推送任务事件"""
    task = _get_task_or_404(task_id)

    def event_stream():
        sent_count = 0
        while True:
            # 重新获取任务以获取最新状态
            current_task = task_store.get(task_id)
            if current_task is None:
                break

            # 推送新事件
            events = current_task.events
            while sent_count < len(events):
                event = events[sent_count]
                yield f"data: {event.model_dump_json()}\n\n"
                sent_count += 1

            # 检查任务是否已完成或失败
            if current_task.status in (TaskStatus.completed, TaskStatus.failed):
                yield "data: [DONE]\n\n"
                break

            # 等待 0.5 秒后再次检查
            time.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/tasks/{task_id}/evidence")
def list_evidence(task_id: str):
    task = _get_task_or_404(task_id)
    return {
        "coverage": task.coverage,
        "evidence": task.evidence,
        "claims": task.claims,
        "conflicts": task.conflicts,
    }


@router.get("/tasks/{task_id}/decision-pack")
def get_decision_pack(task_id: str):
    task = _get_task_or_404(task_id)
    if task.decision_pack is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="决策包尚未生成")
    return {"decision_pack": task.decision_pack, "review": task.review}


@router.post("/tasks/{task_id}/interventions")
def create_intervention(task_id: str, payload: InterventionRequest):
    task = _get_task_or_404(task_id)
    
    # 验证目标是否存在
    target_ids = {task.id}
    target_ids.update(item.id for item in task.evidence)
    if task.decision_pack:
        target_ids.add(task.decision_pack.id)
        for action in task.decision_pack.positioning + task.decision_pack.mvp_priorities:
            target_ids.add(action.title)
    if payload.target_id not in target_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="干预目标不存在")
    
    # 处理 force_rerun 操作
    if payload.action == "force_rerun":
        # 验证 stage 参数
        if payload.stage is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"force_rerun 操作需要指定 stage 参数，有效阶段: {VALID_RERUN_STAGES}"
            )
        
        if payload.stage not in VALID_RERUN_STAGES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"无效的阶段: {payload.stage}，有效阶段: {VALID_RERUN_STAGES}"
            )
        
        # 验证任务状态：只有已完成或失败的任务才能强制重跑
        if task.status not in (TaskStatus.completed, TaskStatus.failed):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"只有已完成或失败的任务才能强制重跑，当前状态: {task.status.value}"
            )
        
        # 记录干预事件（包含所有元数据）
        intervention_metadata = {
            "target": payload.target,
            "target_id": payload.target_id,
            "action": payload.action,
            "reason": payload.reason,
            "stage": payload.stage,
            "previous_status": task.status.value,
            "previous_decision_pack_id": task.decision_pack.id if task.decision_pack else None,
            "previous_review_score": task.review.score if task.review else None,
        }
        task_store.append_event(
            task,
            "human_intervention",
            json.dumps(intervention_metadata, ensure_ascii=False),
            task.status,
        )
        
        # 执行强制重跑
        try:
            task = rerun_from_stage(task, payload.stage)
            return {
                "status": "rerun_completed",
                "task_id": task_id,
                "intervention": payload,
                "new_task_status": task.status.value,
            }
        except Exception as exc:
            logger.exception(f"Force rerun failed for task {task_id}: {exc}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"强制重跑失败: {str(exc)}"
            ) from exc
    
    # 其他操作：仅记录干预事件
    intervention_metadata = {
        "target": payload.target,
        "target_id": payload.target_id,
        "action": payload.action,
        "reason": payload.reason,
        "stage": payload.stage,
    }
    task_store.append_event(
        task,
        "human_intervention",
        json.dumps(intervention_metadata, ensure_ascii=False),
        task.status,
    )
    return {"status": "recorded", "task_id": task_id, "intervention": payload}
