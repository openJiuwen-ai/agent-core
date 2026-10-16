# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Integration points owned by later features; unavailable until those features land.

The formal entries call these and reject the request while they raise, so no
execution silently runs without session state, window assembly or interactive
adaptation. Shared contracts live in schema.integration and are re-exported here
for existing callers. Window assembly (PA-06) and the session state record (PA-10) are integrated.
"""

from typing import TYPE_CHECKING

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error

from ..schema.integration import (
    EXECUTION_HEADER,
    TEMPORARY_BACKGROUND,
    WINDOW_PART_KEY,
    ExecutionEnding,
    GroupMessageReader,
    LoadedRecord,
    NativeReplyAdapter,
    SessionStateStore,
    WaitingScene,
    WindowAssembler,
    WindowAssembly,
)
from .session_state import ExecutionStateStore, WindowStore
from .window_assembly import ExecutionWindowAssembler

if TYPE_CHECKING:
    from ..schema.build_context import PersistentBuildContext

__all__ = [
    "EXECUTION_HEADER",
    "TEMPORARY_BACKGROUND",
    "WINDOW_PART_KEY",
    "ExecutionEnding",
    "GroupMessageReader",
    "LoadedRecord",
    "NativeReplyAdapter",
    "SessionStateStore",
    "WaitingScene",
    "WindowAssembler",
    "WindowAssembly",
    "continuation_support",
    "group_message_reader",
    "native_reply_adapter",
    "session_state_store",
    "window_assembler",
]


def _not_integrated(what: str, feature: str):
    return build_error(StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, error_msg=f"{what} is not integrated yet ({feature})")


def session_state_store(context: "PersistentBuildContext") -> SessionStateStore:
    """Return the WindowStore-backed state store of this Execution."""
    return ExecutionStateStore(WindowStore(context.ports.window_backend))


def window_assembler(context: "PersistentBuildContext", *, chain_limit: int | None = None) -> WindowAssembler:
    """Return the window assembler for new Requests and continuations.

    GroupChatPort also serves explicit cross-group search in private Sessions. Only a group-message
    Request automatically reads current-group background. Knowledge navigation remains unavailable.
    """
    ports = context.ports
    if getattr(ports, "knowledge", None) is not None:
        raise _not_integrated("knowledge navigation", "PA-13")
    reader = group_message_reader(context) if getattr(ports, "group_chat", None) is not None else None
    return ExecutionWindowAssembler(reader, chain_limit=chain_limit)


def continuation_support(context: "PersistentBuildContext") -> None:
    """Continuation opens with the archive query by execution_id (PA-12; F_20 D-10)."""
    raise _not_integrated("continuation", "PA-12 archive query")


def group_message_reader(context: "PersistentBuildContext") -> GroupMessageReader:
    """Return the reader of this Session's group messages."""
    from .group_history import ExecutionGroupMessageReader

    return ExecutionGroupMessageReader()


def native_reply_adapter() -> NativeReplyAdapter:
    """Return the adapter from ConfirmReply items to native interrupt input."""
    raise _not_integrated("interactive reply adaptation", "PA-09")
