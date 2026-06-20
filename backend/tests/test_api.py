from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)

# 测试环境使用同步模式
SYNC_HEADERS = {"X-Sync-Mode": "true"}


def test_create_task_returns_decision_pack() -> None:
    response = client.post(
        "/api/tasks",
        json={
            "product_goal": "为 AI 简历优化工具寻找首版差异化定位和 MVP 功能优先级",
            "competitors": ["ResumeWorded"],
            "comments": "用户反馈模板同质化，中文场景支持不足。",
            "image_names": ["homepage.png"],
            "budget": {
                "max_sources": 8,
                "max_tokens": 12000,
                "max_cost_usd": 1,
                "timeout_seconds": 90,
            },
        },
        headers=SYNC_HEADERS,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["decision_pack"]["positioning"][0]["evidence_ids"]


def test_create_task_rejects_budget_overflow() -> None:
    response = client.post(
        "/api/tasks",
        json={
            "product_goal": "为 AI 简历优化工具寻找首版差异化定位和 MVP 功能优先级",
            "competitors": ["ResumeWorded"],
            "comments": "用户反馈模板同质化。",
            "image_names": ["a.png", "b.png", "c.png"],
            "budget": {
                "max_sources": 1,
                "max_tokens": 12000,
                "max_cost_usd": 1,
                "timeout_seconds": 90,
            },
        },
        headers=SYNC_HEADERS,
    )

    assert response.status_code == 400
    assert "预算" in response.json()["detail"]


def test_intervention_rejects_unknown_target() -> None:
    task_response = client.post(
        "/api/tasks",
        json={
            "product_goal": "为 AI 简历优化工具寻找首版差异化定位和 MVP 功能优先级",
            "competitors": ["ResumeWorded"],
            "comments": "用户反馈模板同质化，中文场景支持不足。",
            "budget": {
                "max_sources": 8,
                "max_tokens": 12000,
                "max_cost_usd": 1,
                "timeout_seconds": 90,
            },
        },
        headers=SYNC_HEADERS,
    )
    task_id = task_response.json()["id"]

    response = client.post(
        f"/api/tasks/{task_id}/interventions",
        json={
            "target": "evidence",
            "target_id": "missing",
            "action": "reject",
            "reason": "目标不存在时应拒绝干预",
        },
    )

    assert response.status_code == 404


def test_sse_stream_events() -> None:
    """SSE smoke test：验证 SSE endpoint 能正确推送事件和 [DONE] 标记"""
    import json

    task_response = client.post(
        "/api/tasks",
        json={
            "product_goal": "为 AI 简历优化工具寻找首版差异化定位和 MVP 功能优先级",
            "competitors": ["ResumeWorded"],
            "comments": "测试 SSE 推送",
            "budget": {
                "max_sources": 8,
                "max_tokens": 12000,
                "max_cost_usd": 1,
                "timeout_seconds": 90,
            },
        },
        headers=SYNC_HEADERS,
    )
    assert task_response.status_code == 200
    task_id = task_response.json()["id"]

    sse_response = client.get(f"/api/tasks/{task_id}/events/stream")
    assert sse_response.status_code == 200
    assert "text/event-stream" in sse_response.headers["content-type"]

    events = []
    for line in sse_response.iter_lines():
        if line and line.startswith("data: "):
            events.append(line[6:])

    assert len(events) >= 1, "SSE 应该推送至少一个事件"
    assert events[-1] == "[DONE]", f"最后一个事件应该是 [DONE]，实际是: {events[-1]}"

    for event in events[:-1]:
        event_data = json.loads(event)
        assert "stage" in event_data
        assert "message" in event_data
        assert "status" in event_data


def test_sse_returns_404_for_nonexistent_task() -> None:
    """SSE endpoint 对不存在的任务返回 404"""
    response = client.get("/api/tasks/nonexistent_task/events/stream")
    assert response.status_code == 404


def test_create_task_async_mode_returns_queued() -> None:
    """异步模式：POST /tasks 立即返回 queued 状态"""
    import time

    response = client.post(
        "/api/tasks",
        json={
            "product_goal": "为 AI 简历优化工具寻找首版差异化定位和 MVP 功能优先级",
            "competitors": ["ResumeWorded"],
            "comments": "异步模式测试",
            "budget": {
                "max_sources": 8,
                "max_tokens": 12000,
                "max_cost_usd": 1,
                "timeout_seconds": 90,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["id"] is not None

    task_id = payload["id"]
    max_wait = 60
    start_time = time.time()
    while time.time() - start_time < max_wait:
        task_response = client.get(f"/api/tasks/{task_id}")
        assert task_response.status_code == 200
        task_data = task_response.json()
        if task_data["status"] in ["completed", "failed"]:
            break
        time.sleep(0.5)

    final_response = client.get(f"/api/tasks/{task_id}")
    assert final_response.status_code == 200
    final_data = final_response.json()
    assert final_data["status"] in ["completed", "failed"]


def test_force_rerun_from_writer_stage() -> None:
    """测试从 writer 阶段强制重跑"""
    import json

    task_response = client.post(
        "/api/tasks",
        json={
            "product_goal": "为 AI 简历优化工具寻找首版差异化定位和 MVP 功能优先级",
            "competitors": ["ResumeWorded"],
            "comments": "测试强制重跑",
            "budget": {
                "max_sources": 8,
                "max_tokens": 12000,
                "max_cost_usd": 1,
                "timeout_seconds": 90,
            },
        },
        headers=SYNC_HEADERS,
    )
    assert task_response.status_code == 200
    task_id = task_response.json()["id"]
    assert task_response.json()["status"] == "completed"
    original_decision_pack_id = task_response.json()["decision_pack"]["id"]

    intervention_response = client.post(
        f"/api/tasks/{task_id}/interventions",
        json={
            "target": "task",
            "target_id": task_id,
            "action": "force_rerun",
            "reason": "测试从 writer 阶段强制重跑",
            "stage": "writer",
        },
    )

    assert intervention_response.status_code == 200
    result = intervention_response.json()
    assert result["status"] == "rerun_completed"
    assert result["intervention"]["action"] == "force_rerun"
    assert result["intervention"]["stage"] == "writer"

    updated_task = client.get(f"/api/tasks/{task_id}").json()
    assert updated_task["status"] == "completed"
    assert updated_task["decision_pack"]["id"] != original_decision_pack_id

    events = updated_task["events"]
    human_intervention_events = [e for e in events if e["stage"] == "human_intervention"]
    assert len(human_intervention_events) >= 1

    intervention_event = human_intervention_events[-1]
    event_data = json.loads(intervention_event["message"])
    assert event_data["action"] == "force_rerun"
    assert event_data["stage"] == "writer"
    assert event_data["target"] == "task"
    assert event_data["reason"] == "测试从 writer 阶段强制重跑"


def test_debug_endpoints_expose_runtime_and_task_state() -> None:
    task_response = client.post(
        "/api/tasks",
        json={
            "product_goal": "为 AI 简历优化工具寻找首版差异化定位和 MVP 功能优先级",
            "competitors": ["ResumeWorded"],
            "comments": "调试端点覆盖",
            "budget": {
                "max_sources": 8,
                "max_tokens": 12000,
                "max_cost_usd": 1,
                "timeout_seconds": 90,
            },
        },
        headers=SYNC_HEADERS,
    )
    assert task_response.status_code == 200
    task_id = task_response.json()["id"]

    runtime_response = client.get("/debug/runtime")
    assert runtime_response.status_code == 200
    runtime_payload = runtime_response.json()
    assert "total_runs" in runtime_payload
    assert "checkpoint_kind_count" in runtime_payload

    task_debug_response = client.get(f"/debug/tasks/{task_id}")
    assert task_debug_response.status_code == 200
    task_debug_payload = task_debug_response.json()
    assert task_debug_payload["task_id"] == task_id
    assert "latest_run" in task_debug_payload
    assert "metrics" in task_debug_payload


def test_force_rerun_invalid_stage_rejected() -> None:
    """测试无效阶段验证：force_rerun 使用无效阶段应返回错误"""
    task_response = client.post(
        "/api/tasks",
        json={
            "product_goal": "为 AI 简历优化工具寻找首版差异化定位和 MVP 功能优先级",
            "competitors": ["ResumeWorded"],
            "comments": "测试无效阶段",
            "budget": {
                "max_sources": 8,
                "max_tokens": 12000,
                "max_cost_usd": 1,
                "timeout_seconds": 90,
            },
        },
        headers=SYNC_HEADERS,
    )
    assert task_response.status_code == 200
    task_id = task_response.json()["id"]

    intervention_response = client.post(
        f"/api/tasks/{task_id}/interventions",
        json={
            "target": "task",
            "target_id": task_id,
            "action": "force_rerun",
            "reason": "测试无效阶段",
            "stage": "invalid_stage",
        },
    )

    assert intervention_response.status_code == 422
    error_detail = intervention_response.json()["detail"]
    assert any("stage" in str(err) for err in error_detail)


def test_force_rerun_missing_stage() -> None:
    """测试缺少 stage 参数：force_rerun 必须指定 stage"""
    task_response = client.post(
        "/api/tasks",
        json={
            "product_goal": "为 AI 简历优化工具寻找首版差异化定位和 MVP 功能优先级",
            "competitors": ["ResumeWorded"],
            "comments": "测试缺少 stage",
            "budget": {
                "max_sources": 8,
                "max_tokens": 12000,
                "max_cost_usd": 1,
                "timeout_seconds": 90,
            },
        },
        headers=SYNC_HEADERS,
    )
    assert task_response.status_code == 200
    task_id = task_response.json()["id"]

    intervention_response = client.post(
        f"/api/tasks/{task_id}/interventions",
        json={
            "target": "task",
            "target_id": task_id,
            "action": "force_rerun",
            "reason": "测试缺少 stage 参数",
        },
    )

    assert intervention_response.status_code == 400
    error_detail = intervention_response.json()["detail"]
    assert "stage 参数" in error_detail


def test_intervention_event_recorded_correctly() -> None:
    """测试干预事件被正确记录"""
    import json

    task_response = client.post(
        "/api/tasks",
        json={
            "product_goal": "为 AI 简历优化工具寻找首版差异化定位和 MVP 功能优先级",
            "competitors": ["ResumeWorded"],
            "comments": "测试干预事件记录",
            "budget": {
                "max_sources": 8,
                "max_tokens": 12000,
                "max_cost_usd": 1,
                "timeout_seconds": 90,
            },
        },
        headers=SYNC_HEADERS,
    )
    assert task_response.status_code == 200
    task_id = task_response.json()["id"]
    decision_pack_id = task_response.json()["decision_pack"]["id"]
    review_score = task_response.json()["review"]["score"]

    intervention_response = client.post(
        f"/api/tasks/{task_id}/interventions",
        json={
            "target": "task",
            "target_id": task_id,
            "action": "force_rerun",
            "reason": "验证干预事件元数据",
            "stage": "reviewer",
        },
    )

    assert intervention_response.status_code == 200

    updated_task = client.get(f"/api/tasks/{task_id}").json()
    events = updated_task["events"]

    human_intervention_events = [e for e in events if e["stage"] == "human_intervention"]
    assert len(human_intervention_events) >= 1

    intervention_event = human_intervention_events[-1]
    event_data = json.loads(intervention_event["message"])

    assert event_data["target"] == "task"
    assert event_data["target_id"] == task_id
    assert event_data["action"] == "force_rerun"
    assert event_data["reason"] == "验证干预事件元数据"
    assert event_data["stage"] == "reviewer"
    assert event_data["previous_status"] == "completed"
    assert event_data["previous_decision_pack_id"] == decision_pack_id
    assert event_data["previous_review_score"] == review_score
    assert "created_at" in intervention_event
    assert intervention_event["status"] == "completed"