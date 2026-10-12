# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Integration points owned by later features; unavailable until those features land.

The formal entries call these and reject the request while they raise, so no
execution silently runs without session state, window assembly or interactive
adaptation. Shared contracts live in schema.integration and are re-exported here
for existing callers. Window assembly (PA-06) is integrated.
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
    "group_message_reader",
    "native_reply_adapter",
    "session_state_store",
    "window_assembler",
]


def _not_integrated(what: str, feature: str):
    return build_error(StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, error_msg=f"{what} is not integrated yet ({feature})")


def session_state_store(context: "PersistentBuildContext") -> SessionStateStore:
    """Return the WindowStore-backed state store."""
    raise _not_integrated("session state record", "PA-10")


def window_assembler(context: "PersistentBuildContext") -> WindowAssembler:
    """Return the window assembler for new Requests.

    An injected port means its background is expected: a group Session (GroupChatPort) needs the
    group message reader, and knowledge navigation (KnowledgePort) is not integrated yet. Either
    missing piece is rejected before any event instead of running without that background.
    """
    ports = context.ports
    if getattr(ports, "knowledge", None) is not None:
        raise _not_integrated("knowledge navigation", "PA-13")
    reader = group_message_reader(context) if getattr(ports, "group_chat", None) is not None else None
    return ExecutionWindowAssembler(reader)


def group_message_reader(context: "PersistentBuildContext") -> GroupMessageReader:
    """Return the reader of this Session's group messages."""
    raise _not_integrated("group message reading", "PA-23")


def native_reply_adapter() -> NativeReplyAdapter:
    """Return the adapter from ConfirmReply items to native interrupt input."""
    raise _not_integrated("interactive reply adaptation", "PA-09")
