# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from pathlib import Path
from typing import Any

from openjiuwen.core.context_engine.context.session_memory_manager import SessionMemoryManager
from openjiuwen.core.foundation.llm import BaseMessage


def _message_plain_text(message: BaseMessage) -> str:
    role = getattr(message, "role", "") or message.__class__.__name__
    content = getattr(message, "content", "") or ""
    return f"[{role}] {content}"


def render_plain(messages: list[BaseMessage]) -> str:
    if not messages:
        return "(empty QA)"
    return "\n".join(_message_plain_text(message) for message in messages)


class QAOverviewGenerator:
    """Thin wrapper delegating per-QA overview to SessionMemoryManager (§3.3)."""

    def __init__(self, session_memory_manager: SessionMemoryManager):
        self._sm = session_memory_manager

    def bind_model_defaults(self, model_config: Any, model_client_config: Any) -> None:
        self._sm.bind_model_defaults(model_config, model_client_config)

    async def generate(
        self,
        ctx: Any,
        store: Any,
        state: Any,
        messages: list[BaseMessage],
        *,
        qa_id: str,
    ) -> None:
        _ = store
        workspace = getattr(ctx, "workspace", None)
        await self._sm.generate_overview_for_qa(
            ctx,
            workspace=workspace,
            qa_id=qa_id,
            messages=messages,
            pending_path=Path(state.pending_path),
            active_path=Path(state.overview_path),
        )
