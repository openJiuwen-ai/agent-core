# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Legacy ContextProcessor base behavior.

The public processor contract is inherited from the current base class so legacy
processors remain valid ContextEngine plug-ins. The offload helper behavior is
kept compatible with upstream/develop's older implementation.
"""

import os
import uuid
from typing import Any, List, Optional

from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.context_engine.processor.base import (
    ContextEvent as ContextEvent,
    ContextProcessor as CurrentContextProcessor,
)
from openjiuwen.core.context_engine.schema.messages import create_offload_message
from openjiuwen.core.foundation.llm import BaseMessage

_OFFLOAD_MESSAGE_HANDLE: str = "[[OFFLOAD: handle={handle}, type={type}]]"
_OFFLOAD_MESSAGE_HANDLE_WITH_PATH: str = "[[OFFLOAD: handle={handle}, type={type}, path={path}]]"

__all__ = ["ContextEvent", "ContextProcessor"]


class ContextProcessor(CurrentContextProcessor):
    """Legacy base with upstream/develop offload fallback semantics."""

    async def offload_messages(
        self,
        role: str,
        content: str,
        messages: List[BaseMessage],
        *,
        context: ModelContext = None,
        offload_handle: str = None,
        offload_type: str = "filesystem",
        offload_path: str = None,
        **kwargs: Any,
    ) -> Optional[BaseMessage]:
        if not messages:
            return None

        if not offload_handle:
            offload_handle = uuid.uuid4().hex

        if context is not None:
            if offload_type == "in_memory":
                return self._offload_messages_to_memory(
                    role, content, messages, context, offload_handle, **kwargs
                )
            if offload_type == "filesystem":
                session_id = context.session_id()
                workspace_dir = context.workspace_dir()
                if not offload_path:
                    offload_path = self._generate_offload_path(workspace_dir, session_id, offload_handle)
                sys_operation = kwargs.get("sys_operation")
                write_success = await self._write_offload_to_file(
                    session_id=session_id,
                    offload_handle=offload_handle,
                    offload_path=offload_path,
                    messages=messages,
                    sys_operation=sys_operation,
                )
                if not write_success:
                    return self._offload_messages_to_memory(
                        role, content, messages, context, offload_handle, **kwargs
                    )
                return await self._offload_messages_to_filesystem(
                    role, content, offload_handle, offload_path, session_id=session_id, **kwargs
                )
        return None

    @staticmethod
    def _generate_offload_path(workspace_dir: str, session_id: str, offload_handle: str) -> str:
        if workspace_dir:
            return os.path.join(
                workspace_dir, "context", session_id + "_context", "offload", offload_handle + ".json"
            )
        return os.path.join("memory", "offloads", session_id, offload_handle + ".json")

    @staticmethod
    def _offload_messages_to_memory(
        role: str,
        content: str,
        messages: List[BaseMessage],
        context: ModelContext,
        offload_handle: str = None,
        **kwargs: Any,
    ) -> Optional[BaseMessage]:
        content = content + _OFFLOAD_MESSAGE_HANDLE.format(handle=offload_handle, type="in_memory")
        if hasattr(context, "offload_messages"):
            context.offload_messages(offload_handle, messages)
            offload_handle = offload_handle if offload_handle else uuid.uuid4().hex
            return create_offload_message(
                role=role,
                content=content,
                offload_handle=offload_handle,
                offload_type="in_memory",
                **kwargs,
            )
        return None

    @staticmethod
    async def _offload_messages_to_filesystem(
        role: str,
        content: str,
        offload_handle: str = None,
        offload_path: str = None,
        session_id: str = None,
        **kwargs: Any,
    ) -> Optional[BaseMessage]:
        if offload_path:
            content = content + _OFFLOAD_MESSAGE_HANDLE_WITH_PATH.format(
                handle=offload_handle, type="filesystem", path=offload_path
            )
        else:
            content = content + _OFFLOAD_MESSAGE_HANDLE.format(handle=offload_handle, type="filesystem")

        return create_offload_message(
            role=role,
            content=content,
            offload_handle=offload_handle,
            offload_type="filesystem",
            **kwargs,
        )
