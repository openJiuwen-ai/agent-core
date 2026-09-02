# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Schemas for third-party external member runtime reliability.

A Claude Agent SDK or OpenAI Codex SDK member surfaces two kinds of
reliability signal: a transient retrying progress (the SDK is still
auto-retrying) and a finalized failure (a startup or turn has ended). This
module holds the shared domain vocabulary for both:

* :data:`ExternalRuntimeFailureCategory` — the closed set of failure
  categories (auth / quota / rate-limit / server / network / process-start /
  sdk-error / unknown).
* :data:`USER_ACTION_REQUIRED` / :func:`user_action_required` — whether a
  category needs the user or an external system to act.
* :class:`ExternalRuntimeFailureReason` — structured reason (raw message, SDK
  error type/code, HTTP status).
* :class:`ExternalRuntimeFailure` — the finalized failure payload.
  ``failure_id`` is the domain correlation id tying together the failed
  message, round result, logs and trace.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ExternalRuntimeFailureCategory = Literal[
    "auth_required",
    "quota_exceeded",
    "rate_limited",
    "server_unavailable",
    "network_timeout",
    "process_start_failed",
    "sdk_error",
    "unknown",
]

ExternalRuntimeAgentKind = Literal["claude", "codex"]

ExternalRuntimePhase = Literal["startup", "turn"]

# Default ``user_action_required`` per category. ``True`` means the category
# needs the user or an external system to act.
USER_ACTION_REQUIRED: dict[str, bool] = {
    "auth_required": True,
    "quota_exceeded": True,
    "rate_limited": False,
    "server_unavailable": False,
    "network_timeout": False,
    "process_start_failed": True,
    "sdk_error": False,
    "unknown": False,
}


def user_action_required(category: str) -> bool:
    """Return the default ``user_action_required`` for a failure category."""
    return USER_ACTION_REQUIRED.get(category, False)


class ExternalRuntimeFailureReason(BaseModel):
    """Structured reason for an external runtime failure."""

    message: str = Field(default="", description="Raw SDK error message text")
    sdk_error_type: str = Field(default="", description="SDK exception type name, if any")
    sdk_error_code: str = Field(default="", description="SDK error code, if any")
    http_status: int | None = Field(default=None, description="HTTP status code of the failing call, if any")


class ExternalRuntimeFailure(BaseModel):
    """Structured final failure payload for an external runtime.

    ``type`` discriminates this payload from other structured JSON payloads.
    """

    type: Literal["external_runtime_failed"] = "external_runtime_failed"
    failure_id: str = Field(..., description="Stable failure correlation id")
    team_name: str = Field(..., description="Team name")
    member_name: str = Field(..., description="Failing member name")
    agent_kind: ExternalRuntimeAgentKind = Field(..., description="Which SDK produced the failure")
    phase: ExternalRuntimePhase = Field(..., description="Startup or turn")
    category: ExternalRuntimeFailureCategory = Field(..., description="Failure category")
    user_action_required: bool = Field(..., description="Whether the user must act")
    summary: str = Field(..., description="One-line description for human/LLM")
    suggested_action: str = Field(default="", description="Suggested handling direction")
    reason: ExternalRuntimeFailureReason = Field(
        default_factory=ExternalRuntimeFailureReason,
        description="Structured reason",
    )
    round_id: int | None = Field(default=None, description="Member round id, None for startup")


__all__ = [
    "USER_ACTION_REQUIRED",
    "ExternalRuntimeAgentKind",
    "ExternalRuntimeFailure",
    "ExternalRuntimeFailureCategory",
    "ExternalRuntimeFailureReason",
    "ExternalRuntimePhase",
    "user_action_required",
]
