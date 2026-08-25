# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Convert Rail-collected trajectories into online RL rail-v1 batches."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import CASE_ID, TRAJECTORY_SOURCE
from openjiuwen.agent_evolving.trajectory.spans import (
    decode_json_attribute,
    iter_spans,
    read_llm_exchange,
    read_rl_fields,
    read_usage,
    span_attributes,
)
from openjiuwen.agent_evolving.trajectory.team import span_category
from openjiuwen.extensions.observability import semconv

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


def _span_meta(span: dict[str, Any], attrs: dict[str, Any]) -> dict[str, Any]:
    """Build detached per-span metadata for the rail-v1 sample."""

    meta = {str(key): _json_value(value) for key, value in attrs.items()}
    for key in ("span_name", "span_id", "parent_span_id", "trace_id"):
        source_key = {
            "span_name": "name",
            "span_id": "spanId",
            "parent_span_id": "parentSpanId",
            "trace_id": "traceId",
        }[key]
        if span.get(source_key) is not None:
            meta.setdefault(key, _json_value(span[source_key]))
    return meta


def _trajectory_cost(trajectory: Trajectory) -> Optional[dict[str, int]]:
    input_tokens = 0
    output_tokens = 0
    for span in iter_spans(trajectory):
        if span_category(span) != "llm":
            continue
        usage = read_usage(span)
        input_tokens += usage.get("prompt_tokens", 0)
        output_tokens += usage.get("completion_tokens", 0)
    if input_tokens <= 0 and output_tokens <= 0:
        return None
    return {"input_tokens": input_tokens, "output_tokens": output_tokens}


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
        trajectory_id = trajectory.trajectory_id
        session_id = str(trajectory.session_id or "")
        samples: list[PerTurnSample] = []
        model_id = self.model_id or ""

        for step_index, span in enumerate(span for span in iter_spans(trajectory) if span_category(span) == "llm"):
            attrs = span_attributes(span)
            prompt_messages, completion_messages = read_llm_exchange(span)
            model = str(attrs.get(semconv.GEN_AI_REQUEST_MODEL) or span.get("name") or "")
            tools = decode_json_attribute(attrs.get(semconv.GEN_AI_TOOL_DEFINITIONS))
            response_message = completion_messages[-1] if completion_messages else None
            response = _json_value(response_message) if response_message is not None else {}
            model_id = model_id or model
            response_text = _extract_text(response.get("content"))
            if not response_text.strip() and not response:
                continue

            detail_meta = _span_meta(span, attrs)
            provider_response_json = decode_json_attribute(detail_meta.get("provider_response_json"))
            token_source = provider_response_json or response_message
            rl_fields = read_rl_fields(span)
            # Prefer immutable RL attributes captured on the canonical span;
            # fall back to provider metadata when a service exposes raw data.
            response_tokens = rl_fields.get("completion_token_ids") or extract_token_ids(token_source)
            prompt_ids = (
                rl_fields.get("prompt_token_ids")
                or extract_prompt_ids({"prompt_ids": detail_meta.get("prompt_ids")})
                or extract_prompt_ids(token_source)
            )
            logprobs = rl_fields.get("logprobs") or extract_logprobs(token_source)
            messages = [_json_value(message) for message in prompt_messages]
            sample = PerTurnSample(
                trajectory_id=trajectory_id,
                step_index=step_index,
                session_id=session_id,
                model_id=model or model_id or "",
                messages=messages,
                response=response,
                response_text=response_text,
                response_tokens=response_tokens,
                logprobs=logprobs,
                prompt_ids=prompt_ids,
                render_fingerprint=decode_json_attribute(detail_meta.get("render_fingerprint"))
                or _fingerprint_payload(messages, tools),
                tools=_json_value(tools),
                meta=detail_meta,
            )
            samples.append(sample)

        trajectory_attrs = trajectory.resource_attributes
        status = str((trajectory_attrs or {}).get("status") or "ok")
        meta = TrajectoryMeta(
            trajectory_id=trajectory_id,
            session_id=session_id,
            status=status,
            total_turns=len(samples),
            extra={
                **dict(trajectory_attrs or {}),
                "source": str(
                    (trajectory_attrs or {}).get(TRAJECTORY_SOURCE)
                    or (trajectory_attrs or {}).get("source")
                    or "offline"
                ),
                "case_id": trajectory_attrs.get(CASE_ID) if trajectory_attrs else None,
                "cost": _trajectory_cost(trajectory),
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
        for span in iter_spans(trajectory):
            if span_category(span) != "llm":
                continue
            prompt_messages, _ = read_llm_exchange(span)
            for message in prompt_messages:
                msg = message
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
