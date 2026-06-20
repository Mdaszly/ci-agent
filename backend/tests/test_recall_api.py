"""召回率测试 API 接口测试。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services.recall_tester import recall_tester

client = TestClient(app)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "recall_dataset.json"


def _load_fixture() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _reset_tester():
    """清空 tester 状态。"""
    recall_tester._datasets.clear()
    recall_tester._history.clear()


class TestRecallDatasetAPI:
    """测试数据集加载接口。"""

    def setup_method(self):
        _reset_tester()

    def test_load_dataset_success(self):
        fixture = _load_fixture()
        response = client.post(
            "/api/tests/recall/dataset",
            json={
                "dataset_id": fixture["dataset_id"],
                "memory_items": fixture["memory_items"],
                "test_cases": fixture["test_cases"],
                "clear_existing": True,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["dataset_id"] == "recall_benchmark_v1"
        assert data["memory_count"] == len(fixture["memory_items"])
        assert data["test_case_count"] == len(fixture["test_cases"])

    def test_load_dataset_invalid_payload(self):
        response = client.post(
            "/api/tests/recall/dataset",
            json={"dataset_id": "test", "memory_items": [], "test_cases": []},
        )
        assert response.status_code == 200  # 空数据集也允许


class TestRecallRunAPI:
    """测试召回率测试执行接口。"""

    def setup_method(self):
        _reset_tester()
        fixture = _load_fixture()
        client.post(
            "/api/tests/recall/dataset",
            json={
                "dataset_id": fixture["dataset_id"],
                "memory_items": fixture["memory_items"],
                "test_cases": fixture["test_cases"],
                "clear_existing": True,
            },
        )

    def test_run_test_lexical_only(self):
        response = client.post(
            "/api/tests/recall/run",
            json={
                "dataset_id": "recall_benchmark_v1",
                "modes": ["lexical_only"],
                "top_k": 5,
                "detailed": False,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "lexical_only" in data["summary"]
        assert data["config"]["total_cases"] > 0

    def test_run_test_dataset_not_found(self):
        response = client.post(
            "/api/tests/recall/run",
            json={
                "dataset_id": "nonexistent",
                "modes": ["lexical_only"],
                "top_k": 5,
            },
        )
        assert response.status_code == 404

    def test_run_test_with_categories_filter(self):
        response = client.post(
            "/api/tests/recall/run",
            json={
                "dataset_id": "recall_benchmark_v1",
                "modes": ["lexical_only"],
                "top_k": 5,
                "categories": ["exact_keyword"],
                "detailed": True,
            },
        )
        assert response.status_code == 200
        data = response.json()
        # 只测试了 exact_keyword 类别
        assert len(data["details"]) <= 5


class TestRecallHistoryAPI:
    """测试历史查询接口。"""

    def setup_method(self):
        _reset_tester()

    def test_empty_history(self):
        response = client.get("/api/tests/recall/history")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0

    def test_history_after_run(self):
        fixture = _load_fixture()
        client.post(
            "/api/tests/recall/dataset",
            json={
                "dataset_id": fixture["dataset_id"],
                "memory_items": fixture["memory_items"],
                "test_cases": fixture["test_cases"],
                "clear_existing": True,
            },
        )
        client.post(
            "/api/tests/recall/run",
            json={
                "dataset_id": "recall_benchmark_v1",
                "modes": ["lexical_only"],
                "top_k": 5,
                "detailed": False,
            },
        )

        response = client.get("/api/tests/recall/history")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1


class TestRecallCompareAPI:
    """测试对比接口。"""

    def setup_method(self):
        _reset_tester()

    def test_compare_not_found(self):
        response = client.get(
            "/api/tests/recall/compare?test_a=nonexistent&test_b=also_nonexistent"
        )
        assert response.status_code == 404
