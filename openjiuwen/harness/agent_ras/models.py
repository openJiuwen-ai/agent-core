# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Agent RAS data models: anomalies, signals, and monitor events."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class Severity(str, Enum):
    """Anomaly severity, ordered low to high."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AnomalyKind(str, Enum):
    """The kind of unhealthy behavior a detector identified."""

    REPEAT_TOOL_CALL = "repeat_tool_call"
    TOOL_CALL_LOOP = "tool_call_loop"
    LLM_THINKING_LOOP = "llm_thinking_loop"
    LLM_THINKING_DEAD_LOOP = "llm_thinking_dead_loop"


class Anomaly(BaseModel):
    """A detected unhealthy condition awaiting remediation."""

    detector: str = Field(..., description="Detector identifier")
    kind: AnomalyKind = Field(..., description="Anomaly category")
    severity: Severity = Field(..., description="Severity level")
    member_name: str = Field(..., description="Affected member")
    summary: str = Field(..., description="One-line description for human/LLM")
    evidence: dict[str, Any] = Field(default_factory=dict)
    timestamp: int = Field(default=0, description="Millisecond Unix epoch when emitted")


class SignalKind(str, Enum):
    """Lifecycle point a signal was captured at."""

    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    TOOL_EXCEPTION = "tool_exception"
    MODEL_EXCEPTION = "model_exception"
    AFTER_MODEL_CALL = "after_model_call"
    BEFORE_MODEL_CALL = "before_model_call"
    STREAM_CHUNK = "stream_chunk"
    INVOKE_RESET = "invoke_reset"


@dataclass(slots=True)
class Signal:
    """One observation captured at a lifecycle point."""

    kind: SignalKind
    member_name: str
    tool_name: str | None = None
    tool_args: Any | None = None
    error: str | None = None
    text_len: int | None = None
    thinking_len: int | None = None
    message_count: int | None = None
    tool_result: Any | None = None
    tool_msg_content: str | None = None
    chunk_type: str | None = None
    chunk_text: str | None = None

    def to_dict(
        self,
        *,
        tool_result_max_bytes: int = 512,
    ) -> dict[str, Any]:
        """Serialize the signal without changing framework-visible values."""
        from openjiuwen.harness.agent_ras.window import stable_result_hash

        payload: dict[str, Any] = {
            "kind": self.kind.value,
            "member_name": self.member_name,
        }
        optional_fields = (
            "tool_name",
            "tool_args",
            "error",
            "text_len",
            "thinking_len",
            "message_count",
            "tool_msg_content",
            "chunk_type",
            "chunk_text",
        )
        for field in optional_fields:
            value = getattr(self, field, None)
            if value is not None:
                payload[field] = value
        if self.tool_result is not None:
            payload["tool_result_hash"] = stable_result_hash(self.tool_result)
            payload["result_preview"] = str(self.tool_result)[:tool_result_max_bytes]
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Signal":
        """Deserialize a dict produced by ``to_dict``."""
        kind_raw = data.get("kind")
        if isinstance(kind_raw, SignalKind):
            kind = kind_raw
        else:
            kind = SignalKind(str(kind_raw))
        tool_result = data.get("tool_result")
        if tool_result is None and data.get("tool_result_hash") is not None:
            tool_result = data["tool_result_hash"]
        return cls(
            kind=kind,
            member_name=str(data.get("member_name") or ""),
            tool_name=data.get("tool_name"),
            tool_args=data.get("tool_args"),
            error=data.get("error"),
            text_len=data.get("text_len"),
            thinking_len=data.get("thinking_len"),
            message_count=data.get("message_count"),
            tool_result=tool_result,
            tool_msg_content=data.get("tool_msg_content"),
            chunk_type=data.get("chunk_type"),
            chunk_text=data.get("chunk_text"),
        )


EventType = Literal[
    "anomaly",
    "recovery_action",
    "metric_update",
    "stream_chunk",
    "stream_truncated",
]


class AgentRASMonitorEvent(BaseModel):
    """One in-process Agent RAS event."""

    event_type: EventType = Field(..., description="Event type literal")
    timestamp: int = Field(..., description="Millisecond Unix epoch")
    agent_id: str = Field(..., description="Owning agent")
    session_id: str = Field(..., description="Owning session")
    anomaly: Optional[Anomaly] = Field(default=None)
    action: Optional[str] = Field(default=None)
    payload: dict[str, Any] = Field(default_factory=dict)
