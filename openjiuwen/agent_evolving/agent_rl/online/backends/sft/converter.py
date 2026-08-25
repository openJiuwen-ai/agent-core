# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Convert rail-collected trajectories into session-level SFT raw payloads."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from openjiuwen.agent_evolving.agent_rl.online.backends.sft.sample_builder import (
    SFT_RAW_PROTOCOL_VERSION,
    assistant_text,
    json_safe,
    normalize_assistant_message,
    normalize_messages,
)
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import TRAJECTORY_SOURCE
from openjiuwen.agent_evolving.trajectory.spans import (
    decode_json_attribute,
    iter_spans,
    read_llm_exchange,
    read_tool_call,
    read_usage,
    span_attributes,
)
from openjiuwen.agent_evolving.trajectory.team import span_category
from openjiuwen.extensions.observability import semconv


@dataclass
class SFTRawStep:
    step_index: int
    type: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    response: dict[str, Any] = field(default_factory=dict)
    response_text: str = ""
    model_id: str = ""
    tools: Any = None
    tool_name: str = ""
    tool_args: Any = None
    tool_result: Any = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class SFTRawTrajectoryBatch:
    protocol_version: str
    raw_id: str
    trajectory_id: str
    session_id: str
    tenant_id: str | None
    user_id: str
    model_id: str
    scenario: str
    source: str
    steps: list[SFTRawStep]
    trajectory_meta: dict[str, Any]
    session_done: bool = False
    flush_reason: str = ""
    original_task: str = ""
    dataset_case: dict[str, Any] = field(default_factory=dict)
    workspace_ref: dict[str, Any] = field(default_factory=dict)
    context_compression: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return json_safe(asdict(self))


class SFTRawTrajectoryConverter:
    """Build one session-level SFT raw trajectory from the current rail snapshot."""

    def __init__(
        self,
        *,
        tenant_id: str | None = None,
        scenario: str = "multi_turn_supervisor",
        model_id: str | None = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.scenario = scenario
        self.model_id = model_id

    def convert(
        self,
        trajectory: Trajectory,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        session_done: bool = False,
        flush_reason: str = "",
        original_task: str = "",
        dataset_case: dict[str, Any] | None = None,
        workspace_ref: dict[str, Any] | None = None,
        context_compression: dict[str, Any] | None = None,
    ) -> SFTRawTrajectoryBatch:
        trajectory_id = str(trajectory.trajectory_id or "")
        session_id = str(trajectory.session_id or "")
        raw_steps: list[SFTRawStep] = []
        model_id = self.model_id or ""

        for step_index, span in enumerate(iter_spans(trajectory)):
            attrs = span_attributes(span)
            category = span_category(span)
            if category == "llm":
                prompt_messages, completion_messages = read_llm_exchange(span)
                response = normalize_assistant_message(completion_messages[-1] if completion_messages else {})
                step_model_id = str(
                    attrs.get(semconv.GEN_AI_REQUEST_MODEL)
                    or attrs.get(semconv.GEN_AI_RESPONSE_MODEL)
                    or ""
                )
                model_id = model_id or step_model_id
                meta = self._span_meta(span, attrs)
                raw_steps.append(
                    SFTRawStep(
                        step_index=step_index,
                        type="llm",
                        messages=normalize_messages(prompt_messages),
                        response=response,
                        response_text=assistant_text(response),
                        model_id=step_model_id or model_id or "",
                        tools=json_safe(decode_json_attribute(attrs.get(semconv.GEN_AI_TOOL_DEFINITIONS))),
                        meta=json_safe(meta),
                    )
                )
                continue
            if category == "tool":
                tool_call = read_tool_call(span)
                raw_steps.append(
                    SFTRawStep(
                        step_index=step_index,
                        type="tool",
                        tool_name=str(tool_call.get("name") or ""),
                        tool_args=json_safe(tool_call.get("input")),
                        tool_result=json_safe(tool_call.get("output")),
                        meta=json_safe(self._span_meta(span, attrs)),
                    )
                )

        meta = json_safe(trajectory.resource_attributes)
        if not isinstance(meta, dict):
            meta = {}
        cost = json_safe(self._trajectory_cost(trajectory))
        if isinstance(cost, dict):
            meta.setdefault("cost", cost)

        tenant = tenant_id if tenant_id is not None else self.tenant_id
        normalized_user_id = str(user_id or tenant or meta.get("tenant_id") or "").strip()
        return SFTRawTrajectoryBatch(
            protocol_version=SFT_RAW_PROTOCOL_VERSION,
            raw_id=trajectory_id,
            trajectory_id=trajectory_id,
            session_id=session_id,
            tenant_id=tenant,
            user_id=normalized_user_id,
            model_id=model_id,
            scenario=self.scenario,
            source=str(meta.get(TRAJECTORY_SOURCE) or meta.get("source") or "rl_online"),
            steps=raw_steps,
            trajectory_meta=meta,
            session_done=session_done,
            flush_reason=flush_reason,
            original_task=original_task,
            dataset_case=json_safe(dataset_case or {}),
            workspace_ref=json_safe(workspace_ref or {}),
            context_compression=json_safe(context_compression or {}),
        )

    @staticmethod
    def _span_meta(span: dict[str, Any], attrs: dict[str, Any]) -> dict[str, Any]:
        meta = {str(key): json_safe(value) for key, value in attrs.items()}
        legacy_meta = decode_json_attribute(meta.get("openjiuwen.legacy.step.meta"))
        if isinstance(legacy_meta, dict):
            meta.update(json_safe(legacy_meta))
        for output_key, span_key in (
            ("span_name", "name"),
            ("span_id", "spanId"),
            ("parent_span_id", "parentSpanId"),
            ("trace_id", "traceId"),
        ):
            if span.get(span_key) is not None:
                meta.setdefault(output_key, json_safe(span[span_key]))
        return meta

    @staticmethod
    def _trajectory_cost(trajectory: Trajectory) -> dict[str, int] | None:
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
