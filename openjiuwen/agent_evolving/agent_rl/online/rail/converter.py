# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Convert Rail-collected trajectories into online RL rail-v1 batches."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from openjiuwen.agent_evolving.trajectory import LLMCallDetail, Trajectory

from .llm_response import extract_logprobs, extract_prompt_ids, extract_token_ids


def _model_dump(value: Any) -> dict[str, Any] | None:
    if not hasattr(value, "model_dump"):
        return None
    try:
        dumped = value.model_dump()
    except Exception:
        return None
    return dumped if isinstance(dumped, dict) else None


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    dumped = _model_dump(value)
    if dumped is not None:
        return _json_value(dumped)
    return str(value)


def _message_to_dict(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        return _json_value(message)
    dumped = _model_dump(message)
    if dumped is not None:
        return _json_value(dumped)
    if getattr(message, "role", None) is not None:
        out = {
            "role": str(getattr(message, "role", "unknown")),
            "content": _json_value(getattr(message, "content", "")),
        }
        name = getattr(message, "name", None)
        if name is not None:
            out["name"] = str(name)
        metadata = getattr(message, "metadata", None)
        if metadata:
            out["metadata"] = _json_value(metadata)
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            out["tool_calls"] = _json_value(tool_calls)
        return out
    return {"role": "unknown", "content": str(message)}


def _response_to_dict(response: Any) -> dict[str, Any]:
    if response is None:
        return {}
    if isinstance(response, dict):
        return _json_value(response)
    dumped = _model_dump(response)
    if dumped is not None:
        return _json_value(dumped)

    out: dict[str, Any] = {
        "role": getattr(response, "role", "assistant"),
        "content": getattr(response, "content", ""),
    }
    tool_calls = getattr(response, "tool_calls", None)
    if tool_calls is not None:
        out["tool_calls"] = _json_value(tool_calls)
    usage = getattr(response, "usage_metadata", None) or getattr(response, "usage", None)
    if usage is not None:
        out["usage"] = _json_value(usage)
    finish_reason = getattr(response, "finish_reason", None)
    if finish_reason is not None:
        out["finish_reason"] = finish_reason
    reasoning_content = getattr(response, "reasoning_content", None)
    if reasoning_content is not None:
        out["reasoning_content"] = reasoning_content
    return out


def _extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str) and item:
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str) and text:
                    parts.append(text)
        return "\n".join(parts)
    return str(value)


def _fingerprint_payload(messages: list[dict[str, Any]], tools: Any) -> dict[str, Any]:
    raw = json.dumps(
        {"messages": messages, "tools": _json_value(tools)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "type": "rail-local-sha256",
        "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    }


@dataclass
class PerTurnSample:
    trajectory_id: str
    step_index: int
    session_id: str
    model_id: str
    messages: list[dict[str, Any]]
    response: dict[str, Any]
    response_text: str
    response_tokens: Optional[list[int]] = None
    logprobs: Optional[list[float]] = None
    prompt_ids: Optional[list[int]] = None
    render_fingerprint: dict[str, Any] = field(default_factory=dict)
    tools: Any = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class TrajectoryMeta:
    trajectory_id: str
    session_id: str
    status: str = "ok"
    total_turns: int = 0
    started_at: float = field(default_factory=time.time)
    ended_at: float = field(default_factory=time.time)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RailV1Batch:
    protocol_version: str
    session_id: str
    tenant_id: Optional[str]
    trajectory_id: str
    model_id: str
    samples: list[PerTurnSample]
    trajectory_meta: TrajectoryMeta
    prev_feedback: Optional[dict[str, Any]] = None
    session_done: bool = False

    def to_dict(self) -> dict[str, Any]:
        return _json_value(asdict(self))


class OnlineTrajectoryConverter:
    """Convert a complete Rail trajectory into a rail-v1 upload payload."""

    def __init__(
        self,
        *,
        tenant_id: Optional[str] = None,
        model_id: Optional[str] = None,
        session_done: bool = False,
    ) -> None:
        self.tenant_id = tenant_id
        self.model_id = model_id
        self.session_done = session_done

    def convert(
        self,
        trajectory: Trajectory,
        *,
        tenant_id: Optional[str] = None,
        session_done: Optional[bool] = None,
    ) -> RailV1Batch:
        trajectory_id = trajectory.execution_id
        session_id = str(trajectory.session_id or "")
        samples: list[PerTurnSample] = []
        model_id = self.model_id or ""

        for step_index, step in enumerate(trajectory.steps):
            if step.kind != "llm" or not isinstance(step.detail, LLMCallDetail):
                continue
            detail = step.detail
            model_id = model_id or detail.model or ""
            messages = [_message_to_dict(message) for message in (detail.messages or [])]
            response = _response_to_dict(detail.response)
            response_text = _extract_text(response.get("content"))
            if not response_text.strip() and not response:
                continue

            detail_meta = dict(getattr(detail, "meta", {}) or {})
            provider_response_json = detail_meta.get("provider_response_json")
            token_source = provider_response_json or detail.response
            # Prefer top-level step fields populated during trajectory
            # collection; fall back to ``provider_response_json`` (vLLM raw
            # payload) or any token data still on ``detail.response``.
            response_tokens = step.completion_token_ids or extract_token_ids(token_source)
            prompt_ids = (
                step.prompt_token_ids
                or step.meta.get("prompt_ids")
                or extract_prompt_ids(token_source)
            )
            logprobs = step.logprobs or extract_logprobs(token_source)
            sample = PerTurnSample(
                trajectory_id=trajectory_id,
                step_index=step_index,
                session_id=session_id,
                model_id=detail.model or model_id or "",
                messages=messages,
                response=response,
                response_text=response_text,
                response_tokens=response_tokens,
                logprobs=logprobs,
                prompt_ids=prompt_ids,
                render_fingerprint=step.meta.get("render_fingerprint") or _fingerprint_payload(messages, detail.tools),
                tools=_json_value(detail.tools),
                meta={**detail_meta, **dict(step.meta or {})},
            )
            samples.append(sample)

        status = str((trajectory.meta or {}).get("status") or "ok")
        meta = TrajectoryMeta(
            trajectory_id=trajectory_id,
            session_id=session_id,
            status=status,
            total_turns=len(samples),
            extra={
                **dict(trajectory.meta or {}),
                "source": trajectory.source,
                "case_id": trajectory.case_id,
                "cost": trajectory.cost,
            },
        )
        return RailV1Batch(
            protocol_version="rail-v1",
            session_id=session_id,
            tenant_id=tenant_id if tenant_id is not None else self.tenant_id,
            trajectory_id=trajectory_id,
            model_id=model_id or "",
            samples=samples,
            trajectory_meta=meta,
            prev_feedback=self.extract_prev_feedback(trajectory),
            session_done=self.session_done if session_done is None else bool(session_done),
        )

    @staticmethod
    def extract_prev_feedback(trajectory: Trajectory) -> Optional[dict[str, Any]]:
        """Use the first user message in the new batch as previous-turn feedback."""
        for step in trajectory.steps:
            if step.kind != "llm" or not isinstance(step.detail, LLMCallDetail):
                continue
            for message in step.detail.messages or []:
                msg = _message_to_dict(message)
                if msg.get("role") != "user":
                    continue
                raw_user_text = _extract_text(msg.get("content")).strip()
                if not raw_user_text:
                    return None
                return {
                    "raw_user_text": raw_user_text,
                    "source": "first_user_msg_of_next_batch",
                }
        return None
