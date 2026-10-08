# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Integration points owned by later features; unavailable until those features land.

The formal entries call these and reject the request while they raise, so no
execution silently runs without session state, window assembly or interactive
adaptation. The value types and markers here are the contracts those features
share, so they can be developed in parallel.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error

if TYPE_CHECKING:
    from openjiuwen.core.single_agent.interrupt.response import InterruptRequest

    from ..schema.build_context import PersistentBuildContext
    from ..schema.context import GroupMessagesResult
    from ..schema.request import AnswerResponse, PermissionResponse, RequestInput

# Metadata key PA-06 sets on the window messages it appends; PA-10 tidies the window by it at the end.
WINDOW_PART_KEY = "persistent_window_part"
# Kept and compressed to one line when the Execution ends.
EXECUTION_HEADER = "execution_header"
# Deleted when the Execution ends; injected again with the latest content next time.
TEMPORARY_BACKGROUND = "temporary_background"


@dataclass(frozen=True)
class WaitingScene:
    """The Request a paused Execution waits for, as stored with the session state record."""

    request_id: str
    reply_target: str


@dataclass(frozen=True)
class LoadedRecord:
    """What the entry needs from the latest record once it has been written into the new Session."""

    waiting: WaitingScene | None = None
    # Last group message the previous Execution saw; recent messages are read after it (PA-23).
    last_group_message_id: str | None = None


@dataclass(frozen=True)
class ExecutionEnding:
    """How an Execution ended, so the state record can store its end marker."""

    outcome: str
    request_id: str
    reply_target: str | None
    output_refs: tuple[str, ...] = ()
    # A final answer too large for one output event; must be saved so a later handoff needs no rerun.
    pending_answer: str | None = None
    # Cursor for the next Execution's recent group messages, kept in the end marker.
    last_group_message_id: str | None = None


@dataclass(frozen=True)
class WindowAssembly:
    """Native input for a new Request once its header, background and task input are arranged."""

    native_input: Any
    # Last group message placed in this Execution's background; None keeps no cursor.
    last_group_message_id: str | None = None


class SessionStateStore(Protocol):
    """Loads and saves the whitelisted session state record (PA-10)."""

    async def load(self, context: "PersistentBuildContext") -> LoadedRecord:
        """Write the latest record into the new Session; an empty LoadedRecord when there is none."""

    async def save(self, context: "PersistentBuildContext", ending: ExecutionEnding) -> None:
        """Tidy the window and save the record in one write."""


class WindowAssembler(Protocol):
    """Arranges the window of a new Request after the loaded record (PA-06)."""

    async def assemble(
        self, context: "PersistentBuildContext", request: "RequestInput", record: LoadedRecord
    ) -> WindowAssembly:
        """Append the execution header and temporary background, marked by WINDOW_PART_KEY; return native input."""


class GroupMessageReader(Protocol):
    """Reads this Session's group messages through GroupChatPort (PA-23); shared by PA-06 and PA-07."""

    async def recent_messages(
        self, context: "PersistentBuildContext", *, after: str | None = None, exclude_message_id: str | None = None
    ) -> "GroupMessagesResult":
        """Messages after the cursor plus a few before it for continuity, without the trigger message."""


class NativeReplyAdapter(Protocol):
    """Converts one validated reply into native interrupt input (PA-09)."""

    def to_native(self, response: "PermissionResponse | AnswerResponse", request: "InterruptRequest") -> Any:
        """Return the value for InteractiveInput.user_inputs[tool_call_id]."""


def _not_integrated(what: str, feature: str):
    return build_error(StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, error_msg=f"{what} is not integrated yet ({feature})")


def session_state_store(context: "PersistentBuildContext") -> SessionStateStore:
    """Return the WindowStore-backed state store."""
    raise _not_integrated("session state record", "PA-10")


def window_assembler(context: "PersistentBuildContext") -> WindowAssembler:
    """Return the window assembler for new Requests."""
    raise _not_integrated("window assembly", "PA-06")


def group_message_reader(context: "PersistentBuildContext") -> GroupMessageReader:
    """Return the reader of this Session's group messages."""
    raise _not_integrated("group message reading", "PA-23")


def native_reply_adapter() -> NativeReplyAdapter:
    """Return the adapter from ConfirmReply items to native interrupt input."""
    raise _not_integrated("interactive reply adaptation", "PA-09")
