# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Integration points owned by later features; unavailable until those features land.

The formal entries call these and reject the request while they raise, so no
execution silently runs without session state or interactive adaptation.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error

if TYPE_CHECKING:
    from openjiuwen.core.single_agent.interrupt.response import InterruptRequest

    from ..schema.build_context import PersistentBuildContext
    from ..schema.request import AnswerResponse, PermissionResponse


@dataclass(frozen=True)
class WaitingScene:
    """The Request a paused Execution waits for, as stored with the session state record."""

    request_id: str
    reply_target: str


@dataclass(frozen=True)
class ExecutionEnding:
    """How an Execution ended, so the state record can store its end marker."""

    outcome: str
    request_id: str
    reply_target: str | None
    output_refs: tuple[str, ...] = ()


class SessionStateStore(Protocol):
    """Loads and saves the whitelisted session state record (PA-09)."""

    async def load(self, context: "PersistentBuildContext") -> WaitingScene | None:
        """Write the latest record into the new Session; return the waiting scene, if any."""

    async def save(self, context: "PersistentBuildContext", ending: ExecutionEnding) -> None:
        """Tidy the window and save the record in one write."""


class NativeReplyAdapter(Protocol):
    """Converts one validated reply into native interrupt input (PA-08)."""

    def to_native(self, response: "PermissionResponse | AnswerResponse", request: "InterruptRequest") -> Any:
        """Return the value for InteractiveInput.user_inputs[tool_call_id]."""


def session_state_store(context: "PersistentBuildContext") -> SessionStateStore:
    """Return the WindowStore-backed state store."""
    raise build_error(
        StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, error_msg="session state records are not integrated yet (PA-09)"
    )


def native_reply_adapter() -> NativeReplyAdapter:
    """Return the adapter from ConfirmReply items to native interrupt input."""
    raise build_error(
        StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, error_msg="interactive reply adaptation is not integrated yet (PA-08)"
    )
