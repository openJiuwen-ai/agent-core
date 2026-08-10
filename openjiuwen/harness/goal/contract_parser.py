# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Contract drafting and parsing for the goal completion contract mechanism.

Two entry points (host calls these before ``GoalManager.set``; the manager
itself never invokes an LLM, to keep the control lock unheld):

- ``draft_contract(objective, model, language)`` — auxiliary LLM call that
  turns a vague objective into a structured ``GoalContract`` JSON. Mirrors the
  ``_invoke_transcript_assessor`` model.invoke pattern (tools=[], temp=0).
  Failure degrades to an empty contract (does not block the host's ``set``).
- ``parse_contract_from_text(text)`` — inline ``verify:``/``constraints:``/
  ``boundaries:``/``stop when:`` extraction, returns ``(objective, contract)``.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

from openjiuwen.core.common.logging import LazyLogger, LogManager
from openjiuwen.core.foundation.llm import SystemMessage, UserMessage
from openjiuwen.harness.goal.schema import GoalContract

logger = LazyLogger(lambda: LogManager.get_logger("goal"))

_JSON_BLOCK_PATTERN = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?\s*```",
    re.DOTALL,
)

DRAFT_CONTRACT_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": (
        "你将用户的自然语言目标转换为自主编码代理的结构化完成契约。契约有五个字段：\n"
        "- outcome: 完成时必须为真的单一结束状态\n"
        "- verification: 证明成果的具体测试/命令/工件（必须具体可检查）\n"
        "- constraints: 不能改变或退化的内容\n"
        "- boundaries: 哪些文件、目录、工具或系统在范围内\n"
        "- stop_when: 代理应停止并请求人工输入而非继续推进的条件\n\n"
        "从目标和隐含的项目上下文中推断合理、具体的值。优先选择具体的验证方式"
        "（命名的测试命令、构建、基准测试），而非模糊短语。每个字段保持一到两句话。"
        "如果某个字段确实无法推断，使用空字符串。\n\n"
        "仅用一行回复单个 JSON 对象：\n"
        '{"outcome": "...", "verification": "...", "constraints": "...", '
        '"boundaries": "...", "stop_when": "..."}'
    ),
    "en": (
        "You turn a user's plain-language objective into a structured completion "
        "contract for an autonomous coding agent. The contract has five fields:\n"
        "- outcome: the single end state that must be true when done\n"
        "- verification: the specific test / command / artifact that PROVES the "
        "outcome (must be concrete and checkable)\n"
        "- constraints: what must NOT change or regress\n"
        "- boundaries: which files, dirs, tools, or systems are in scope\n"
        "- stop_when: the condition under which the agent should stop and ask "
        "for human input instead of pushing on\n\n"
        "Infer sensible, specific values from the objective and any project "
        "context implied by it. Prefer concrete verification (a named test "
        "command, a build, a benchmark) over vague phrases. Keep each field to "
        "one or two sentences. If a field genuinely cannot be inferred, use an "
        "empty string for it.\n\n"
        "Reply ONLY with a single JSON object on one line:\n"
        '{"outcome": "...", "verification": "...", "constraints": "...", '
        '"boundaries": "...", "stop_when": "..."}'
    ),
}


def _parse_contract_json(text: str) -> Optional[Dict[str, Any]]:
    """Parse a contract JSON object from raw model output.

    Three-layer fallback (mirrors ``_parse_assessment_json``): bare JSON →
    ``json fence → outermost ``{ ... }`` span. Robust to fenced output and
    nested code blocks inside field values.
    """
    text = text.strip()
    if not text:
        return None
    data: Optional[Dict[str, Any]] = None
    try:
        data = json.loads(text)
    except ValueError:
        match = _JSON_BLOCK_PATTERN.search(text)
        if match:
            try:
                data = json.loads(match.group(1).strip())
            except ValueError:
                data = None
    if not isinstance(data, dict):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(text[start : end + 1])
            except ValueError:
                data = None
    return data if isinstance(data, dict) else None


async def draft_contract(
    objective: str,
    model: Any,
    language: str = "cn",
) -> GoalContract:
    """Draft a structured contract from a vague objective via an auxiliary LLM call.

    Mirrors ``TaskCompletionRail._invoke_transcript_assessor``: same model.invoke
    pattern (tools=[], temperature=0.0, top_p=1.0) with TypeError fallback to a
    no-sampling-kwargs call for custom model objects. Failure degrades to an empty
    contract — never raises, so the host's ``GoalManager.set`` is never blocked.
    """
    if not objective.strip():
        return GoalContract()
    system_prompt = DRAFT_CONTRACT_SYSTEM_PROMPT.get(
        language, DRAFT_CONTRACT_SYSTEM_PROMPT["cn"]
    )
    try:
        response = await model.invoke(
            [
                SystemMessage(content=system_prompt),
                UserMessage(content=objective),
            ],
            tools=[],
            temperature=0.0,
            top_p=1.0,
        )
    except TypeError:
        try:
            response = await model.invoke(
                [
                    SystemMessage(content=system_prompt),
                    UserMessage(content=objective),
                ],
                tools=[],
            )
        except Exception:
            logger.exception("[draft_contract] auxiliary model invocation failed")
            return GoalContract()
    except Exception:
        logger.exception("[draft_contract] auxiliary model invocation failed")
        return GoalContract()

    content = getattr(response, "content", None)
    text = content if isinstance(content, str) else str(content or "")
    data = _parse_contract_json(text)
    if not isinstance(data, dict):
        logger.info("[draft_contract] failed to parse contract JSON, returning empty")
        return GoalContract()
    return GoalContract.from_dict(data)


_INLINE_FIELD_MAP = {
    "verify": "verification",
    "verification": "verification",
    "constraint": "constraints",
    "constraints": "constraints",
    "boundary": "boundaries",
    "boundaries": "boundaries",
    "stop_when": "stop_when",
    "stop when": "stop_when",
}

_INLINE_LINE_PATTERN = re.compile(
    r"^\s*(verify|verification|constraint|constraints|boundary|boundaries|stop\s+when)\s*:\s*(.+?)\s*$",
    re.IGNORECASE,
)


def parse_contract_from_text(text: str) -> Tuple[str, GoalContract]:
    """Extract inline contract fields from natural-language text.

    Supports ``verify:``/``verification:``/``constraints:``/``boundaries:``/
    ``stop when:`` lines (case-insensitive). Returns ``(objective_without_inline,
    contract)`` — the objective has inline contract lines stripped so it can be
    passed to ``GoalManager.set`` separately from the contract.
    """
    contract = GoalContract()
    objective_lines: list[str] = []
    for line in text.splitlines():
        match = _INLINE_LINE_PATTERN.match(line)
        if match:
            key = re.sub(r"\s+", "_", match.group(1).lower().strip())
            field = _INLINE_FIELD_MAP.get(key)
            if field:
                setattr(contract, field, match.group(2).strip())
                continue
        objective_lines.append(line)
    objective = "\n".join(objective_lines).strip()
    return objective, contract


__all__ = [
    "DRAFT_CONTRACT_SYSTEM_PROMPT",
    "draft_contract",
    "parse_contract_from_text",
]
