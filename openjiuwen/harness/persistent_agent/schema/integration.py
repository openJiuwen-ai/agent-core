# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared value types, protocols and window markers for execution integrations.

This module does not import integration factories or concrete implementations,
so both can depend on the same contracts without a circular import.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from openjiuwen.core.single_agent.interrupt.response import InterruptRequest

    from .build_context import PersistentBuildContext
    from .context import GroupMessagesResult
    from .request import AnswerResponse, PermissionResponse, RequestInput
    from .window import WindowRollover

# Session state key and context ID the native ReAct agent loads its window from.
CONTEXT_STATE_KEY = "context"
CONTEXT_ID = "default_context_id"

# Metadata key PA-06 sets on the window messages it appends; PA-10 tidies the window by it at the end.
WINDOW_PART_KEY = "persistent_window_part"
# Kept and compressed to one line when the Execution ends.
EXECUTION_HEADER = "execution_header"
# Deleted when the Execution ends; injected again with the latest content next time.
TEMPORARY_BACKGROUND = "temporary_background"
# Window seed survives terminal save and is replaced by the next rollover, not by every Request.
WINDOW_HANDOFF = "window_handoff"
# Set by PA-10 on the one-line marker it appends when an Execution ends, unless it waits for a reply.
EXECUTION_END = "execution_end"
# Set on the user message, before the end marker, that keeps the final answer as it was handed off
# when after_invoke rails changed it from the native reply (PA-08).
HANDOFF_TEXT = "handoff_text"
# Set by PA-10 on the tool result it adds for a call that a stop, cancel or error left without one: the
# result was not obtained and its effect must be checked. It is neither a real result nor a failure; the
# end record says why the Execution ended.
STOPPED_TOOL_PLACEHOLDER = "stopped_tool_placeholder"


@dataclass(frozen=True)
class WaitingScene:
    """The Request a paused Execution waits for, as stored with the session state record."""

    request_id: str
    reply_target: str
    spec_fingerprint: str | None = None


@dataclass(frozen=True)
class SessionCheckpoint:
    """Identity of a safe request-start or completed ReAct boundary committed to Session state."""

    request_id: str
    execution_id: str
    iteration_seq: int
    kind: Literal["react_iteration", "request_start", "rollover"]
    last_group_message_id: str | None = None


@dataclass(frozen=True)
class RolloverCheckpoint:
    """Metadata for the most recently committed transition between two active windows."""

    old_window_id: str
    old_window_seq: int
    new_window_id: str
    new_window_seq: int
    archive_ref: str
    trigger_kind: Literal["agent", "capacity_fallback", "request_entry"]
    token_usage: dict[str, int | float | str | bool | None]


@dataclass(frozen=True)
class LoadedRecord:
    """What the entry needs from the latest record once it has been written into the new Session."""

    waiting: WaitingScene | None = None
    # Group scan position the host confirmed to an earlier Execution; recent messages are read after it.
    last_group_message_id: str | None = None
    checkpoint: SessionCheckpoint | None = None
    current_window_id: str | None = None
    window_seq: int | None = None
    last_rollover: RolloverCheckpoint | None = None


@dataclass(frozen=True)
class ExecutionEnding:
    """How an Execution ended, so the state record can store its end marker."""

    outcome: str
    request_id: str
    reply_target: str | None
    output_refs: tuple[str, ...] = ()
    # Group scan position for the next Execution, kept in the end marker.
    last_group_message_id: str | None = None
    # The final answer used for handoff; kept in the window when it differs from the native reply.
    answer_text: str | None = None
    # Message candidates the answer was handed off as, and the (path, share mode, output_id) of each file.
    answer_segments: int = 0
    shared_files: tuple[tuple[str, str, str], ...] = ()
    # Why nothing was handed off: logical paths and categories of the parts not prepared.
    handoff_failure: str | None = None


@dataclass(frozen=True)
class WindowAssembly:
    """Native input for a new Request once its header, background and task input are arranged."""

    native_input: Any
    # Group scan position after this read: the host-confirmed one, or the loaded one without trusted progress.
    last_group_message_id: str | None = None


class SessionStateStore(Protocol):
    """Loads, checkpoints and terminally saves the whitelisted session state record (PA-10 / PA-11)."""

    async def load(self, context: "PersistentBuildContext") -> LoadedRecord:
        """Write the latest record into the new Session; an empty LoadedRecord when there is none."""

    async def save(self, context: "PersistentBuildContext", ending: ExecutionEnding) -> None:
        """Tidy the window and save the record in one write."""

    async def checkpoint(self, context: "PersistentBuildContext", checkpoint: SessionCheckpoint) -> None:
        """Save a safe request-start or completed ReAct boundary without terminal cleanup."""

    async def rollover(self, context: "PersistentBuildContext", transition: "WindowRollover") -> "RolloverCheckpoint":
        """Archive the old window, then atomically save its successor and the iteration checkpoint."""


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
