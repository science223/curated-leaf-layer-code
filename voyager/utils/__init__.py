from .file_utils import *
from .json_utils import *
from .record_utils import EventRecorder


import re as _re

def parse_llm_json(text: str) -> dict:
    """
    从 LLM 输出中提取 JSON，兼容 DeepSeek/GPT 的 Markdown 代码块包裹。
    支持以下格式：
        ```json { ... } ```
        ``` { ... } ```
        { ... }   （裸 JSON）
    """
    text = text.strip()
    # 去掉 ```json ... ``` 或 ``` ... ``` 包裹
    md_match = _re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if md_match:
        text = md_match.group(1).strip()
    try:
        import json as _json
        return _json.loads(text)
    except _json.JSONDecodeError:
        # 兜底：找第一个合法 JSON 对象
        obj_match = _re.search(r"\{[\s\S]*\}", text)
        if obj_match:
            import json as _json
            return _json.loads(obj_match.group(0))
        raise
