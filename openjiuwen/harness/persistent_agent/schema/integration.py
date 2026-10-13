# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared value types, protocols and window markers for execution integrations.

This module does not import integration factories or concrete implementations,
so both can depend on the same contracts without a circular import.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from openjiuwen.core.single_agent.interrupt.response import InterruptRequest

    from .build_context import PersistentBuildContext
    from .context import GroupMessagesResult
    from .request import AnswerResponse, PermissionResponse, RequestInput

# Metadata key PA-06 sets on the window messages it appends; PA-10 tidies the window by it at the end.
WINDOW_PART_KEY = "persistent_window_part"
# Kept and compressed to one line when the Execution ends.
EXECUTION_HEADER = "execution_header"
# Deleted when the Execution ends; injected again with the latest content next time.
TEMPORARY_BACKGROUND = "temporary_background"
# Set by PA-10 on the tool result it adds for a call that a stop left without one: the result was not
# obtained and its effect must be checked. It is neither a real result nor a failure.
STOPPED_TOOL_PLACEHOLDER = "stopped_tool_placeholder"


@dataclass(frozen=True)
class WaitingScene:
    """The Request a paused Execution waits for, as stored with the session state record."""

    request_id: str
    reply_target: str


@dataclass(frozen=True)
class LoadedRecord:
    """What the entry needs from the latest record once it has been written into the new Session."""

    waiting: WaitingScene | None = None
    # Group scan position the host confirmed to an earlier Execution; recent messages are read after it.
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
    # Group scan position for the next Execution, kept in the end marker.
    last_group_message_id: str | None = None


@dataclass(frozen=True)
class WindowAssembly:
    """Native input for a new Request once its header, background and task input are arranged."""

    native_input: Any
    # Group scan position after this read: the host-confirmed one, or the loaded one without trusted progress.
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
    """Reads this Session's group messages through GroupChatPort (PA-23) for window assembly (PA-06)."""

    async def recent_messages(
        self, context: "PersistentBuildContext", *, after: str | None = None, trigger_message_id: str | None = None
    ) -> "GroupMessagesResult":
        """Messages after the cursor merged with a backfill from the trigger up to this read's upper bound,
        deduplicated and without the trigger: a queued Request's trigger may be older than the cursor, and
        what was said about it meanwhile is not in the window. ``scanned_through`` is the scan position the
        host confirmed for the read after the cursor, or None to keep the cursor."""


class NativeReplyAdapter(Protocol):
    """Converts one validated reply into native interrupt input (PA-09)."""

    def to_native(self, response: "PermissionResponse | AnswerResponse", request: "InterruptRequest") -> Any:
        """Return the value for InteractiveInput.user_inputs[tool_call_id]."""
