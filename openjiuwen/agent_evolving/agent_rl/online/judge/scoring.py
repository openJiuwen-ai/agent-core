# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import math
import re
from numbers import Real
from typing import Any, Optional

JUDGE_PROMPT_TEMPLATE = """你是一个专业的 AI Agent 质量评估器。请对以下 Agent 对话轮次打分。

## 用户指令
{instruction_text}

## Agent 回复
{response_text}

## 用户反馈（下一轮输入）
{followup_user_feedback}

## 评分维度（各 0-10 分）
1. 任务完成度：Agent 是否完成了用户意图？
2. 响应质量：回答是否准确、有帮助、简洁？
3. 工具使用合理性：工具调用是否必要且正确？
4. 对话连贯性：多轮对话是否自然流畅？

请严格以 JSON 格式返回，不要添加任何其他文字：
{{"task_completion": 8, "response_quality": 7, "tool_usage": 9, "coherence": 8, "overall": 8.0, "reason": "..."}}"""

_DIMENSION_ALIASES = (
    ("task_completion", "task_completion_score"),
    ("response_quality", "response_quality_score"),
    ("tool_usage", "tool_usage_score"),
    ("coherence", "coherence_score"),
)


def build_judge_prompt(
    *,
    instruction_text: str = "",
    response_text: str = "",
    followup_user_feedback: str = "",
) -> str:
    """Format the canonical judge prompt."""
    return JUDGE_PROMPT_TEMPLATE.format(
        instruction_text=instruction_text or "(无)",
        response_text=response_text or "(无回复)",
        followup_user_feedback=followup_user_feedback or "(无反馈)",
    )


def parse_judge_scores(content: str, *, raise_on_error: bool = True) -> Optional[dict[str, Any]]:
    """Parse judge JSON output and fill ``overall`` when dimension scores exist."""
    content = content.strip()
    decoder = json.JSONDecoder()
    candidates = [content]
    for block in re.findall(r"```(?:json)?\s*(.*?)```", content, re.DOTALL | re.IGNORECASE):
        block = block.strip()
        if block:
            candidates.append(block)

    for candidate in candidates:
        parsed = _load_json_object(candidate, decoder)
        if isinstance(parsed, dict) and _ensure_overall(parsed):
            return parsed

    if raise_on_error:
        raise ValueError(f"Cannot parse judge response: {content[:200]}")
    return None


def normalize_overall_score(overall: float) -> float:
    """Map raw 0-10 judge score to the RL reward range ``[0, 1]``."""
    return max(0.0, min(1.0, float(overall) / 10.0))


def _load_json_object(content: str, decoder: json.JSONDecoder) -> Optional[dict[str, Any]]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = _extract_first_json_object(content, decoder)
    return parsed if isinstance(parsed, dict) else None


def _extract_first_json_object(content: str, decoder: json.JSONDecoder) -> Optional[dict[str, Any]]:
    for match in re.finditer(r"\{", content):
        try:
            start = match.start()
            parsed, _ = decoder.raw_decode(content[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _ensure_overall(scores: dict[str, Any]) -> bool:
    overall = scores.get("overall")
    if not isinstance(overall, bool) and isinstance(overall, Real) and math.isfinite(overall):
        return True
    values: list[float] = []
    for aliases in _DIMENSION_ALIASES:
        for key in aliases:
            value = scores.get(key)
            if not isinstance(value, bool) and isinstance(value, Real) and math.isfinite(value):
                values.append(float(value))
                break
    if values:
        scores["overall"] = sum(values) / len(values)
        return True
    return False
