# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Trajectory data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

# --- Common types ---
StepKind = Literal["llm", "tool"]
CostInfo = Dict[str, int]  # {"input_tokens": N, "output_tokens": M}
UpdateKey = Tuple[str, str]  # (operator_id, target)
Updates = Dict[UpdateKey, Any]


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
    """Complete execution trajectory."""

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
