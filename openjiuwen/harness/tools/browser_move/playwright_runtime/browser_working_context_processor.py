# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Ephemeral prompt injection for durable browser-agent working context."""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.core.context_engine import ContextEngine, ContextWindow, ModelContext
from openjiuwen.core.context_engine.processor.base import ContextEvent, ContextProcessor
from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage, UserMessage

from .browser_working_context import (
    BROWSER_TOOL_MEMORY_METADATA_KEY,
    BrowserWorkingContextConfig,
    BrowserWorkingContextStore,
)


_BROWSER_WORKING_CONTEXT_MESSAGE_NAME = "browser_working_context"
_BROWSER_WORKING_CONTEXT_METADATA_KEY = "browser_working_context"


class BrowserWorkingContextProcessorConfig(BrowserWorkingContextConfig):
    """Configuration for durable browser working-context prompt injection."""


@ContextEngine.register_processor()
class BrowserWorkingContextProcessor(ContextProcessor):
    """Inject a bounded session-backed working context without persisting the message."""

    @property
    def config(self) -> BrowserWorkingContextProcessorConfig:
        return self._config

    async def trigger_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs: Any,
    ) -> bool:
        del context, context_window, kwargs
        return True

    async def on_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs: Any,
    ) -> tuple[ContextEvent | None, ContextWindow]:
        del kwargs
        store = BrowserWorkingContextStore(self.config)
        session = context.get_session_ref() if context is not None else None
        if context is not None:
            store.commit_pending_from_messages(session, context.get_messages())

        prompt_text = store.render_and_consume_one_step(session)
        context_window.context_messages = [
            self._prompt_safe_message(message)
            for message in context_window.context_messages
            if not self._is_working_context_message(message)
        ]
        context_window.context_messages.append(
            UserMessage(
                name=_BROWSER_WORKING_CONTEXT_MESSAGE_NAME,
                metadata={_BROWSER_WORKING_CONTEXT_METADATA_KEY: True},
                content=prompt_text,
            )
        )
        return None, context_window

    @staticmethod
    def _is_working_context_message(message: BaseMessage) -> bool:
        return message.name == _BROWSER_WORKING_CONTEXT_MESSAGE_NAME or bool(
            message.metadata.get(_BROWSER_WORKING_CONTEXT_METADATA_KEY)
        )

    @staticmethod
    def _prompt_safe_message(message: BaseMessage) -> BaseMessage:
        """Keep the raw diagnostic tool result stored while rendering only its retention."""

        if not isinstance(message, ToolMessage):
            return message
        if not isinstance(
            message.metadata.get(BROWSER_TOOL_MEMORY_METADATA_KEY),
            dict,
        ):
            return message
        safe_message = message.model_copy(deep=True)
        safe_message.content = (
            "[Complete tool result retained in diagnostic history. "
            "Prompt-safe retained content is in <browser_working_context>.]"
        )
        return safe_message

    def load_state(self, state: Dict[str, Any]) -> None:
        del state

    def save_state(self) -> Dict[str, Any]:
        return {}


__all__ = [
    "BrowserWorkingContextProcessor",
    "BrowserWorkingContextProcessorConfig",
]
