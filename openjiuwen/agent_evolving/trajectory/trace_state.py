# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Per-trace trajectory state and OTLP TraceData projection."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from threading import RLock
from typing import Any

from openjiuwen.agent_evolving.trajectory.semconv import (
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OPERATION_NAME,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_SYSTEM_INSTRUCTIONS,
    GEN_AI_TOOL_CALL_ARGUMENTS,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_CALL_RESULT,
    GEN_AI_TOOL_DEFINITIONS,
    GEN_AI_TOOL_NAME,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    GEN_AI_USAGE_TOTAL_TOKENS,
    LEGACY_PARENT_LLM_CALL,
    OJ_AGENT_INPUTS,
    OJ_AGENT_INVOKE_TYPE,
    OJ_AGENT_NAME,
    OJ_AGENT_OUTPUTS,
    OJ_CHILD_INVOKE_IDS,
    OJ_ERROR,
    OJ_INNER_ERROR,
    OJ_INVOKE_ID,
    OJ_MEMBER_ID,
    OJ_MEMBER_NAME,
    OJ_MEMBER_ROLE,
    OJ_META_DATA,
    OJ_PARENT_INVOKE_ID,
    OJ_PARENT_NODE_ID,
    OJ_RL_ATTEMPT_SEQ,
    OJ_RL_COMPLETION_TOKEN_IDS,
    OJ_RL_FINAL_REWARD,
    OJ_RL_LOGPROBS,
    OJ_RL_PROMPT_TOKEN_IDS,
    OJ_RL_REWARD,
    OJ_RL_REWARD_SOURCE,
    OJ_RL_ROLLOUT_ID,
    OJ_SESSION_ID,
    OJ_SOURCE_IDS,
    OJ_STATUS,
    OJ_TEAM_ID,
    OJ_TEAM_NAME,
    OJ_TRACE_ID,
    OJ_WORKFLOW_COMPONENT_ID,
    OJ_WORKFLOW_COMPONENT_NAME,
    OJ_WORKFLOW_COMPONENT_TYPE,
    OJ_WORKFLOW_ID,
    OJ_WORKFLOW_INPUTS,
    OJ_WORKFLOW_NAME,
    OJ_WORKFLOW_OUTPUTS,
    OJ_WORKFLOW_VERSION,
    TRAJECTORY_END_REASON,
    TRAJECTORY_ID,
    TRAJECTORY_INCOMPLETE,
    TRAJECTORY_INVOKE_TYPE,
    TRAJECTORY_PARENT_ID,
    TRAJECTORY_SCHEMA_VERSION,
    TRAJECTORY_SCHEMA_VERSION_ATTR,
    TRAJECTORY_SCOPE_NAME,
    TRAJECTORY_SOURCE,
    TRAJECTORY_STEP_KIND,
    TRAJECTORY_TASK_HASH,
    TRAJECTORY_TRACE_ID,
)
from openjiuwen.agent_evolving.trajectory.span_codec import (
    as_message_list,
    attributes_to_otlp,
    datetime_to_nanos,
    json_safe,
    normalize_error,
    normalize_trace_id_hex,
    now_nanos,
    span_id_hex,
    unwrap_io,
    unwrap_nested_io,
)
from openjiuwen.agent_evolving.trajectory.types import (
    LLMCallDetail,
    ToolCallDetail,
    Trajectory,
    TrajectoryStep,
)
from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.tracer.data import InvokeType, NodeStatus
from openjiuwen.core.session.tracer.span import TraceAgentSpan


@dataclass
class TrajectorySpanState:
    trace_id: str
    invoke_id: str
    name: str
    parent_invoke_id: str | None = None
    span_kind: str = "SPAN_KIND_INTERNAL"
    start_time_unix_nano: int = field(default_factory=now_nanos)
    end_time_unix_nano: int | None = None
    status: str = NodeStatus.START.value
    inputs: Any = None
    outputs: Any = None
    error: dict[str, Any] | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    def span_id(self) -> str:
        return span_id_hex(self.trace_id, self.invoke_id)

    def parent_span_id(self) -> str | None:
        if not self.parent_invoke_id:
            return None
        return span_id_hex(self.trace_id, self.parent_invoke_id)

    def to_otlp_span(self) -> dict[str, Any]:
        span = {
            "traceId": normalize_trace_id_hex(self.trace_id),
            "spanId": self.span_id(),
            "name": self.name or self.invoke_id,
            "kind": self.span_kind,
            "startTimeUnixNano": str(self.start_time_unix_nano),
            "attributes": attributes_to_otlp(self.attributes),
            "status": self.otlp_status(),
        }
        parent_span_id = self.parent_span_id()
        if parent_span_id:
            span["parentSpanId"] = parent_span_id
        if self.end_time_unix_nano is not None:
            span["endTimeUnixNano"] = str(self.end_time_unix_nano)
        if self.events:
            span["events"] = [
                {
                    "name": item["name"],
                    "timeUnixNano": str(item["timeUnixNano"]),
                    "attributes": attributes_to_otlp(item.get("attributes", {})),
                }
                for item in self.events
            ]
        return span

    def otlp_status(self) -> dict[str, Any]:
        if self.error or self.status == NodeStatus.ERROR.value:
            message = ""
            if self.error:
                message = str(self.error.get("message") or self.error)
            return {"code": "STATUS_CODE_ERROR", "message": message}
        if self.status == NodeStatus.FINISH.value:
            return {"code": "STATUS_CODE_OK"}
        return {"code": "STATUS_CODE_UNSET"}


class TrajectoryTraceState:
    """Per-trace mutable trajectory state."""

    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        self.spans: dict[str, TrajectorySpanState] = {}
        self.order: list[str] = []
        self.workflow_layer_roots: dict[str, str] = {}
        self.workflow_component_spans: dict[str, str] = {}
        self.lock = RLock()

    def has_spans(self) -> bool:
        with self.lock:
            return bool(self.order)

    def record_agent_start(
        self,
        *,
        span: TraceAgentSpan | None,
        invoke_type: str,
        inputs: Any,
        instance_info: dict | None,
    ) -> None:
        if span is None or not span.invoke_id:
            return
        instance_info = dict(instance_info or {})
        name = str(instance_info.get("class_name") or span.name or invoke_type)
        parent_invoke_id = span.parent_invoke_id
        with self.lock:
            state = self.upsert_span(span.invoke_id, name=name, parent_invoke_id=parent_invoke_id)
            state.span_kind = "SPAN_KIND_CLIENT" if invoke_type == InvokeType.LLM.value else "SPAN_KIND_INTERNAL"
            state.start_time_unix_nano = datetime_to_nanos(span.start_time) or state.start_time_unix_nano
            state.status = NodeStatus.START.value
            state.inputs = json_safe(inputs)
            state.attributes.update(
                {
                    OJ_TRACE_ID: span.trace_id,
                    OJ_INVOKE_ID: span.invoke_id,
                    OJ_PARENT_INVOKE_ID: parent_invoke_id,
                    OJ_AGENT_INVOKE_TYPE: invoke_type,
                    OJ_AGENT_NAME: name,
                    OJ_META_DATA: json_safe(span.meta_data or instance_info),
                    TRAJECTORY_INVOKE_TYPE: invoke_type,
                }
            )
            if span.child_invokes_id is not None:
                state.attributes[OJ_CHILD_INVOKE_IDS] = json_safe(span.child_invokes_id)
            if inputs is not None:
                state.attributes[OJ_AGENT_INPUTS] = json_safe(inputs)
            if invoke_type == InvokeType.LLM.value:
                state.attributes[GEN_AI_REQUEST_MODEL] = name
                state.attributes[GEN_AI_OPERATION_NAME] = "chat"
                state.attributes[GEN_AI_INPUT_MESSAGES] = json_safe(as_message_list(unwrap_io(inputs, "inputs")))
                input_payload = unwrap_nested_io(json_safe(inputs), preferred="inputs")
                tool_definitions = None
                system_instructions = None
                if isinstance(input_payload, dict):
                    for key in ("tool_definitions", "tools", "functions"):
                        if input_payload.get(key) is not None:
                            tool_definitions = input_payload[key]
                            break
                    for key in ("system_instructions", "system", "system_prompt"):
                        if input_payload.get(key) is not None:
                            system_instructions = input_payload[key]
                            break
                if tool_definitions is not None:
                    state.attributes[GEN_AI_TOOL_DEFINITIONS] = json_safe(tool_definitions)
                if system_instructions is not None:
                    state.attributes[GEN_AI_SYSTEM_INSTRUCTIONS] = json_safe(system_instructions)
            elif invoke_type == InvokeType.PLUGIN.value:
                state.attributes[GEN_AI_OPERATION_NAME] = "execute_tool"
                state.attributes[GEN_AI_TOOL_NAME] = name
                state.attributes[GEN_AI_TOOL_CALL_ARGUMENTS] = json_safe(unwrap_io(inputs, "inputs"))
                input_payload = unwrap_nested_io(json_safe(inputs), preferred="inputs")
                tool_call_id = None
                if isinstance(input_payload, dict):
                    for key in ("tool_call_id", "call_id", "id"):
                        if input_payload.get(key) is not None:
                            tool_call_id = input_payload[key]
                            break
                if tool_call_id is not None:
                    state.attributes[GEN_AI_TOOL_CALL_ID] = str(tool_call_id)

    def record_agent_event(
        self,
        *,
        span: TraceAgentSpan | None,
        name: str,
        attributes: dict[str, Any],
    ) -> None:
        if span is None or not span.invoke_id:
            return
        with self.lock:
            state = self.upsert_span(span.invoke_id, name=span.name or name, parent_invoke_id=span.parent_invoke_id)
            state.status = NodeStatus.RUNNING.value
            state.events.append(
                {
                    "name": name,
                    "timeUnixNano": now_nanos(),
                    "attributes": json_safe(attributes),
                }
            )

    def record_agent_end(self, *, span: TraceAgentSpan | None, outputs: Any) -> None:
        if span is None or not span.invoke_id:
            return
        with self.lock:
            state = self.upsert_span(
                span.invoke_id,
                name=span.name or span.invoke_id,
                parent_invoke_id=span.parent_invoke_id,
            )
            state.outputs = json_safe(outputs)
            state.end_time_unix_nano = datetime_to_nanos(span.end_time) or now_nanos()
            state.status = NodeStatus.FINISH.value
            state.attributes[OJ_AGENT_OUTPUTS] = json_safe(outputs)
            state.attributes[OJ_STATUS] = NodeStatus.FINISH.value
            if state.attributes.get(TRAJECTORY_INVOKE_TYPE) == InvokeType.LLM.value:
                state.attributes[GEN_AI_OUTPUT_MESSAGES] = json_safe(as_message_list(unwrap_io(outputs, "outputs")))
                output_payload = unwrap_nested_io(json_safe(outputs), preferred="outputs")
                if isinstance(output_payload, dict):
                    rl_aliases = {
                        OJ_RL_PROMPT_TOKEN_IDS: ("prompt_token_ids", "prompt_ids", "input_ids"),
                        OJ_RL_COMPLETION_TOKEN_IDS: (
                            "completion_token_ids",
                            "token_ids",
                            "response_ids",
                        ),
                        OJ_RL_LOGPROBS: ("logprobs", "response_logprobs"),
                        OJ_RL_REWARD: ("reward", "score"),
                    }
                    for attr_name, keys in rl_aliases.items():
                        for key in keys:
                            if key in output_payload and output_payload[key] is not None:
                                state.attributes[attr_name] = json_safe(output_payload[key])
                                break
                    usage = output_payload.get("usage")
                    if not isinstance(usage, dict):
                        usage = output_payload
                    input_tokens = usage.get("input_tokens")
                    if input_tokens is None:
                        input_tokens = usage.get("prompt_tokens")
                    output_tokens = usage.get("output_tokens")
                    if output_tokens is None:
                        output_tokens = usage.get("completion_tokens")
                    total_tokens = usage.get("total_tokens")
                    if total_tokens is None and input_tokens is not None and output_tokens is not None:
                        total_tokens = int(input_tokens or 0) + int(output_tokens or 0)
                    if input_tokens is not None:
                        state.attributes[GEN_AI_USAGE_INPUT_TOKENS] = int(input_tokens or 0)
                    if output_tokens is not None:
                        state.attributes[GEN_AI_USAGE_OUTPUT_TOKENS] = int(output_tokens or 0)
                    if total_tokens is not None:
                        state.attributes[GEN_AI_USAGE_TOTAL_TOKENS] = int(total_tokens or 0)
                response_model = None
                finish_reasons = None
                if isinstance(output_payload, dict):
                    response_model = output_payload.get("response_model")
                    if response_model is None:
                        response_model = output_payload.get("model")
                    finish_reasons = output_payload.get("finish_reasons")
                    if finish_reasons is None:
                        finish_reasons = output_payload.get("finish_reason")
                if response_model is not None:
                    state.attributes[GEN_AI_RESPONSE_MODEL] = str(response_model)
                if finish_reasons is not None:
                    if not isinstance(finish_reasons, list):
                        finish_reasons = [finish_reasons]
                    state.attributes[GEN_AI_RESPONSE_FINISH_REASONS] = json_safe(finish_reasons)
            elif state.attributes.get(TRAJECTORY_INVOKE_TYPE) == InvokeType.PLUGIN.value:
                state.attributes[GEN_AI_TOOL_CALL_RESULT] = json_safe(unwrap_io(outputs, "outputs"))
                output_payload = unwrap_nested_io(json_safe(outputs), preferred="inputs")
                tool_call_id = None
                if isinstance(output_payload, dict):
                    for key in ("tool_call_id", "call_id", "id"):
                        if output_payload.get(key) is not None:
                            tool_call_id = output_payload[key]
                            break
                if tool_call_id is not None and GEN_AI_TOOL_CALL_ID not in state.attributes:
                    state.attributes[GEN_AI_TOOL_CALL_ID] = str(tool_call_id)

    def record_agent_error(self, *, span: TraceAgentSpan | None, error: Any) -> None:
        if span is None or not span.invoke_id:
            return
        with self.lock:
            state = self.upsert_span(
                span.invoke_id,
                name=span.name or span.invoke_id,
                parent_invoke_id=span.parent_invoke_id,
            )
            error_info = normalize_error(error)
            state.error = error_info
            state.end_time_unix_nano = datetime_to_nanos(span.end_time) or now_nanos()
            state.status = NodeStatus.ERROR.value
            state.attributes[OJ_ERROR] = error_info
            state.attributes[OJ_STATUS] = NodeStatus.ERROR.value

    def record_workflow_start(
        self,
        *,
        invoke_id: str,
        metadata: dict | None,
        inputs: Any,
        source_ids: list | None,
        parent_node_id: str,
    ) -> None:
        metadata = dict(metadata or {})
        is_root = "workflow_id" in metadata and "component_id" not in metadata
        name = str(metadata.get("workflow_name") or metadata.get("component_name") or invoke_id)
        component_type = str(metadata.get("component_type") or "")
        with self.lock:
            parent_invoke_id = self.resolve_workflow_parent(parent_node_id, metadata)
            state = self.upsert_span(invoke_id, name=name, parent_invoke_id=parent_invoke_id)
            state.span_kind = "SPAN_KIND_CLIENT" if "LLM" in component_type else "SPAN_KIND_INTERNAL"
            state.inputs = json_safe(inputs)
            state.status = NodeStatus.START.value
            state.attributes.update(
                {
                    OJ_TRACE_ID: self.trace_id,
                    OJ_INVOKE_ID: invoke_id,
                    OJ_PARENT_INVOKE_ID: parent_invoke_id,
                    OJ_PARENT_NODE_ID: parent_node_id,
                    OJ_WORKFLOW_INPUTS: json_safe(inputs),
                    OJ_SOURCE_IDS: json_safe(source_ids),
                    TRAJECTORY_INVOKE_TYPE: (component_type or InvokeType.WORKFLOW.value).lower(),
                }
            )
            self.set_workflow_attributes(state, metadata)
            if is_root:
                self.workflow_layer_roots[parent_node_id] = invoke_id
                self.workflow_layer_roots[invoke_id] = invoke_id
            else:
                component_id = metadata.get("component_id")
                if component_id:
                    self.workflow_component_spans[str(component_id)] = invoke_id

    def record_workflow_inputs(self, *, invoke_id: str, inputs: Any, metadata: dict | None) -> None:
        with self.lock:
            state = self.upsert_span(invoke_id, name=invoke_id)
            state.inputs = json_safe(inputs)
            state.attributes[OJ_WORKFLOW_INPUTS] = json_safe(inputs)
            self.set_workflow_attributes(state, metadata or {})

    def record_workflow_event(
        self,
        *,
        invoke_id: str,
        name: str,
        attributes: dict[str, Any] | None = None,
        exception: Exception | None = None,
    ) -> None:
        with self.lock:
            state = self.upsert_span(invoke_id, name=invoke_id)
            if exception is not None:
                error_info = normalize_error(exception)
                state.error = error_info
                state.status = NodeStatus.ERROR.value
                state.end_time_unix_nano = now_nanos()
                state.attributes[OJ_ERROR] = error_info
                state.attributes[OJ_STATUS] = NodeStatus.ERROR.value
                if attributes and "inner_error" in attributes:
                    state.attributes[OJ_INNER_ERROR] = json_safe(attributes["inner_error"])
            if attributes is not None:
                state.events.append(
                    {
                        "name": name,
                        "timeUnixNano": now_nanos(),
                        "attributes": json_safe(attributes),
                    }
                )

    def record_workflow_outputs(self, *, invoke_id: str, outputs: Any) -> None:
        with self.lock:
            state = self.upsert_span(invoke_id, name=invoke_id)
            state.outputs = json_safe(outputs)
            state.attributes[OJ_WORKFLOW_OUTPUTS] = json_safe(outputs)

    def record_workflow_done(self, *, invoke_id: str, outputs: Any = None) -> None:
        with self.lock:
            state = self.upsert_span(invoke_id, name=invoke_id)
            if outputs is not None:
                state.outputs = json_safe(outputs)
                state.attributes[OJ_WORKFLOW_OUTPUTS] = json_safe(outputs)
            state.end_time_unix_nano = now_nanos()
            if state.status != NodeStatus.ERROR.value:
                state.status = NodeStatus.FINISH.value
                state.attributes[OJ_STATUS] = NodeStatus.FINISH.value
            self.remove_workflow_mapping(invoke_id)

    def to_otlp_trace_data(
        self,
        *,
        session_id: str = "",
        trajectory_id: str | None = None,
        resource_attributes: dict[str, Any] | None = None,
        end_reason: str | None = None,
        max_steps: int | None = None,
        finalize: bool = False,
    ) -> dict[str, Any]:
        states = self.snapshot_states(finalize=finalize, max_steps=max_steps)
        resource_attrs = self.resource_attributes(
            states,
            session_id=session_id,
            trajectory_id=trajectory_id or self.trace_id,
            resource_attributes=resource_attributes,
            end_reason=end_reason,
            finalize=finalize,
        )
        return {
            "resourceSpans": [
                {
                    "resource": {"attributes": attributes_to_otlp(resource_attrs)},
                    "scopeSpans": [
                        {
                            "scope": {
                                "name": TRAJECTORY_SCOPE_NAME,
                                "version": TRAJECTORY_SCHEMA_VERSION,
                            },
                            "spans": [state.to_otlp_span() for state in states],
                        }
                    ],
                }
            ]
        }

    def to_trajectory(
        self,
        *,
        session_id: str,
        source: str = "online",
        case_id: str | None = None,
        member_id: str | None = None,
        meta: dict[str, Any] | None = None,
        max_steps: int | None = None,
        finalize: bool = False,
    ) -> Trajectory | None:
        if not self.has_spans():
            return None
        trajectory_meta = dict(meta or {})
        trajectory_id = str(trajectory_meta.get(TRAJECTORY_ID) or trajectory_meta.get("trajectory_id") or self.trace_id)
        trajectory_meta[TRAJECTORY_TRACE_ID] = self.trace_id
        if member_id:
            trajectory_meta.setdefault("member_id", member_id)
        source_value = source or trajectory_meta.pop("source", None)
        trajectory_meta.pop("source", None)
        resource_meta = dict(trajectory_meta)
        if source_value:
            resource_meta[TRAJECTORY_SOURCE] = source_value
        if case_id is not None:
            resource_meta.setdefault("case_id", case_id)
        if member_id:
            resource_meta.setdefault("member_id", member_id)
        return Trajectory(
            otlp_trace=self.to_otlp_trace_data(
                session_id=session_id,
                trajectory_id=trajectory_id,
                resource_attributes=resource_meta,
                max_steps=max_steps,
                finalize=finalize,
            ),
        )

    def resource_attributes(
        self,
        states: list[TrajectorySpanState],
        *,
        session_id: str,
        trajectory_id: str,
        resource_attributes: dict[str, Any] | None,
        end_reason: str | None,
        finalize: bool,
    ) -> dict[str, Any]:
        source = dict(resource_attributes or {})
        attrs: dict[str, Any] = {
            "service.name": "openjiuwen.trajectory",
            TRAJECTORY_ID: trajectory_id,
            TRAJECTORY_SCHEMA_VERSION_ATTR: TRAJECTORY_SCHEMA_VERSION,
            OJ_SESSION_ID: session_id or "",
            TRAJECTORY_END_REASON: end_reason or self.derive_end_reason(states, finalize=finalize),
        }
        aliases = {
            "case_id": ("case_id", "openjiuwen.case_id"),
            TRAJECTORY_PARENT_ID: ("parent_trajectory_id", "parent_id", TRAJECTORY_PARENT_ID),
            TRAJECTORY_TASK_HASH: ("task_hash", TRAJECTORY_TASK_HASH),
            OJ_TEAM_ID: ("team_id", OJ_TEAM_ID),
            OJ_TEAM_NAME: ("team_name", OJ_TEAM_NAME),
            OJ_MEMBER_ID: ("member_id", OJ_MEMBER_ID),
            OJ_MEMBER_NAME: ("member_name", OJ_MEMBER_NAME),
            OJ_MEMBER_ROLE: ("member_role", "role", OJ_MEMBER_ROLE),
            OJ_RL_FINAL_REWARD: ("final_reward", OJ_RL_FINAL_REWARD),
            OJ_RL_REWARD_SOURCE: ("reward_source", OJ_RL_REWARD_SOURCE),
            OJ_RL_ROLLOUT_ID: ("rollout_id", OJ_RL_ROLLOUT_ID),
            OJ_RL_ATTEMPT_SEQ: ("attempt_seq", OJ_RL_ATTEMPT_SEQ),
        }
        for attr_name, keys in aliases.items():
            for key in keys:
                value = source.get(key)
                if value is not None:
                    attrs[attr_name] = value
                    break
        reserved_source_keys = {
            "source",
            "trajectory_id",
            TRAJECTORY_ID,
        }
        for key, value in source.items():
            if value is None or key in attrs or key in reserved_source_keys:
                continue
            attrs[key] = value
        return attrs

    @staticmethod
    def derive_end_reason(states: list[TrajectorySpanState], *, finalize: bool) -> str:
        if any(state.error or state.status == NodeStatus.ERROR.value for state in states):
            return "error"
        if any(state.attributes.get(TRAJECTORY_INCOMPLETE) or state.status == "incomplete" for state in states):
            return "interrupted"
        if finalize:
            return "success"
        return "unknown"

    def upsert_span(
        self,
        invoke_id: str,
        *,
        name: str,
        parent_invoke_id: str | None = None,
    ) -> TrajectorySpanState:
        if invoke_id not in self.spans:
            self.spans[invoke_id] = TrajectorySpanState(
                trace_id=self.trace_id,
                invoke_id=invoke_id,
                parent_invoke_id=parent_invoke_id,
                name=name,
            )
            self.order.append(invoke_id)
            if parent_invoke_id and parent_invoke_id not in self.spans:
                self.spans[parent_invoke_id] = TrajectorySpanState(
                    trace_id=self.trace_id,
                    invoke_id=parent_invoke_id,
                    name=f"incomplete.{parent_invoke_id}",
                    status="incomplete",
                    attributes={
                        OJ_TRACE_ID: self.trace_id,
                        OJ_INVOKE_ID: parent_invoke_id,
                        TRAJECTORY_INCOMPLETE: True,
                    },
                )
                self.order.insert(max(len(self.order) - 1, 0), parent_invoke_id)
        state = self.spans[invoke_id]
        if name and (not state.name or state.name.startswith("incomplete.")):
            state.name = name
        if parent_invoke_id and not state.parent_invoke_id:
            state.parent_invoke_id = parent_invoke_id
        return state

    def resolve_workflow_parent(self, parent_node_id: str, metadata: dict[str, Any]) -> str | None:
        is_root = "workflow_id" in metadata and "component_id" not in metadata
        if parent_node_id == "" and is_root:
            return None
        if parent_node_id == "" and not is_root:
            return self.workflow_layer_roots.get("")
        if parent_node_id != "" and is_root:
            return self.workflow_component_spans.get(parent_node_id)
        return self.workflow_component_spans.get(parent_node_id) or self.workflow_layer_roots.get(parent_node_id)

    @staticmethod
    def set_workflow_attributes(state: TrajectorySpanState, metadata: dict[str, Any]) -> None:
        if not metadata:
            return
        for key, value in metadata.items():
            if key.startswith("openjiuwen."):
                state.attributes[key] = json_safe(value)
        if "workflow_id" in metadata:
            state.attributes[OJ_WORKFLOW_ID] = metadata.get("workflow_id")
        if "workflow_name" in metadata:
            state.attributes[OJ_WORKFLOW_NAME] = metadata.get("workflow_name")
        if "workflow_version" in metadata:
            state.attributes[OJ_WORKFLOW_VERSION] = metadata.get("workflow_version")
        if "component_id" in metadata:
            state.attributes[OJ_WORKFLOW_COMPONENT_ID] = metadata.get("component_id")
        if "component_name" in metadata:
            state.attributes[OJ_WORKFLOW_COMPONENT_NAME] = metadata.get("component_name")
        if "component_type" in metadata:
            state.attributes[OJ_WORKFLOW_COMPONENT_TYPE] = metadata.get("component_type")

    def remove_workflow_mapping(self, invoke_id: str) -> None:
        for key, value in list(self.workflow_layer_roots.items()):
            if value == invoke_id:
                self.workflow_layer_roots.pop(key, None)
        for key, value in list(self.workflow_component_spans.items()):
            if value == invoke_id:
                self.workflow_component_spans.pop(key, None)

    def snapshot_states(
        self,
        *,
        finalize: bool,
        max_steps: int | None = None,
    ) -> list[TrajectorySpanState]:
        if max_steps is not None and max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        with self.lock:
            order = self.order[-max_steps:] if max_steps is not None else self.order
            states = [self._clone_span_state(self.spans[invoke_id]) for invoke_id in order if invoke_id in self.spans]
        if finalize:
            now = now_nanos()
            for state in states:
                if state.end_time_unix_nano is None:
                    state.end_time_unix_nano = now
                    if state.status not in (NodeStatus.ERROR.value, NodeStatus.FINISH.value):
                        state.status = "incomplete"
                    state.attributes[TRAJECTORY_INCOMPLETE] = True
                    state.attributes[OJ_STATUS] = state.status
        return states

    @staticmethod
    def _clone_span_state(state: TrajectorySpanState) -> TrajectorySpanState:
        """Create a projection snapshot without deep-copying nested payloads.

        Finalize and projection only mutate scalar fields and top-level
        ``attributes`` / ``events`` containers. Nested inputs/outputs/attribute
        values are treated as immutable after ``json_safe`` writes, so sharing
        them avoids O(n) deepcopy cost on long trajectories.
        """
        return replace(
            state,
            attributes=dict(state.attributes),
            events=list(state.events),
            error=dict(state.error) if isinstance(state.error, dict) else state.error,
        )

    def project_legacy_steps(self, states: list[TrajectorySpanState]) -> list[TrajectoryStep]:
        steps = []
        llm_refs: dict[str, str] = {}
        for state in states:
            step_kind = self.legacy_step_kind(state)
            if step_kind is None:
                logger.warning(
                    "Skipping span in legacy projection: unable to classify step kind "
                    "(trace_id=%s, invoke_id=%s, name=%s, operation_name=%s, "
                    "invoke_type=%s, component_type=%s)",
                    state.trace_id,
                    state.invoke_id,
                    state.name,
                    state.attributes.get(GEN_AI_OPERATION_NAME),
                    state.attributes.get(TRAJECTORY_INVOKE_TYPE)
                    or state.attributes.get(OJ_AGENT_INVOKE_TYPE),
                    state.attributes.get(OJ_WORKFLOW_COMPONENT_TYPE),
                )
                continue
            if step_kind == "llm":
                llm_ref = f"llm_{len(llm_refs) + 1:04d}"
                llm_refs[state.invoke_id] = llm_ref
                steps.append(self.build_llm_step(state))
                continue
            parent_llm_ref = llm_refs.get(state.parent_invoke_id or "")
            steps.append(self.build_tool_step(state, parent_llm_ref=parent_llm_ref))
        return steps

    @staticmethod
    def legacy_step_kind(state: TrajectorySpanState) -> str | None:
        operation_name = str(state.attributes.get(GEN_AI_OPERATION_NAME) or "").lower()
        if operation_name in ("chat", "text_completion", "generate_content"):
            return "llm"
        if operation_name == "execute_tool":
            return "tool"
        has_llm_messages = (
            state.attributes.get(GEN_AI_INPUT_MESSAGES) is not None
            or state.attributes.get(GEN_AI_OUTPUT_MESSAGES) is not None
        )
        if has_llm_messages:
            return "llm"
        if (
            state.attributes.get(GEN_AI_TOOL_NAME) is not None
            or state.attributes.get(GEN_AI_TOOL_CALL_ARGUMENTS) is not None
            or state.attributes.get(GEN_AI_TOOL_CALL_RESULT) is not None
        ):
            return "tool"
        invoke_type = str(
            state.attributes.get(TRAJECTORY_INVOKE_TYPE) or state.attributes.get(OJ_AGENT_INVOKE_TYPE) or ""
        ).lower()
        component_type = str(state.attributes.get(OJ_WORKFLOW_COMPONENT_TYPE) or "").lower()
        if invoke_type == InvokeType.LLM.value or component_type == "llm":
            return "llm"
        if invoke_type in (InvokeType.PLUGIN.value, "tool") or component_type in ("tool", "plugin"):
            return "tool"
        explicit = state.attributes.get(TRAJECTORY_STEP_KIND)
        if explicit in ("llm", "tool"):
            return explicit
        span_name = str(state.name or "").lower()
        if span_name.startswith("llm.") or span_name == "llm.call":
            return "llm"
        if span_name.startswith("tool.") or span_name.startswith("execute_tool "):
            return "tool"
        return None

    def build_llm_step(self, state: TrajectorySpanState) -> TrajectoryStep:
        inputs = unwrap_io(state.inputs, "inputs")
        outputs = unwrap_io(state.outputs, "outputs")
        input_tokens = state.attributes.get(GEN_AI_USAGE_INPUT_TOKENS)
        output_tokens = state.attributes.get(GEN_AI_USAGE_OUTPUT_TOKENS)
        usage = None
        if input_tokens is not None or output_tokens is not None:
            usage = {
                "prompt_tokens": int(input_tokens or 0),
                "completion_tokens": int(output_tokens or 0),
            }
        output_payload = json_safe(outputs)
        if usage is None and isinstance(output_payload, dict) and isinstance(output_payload.get("usage"), dict):
            usage = output_payload["usage"]
        output_messages = state.attributes.get(GEN_AI_OUTPUT_MESSAGES)
        if isinstance(output_messages, list) and len(output_messages) == 1:
            response = output_messages[0]
        elif output_messages is not None:
            response = output_messages
        else:
            response = json_safe(outputs)
        detail = LLMCallDetail(
            model=str(state.attributes.get(GEN_AI_REQUEST_MODEL) or state.name or ""),
            messages=json_safe(state.attributes.get(GEN_AI_INPUT_MESSAGES) or as_message_list(inputs)),
            response=json_safe(response),
            tools=json_safe(state.attributes.get(GEN_AI_TOOL_DEFINITIONS)),
            usage=usage,
            meta=self.legacy_meta(state),
        )
        return TrajectoryStep(
            kind="llm",
            error=state.error,
            start_time_ms=state.start_time_unix_nano // 1_000_000,
            end_time_ms=state.end_time_unix_nano // 1_000_000 if state.end_time_unix_nano else None,
            detail=detail,
            reward=state.attributes.get(OJ_RL_REWARD),
            prompt_token_ids=json_safe(state.attributes.get(OJ_RL_PROMPT_TOKEN_IDS)),
            completion_token_ids=json_safe(state.attributes.get(OJ_RL_COMPLETION_TOKEN_IDS)),
            logprobs=json_safe(state.attributes.get(OJ_RL_LOGPROBS)),
            meta=self.legacy_meta(state),
        )

    def build_tool_step(self, state: TrajectorySpanState, *, parent_llm_ref: str | None) -> TrajectoryStep:
        meta = self.legacy_meta(state)
        if parent_llm_ref:
            meta["parent_llm_call"] = parent_llm_ref
            meta[LEGACY_PARENT_LLM_CALL] = parent_llm_ref
        detail = ToolCallDetail(
            tool_name=str(state.attributes.get(GEN_AI_TOOL_NAME) or state.name or ""),
            call_args=json_safe(state.attributes.get(GEN_AI_TOOL_CALL_ARGUMENTS, unwrap_io(state.inputs, "inputs"))),
            call_result=json_safe(state.attributes.get(GEN_AI_TOOL_CALL_RESULT, unwrap_io(state.outputs, "outputs"))),
            tool_call_id=state.attributes.get(GEN_AI_TOOL_CALL_ID),
        )
        return TrajectoryStep(
            kind="tool",
            error=state.error,
            start_time_ms=state.start_time_unix_nano // 1_000_000,
            end_time_ms=state.end_time_unix_nano // 1_000_000 if state.end_time_unix_nano else None,
            detail=detail,
            meta=meta,
        )

    @staticmethod
    def legacy_meta(state: TrajectorySpanState) -> dict[str, Any]:
        return {
            "operator_id": state.attributes.get(GEN_AI_TOOL_NAME) or state.attributes.get(OJ_AGENT_NAME) or state.name,
            "span_name": state.name,
            "invoke_id": state.invoke_id,
            "parent_invoke_id": state.parent_invoke_id,
            "span_id": state.span_id(),
            "parent_span_id": state.parent_span_id(),
            "status": state.status,
            "attributes": json_safe(state.attributes),
        }

    @staticmethod
    def calculate_cost(steps: list[TrajectoryStep]) -> dict[str, int] | None:
        input_tokens = 0
        output_tokens = 0
        for step in steps:
            detail = step.detail
            if step.kind != "llm" or not isinstance(detail, LLMCallDetail) or not detail.usage:
                continue
            input_tokens += int(detail.usage.get("prompt_tokens", 0) or 0)
            output_tokens += int(detail.usage.get("completion_tokens", 0) or 0)
        if input_tokens <= 0 and output_tokens <= 0:
            return None
        return {"input_tokens": input_tokens, "output_tokens": output_tokens}


__all__ = [
    "TrajectorySpanState",
    "TrajectoryTraceState",
]
