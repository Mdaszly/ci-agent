"""LLM 响应解析的共享工具函数。"""
from __future__ import annotations


def strip_code_fence(content: str) -> str:
    """剥离 LLM 返回内容中的 markdown code fence。

    处理形如 ```json\\n...\\n``` 或 ```\\n...\\n``` 的包裹，
    返回 fence 内部的纯文本。若内容不以 ``` 开头则原样返回。
    """
    if not content.startswith("```"):
        return content
    lines = content.split("\n")
    return "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
