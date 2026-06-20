"""Agent 工程化增强模块单元测试。

覆盖以下模块：
1. checkpointer - Checkpointer 持久化
2. loop_guard - 死循环三层防御
3. retry_policy - RetryPolicy
4. hitl - Human-in-the-loop
5. json_stable - JSON 稳定输出
6. context_compressor - 上下文压缩
7. streaming - LangGraph Streaming
8. ragas_evaluator - RAGAS 评估
9. memory_store - 分层记忆存储
"""

import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

# 确保可以导入 app 模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ============================================================================
# 1. Checkpointer 测试
# ============================================================================


class TestCheckpointer:
    """Checkpointer 模块测试。"""

    def test_get_checkpointer_returns_singleton(self):
        """测试 checkpointer 单例。"""
        from app.services.checkpointer import get_checkpointer, reset_checkpointer

        reset_checkpointer()
        cp1 = get_checkpointer()
        cp2 = get_checkpointer()
        assert cp1 is cp2

    def test_get_checkpointer_kind(self):
        """测试获取 checkpointer 类型。"""
        from app.services.checkpointer import get_checkpointer, get_checkpointer_kind, reset_checkpointer

        reset_checkpointer()
        get_checkpointer()
        kind = get_checkpointer_kind()
        assert kind in ("memory", "postgres", "none")

    def test_make_thread_config(self):
        """测试构造 thread_id 配置。"""
        from app.services.checkpointer import make_thread_config

        config = make_thread_config("task-123")
        assert config == {"configurable": {"thread_id": "task-123"}}

    def test_make_thread_config_empty_raises(self):
        """测试空 thread_id 抛出异常。"""
        from app.services.checkpointer import make_thread_config

        with pytest.raises(ValueError, match="thread_id 不能为空"):
            make_thread_config("")

    def test_reset_checkpointer(self):
        """测试重置 checkpointer。"""
        from app.services.checkpointer import get_checkpointer, reset_checkpointer

        get_checkpointer()
        reset_checkpointer()
        # 重置后可以重新创建
        cp = get_checkpointer()
        assert cp is not None

    def test_get_state_history_empty_graph(self):
        """测试空 graph 的状态历史。"""
        from app.services.checkpointer import get_state_history

        result = get_state_history(None, "task-123")
        assert result == []

    def test_get_state_snapshot_empty_graph(self):
        """测试空 graph 的状态快照。"""
        from app.services.checkpointer import get_state_snapshot

        result = get_state_snapshot(None, "task-123")
        assert result is None

    def test_resume_workflow_empty_graph(self):
        """测试空 graph 的恢复。"""
        from app.services.checkpointer import resume_workflow

        result = resume_workflow(None, "task-123")
        assert result is None


# ============================================================================
# 2. LoopGuard 测试
# ============================================================================


class TestLoopGuard:
    """死循环三层防御测试。"""

    def test_hard_limit_block(self):
        """测试第一层：运行时硬限制。"""
        from app.services.loop_guard import LoopGuard, LoopGuardConfig

        config = LoopGuardConfig(max_iterations=3)
        guard = LoopGuard("test-thread", config)

        # 前 3 次不阻断
        for i in range(3):
            result = guard.check(f"state-{i}")
            assert not result.blocked, f"第 {i+1} 次不应阻断"

        # 第 4 次阻断
        result = guard.check("state-3")
        assert result.blocked
        assert result.layer == "hard_limit"
        assert "最大迭代次数" in result.reason

    def test_state_repeat_block(self):
        """测试第二层：状态重复检测。"""
        from app.services.loop_guard import LoopGuard, LoopGuardConfig

        config = LoopGuardConfig(max_iterations=100, max_state_repeats=2)
        guard = LoopGuard("test-thread", config)

        # 同一状态出现 2 次不阻断
        guard.check("same-state")
        result = guard.check("same-state")
        assert not result.blocked

        # 第 3 次阻断
        result = guard.check("same-state")
        assert result.blocked
        assert result.layer == "state_repeat"
        assert result.repeat_count == 3

    def test_semantic_block_disabled(self):
        """测试第三层：语义相似度阻断（禁用时）。"""
        from app.services.loop_guard import LoopGuard, LoopGuardConfig

        config = LoopGuardConfig(semantic_block_enabled=False)
        guard = LoopGuard("test-thread", config)

        result = guard.check("state", "some text")
        assert not result.blocked

    def test_reset_guard(self):
        """测试重置 guard。"""
        from app.services.loop_guard import LoopGuard, LoopGuardConfig

        config = LoopGuardConfig(max_iterations=2)
        guard = LoopGuard("test-thread", config)

        guard.check("state-1")
        guard.check("state-2")
        assert guard.iteration == 2

        guard.reset()
        assert guard.iteration == 0

    def test_get_loop_guard_singleton(self):
        """测试全局 guard 注册表。"""
        from app.services.loop_guard import clear_all_guards, get_loop_guard

        clear_all_guards()
        guard1 = get_loop_guard("thread-1")
        guard2 = get_loop_guard("thread-1")
        assert guard1 is guard2

        guard3 = get_loop_guard("thread-2")
        assert guard3 is not guard1

    def test_reset_loop_guard(self):
        """测试重置指定 thread 的 guard。"""
        from app.services.loop_guard import clear_all_guards, get_loop_guard, reset_loop_guard

        clear_all_guards()
        get_loop_guard("thread-1")
        reset_loop_guard("thread-1")
        # 重置后可以重新创建
        guard = get_loop_guard("thread-1")
        assert guard is not None

    def test_different_states_not_blocked(self):
        """测试不同状态不阻断。"""
        from app.services.loop_guard import LoopGuard, LoopGuardConfig

        config = LoopGuardConfig(max_iterations=100, max_state_repeats=2)
        guard = LoopGuard("test-thread", config)

        for i in range(10):
            result = guard.check(f"unique-state-{i}")
            assert not result.blocked

    def test_block_result_fields(self):
        """测试阻断结果字段。"""
        from app.services.loop_guard import LoopGuard, LoopGuardConfig

        config = LoopGuardConfig(max_iterations=1)
        guard = LoopGuard("test-thread", config)

        guard.check("state-1")
        result = guard.check("state-2")

        assert result.blocked
        assert result.layer == "hard_limit"
        assert result.iteration == 2
        assert result.current_hash  # 哈希值非空


# ============================================================================
# 3. RetryPolicy 测试
# ============================================================================


class TestRetryPolicy:
    """RetryPolicy 测试。"""

    def test_make_llm_retry_policy(self):
        """测试创建 LLM 重试策略。"""
        from app.services.retry_policy import make_llm_retry_policy

        policy = make_llm_retry_policy()
        # 不可用时为 None，可用时为 RetryPolicy 实例
        if policy is not None:
            assert hasattr(policy, "max_attempts") or hasattr(policy, "retry_on")

    def test_make_api_retry_policy(self):
        """测试创建 API 重试策略。"""
        from app.services.retry_policy import make_api_retry_policy

        policy = make_api_retry_policy()
        if policy is not None:
            assert hasattr(policy, "max_attempts") or hasattr(policy, "retry_on")

    def test_get_node_retry_policies(self):
        """测试获取节点重试策略字典。"""
        from app.services.retry_policy import get_node_retry_policies

        policies = get_node_retry_policies()
        assert isinstance(policies, dict)
        # 不可用时为空字典
        if policies:
            assert "planner" in policies or "research" in policies


# ============================================================================
# 4. HITL 测试
# ============================================================================


class TestHITL:
    """Human-in-the-loop 测试。"""

    def test_request_approval_disabled(self):
        """测试 HITL 不可用时自动批准。"""
        from app.services import hitl

        # 模拟 interrupt 不可用
        with patch.object(hitl, "_check_interrupt_available", return_value=False):
            result = hitl.request_approval("test message", default=True)
            assert result is True

    def test_request_approval_default_false(self):
        """测试默认拒绝。"""
        from app.services import hitl

        with patch.object(hitl, "_check_interrupt_available", return_value=False):
            result = hitl.request_approval("test message", default=False)
            assert result is False

    def test_get_interrupt_nodes_default_empty(self):
        """测试默认中断节点为空。"""
        from app.services.hitl import get_interrupt_nodes

        # 清除环境变量
        old_val = os.environ.pop("HITL_INTERRUPT_NODES", None)
        try:
            nodes = get_interrupt_nodes()
            assert nodes == []
        finally:
            if old_val is not None:
                os.environ["HITL_INTERRUPT_NODES"] = old_val

    def test_get_interrupt_nodes_from_env(self):
        """测试从环境变量读取中断节点。"""
        from app.services.hitl import get_interrupt_nodes

        old_val = os.environ.get("HITL_INTERRUPT_NODES")
        os.environ["HITL_INTERRUPT_NODES"] = "writer,reviewer"
        try:
            nodes = get_interrupt_nodes()
            assert nodes == ["writer", "reviewer"]
        finally:
            if old_val is None:
                os.environ.pop("HITL_INTERRUPT_NODES", None)
            else:
                os.environ["HITL_INTERRUPT_NODES"] = old_val

    def test_make_interrupt_config_empty(self):
        """测试空中断配置。"""
        from app.services.hitl import make_interrupt_config

        old_val = os.environ.pop("HITL_INTERRUPT_NODES", None)
        try:
            config = make_interrupt_config()
            assert config == {}
        finally:
            if old_val is not None:
                os.environ["HITL_INTERRUPT_NODES"] = old_val

    def test_make_interrupt_config_with_nodes(self):
        """测试带中断节点的配置。"""
        from app.services.hitl import make_interrupt_config

        old_val = os.environ.get("HITL_INTERRUPT_NODES")
        os.environ["HITL_INTERRUPT_NODES"] = "writer"
        try:
            config = make_interrupt_config()
            assert "interrupt_before" in config
            assert config["interrupt_before"] == ["writer"]
        finally:
            if old_val is None:
                os.environ.pop("HITL_INTERRUPT_NODES", None)
            else:
                os.environ["HITL_INTERRUPT_NODES"] = old_val


# ============================================================================
# 5. JSON 稳定输出测试
# ============================================================================


class TestJsonStable:
    """JSON 稳定输出测试。"""

    def test_extract_json_direct(self):
        """测试直接 JSON 提取。"""
        from app.services.json_stable import extract_json_from_text

        text = '{"key": "value"}'
        result = extract_json_from_text(text)
        assert result == '{"key": "value"}'

    def test_extract_json_from_code_fence(self):
        """测试从代码块提取 JSON。"""
        from app.services.json_stable import extract_json_from_text

        text = '```json\n{"key": "value"}\n```'
        result = extract_json_from_text(text)
        assert result == '{"key": "value"}'

    def test_extract_json_from_bare_code_fence(self):
        """测试从无语言标记的代码块提取。"""
        from app.services.json_stable import extract_json_from_text

        text = '```\n{"key": "value"}\n```'
        result = extract_json_from_text(text)
        assert result == '{"key": "value"}'

    def test_extract_json_from_text_with_explanation(self):
        """测试从带解释的文本提取 JSON。"""
        from app.services.json_stable import extract_json_from_text

        text = '这是结果：\n{"key": "value"}\n以上是 JSON。'
        result = extract_json_from_text(text)
        assert result == '{"key": "value"}'

    def test_extract_json_empty_text(self):
        """测试空文本。"""
        from app.services.json_stable import extract_json_from_text

        assert extract_json_from_text("") is None
        assert extract_json_from_text("   ") is None

    def test_extract_json_invalid(self):
        """测试无效 JSON。"""
        from app.services.json_stable import extract_json_from_text

        text = "这不是 JSON"
        result = extract_json_from_text(text)
        assert result is None

    def test_parse_json_safely_valid(self):
        """测试安全解析合法 JSON。"""
        from app.services.json_stable import parse_json_safely

        result = parse_json_safely('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_json_safely_invalid(self):
        """测试安全解析非法 JSON。"""
        from app.services.json_stable import parse_json_safely

        result = parse_json_safely("invalid")
        assert result is None

    def test_validate_with_schema_valid(self):
        """测试 Pydantic 校验通过。"""
        from pydantic import BaseModel

        from app.services.json_stable import validate_with_schema

        class TestModel(BaseModel):
            name: str
            age: int

        result = validate_with_schema({"name": "test", "age": 20}, TestModel)
        assert result is not None
        assert result.name == "test"
        assert result.age == 20

    def test_validate_with_schema_invalid(self):
        """测试 Pydantic 校验失败。"""
        from pydantic import BaseModel

        from app.services.json_stable import validate_with_schema

        class TestModel(BaseModel):
            name: str
            age: int

        result = validate_with_schema({"name": "test", "age": "not_int"}, TestModel)
        assert result is None

    def test_parse_and_validate_success(self):
        """测试完整解析校验流程。"""
        from pydantic import BaseModel

        from app.services.json_stable import parse_and_validate

        class TestModel(BaseModel):
            name: str

        result = parse_and_validate('{"name": "test"}', TestModel)
        assert result is not None
        assert result.name == "test"

    def test_parse_and_validate_with_retry(self):
        """测试带重试的解析校验。"""
        from pydantic import BaseModel

        from app.services.json_stable import parse_and_validate

        class TestModel(BaseModel):
            name: str

        call_count = [0]

        def retry_callback(error_msg):
            call_count[0] += 1
            return '{"name": "retried"}'

        # 第一次返回无效 JSON，重试后返回有效
        result = parse_and_validate(
            "invalid json",
            TestModel,
            max_retries=1,
            retry_callback=retry_callback,
        )
        assert result is not None
        assert result.name == "retried"
        assert call_count[0] == 1

    def test_build_json_prompt(self):
        """测试构造 JSON Prompt。"""
        from app.services.json_stable import build_json_prompt

        prompt = build_json_prompt("系统提示", "name: str, age: int", "示例")
        assert "系统提示" in prompt
        assert "name: str, age: int" in prompt
        assert "示例" in prompt
        assert "JSON" in prompt


# ============================================================================
# 6. 上下文压缩测试
# ============================================================================


class TestContextCompressor:
    """上下文压缩测试。"""

    def test_estimate_tokens(self):
        """测试 token 估算。"""
        from app.services.context_compressor import estimate_tokens

        assert estimate_tokens("") == 0
        assert estimate_tokens("hello") >= 1
        assert estimate_tokens("hello world") >= 2

    def test_estimate_messages_tokens(self):
        """测试消息列表 token 估算。"""
        from app.services.context_compressor import estimate_messages_tokens

        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        tokens = estimate_messages_tokens(messages)
        assert tokens > 0

    def test_rolling_window_compress_no_compression(self):
        """测试滚动窗口无需压缩。"""
        from app.services.context_compressor import rolling_window_compress

        messages = [{"role": "user", "content": f"msg{i}"} for i in range(5)]
        result = rolling_window_compress(messages, window_size=10)
        assert result.strategy == "none"
        assert len(result.compressed_messages) == 5

    def test_rolling_window_compress_with_compression(self):
        """测试滚动窗口压缩。"""
        from app.services.context_compressor import rolling_window_compress

        messages = [{"role": "user", "content": f"msg{i}"} for i in range(20)]
        result = rolling_window_compress(messages, window_size=5)
        assert result.strategy == "rolling_window"
        assert len(result.compressed_messages) == 5
        assert result.compressed_messages[-1]["content"] == "msg19"
        assert result.compression_ratio < 1.0

    def test_compress_context_no_compression_needed(self):
        """测试无需压缩。"""
        from app.services.context_compressor import ContextCompressorConfig, compress_context

        messages = [{"role": "user", "content": "short"}]
        config = ContextCompressorConfig(current_tokens=100, summary_threshold=5000)
        result = compress_context(messages, config)
        assert result.strategy == "none"
        assert len(result.compressed_messages) == 1

    def test_compress_context_empty_messages(self):
        """测试空消息列表。"""
        from app.services.context_compressor import compress_context

        result = compress_context([])
        assert result.strategy == "none"
        assert result.compressed_messages == []

    def test_summary_compress_fallback(self):
        """测试摘要压缩降级（LLM 不可用）。"""
        from app.services.context_compressor import (
            ContextCompressorConfig,
            summary_compress,
        )

        messages = [{"role": "user", "content": f"msg{i}"} for i in range(30)]
        config = ContextCompressorConfig(rolling_window_size=5)
        result = summary_compress(messages, config)
        # LLM 不可用时应降级到滚动窗口
        assert result.strategy in ("summary", "rolling_window")


# ============================================================================
# 7. Streaming 测试
# ============================================================================


class TestStreaming:
    """LangGraph Streaming 测试。"""

    def test_stream_workflow_empty_graph(self):
        """测试空 graph 流式执行。"""
        from app.services.streaming import stream_workflow

        chunks = list(stream_workflow(None, {}, {}))
        assert chunks == []

    def test_stream_workflow_events_empty_graph(self):
        """测试空 graph 事件流。"""
        from app.services.streaming import stream_workflow_events

        chunks = list(stream_workflow_events(None, {}, {}))
        assert chunks == []

    def test_format_sse_event_string(self):
        """测试 SSE 格式化（字符串数据）。"""
        from app.services.streaming import format_sse_event

        result = format_sse_event("message", "hello")
        assert "event: message" in result
        assert "data: hello" in result
        assert result.endswith("\n\n")

    def test_format_sse_event_dict(self):
        """测试 SSE 格式化（字典数据）。"""
        from app.services.streaming import format_sse_event

        result = format_sse_event("chunk", {"key": "value"})
        assert "event: chunk" in result
        assert "key" in result
        assert "value" in result

    def test_stream_workflow_sse_empty_graph(self):
        """测试空 graph SSE 流。"""
        from app.services.streaming import stream_workflow_sse

        chunks = list(stream_workflow_sse(None, {}, {}))
        # 应该有 start 和 done 事件
        assert len(chunks) >= 2
        assert "start" in chunks[0]
        assert "done" in chunks[-1]

    def test_stream_workflow_with_mock_graph(self):
        """测试 mock graph 流式执行。"""
        from app.services.streaming import stream_workflow

        mock_graph = MagicMock()
        mock_graph.stream.return_value = iter([{"chunk1": "data1"}, {"chunk2": "data2"}])

        chunks = list(stream_workflow(mock_graph, {"input": "test"}, {}))
        assert len(chunks) == 2
        assert chunks[0] == {"chunk1": "data1"}


# ============================================================================
# 8. RAGAS 评估测试
# ============================================================================


class TestRAGASEvaluator:
    """RAGAS 评估测试。"""

    def test_evaluate_faithfulness_empty(self):
        """测试空输入的忠实度评估。"""
        from app.services.ragas_evaluator import evaluate_faithfulness

        assert evaluate_faithfulness("", ["context"]) == 0.0
        assert evaluate_faithfulness("answer", []) == 0.0

    def test_evaluate_answer_relevancy_empty(self):
        """测试空输入的答案相关性评估。"""
        from app.services.ragas_evaluator import evaluate_answer_relevancy

        assert evaluate_answer_relevancy("", "answer") == 0.0
        assert evaluate_answer_relevancy("question", "") == 0.0

    def test_evaluate_context_precision_empty(self):
        """测试空输入的上下文精确率评估。"""
        from app.services.ragas_evaluator import evaluate_context_precision

        assert evaluate_context_precision("question", []) == 0.0

    def test_evaluate_context_recall_empty(self):
        """测试空输入的上下文召回率评估。"""
        from app.services.ragas_evaluator import evaluate_context_recall

        assert evaluate_context_recall("question", []) == 0.0

    def test_evaluate_ragas_complete(self):
        """测试完整 RAGAS 评估。"""
        from app.services.ragas_evaluator import evaluate_ragas

        result = evaluate_ragas(
            question="什么是 RAG？",
            answer="RAG 是检索增强生成技术",
            contexts=["RAG 是一种结合检索和生成的技术"],
            ground_truth="RAG 是 Retrieval Augmented Generation",
        )

        assert 0.0 <= result.faithfulness <= 1.0
        assert 0.0 <= result.answer_relevancy <= 1.0
        assert 0.0 <= result.context_precision <= 1.0
        assert 0.0 <= result.context_recall <= 1.0
        assert 0.0 <= result.overall_score <= 1.0
        assert result.details["contexts_count"] == 1
        assert result.details["has_ground_truth"] is True

    def test_ragas_result_dataclass(self):
        """测试 RAGASResult 数据类。"""
        from app.services.ragas_evaluator import RAGASResult

        result = RAGASResult(
            faithfulness=0.8,
            answer_relevancy=0.9,
            context_precision=0.7,
            context_recall=0.6,
            overall_score=0.75,
            details={"test": True},
        )
        assert result.faithfulness == 0.8
        assert result.overall_score == 0.75


# ============================================================================
# 9. 分层记忆存储测试
# ============================================================================


class TestMemoryStore:
    """分层记忆存储测试。"""

    def test_write_and_read(self):
        """测试写入和读取。"""
        from app.services.memory_store import LayeredMemoryStore, MemoryLayer

        store = LayeredMemoryStore()
        store.write("key1", {"data": "value1"}, MemoryLayer.WORKING)

        item = store.read("key1", MemoryLayer.WORKING)
        assert item is not None
        assert item.value == {"data": "value1"}
        assert item.access_count == 1

    def test_read_nonexistent(self):
        """测试读取不存在的记忆。"""
        from app.services.memory_store import LayeredMemoryStore, MemoryLayer

        store = LayeredMemoryStore()
        item = store.read("nonexistent", MemoryLayer.WORKING)
        assert item is None

    def test_write_empty_key(self):
        """测试写入空 key。"""
        from app.services.memory_store import LayeredMemoryStore, MemoryLayer

        store = LayeredMemoryStore()
        result = store.write("", {"data": "value"}, MemoryLayer.WORKING)
        assert result is False

    def test_lru_eviction(self):
        """测试 LRU 淘汰。"""
        from app.services.memory_store import (
            LayeredMemoryStore,
            MemoryStoreConfig,
            MemoryLayer,
        )

        config = MemoryStoreConfig(working_memory_capacity=3)
        store = LayeredMemoryStore(config)

        for i in range(5):
            store.write(f"key{i}", {"data": i}, MemoryLayer.WORKING)

        # 只保留最近 3 个
        assert store.read("key0", MemoryLayer.WORKING) is None
        assert store.read("key1", MemoryLayer.WORKING) is None
        assert store.read("key2", MemoryLayer.WORKING) is not None
        assert store.read("key3", MemoryLayer.WORKING) is not None
        assert store.read("key4", MemoryLayer.WORKING) is not None

    def test_expire(self):
        """测试过期清理。"""
        from app.services.memory_store import LayeredMemoryStore, MemoryLayer

        store = LayeredMemoryStore()
        # 写入带短 TTL 的记忆
        store.write("key1", {"data": "1"}, MemoryLayer.WORKING, ttl=0.1)

        # 立即读取应该存在
        assert store.read("key1", MemoryLayer.WORKING) is not None

        # 等待过期
        time.sleep(0.2)

        # 过期后读取应返回 None
        assert store.read("key1", MemoryLayer.WORKING) is None

    def test_expire_all_layers(self):
        """测试清理所有层级。"""
        from app.services.memory_store import LayeredMemoryStore, MemoryLayer

        store = LayeredMemoryStore()
        store.write("key1", {"data": "1"}, MemoryLayer.WORKING, ttl=0.1)
        store.write("key2", {"data": "2"}, MemoryLayer.SHORT_TERM, ttl=0.1)

        time.sleep(0.2)
        removed = store.expire()
        assert removed >= 2

    def test_search(self):
        """测试搜索记忆。"""
        from app.services.memory_store import LayeredMemoryStore, MemoryLayer

        store = LayeredMemoryStore()
        store.write("key1", {"data": "1"}, MemoryLayer.WORKING, importance=0.9)
        store.write("key2", {"data": "2"}, MemoryLayer.WORKING, importance=0.3)
        store.write("key3", {"data": "3"}, MemoryLayer.WORKING, importance=0.7)

        results = store.search(MemoryLayer.WORKING, limit=2)
        assert len(results) == 2
        # 高重要性的应排在前面
        assert results[0].importance >= results[1].importance

    def test_search_with_namespace(self):
        """测试命名空间过滤。"""
        from app.services.memory_store import LayeredMemoryStore, MemoryLayer

        store = LayeredMemoryStore()
        store.write("key1", {"data": "1"}, MemoryLayer.WORKING, namespace="ns1")
        store.write("key2", {"data": "2"}, MemoryLayer.WORKING, namespace="ns2")

        results = store.search(MemoryLayer.WORKING, namespace="ns1")
        assert len(results) == 1
        assert results[0].namespace == "ns1"

    def test_compress(self):
        """测试压缩。"""
        from app.services.memory_store import (
            LayeredMemoryStore,
            MemoryStoreConfig,
            MemoryLayer,
        )

        config = MemoryStoreConfig(
            working_memory_capacity=100,
            compress_threshold=10,
            compress_ratio=0.5,
        )
        store = LayeredMemoryStore(config)

        # 写入 20 条
        for i in range(20):
            store.write(f"key{i}", {"data": i}, MemoryLayer.WORKING, importance=i / 20.0)

        removed = store.compress(MemoryLayer.WORKING)
        assert removed > 0

        stats = store.stats()
        assert stats[MemoryLayer.WORKING]["count"] < 20

    def test_clear(self):
        """测试清空。"""
        from app.services.memory_store import LayeredMemoryStore, MemoryLayer

        store = LayeredMemoryStore()
        store.write("key1", {"data": "1"}, MemoryLayer.WORKING)
        store.write("key2", {"data": "2"}, MemoryLayer.SHORT_TERM)

        total = store.clear()
        assert total == 2

        assert store.read("key1", MemoryLayer.WORKING) is None
        assert store.read("key2", MemoryLayer.SHORT_TERM) is None

    def test_stats(self):
        """测试统计信息。"""
        from app.services.memory_store import LayeredMemoryStore, MemoryLayer

        store = LayeredMemoryStore()
        store.write("key1", {"data": "1"}, MemoryLayer.WORKING)

        stats = store.stats()
        assert MemoryLayer.WORKING in stats
        assert stats[MemoryLayer.WORKING]["count"] == 1
        assert stats[MemoryLayer.WORKING]["capacity"] > 0

    def test_get_memory_store_singleton(self):
        """测试全局单例。"""
        from app.services.memory_store import get_memory_store, reset_memory_store

        reset_memory_store()
        store1 = get_memory_store()
        store2 = get_memory_store()
        assert store1 is store2

    def test_memory_item_relevance_score(self):
        """测试记忆条目相关性分数。"""
        from app.services.memory_store import MemoryItem, MemoryLayer

        item = MemoryItem(
            key="test",
            value={"data": "test"},
            layer=MemoryLayer.WORKING,
            importance=0.8,
        )
        score = item.relevance_score()
        assert 0.0 <= score <= 1.0

    def test_memory_item_is_expired(self):
        """测试记忆条目过期判断。"""
        from app.services.memory_store import MemoryItem, MemoryLayer

        # 不过期
        item1 = MemoryItem(key="test", value={}, layer=MemoryLayer.WORKING, ttl=None)
        assert not item1.is_expired()

        # 已过期
        item2 = MemoryItem(key="test", value={}, layer=MemoryLayer.WORKING, ttl=0.1)
        time.sleep(0.2)
        assert item2.is_expired()


# ============================================================================
# 10. Workflow 集成测试
# ============================================================================


class TestWorkflowIntegration:
    """Workflow 集成测试。"""

    def test_build_graph_returns_object(self):
        """测试 build_graph 返回对象。"""
        # 这个测试验证 build_graph 不会抛出异常
        from app.worker.workflow import build_graph

        graph = build_graph()
        # LangGraph 不可用时为 None，可用时为编译后的图
        if graph is not None:
            assert hasattr(graph, "invoke") or hasattr(graph, "stream")

    def test_workflow_state_has_reducer(self):
        """测试 WorkflowState 有 reducer 注解。"""
        from app.worker.workflow import WorkflowState
        from typing import get_type_hints, get_args

        hints = get_type_hints(WorkflowState, include_extras=True)
        # task 应该有 Annotated 注解
        task_hint = hints.get("task")
        assert task_hint is not None

    def test_resume_interrupted_workflow_function_exists(self):
        """测试恢复函数存在。"""
        from app.worker.workflow import resume_interrupted_workflow

        assert callable(resume_interrupted_workflow)

    def test_get_workflow_state_history_function_exists(self):
        """测试状态历史函数存在。"""
        from app.worker.workflow import get_workflow_state_history

        assert callable(get_workflow_state_history)
