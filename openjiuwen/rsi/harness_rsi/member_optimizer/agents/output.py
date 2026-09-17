# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Structured output helpers for Member Optimizer Agents."""

from __future__ import annotations

import json
import re
from typing import Any, Callable

import yaml

from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.rsi.harness_rsi.model_call import model_output_has_mojibake

_FENCED_MAPPING_RE = re.compile(
    r"```(?:json|yaml|yml)?\s*(\{[\s\S]*?\})\s*```",
    re.IGNORECASE,
)
_BARE_MAPPING_RE = re.compile(r"(\{[\s\S]*\})", re.DOTALL)

ParseResponse = Callable[[str], dict[str, Any]]
ValidateResponse = Callable[[dict[str, Any]], list[str]]
BuildRetryMessage = Callable[[Any, Any], str]


def extract_agent_text(response: Any) -> str:
    """Normalize DeepAgent-style response dicts into text."""
    if isinstance(response, dict):
        for key in ("text", "output", "content", "answer", "response", "result"):
            value = response.get(key)
            if value is None:
                continue
            text = str(value)
            if text.strip():
                return text
    return str(response or "")


def parse_json_object_response(text: str) -> dict[str, Any]:
    """Extract a JSON object from an agent text response."""
    payload = _extract_mapping_text(text)
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("agent response must be a JSON object")
    return parsed


def parse_yaml_or_json_object_response(text: str) -> dict[str, Any]:
    """Extract a YAML or JSON mapping from an agent text response."""
    stripped = text.strip()
    for pattern in (
        r"```(?:yaml|yml)\s*([\s\S]*?)\s*```",
        r"```json\s*([\s\S]*?)\s*```",
    ):
        match = re.search(pattern, stripped, re.IGNORECASE)
        if match:
            parsed = yaml.safe_load(match.group(1)) or {}
            if not isinstance(parsed, dict):
                raise ValueError("agent response must be a YAML/JSON mapping")
            return parsed

    if _BARE_MAPPING_RE.search(stripped):
        return parse_json_object_response(stripped)

    parsed = yaml.safe_load(stripped) or {}
    if not isinstance(parsed, dict):
        raise ValueError("agent response must be a YAML/JSON mapping")
    return parsed


async def invoke_member_optimizer_agent_structured(
    *,
    agent: Any,
    agent_name: str,
    user_message: str,
    session_id: str,
    retry_limit: int,
    parse_response: ParseResponse,
    validate_response: ValidateResponse | None = None,
    build_retry_message: BuildRetryMessage | None = None,
) -> dict[str, Any]:
    """Invoke a Member Optimizer Agent and retry until structured output validates."""
    attempt = 0
    message = user_message
    last_error = ""
    previous: dict[str, Any] = {}

    while attempt <= retry_limit:
        try:
            session = Session(
                session_id=session_id,
                card=getattr(agent, "card", None) or AgentCard(name=agent_name),
            )
            response = await agent.invoke(
                inputs={"query": message},
                session=session,
            )
            raw_text = extract_agent_text(response)
            if model_output_has_mojibake(raw_text):
                raise ValueError("agent response contains mojibake")
            raw = parse_response(raw_text)
            errors = validate_response(raw) if validate_response is not None else []
            if not errors:
                return raw
            last_error = f"validation errors: {'; '.join(errors)}"
            previous = raw
        except (yaml.YAMLError, KeyError, TypeError, ValueError) as exc:
            last_error = f"parse error: {exc}"
            previous = {}
        except Exception as exc:
            last_error = f"agent error: {exc}"
            previous = {}

        attempt += 1
        if attempt > retry_limit:
            break
        message = (
            build_retry_message(previous, last_error)
            if build_retry_message is not None
            else _default_retry_message(user_message, previous, last_error)
        )

    raise RuntimeError(f"{agent_name} failed after {retry_limit + 1} attempts. Last error: {last_error}")


def _extract_mapping_text(text: str) -> str:
    stripped = text.strip()
    match = _FENCED_MAPPING_RE.search(stripped)
    if match:
        return match.group(1)
    match = _BARE_MAPPING_RE.search(stripped)
    if match:
        return match.group(1)
    return stripped


def _default_retry_message(
    original_message: str,
    previous: dict[str, Any],
    error: str,
) -> str:
    previous_text = json.dumps(previous, ensure_ascii=False, indent=2) if previous else "{}"
    return f"""{original_message}

## Previous Invalid Output

```json
{previous_text}
```

## Validation Error

{error}

Return ONLY a corrected JSON or YAML object matching the agent output schema.
"""


__all__ = [
    "BuildRetryMessage",
    "ParseResponse",
    "ValidateResponse",
    "extract_agent_text",
    "invoke_member_optimizer_agent_structured",
    "parse_json_object_response",
    "parse_yaml_or_json_object_response",
]
