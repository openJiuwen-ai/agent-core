# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Trajectory data model."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from openjiuwen.agent_evolving.trajectory.semconv import (
    CASE_ID,
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OPERATION_NAME,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_TOOL_CALL_ARGUMENTS,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_CALL_RESULT,
    GEN_AI_TOOL_DEFINITIONS,
    GEN_AI_TOOL_NAME,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    LEGACY_OPERATOR_ID,
    LEGACY_PARENT_LLM_CALL,
    LEGACY_STEP_META,
    OJ_AGENT_INVOKE_TYPE,
    OJ_AGENT_NAME,
    OJ_ERROR,
    OJ_INVOKE_ID,
    OJ_PARENT_INVOKE_ID,
    OJ_RL_COMPLETION_TOKEN_IDS,
    OJ_RL_LOGPROBS,
    OJ_RL_PROMPT_TOKEN_IDS,
    OJ_RL_REWARD,
    OJ_SESSION_ID,
    OJ_WORKFLOW_COMPONENT_TYPE,
    OLD_OJ_SESSION_ID,
    OLD_TRAJECTORY_ID,
    TRAJECTORY_END_REASON,
    TRAJECTORY_ID,
    TRAJECTORY_INVOKE_TYPE,
    TRAJECTORY_SCHEMA_VERSION,
    TRAJECTORY_SCHEMA_VERSION_ATTR,
    TRAJECTORY_SOURCE,
    TRAJECTORY_STEP_KIND,
)
from openjiuwen.agent_evolving.trajectory.span_codec import (
    normalize_trace_id_hex,
    otlp_value_to_python,
    to_otlp_value,
)

# --- Common types ---
StepKind = Literal["llm", "tool"]
CostInfo = Dict[str, int]  # {"input_tokens": N, "output_tokens": M}
UpdateKey = Tuple[str, str]  # (operator_id, target)
Updates = Dict[UpdateKey, Any]

RESOURCE_META_EXCLUDE_KEYS = frozenset(
    {
        "service.name",
        TRAJECTORY_ID,
        OLD_TRAJECTORY_ID,
        TRAJECTORY_SCHEMA_VERSION_ATTR,
        OJ_SESSION_ID,
        OLD_OJ_SESSION_ID,
        CASE_ID,
        TRAJECTORY_SOURCE,
        "source",
        TRAJECTORY_END_REASON,
    }
)


def _json_safe(value: Any) -> Any:
    """Convert common message/tool-call objects to plain JSON-like values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
        except Exception:
            dumped = None
        if isinstance(dumped, dict):
            return _json_safe(dumped)

    return str(value)


def _otlp_resource_attributes(otlp_trace: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(otlp_trace, dict):
        return {}
    resource_spans = otlp_trace.get("resourceSpans") or []
    if not resource_spans:
        return {}
    resource = resource_spans[0].get("resource") or {}
    attributes = resource.get("attributes") or []
    return {
        item.get("key"): otlp_value_to_python(item.get("value") or {})
        for item in attributes
        if item.get("key")
    }


def _otlp_spans(otlp_trace: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(otlp_trace, dict):
        return []
    spans: List[Dict[str, Any]] = []
    for resource_span in otlp_trace.get("resourceSpans") or []:
        for scope_span in resource_span.get("scopeSpans") or []:
            spans.extend(scope_span.get("spans") or [])
    return spans


def _otlp_attribute_map(attributes: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        item.get("key"): otlp_value_to_python(item.get("value") or {})
        for item in attributes or []
        if item.get("key")
    }


def _otlp_span_attribute_maps(otlp_trace: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_otlp_attribute_map(span.get("attributes") or []) for span in _otlp_spans(otlp_trace)]


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _otlp_usage_cost(otlp_trace: Optional[Dict[str, Any]]) -> Optional[CostInfo]:
    input_tokens = 0
    output_tokens = 0

    for attrs in _otlp_span_attribute_maps(otlp_trace):
        input_value = _to_int(attrs.get(GEN_AI_USAGE_INPUT_TOKENS))
        output_value = _to_int(attrs.get(GEN_AI_USAGE_OUTPUT_TOKENS))
        if input_value is not None:
            input_tokens += input_value
        if output_value is not None:
            output_tokens += output_value

    if input_tokens <= 0 and output_tokens <= 0:
        return None
    return {"input_tokens": input_tokens, "output_tokens": output_tokens}


def _otlp_trajectory_id(resource_attrs: Dict[str, Any]) -> str:
    value = resource_attrs.get(TRAJECTORY_ID) or resource_attrs.get(OLD_TRAJECTORY_ID)
    return str(value or "")


def _otlp_trajectory_source(resource_attrs: Dict[str, Any]) -> str:
    value = resource_attrs.get(TRAJECTORY_SOURCE) or resource_attrs.get("source")
    return str(value or "offline")


def _legacy_trajectory_source(legacy: "LegacyTrajectory") -> str:
    value = legacy.source
    legacy_meta_source = legacy.meta.get("source")
    if value == "offline" and legacy_meta_source is not None:
        return str(legacy_meta_source)
    return value


def _otlp_trajectory_meta(resource_attrs: Dict[str, Any]) -> Dict[str, Any]:
    meta = {
        key: deepcopy(value)
        for key, value in resource_attrs.items()
        if key not in RESOURCE_META_EXCLUDE_KEYS
    }

    aliases = {
        "member_id": "openjiuwen.member.id",
        "member_name": "openjiuwen.member.name",
        "member_role": "openjiuwen.member.role",
        "team_id": "openjiuwen.team.id",
        "team_name": "openjiuwen.team.name",
    }
    for target, source in aliases.items():
        if source in resource_attrs and target not in meta:
            meta[target] = deepcopy(resource_attrs[source])
    return meta


def _otlp_legacy_step_kind(span: Dict[str, Any], attrs: Dict[str, Any]) -> Optional[str]:
    operation_name = str(attrs.get(GEN_AI_OPERATION_NAME) or "").lower()
    if operation_name in ("chat", "text_completion", "generate_content"):
        return "llm"
    if operation_name == "execute_tool":
        return "tool"
    if attrs.get(GEN_AI_INPUT_MESSAGES) is not None or attrs.get(GEN_AI_OUTPUT_MESSAGES) is not None:
        return "llm"
    if (
        attrs.get(GEN_AI_TOOL_NAME) is not None
        or attrs.get(GEN_AI_TOOL_CALL_ARGUMENTS) is not None
        or attrs.get(GEN_AI_TOOL_CALL_RESULT) is not None
    ):
        return "tool"

    invoke_type = str(
        attrs.get(TRAJECTORY_INVOKE_TYPE)
        or attrs.get(OJ_AGENT_INVOKE_TYPE)
        or ""
    ).lower()
    component_type = str(attrs.get(OJ_WORKFLOW_COMPONENT_TYPE) or "").lower()
    if invoke_type == "llm" or component_type == "llm":
        return "llm"
    if invoke_type in ("plugin", "tool") or component_type in ("tool", "plugin"):
        return "tool"

    explicit = attrs.get(TRAJECTORY_STEP_KIND)
    if explicit in ("llm", "tool"):
        return explicit

    span_name = str(span.get("name") or "").lower()
    if span_name.startswith("llm.") or span_name == "llm.call":
        return "llm"
    if span_name.startswith("tool.") or span_name.startswith("execute_tool "):
        return "tool"
    return None


def _otlp_legacy_usage(attrs: Dict[str, Any]) -> Optional[Dict[str, int]]:
    input_tokens = _to_int(attrs.get(GEN_AI_USAGE_INPUT_TOKENS))
    output_tokens = _to_int(attrs.get(GEN_AI_USAGE_OUTPUT_TOKENS))
    if input_tokens is None and output_tokens is None:
        return None
    return {
        "prompt_tokens": input_tokens or 0,
        "completion_tokens": output_tokens or 0,
    }


def _otlp_span_error(span: Dict[str, Any], attrs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    error = attrs.get(OJ_ERROR)
    if isinstance(error, dict):
        return deepcopy(error)
    status = span.get("status") or {}
    if status.get("code") == "STATUS_CODE_ERROR":
        return {"message": status.get("message", "")}
    return None


def _otlp_legacy_reward(attrs: Dict[str, Any]) -> Optional[float]:
    reward = attrs.get(OJ_RL_REWARD)
    if reward is None:
        return None
    try:
        return float(reward)
    except (TypeError, ValueError):
        return None


def _nanos_to_ms(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value) // 1_000_000
    except (TypeError, ValueError):
        return None


def _otlp_legacy_meta(span: Dict[str, Any], attrs: Dict[str, Any]) -> Dict[str, Any]:
    legacy_meta = attrs.get(LEGACY_STEP_META)
    meta = deepcopy(legacy_meta) if isinstance(legacy_meta, dict) else {}
    meta.setdefault(
        "operator_id",
        attrs.get(LEGACY_OPERATOR_ID)
        or attrs.get(GEN_AI_TOOL_NAME)
        or attrs.get(OJ_AGENT_NAME)
        or span.get("name"),
    )
    meta.setdefault("span_name", span.get("name"))
    meta.setdefault("invoke_id", attrs.get(OJ_INVOKE_ID))
    meta.setdefault("parent_invoke_id", attrs.get(OJ_PARENT_INVOKE_ID))
    meta.setdefault("span_id", span.get("spanId"))
    meta.setdefault("parent_span_id", span.get("parentSpanId"))
    meta.setdefault("status", span.get("status"))
    meta["attributes"] = deepcopy(attrs)
    return meta


def _otlp_llm_step(span: Dict[str, Any], attrs: Dict[str, Any]) -> TrajectoryStep:
    usage = _otlp_legacy_usage(attrs)
    output_messages = attrs.get(GEN_AI_OUTPUT_MESSAGES)
    if isinstance(output_messages, list) and len(output_messages) == 1:
        response = output_messages[0]
    else:
        response = output_messages
    meta = _otlp_legacy_meta(span, attrs)
    return TrajectoryStep(
        kind="llm",
        error=_otlp_span_error(span, attrs),
        start_time_ms=_nanos_to_ms(span.get("startTimeUnixNano")),
        end_time_ms=_nanos_to_ms(span.get("endTimeUnixNano")),
        detail=LLMCallDetail(
            model=str(attrs.get(GEN_AI_REQUEST_MODEL) or span.get("name") or ""),
            messages=_json_safe(attrs.get(GEN_AI_INPUT_MESSAGES) or []),
            response=_json_safe(response),
            tools=_json_safe(attrs.get(GEN_AI_TOOL_DEFINITIONS)),
            usage=usage,
            meta=meta,
        ),
        reward=_otlp_legacy_reward(attrs),
        prompt_token_ids=_json_safe(attrs.get(OJ_RL_PROMPT_TOKEN_IDS)),
        completion_token_ids=_json_safe(attrs.get(OJ_RL_COMPLETION_TOKEN_IDS)),
        logprobs=_json_safe(attrs.get(OJ_RL_LOGPROBS)),
        meta=meta,
    )


def _otlp_tool_step(
    span: Dict[str, Any],
    attrs: Dict[str, Any],
    parent_llm_ref: Optional[str],
) -> TrajectoryStep:
    meta = _otlp_legacy_meta(span, attrs)
    if parent_llm_ref:
        meta["parent_llm_call"] = parent_llm_ref
        meta[LEGACY_PARENT_LLM_CALL] = parent_llm_ref
    return TrajectoryStep(
        kind="tool",
        error=_otlp_span_error(span, attrs),
        start_time_ms=_nanos_to_ms(span.get("startTimeUnixNano")),
        end_time_ms=_nanos_to_ms(span.get("endTimeUnixNano")),
        detail=ToolCallDetail(
            tool_name=str(attrs.get(GEN_AI_TOOL_NAME) or span.get("name") or ""),
            call_args=_json_safe(attrs.get(GEN_AI_TOOL_CALL_ARGUMENTS)),
            call_result=_json_safe(attrs.get(GEN_AI_TOOL_CALL_RESULT)),
            tool_call_id=attrs.get(GEN_AI_TOOL_CALL_ID),
        ),
        reward=_otlp_legacy_reward(attrs),
        meta=meta,
    )


def _otlp_legacy_steps(otlp_trace: Optional[Dict[str, Any]]) -> List[TrajectoryStep]:
    steps: List[TrajectoryStep] = []
    llm_refs: Dict[str, str] = {}
    for span in _otlp_spans(otlp_trace):
        attrs = _otlp_attribute_map(span.get("attributes") or [])
        step_kind = _otlp_legacy_step_kind(span, attrs)
        if step_kind is None:
            continue
        if step_kind == "llm":
            invoke_id = str(attrs.get(OJ_INVOKE_ID) or "")
            if invoke_id:
                llm_refs[invoke_id] = f"llm_{len(llm_refs) + 1:04d}"
            steps.append(_otlp_llm_step(span, attrs))
            continue
        parent_llm_ref = llm_refs.get(str(attrs.get(OJ_PARENT_INVOKE_ID) or ""))
        steps.append(_otlp_tool_step(span, attrs, parent_llm_ref))
    return steps


def _ensure_otlp_resource_attributes(
    otlp_trace: Optional[Dict[str, Any]],
    attributes: Dict[str, Any],
) -> Dict[str, Any]:
    trace = deepcopy(otlp_trace) if isinstance(otlp_trace, dict) else {}
    resource_spans = trace.setdefault("resourceSpans", [])
    if not resource_spans:
        resource_spans.append({"resource": {"attributes": []}})
    resource_span = resource_spans[0]
    resource = resource_span.setdefault("resource", {})
    resource_attributes = resource.setdefault("attributes", [])

    existing_keys = {
        item.get("key")
        for item in resource_attributes
        if isinstance(item, dict) and item.get("key")
    }
    for key, value in attributes.items():
        if value is None or key in existing_keys:
            continue
        resource_attributes.append({"key": key, "value": to_otlp_value(value)})
        existing_keys.add(key)
    return trace


# =============================================================================
# StepDetail Union Types
# =============================================================================


@dataclass
class LLMCallDetail:
    """Complete LLM call execution data."""

    model: str
    messages: List[Any]
    response: Optional[Any] = None
    tools: Optional[List[Dict[str, Any]]] = None
    usage: Optional[Dict[str, Any]] = None
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallDetail:
    """Complete tool call execution data."""

    tool_name: str
    call_args: Any = None
    call_result: Any = None
    tool_description: Optional[str] = None
    tool_schema: Optional[Dict[str, Any]] = None
    tool_call_id: Optional[str] = None
    """Tool call ID for script artifact tracking. Defaults to None."""


StepDetail = Union[LLMCallDetail, ToolCallDetail]


# =============================================================================
# TrajectoryStep
# =============================================================================


@dataclass
class TrajectoryStep:
    """Single step in execution.

    Field categories:
    - Core execution facts: kind, error, timestamps
    - Structured detail: detail (LLMCallDetail | ToolCallDetail | None)
    - Post-injection: reward, logprobs, prompt_token_ids,
      completion_token_ids (filled during collection)
    - Extension: meta (operator_id, invoke relationships, etc.)

    Token-level fields (``prompt_token_ids`` / ``completion_token_ids`` /
    ``logprobs``) are lifted out of the LLM response by the trajectory
    collection module and stripped from ``detail.response`` to avoid
    duplicate storage.
    """

    kind: StepKind
    error: Optional[Dict[str, Any]] = None
    start_time_ms: Optional[int] = None
    end_time_ms: Optional[int] = None

    detail: Optional[StepDetail] = None
    """Structured step data.

    LLM steps: LLMCallDetail with full messages/response/tools
    Tool steps: ToolCallDetail with args/result + augmented schema
    Other steps: detail=None, I/O in meta as backup
    """

    reward: Optional[float] = None
    """Scalar reward from PRM or SignalDetector."""

    prompt_token_ids: Optional[List[int]] = None
    """Prompt token IDs lifted from the LLM response. Only for kind='llm'."""

    completion_token_ids: Optional[List[int]] = None
    """Response (completion) token IDs lifted from the LLM response. Only for kind='llm'."""

    logprobs: Optional[Any] = None
    """Token log probabilities lifted from the LLM response. Only for kind='llm'."""

    meta: Dict[str, Any] = field(default_factory=dict)
    """Extension metadata including:
    - operator_id: disambiguated from span
    - agent_id: agent identifier when available
    - inputs/outputs: backup for non-LLM/Tool steps
    - span_name: original span.name for debugging
    - invoke_id, parent_invoke_id, child_invokes: invoke relationships
    """


# =============================================================================
# Trajectory
# =============================================================================


@dataclass
class Trajectory:
    """OTLP-first trajectory with an optional step projection.

    ``otlp_trace`` is the durable payload. Compatibility/read-model
    projections are derived by ``to_legacy_trajectory``.
    """

    otlp_trace: Optional[Dict[str, Any]] = None
    """OTLP TraceData JSON used as the durable trajectory payload."""


@dataclass
class LegacyTrajectory:
    """Backward-compatible step-based execution trajectory view."""

    execution_id: str
    """Unique identifier for this execution."""

    steps: List[TrajectoryStep]
    """Ordered list of execution steps."""

    source: str = "offline"
    """Execution source: 'online' (deepagents) | 'offline' (trainer)"""

    case_id: Optional[str] = None
    """Offline: dataset case identifier. Online: None."""

    session_id: Optional[str] = None
    """Online: conversation session ID. Offline: can reuse case_id or None."""

    cost: Optional[CostInfo] = None
    """Aggregated cost metrics: input_tokens, output_tokens."""

    meta: Dict[str, Any] = field(default_factory=dict)
    """Extension metadata for trajectory-level attributes such as:
    - member_id: team member identifier for trajectory aggregation
    - member_count: number of members in a combined team trajectory
    """

    @staticmethod
    def _message_to_dict(message: Any) -> Dict[str, Any]:
        """Normalize runtime message objects into message-like dicts."""
        if isinstance(message, dict):
            return _json_safe(message)

        role = getattr(message, "role", None)
        if role is not None:
            item: Dict[str, Any] = {
                "role": role,
                "content": _json_safe(getattr(message, "content", "")),
            }
            name = getattr(message, "name", None)
            if name is not None:
                item["name"] = name
            metadata = getattr(message, "metadata", None)
            if metadata:
                item["metadata"] = _json_safe(metadata)
            tool_calls = getattr(message, "tool_calls", None)
            if tool_calls:
                item["tool_calls"] = _json_safe(tool_calls)
            return item

        model_dump = getattr(message, "model_dump", None)
        if callable(model_dump):
            try:
                dumped = model_dump()
            except Exception:
                dumped = None
            if isinstance(dumped, dict):
                return _json_safe(dumped)

        return {"role": "unknown", "content": str(message)}

    def to_messages(self) -> List[Dict[str, Any]]:
        """Return message-like dicts recorded by LLM trajectory steps."""
        messages: List[Dict[str, Any]] = []
        for step in self.steps:
            if step.kind != "llm" or not isinstance(step.detail, LLMCallDetail):
                continue
            messages.extend(self._message_to_dict(message) for message in step.detail.messages)
            response = step.detail.response
            response_message = self._message_to_dict(response) if response is not None else None
            if response_message and ("role" in response_message or "content" in response_message):
                messages.append(response_message)
        return messages


def to_legacy_trajectory(trajectory: Union[Trajectory, LegacyTrajectory]) -> LegacyTrajectory:
    """Return a detached step-based compatibility view."""
    if isinstance(trajectory, LegacyTrajectory):
        meta = deepcopy(trajectory.meta)
        meta.pop("source", None)
        return LegacyTrajectory(
            execution_id=trajectory.execution_id,
            steps=deepcopy(trajectory.steps),
            source=_legacy_trajectory_source(trajectory),
            case_id=trajectory.case_id,
            session_id=trajectory.session_id,
            cost=deepcopy(trajectory.cost),
            meta=meta,
        )

    resource_attrs = _otlp_resource_attributes(trajectory.otlp_trace)
    return LegacyTrajectory(
        execution_id=_otlp_trajectory_id(resource_attrs),
        steps=_otlp_legacy_steps(trajectory.otlp_trace),
        source=_otlp_trajectory_source(resource_attrs),
        case_id=resource_attrs.get(CASE_ID),
        session_id=resource_attrs.get(OJ_SESSION_ID) or resource_attrs.get(OLD_OJ_SESSION_ID),
        cost=_otlp_usage_cost(trajectory.otlp_trace),
        meta=_otlp_trajectory_meta(resource_attrs),
    )


def _ms_to_nanos(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    return str(int(value) * 1_000_000)


def _append_otlp_attr(attributes: List[Dict[str, Any]], key: str, value: Any) -> None:
    if value is None:
        return
    attributes.append({"key": key, "value": to_otlp_value(value)})


def _legacy_step_to_otlp_span(step: TrajectoryStep, index: int, trace_id: str) -> Dict[str, Any]:
    detail = step.detail
    meta = dict(step.meta or {})
    attributes: List[Dict[str, Any]] = []
    _append_otlp_attr(attributes, TRAJECTORY_STEP_KIND, step.kind)
    _append_otlp_attr(attributes, OJ_INVOKE_ID, meta.get("invoke_id"))
    _append_otlp_attr(attributes, OJ_PARENT_INVOKE_ID, meta.get("parent_invoke_id"))
    _append_otlp_attr(attributes, OJ_ERROR, step.error)
    _append_otlp_attr(attributes, LEGACY_OPERATOR_ID, meta.get("operator_id"))
    _append_otlp_attr(attributes, OJ_RL_REWARD, step.reward)
    _append_otlp_attr(attributes, OJ_RL_PROMPT_TOKEN_IDS, _json_safe(step.prompt_token_ids))
    _append_otlp_attr(attributes, OJ_RL_COMPLETION_TOKEN_IDS, _json_safe(step.completion_token_ids))
    _append_otlp_attr(attributes, OJ_RL_LOGPROBS, _json_safe(step.logprobs))
    if meta:
        _append_otlp_attr(attributes, LEGACY_STEP_META, _json_safe(meta))

    span_name = str(meta.get("span_name") or f"{step.kind}.{index + 1}")
    if step.kind == "llm" and isinstance(detail, LLMCallDetail):
        span_name = str(meta.get("span_name") or detail.model or span_name)
        _append_otlp_attr(attributes, GEN_AI_OPERATION_NAME, "chat")
        _append_otlp_attr(attributes, GEN_AI_REQUEST_MODEL, detail.model)
        _append_otlp_attr(attributes, GEN_AI_INPUT_MESSAGES, _json_safe(detail.messages))
        if detail.response is not None:
            response = _json_safe(detail.response)
            _append_otlp_attr(
                attributes,
                GEN_AI_OUTPUT_MESSAGES,
                response if isinstance(response, list) else [response],
            )
        _append_otlp_attr(attributes, GEN_AI_TOOL_DEFINITIONS, _json_safe(detail.tools))
        usage = detail.usage or {}
        _append_otlp_attr(
            attributes,
            GEN_AI_USAGE_INPUT_TOKENS,
            usage.get("prompt_tokens", usage.get("input_tokens")),
        )
        _append_otlp_attr(
            attributes,
            GEN_AI_USAGE_OUTPUT_TOKENS,
            usage.get("completion_tokens", usage.get("output_tokens")),
        )
    elif step.kind == "tool" and isinstance(detail, ToolCallDetail):
        span_name = str(meta.get("span_name") or detail.tool_name or span_name)
        _append_otlp_attr(attributes, GEN_AI_OPERATION_NAME, "execute_tool")
        _append_otlp_attr(attributes, GEN_AI_TOOL_NAME, detail.tool_name)
        _append_otlp_attr(attributes, GEN_AI_TOOL_CALL_ARGUMENTS, _json_safe(detail.call_args))
        _append_otlp_attr(attributes, GEN_AI_TOOL_CALL_RESULT, _json_safe(detail.call_result))
        _append_otlp_attr(attributes, GEN_AI_TOOL_CALL_ID, detail.tool_call_id)

    span = {
        "traceId": normalize_trace_id_hex(trace_id),
        "spanId": str(meta.get("span_id") or f"{index + 1:016x}"),
        "name": span_name,
        "kind": "SPAN_KIND_INTERNAL",
        "attributes": attributes,
        "status": {"code": "STATUS_CODE_ERROR" if step.error else "STATUS_CODE_OK"},
    }
    if meta.get("parent_span_id"):
        span["parentSpanId"] = str(meta["parent_span_id"])
    start_nanos = _ms_to_nanos(step.start_time_ms)
    end_nanos = _ms_to_nanos(step.end_time_ms)
    if start_nanos is not None:
        span["startTimeUnixNano"] = start_nanos
    if end_nanos is not None:
        span["endTimeUnixNano"] = end_nanos
    return span


def _ensure_otlp_step_spans(trace: Dict[str, Any], legacy: LegacyTrajectory) -> Dict[str, Any]:
    if not legacy.steps:
        return trace
    resource_spans = trace.setdefault("resourceSpans", [])
    if not resource_spans:
        resource_spans.append({"resource": {"attributes": []}})
    resource_span = resource_spans[0]
    scope_spans = resource_span.setdefault("scopeSpans", [])
    if not scope_spans:
        scope_spans.append(
            {
                "scope": {
                    "name": "openjiuwen.agent_evolving.trajectory",
                    "version": TRAJECTORY_SCHEMA_VERSION,
                },
                "spans": [],
            }
        )
    spans = scope_spans[0].setdefault("spans", [])
    if spans:
        return trace
    spans.extend(
        _legacy_step_to_otlp_span(step, index, legacy.execution_id)
        for index, step in enumerate(legacy.steps)
    )
    return trace


def trajectory_from_legacy(
    legacy: LegacyTrajectory,
    *,
    otlp_trace: Optional[Dict[str, Any]] = None,
) -> Trajectory:
    """Wrap a legacy trajectory view in the OTLP-first trajectory type."""
    resource_attrs = deepcopy(legacy.meta)
    resource_attrs.pop("source", None)
    resource_attrs.update(
        {
            TRAJECTORY_ID: legacy.execution_id,
            TRAJECTORY_SCHEMA_VERSION_ATTR: TRAJECTORY_SCHEMA_VERSION,
            OJ_SESSION_ID: legacy.session_id,
            CASE_ID: legacy.case_id,
            TRAJECTORY_SOURCE: _legacy_trajectory_source(legacy),
            TRAJECTORY_END_REASON: legacy.meta.get(TRAJECTORY_END_REASON)
            or legacy.meta.get("end_reason")
            or "unknown",
        }
    )
    trace = _ensure_otlp_resource_attributes(
        otlp_trace,
        resource_attrs,
    )
    trace = _ensure_otlp_step_spans(trace, legacy)
    return Trajectory(
        otlp_trace=trace,
    )
