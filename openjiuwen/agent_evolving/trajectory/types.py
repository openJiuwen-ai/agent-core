# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Trajectory data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union, Literal


# --- Common types ---
StepKind = Literal["llm", "tool"]
CostInfo = Dict[str, int]  # {"input_tokens": N, "output_tokens": M}
UpdateKey = Tuple[str, str]  # (operator_id, target)
Updates = Dict[UpdateKey, Any]


# =============================================================================
# StepDetail Union Types
# =============================================================================


@dataclass
class LLMCallDetail:
    """Complete LLM call execution data."""

    model: str
    messages: List[Dict[str, Any]]
    response: Optional[Dict[str, Any]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    usage: Optional[Dict[str, Any]] = None


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
    - Post-injection: reward, log_probs, token_ids (filled after creation)
    - Extension: meta (operator_id, invoke relationships, etc.)
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

    log_probs: Optional[List[float]] = None
    """Token log probabilities. Only for kind='llm'."""

    token_ids: Optional[List[int]] = None
    """Response token IDs. Only for kind='llm'."""

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
