# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Extract token-level fields from LLM responses for online Rail (rail-v1).

Single source of truth for:
- :class:`RLOnlineRail` (step hook: fill ``TrajectoryStep`` / ``LLMCallDetail.meta``)
- :class:`OnlineTrajectoryConverter` (fallback when step fields are empty)

See ``docs/zh/.../AgentRL在线Rail复用方案.md`` (token 级数据 / 端云对齐).
"""

from __future__ import annotations

from typing import Any, Optional



def _provider_response_json(response: Any) -> dict[str, Any] | None:
    if isinstance(response, dict):
        return response
    metadata = getattr(response, "metadata", None)
    return metadata if isinstance(metadata, dict) else None


def _first_choice(response_json: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(response_json, dict):
        return None
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    choice = choices[0]
    return choice if isinstance(choice, dict) else None


def _extract_int_list(response: Any, *field_names: str, runtime_field: str | None = None) -> Optional[list[int]]:
    response_json = _provider_response_json(response)
    candidates: list[Any] = []
    if runtime_field and isinstance(response_json, dict):
        candidates.append(response_json.get(runtime_field))
    choice = _first_choice(response_json)
    if isinstance(choice, dict):
        candidates.extend(choice.get(name) for name in field_names)
    if isinstance(response_json, dict):
        candidates.extend(response_json.get(name) for name in field_names)

    for candidate in candidates:
        if isinstance(candidate, list):
            out: list[int] = []
            for item in candidate:
                try:
                    out.append(int(item))
                except (TypeError, ValueError):
                    continue
            if out:
                return out
    return None


def extract_logprobs(response: Any) -> Optional[list[float]]:
    """Best-effort logprob list from an OpenAI-style or dict response."""
    if response is None:
        return None
    response_json = _provider_response_json(response)
    choice = _first_choice(response_json)
    direct = choice.get("logprobs") if isinstance(choice, dict) else None
    if direct is None and isinstance(response_json, dict):
        direct = response_json.get("logprobs")
    if direct is None:
        return None
    if isinstance(direct, list):
        out: list[float] = []
        for item in direct:
            try:
                out.append(float(item))
            except (TypeError, ValueError):
                continue
        return out or None
    content = getattr(direct, "content", None)
    if content is None and isinstance(direct, dict):
        content = direct.get("content")
    if isinstance(content, list):
        out = []
        for item in content:
            try:
                logprob = item.get("logprob") if isinstance(item, dict) else getattr(item, "logprob")
                out.append(float(logprob))
            except (AttributeError, TypeError, ValueError):
                continue
        return out or None
    return None


def extract_token_ids(response: Any) -> Optional[list[int]]:
    """Best-effort response token id list (vLLM ``return_token_ids`` or similar)."""
    if response is None:
        return None
    return _extract_int_list(response, "completion_token_ids", "token_ids", "response_tokens")


def extract_prompt_ids(response: Any) -> Optional[list[int]]:
    """Best-effort prompt token id list from vLLM ``return_token_ids`` payloads."""
    if response is None:
        return None
    return _extract_int_list(response, "prompt_token_ids", "prompt_ids")
