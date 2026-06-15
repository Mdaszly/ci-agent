"""Writer 输出校验测试"""
import pytest
from unittest.mock import patch, MagicMock

from app.models.schemas import (
    DecisionAction,
    DecisionPack,
    Evidence,
    EvidenceDimension,
    SourceType,
    TaskCreateRequest,
    TaskRecord,
)
from app.services.llm import LLMNotConfiguredError
from app.worker.workflow import _validate_evidence_ids, writer, WorkflowState


class TestWriterValidation:
    """测试 Writer 输出校验功能"""

    def _create_test_evidence(self, ev_id: str) -> Evidence:
        """创建测试用的 Evidence 对象"""
        return Evidence(
            id=ev_id,
            source_type=SourceType.url,
            source_url="https://example.com",
            competitor="test",
            dimension=EvidenceDimension.feature,
            claim=f"Test claim for {ev_id}",
            quote=f"Test quote for {ev_id}",
            confidence=0.8,
            freshness="recent",
            content_hash=f"hash_{ev_id}",
            license_risk="low",
        )

    def test_validate_evidence_ids_valid(self):
        """测试校验有效的 evidence_ids"""
        evidence = [
            self._create_test_evidence("ev_001"),
            self._create_test_evidence("ev_002"),
        ]
        task = TaskRecord(
            id="test-task",
            request=TaskCreateRequest(
                product_goal="test goal",
                competitors=["test"],
                urls=["https://example.com"],
            ),
            evidence=evidence,
        )
        
        valid, invalid = _validate_evidence_ids(task, ["ev_001", "ev_002"])
        assert valid is True
        assert invalid == []

    def test_validate_evidence_ids_invalid(self):
        """测试校验无效的 evidence_ids"""
        evidence = [self._create_test_evidence("ev_001")]
        task = TaskRecord(
            id="test-task",
            request=TaskCreateRequest(
                product_goal="test goal",
                competitors=["test"],
                urls=["https://example.com"],
            ),
            evidence=evidence,
        )
        
        valid, invalid = _validate_evidence_ids(task, ["ev_001", "ev_fake"])
        assert valid is False
        assert invalid == ["ev_fake"]

    @patch('app.worker.workflow.llm_client')
    def test_writer_llm_not_configured(self, mock_llm):
        """测试未配置 LLM_API_KEY 时任务失败"""
        mock_llm.chat_completion_json_sync.side_effect = LLMNotConfiguredError("Not configured")
        
        evidence = [self._create_test_evidence("ev_001")]
        task = TaskRecord(
            id="test-task",
            request=TaskCreateRequest(
                product_goal="test goal",
                competitors=["test"],
                urls=["https://example.com"],
            ),
            evidence=evidence,
        )
        
        state: WorkflowState = {"task": task}
        result = writer(state)
        
        assert result["task"].status == "failed"
        assert result["task"].decision_pack is None

    @patch('app.worker.workflow.llm_client')
    def test_writer_valid_evidence_ids(self, mock_llm):
        """测试 LLM 输出有效的 evidence_ids"""
        mock_llm.chat_completion_json_sync.return_value = {
            "summary": "test summary",
            "positioning": [
                {
                    "title": "test positioning",
                    "dimension": "positioning",
                    "recommendation": "test rec",
                    "rationale": "test rationale",
                    "evidence_ids": ["ev_001"],
                    "priority": "P0",
                }
            ],
            "mvp_priorities": [
                {
                    "title": "test mvp",
                    "dimension": "feature",
                    "recommendation": "test rec",
                    "rationale": "test rationale",
                    "evidence_ids": ["ev_001"],
                    "priority": "P0",
                }
            ],
        }
        
        evidence = [self._create_test_evidence("ev_001")]
        task = TaskRecord(
            id="test-task",
            request=TaskCreateRequest(
                product_goal="test goal",
                competitors=["test"],
                urls=["https://example.com"],
            ),
            evidence=evidence,
        )
        
        state: WorkflowState = {"task": task}
        result = writer(state)
        
        assert result["task"].status != "failed"
        assert result["task"].decision_pack is not None
        assert len(result["task"].decision_pack.positioning) == 1
        assert len(result["task"].decision_pack.mvp_priorities) == 1

    @patch('app.worker.workflow.llm_client')
    def test_writer_invalid_evidence_ids(self, mock_llm):
        """测试 LLM 输出无效的 evidence_ids 时任务失败"""
        from app.services.llm import LLMError
        
        mock_llm.chat_completion_json_sync.return_value = {
            "summary": "test summary",
            "positioning": [
                {
                    "title": "test positioning",
                    "dimension": "positioning",
                    "recommendation": "test rec",
                    "rationale": "test rationale",
                    "evidence_ids": ["ev_fake"],
                    "priority": "P0",
                }
            ],
            "mvp_priorities": [],
        }
        
        evidence = [self._create_test_evidence("ev_001")]
        task = TaskRecord(
            id="test-task",
            request=TaskCreateRequest(
                product_goal="test goal",
                competitors=["test"],
                urls=["https://example.com"],
            ),
            evidence=evidence,
        )
        
        state: WorkflowState = {"task": task}
        result = writer(state)
        
        assert result["task"].status == "failed"
        assert result["task"].decision_pack is None

    @patch('app.worker.workflow.llm_client')
    def test_writer_no_evidence(self, mock_llm):
        """测试没有证据时任务失败"""
        task = TaskRecord(
            id="test-task",
            request=TaskCreateRequest(
                product_goal="test goal",
                competitors=["test"],
                urls=[],
            ),
            evidence=[],
        )
        
        state: WorkflowState = {"task": task}
        result = writer(state)
        
        assert result["task"].status == "failed"
        assert result["task"].decision_pack is None
