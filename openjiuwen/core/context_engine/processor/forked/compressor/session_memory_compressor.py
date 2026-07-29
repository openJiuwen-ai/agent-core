from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from openjiuwen.core.context_engine.base import ContextWindow, ModelContext
from openjiuwen.core.context_engine.context.session_memory_manager import (
    SessionMemoryConfig,
    find_message_index_by_context_message_id,
    get_session_memory_runtime,
)
from openjiuwen.core.context_engine.processor.forked.base import ContextEvent, ContextProcessor
from openjiuwen.core.context_engine.processor.forked.compressor.support.util import (
    count_messages_tokens,
    resolve_context_max,
)
from openjiuwen.core.foundation.llm import BaseMessage, UserMessage

SESSION_MEMORY_BLOCK_OPEN = "<memory_block_session>"
SESSION_MEMORY_BLOCK_CLOSE = "</memory_block_session>"


class SessionMemoryCompressorConfig(BaseModel):
    enabled: bool = Field(default=False)
    trigger_context_ratio: float = Field(default=0.8, gt=0.0, lt=1.0)
    session_memory_path: str | None = Field(default=None)
    max_notes_chars: int = Field(default=120_000, gt=0)
    memory: SessionMemoryConfig = Field(default_factory=SessionMemoryConfig)


class SessionMemoryCompressor(ContextProcessor):
    """Materialize committed session memory as a compact context block."""

    memory_block_open = SESSION_MEMORY_BLOCK_OPEN
    memory_block_close = SESSION_MEMORY_BLOCK_CLOSE

    def __init__(self, config: SessionMemoryCompressorConfig):
        super().__init__(config)

    async def trigger_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs: Any,
    ) -> bool:
        _ = kwargs
        if not self.config.enabled:
            return False
        if not self._context_reaches_threshold(context, context_window):
            return False
        return self._build_candidate(context, context_window, check_threshold=False) is not None

    async def on_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs: Any,
    ) -> tuple[ContextEvent | None, ContextWindow]:
        _ = kwargs
        candidate = self._build_candidate(context, context_window)
        if candidate is None:
            return None, context_window

        memory_message, preserved_messages = candidate
        original_messages = list(context_window.context_messages)
        new_messages = [memory_message, *preserved_messages]
        if len(new_messages) > len(original_messages):
            return None, context_window

        context_window.context_messages = new_messages
        context.set_messages(new_messages)
        return (
            ContextEvent(
                event_type=self.processor_type(),
                messages_to_modify=list(range(len(original_messages))),
                compact_summary=memory_message.content,
            ),
            context_window,
        )

    def _build_candidate(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        *,
        check_threshold: bool = True,
    ) -> tuple[UserMessage, list[BaseMessage]] | None:
        if not self.config.enabled:
            return None
        if check_threshold and not self._context_reaches_threshold(context, context_window):
            return None
        notes = self._load_notes(context)
        if not notes:
            return None
        if len(notes) > self.config.max_notes_chars:
            return None

        runtime = self._get_runtime(context)
        anchor = runtime.get("notes_upto_message_id")
        messages = list(context_window.context_messages or [])
        anchor_index = find_message_index_by_context_message_id(messages, anchor)
        if anchor_index < 0:
            return None

        preserved_messages = messages[anchor_index + 1:]  # fmt: skip
        memory_content = (
            f"{SESSION_MEMORY_BLOCK_OPEN}\n"
            "<meaning>Durable asynchronously maintained session memory; not a new user request.</meaning>\n"
            "<conflict_policy>New raw messages and the latest explicit user intent "
            "override this memory.</conflict_policy>\n"
            f"<coverage>notes_upto_message_id={anchor}; coverage_type=completed_api_rounds</coverage>\n"
            "<summary>\n"
            f"{notes.strip()}\n"
            "</summary>\n"
            f"{SESSION_MEMORY_BLOCK_CLOSE}"
        )
        return UserMessage(content=memory_content), preserved_messages

    def _context_reaches_threshold(self, context: ModelContext, context_window: ContextWindow) -> bool:
        messages = list(context_window.system_messages or []) + list(context_window.context_messages or [])
        token_counter_factory = getattr(context, "token_counter", None)
        token_counter = token_counter_factory() if callable(token_counter_factory) else None
        current_tokens = count_messages_tokens(messages, token_counter, self.processor_type())
        context_max = resolve_context_max(context)
        threshold = max(int(context_max * self.config.trigger_context_ratio), 1)
        reached = current_tokens >= threshold
        if not reached:
            return False
        return True

    def _load_notes(self, context: ModelContext) -> str:
        path = self._resolve_notes_path(context)
        if path is None or not path.exists() or not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _resolve_notes_path(self, context: ModelContext) -> Path | None:
        configured = self.config.session_memory_path
        if configured:
            return Path(configured).expanduser().resolve()
        runtime = self._get_runtime(context)
        memory_path = runtime.get("memory_path")
        if memory_path:
            return Path(memory_path).expanduser().resolve()
        workspace_dir = context.workspace_dir() if hasattr(context, "workspace_dir") else ""
        session_id = context.session_id() if hasattr(context, "session_id") else ""
        if not workspace_dir or not session_id:
            return None
        return Path(workspace_dir) / "context" / f"{session_id}_context" / "session_memory" / "session_context.md"

    @staticmethod
    def _get_runtime(context: ModelContext) -> dict[str, Any]:
        session = context.get_session_ref() if hasattr(context, "get_session_ref") else None
        return get_session_memory_runtime(session)

    def load_state(self, state: dict[str, Any]) -> None:
        _ = state

    def save_state(self) -> dict[str, Any]:
        return {}


__all__ = [
    "SessionMemoryCompressor",
    "SessionMemoryCompressorConfig",
]
