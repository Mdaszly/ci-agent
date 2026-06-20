"""JSON 稳定输出模块。

实现大厂面试要求的"四层防御"确保 LLM 稳定输出 JSON：
1. Prompt 约束（明确要求只输出 JSON + Few-shot）
2. JSON Mode（调用 LLM 时启用 response_format）
3. 解析层约束（json.loads + markdown code fence 剥离）
4. 业务层校验（Pydantic Schema 校验 + 失败重试）

设计原则：
- 可复用：所有需要 LLM 输出 JSON 的节点都可调用
- 可降级：校验失败时返回 None 或默认值
- 可观测：记录每次校验失败的原因
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Type, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# JSON 提取正则：匹配 ```json ... ``` 或 ``` ... ``` 代码块
_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)
# 匹配裸 JSON 对象/数组
_BARE_JSON_RE = re.compile(r"(\{.*\}|\[.*\])", re.DOTALL)


def extract_json_from_text(text: str) -> str | None:
    """从 LLM 输出文本中提取 JSON 字符串。

    按优先级尝试：
    1. 直接解析（理想情况）
    2. 从 ```json ... ``` 代码块提取
    3. 从 ``` ... ``` 代码块提取
    4. 从裸文本中提取 { ... } 或 [ ... ]

    Args:
        text: LLM 输出的原始文本

    Returns:
        JSON 字符串或 None
    """
    if not text or not text.strip():
        return None

    text = text.strip()

    # 1. 直接就是合法 JSON
    if text.startswith("{") or text.startswith("["):
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass

    # 2. 从 ```json ... ``` 提取
    match = _CODE_FENCE_RE.search(text)
    if match:
        candidate = match.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    # 3. 从裸文本提取 { ... } 或 [ ... ]
    match = _BARE_JSON_RE.search(text)
    if match:
        candidate = match.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    return None


def parse_json_safely(text: str) -> dict | list | None:
    """安全解析 JSON 文本。

    Args:
        text: LLM 输出文本

    Returns:
        解析后的 dict/list 或 None
    """
    json_str = extract_json_from_text(text)
    if json_str is None:
        return None
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.warning(f"JSON 解析失败: {e}, text={text[:100]}...")
        return None


def validate_with_schema(data: dict, schema: Type[T]) -> T | None:
    """用 Pydantic Schema 校验数据。

    Args:
        data: 待校验的字典
        schema: Pydantic 模型类

    Returns:
        校验通过的模型实例或 None
    """
    try:
        return schema.model_validate(data)
    except ValidationError as e:
        logger.warning(f"Pydantic 校验失败: {e}")
        return None


def parse_and_validate(
    text: str,
    schema: Type[T],
    *,
    max_retries: int = 2,
    retry_callback=None,
) -> T | None:
    """解析 + 校验 + 重试的完整流程。

    四层防御：
    1. extract_json_from_text（解析层）
    2. parse_json_safely（安全解析）
    3. validate_with_schema（业务校验）
    4. 失败时调用 retry_callback 重新生成

    Args:
        text: LLM 输出文本
        schema: Pydantic 模型类
        max_retries: 最大重试次数
        retry_callback: 重试回调函数，签名 (error_msg: str) -> str，返回新的 LLM 输出

    Returns:
        校验通过的模型实例或 None
    """
    current_text = text

    for attempt in range(max_retries + 1):
        # 第一层：提取 JSON
        json_str = extract_json_from_text(current_text)
        if json_str is None:
            logger.warning(f"第 {attempt + 1} 次尝试：无法从文本提取 JSON")
            if attempt < max_retries and retry_callback:
                error_msg = f"无法从你的输出中提取 JSON。请只输出合法 JSON，不要包含任何解释文字。上次输出: {current_text[:200]}"
                current_text = retry_callback(error_msg)
                continue
            return None

        # 第二层：安全解析
        data = parse_json_safely(current_text)
        if data is None:
            logger.warning(f"第 {attempt + 1} 次尝试：JSON 解析失败")
            if attempt < max_retries and retry_callback:
                error_msg = f"JSON 解析失败。请检查格式。上次输出: {current_text[:200]}"
                current_text = retry_callback(error_msg)
                continue
            return None

        # 第三层：Pydantic 校验
        result = validate_with_schema(data, schema)
        if result is not None:
            return result

        # 校验失败
        if attempt < max_retries and retry_callback:
            error_msg = f"数据校验失败，请按 Schema 要求重新输出。上次输出: {current_text[:200]}"
            current_text = retry_callback(error_msg)
            continue

    return None


def build_json_prompt(system_prompt: str, schema_hint: str, few_shot: str = "") -> str:
    """构造强制 JSON 输出的 Prompt。

    Args:
        system_prompt: 系统提示词
        schema_hint: Schema 描述（字段名、类型、约束）
        few_shot: 可选的 Few-shot 示例

    Returns:
        完整的 Prompt
    """
    prompt = f"""{system_prompt}

【输出格式要求】
你必须只输出合法的 JSON，不要输出任何解释文字、markdown 标记或代码块标记。
不要在 JSON 前后添加任何内容。

【Schema 要求】
{schema_hint}

"""
    if few_shot:
        prompt += f"""【示例】
{few_shot}

"""
    prompt += "请直接输出 JSON："
    return prompt
