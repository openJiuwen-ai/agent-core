# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""In-memory prompt attachment management for DeepAgent prompt assembly."""
from __future__ import annotations

import copy
import hashlib
import html
import json
import re
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Iterable

from pydantic import BaseModel, Field

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.base import ContextWindow, ModelContext
from openjiuwen.core.foundation.llm import BaseMessage, UserMessage


class PromptAttachmentScope(str, Enum):
    """Visibility lifecycle for a prompt attachment inside one DeepAgent instance."""

    SESSION = "session"
    TURN = "turn"


class PromptAttachmentKind(str, Enum):
    """Built-in prompt attachment kinds.

    Keeping built-ins explicit helps callers align on stable, documented
    categories while still allowing custom string kinds when needed.
    """

    # Fallback for material that does not yet have a stable product category.
    GENERIC = "generic"
    # Plain text material supplied by an adapter, rail, or user input parser.
    TEXT = "text"
    # Per-call runtime hints such as mode, budget, environment, or date notes.
    RUNTIME = "runtime"
    # Memory or context-engine summaries that should move out of system prompt.
    MEMORY = "memory"
    # File references, opened-file summaries, uploaded file summaries, etc.
    FILE = "file"
    # Tool-related runtime results or tool-use hints that are not long history.
    TOOL = "tool"
    # LSP errors, static analysis output, runtime warnings, and similar signals.
    DIAGNOSTIC = "diagnostic"
    # Task/todo reminders inspired by Claude Code-style reminder material.
    TODO_REMINDER = "todo_reminder"
    # Workspace diff, selected files, dirty tree summaries, or project deltas.
    WORKSPACE_DELTA = "workspace_delta"


class PromptAttachment(BaseModel):
    """Structured dynamic prompt fragment injected into one model call."""

    id: str
    scope: PromptAttachmentScope
    kind: PromptAttachmentKind | str = PromptAttachmentKind.GENERIC
    content: str | None = None
    priority: int = 0
    source: str | None = None
    session_id: str | None = None
    invoke_turn_id: str | None = None
    # Timestamp fields use `_utc_now()` format: ISO-8601 UTC with an explicit
    # "+00:00" offset, for example "2026-06-03T01:23:45.123456+00:00".
    # `expires_at` is compared lexicographically against `_utc_now()`, so callers
    # should not pass local-time strings or "Z" suffixed timestamps unless the
    # expiration check is changed to datetime parsing.
    created_at: str | None = None
    updated_at: str | None = None
    expires_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_kind: str = "text/plain"
    content_path: str | None = None
    content_sha256: str | None = None


class PromptAttachmentUpdate(BaseModel):
    """Explicit update schema for fields callers are allowed to modify.

    Only fields set by the caller are applied. Passing a field explicitly as
    None means "set this field to None"; leaving it unset means "do not change".
    Internal fields such as id, created_at, updated_at, and content_sha256 are
    intentionally excluded.
    """

    scope: PromptAttachmentScope | str | None = None
    kind: PromptAttachmentKind | str | None = None
    content: str | None = None
    priority: int | None = None
    source: str | None = None
    session_id: str | None = None
    invoke_turn_id: str | None = None
    # Same format as PromptAttachment.expires_at: `_utc_now()` style ISO-8601 UTC
    # with "+00:00". Passing None explicitly clears the expiration.
    expires_at: str | None = None
    metadata: dict[str, Any] | None = None
    content_kind: str | None = None


WindowMutator = Callable[[ModelContext, ContextWindow], Awaitable[ContextWindow]]


_SCOPE_ORDER = {
    PromptAttachmentScope.SESSION: 0,
    PromptAttachmentScope.TURN: 1,
}
_DEFAULT_MAX_PROMPT_ATTACHMENT_CHARS = 12000
_DEFAULT_MAX_RENDERED_CHARS = 48000


def _utc_now() -> str:
    """Return the canonical timestamp format used by PromptAttachment time fields."""

    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _kind_value(kind: PromptAttachmentKind | str) -> str:
    return kind.value if isinstance(kind, PromptAttachmentKind) else str(kind)


def _xml_text(text: str) -> str:
    return html.escape(text, quote=False)


def _xml_attr(text: str | None) -> str:
    return html.escape("" if text is None else str(text), quote=True).replace("'", "&apos;")


def _content_sha256(content: str | None) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def hash_rendered(rendered: str) -> str:
    """Return a stable sha256 hash for rendered prompt attachment text."""

    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def hash_prompt_attachment(prompt_attachment: PromptAttachment) -> str:
    """Return a stable semantic hash for one prompt attachment."""

    payload = prompt_attachment.model_dump(mode="json")
    for key in ("content_sha256", "created_at", "updated_at"):
        payload.pop(key, None)
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _safe_id_part(value: str | None, *, fallback: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return fallback
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._-")
    if safe:
        return safe[:80]
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _section_value(section: Any) -> str:
    value = getattr(section, "value", section)
    return _safe_id_part(str(value), fallback="section")


def _resolve_session_id_from_context(ctx: Any) -> str | None:
    session = getattr(ctx, "session", None)
    if session is not None:
        get_session_id = getattr(session, "get_session_id", None)
        if callable(get_session_id):
            session_id = get_session_id()
            if session_id:
                return str(session_id)
        session_id = getattr(session, "session_id", None)
        if session_id:
            return str(session_id)

    for source in (getattr(ctx, "inputs", None), getattr(ctx, "extra", None)):
        if isinstance(source, dict):
            session_id = source.get("session_id") or source.get("_session_id")
            if session_id:
                return str(session_id)
    return None


def _resolve_invoke_turn_id_from_context(ctx: Any) -> str | None:
    for source in (getattr(ctx, "inputs", None), getattr(ctx, "extra", None)):
        if isinstance(source, dict):
            invoke_turn_id = (
                source.get("_invoke_turn_id")
                or source.get("invoke_turn_id")
                or source.get("request_id")
            )
            if invoke_turn_id:
                return str(invoke_turn_id)
    return None


class PromptAttachmentContextWriter:
    """Context-aware prompt attachment writer for rail migration.

    Rails should not each reimplement session/turn lookup, id construction, or
    TURN-scope validation. This facade centralizes that glue while still writing
    through the same PromptAttachmentManager instance, so the manager remains the
    single storage/rendering path.
    """

    def __init__(
        self,
        manager: "PromptAttachmentManager",
        ctx: Any,
    ) -> None:
        self._manager = manager
        self._ctx = ctx
        self.session_id = _resolve_session_id_from_context(ctx)
        self.invoke_turn_id = _resolve_invoke_turn_id_from_context(ctx)

    async def upsert_section(
        self,
        *,
        section: str,
        content: str,
        scope: PromptAttachmentScope | str,
        kind: PromptAttachmentKind | str,
        source: str,
        priority: int = 0,
        metadata: dict[str, Any] | None = None,
        content_kind: str = "text/plain",
        expires_at: str | None = None,
    ) -> PromptAttachment:
        """Create or update one context-scoped prompt attachment section."""

        scope_value = PromptAttachmentScope(self._manager.scope_value(scope))
        session_id = self._require_session_id(scope_value)
        invoke_turn_id = self._require_invoke_turn_id(scope_value)
        section_id = _section_value(section)
        item_id = self._make_section_id(
            section_id=section_id,
            scope=scope_value,
            session_id=session_id,
            invoke_turn_id=invoke_turn_id,
        )
        merged_metadata = {
            **(metadata or {}),
            "section": section_id,
            "source": source,
        }
        item = await self._manager.upsert_text(
            content=content,
            scope=scope_value,
            session_id=session_id,
            invoke_turn_id=invoke_turn_id,
            prompt_attachment_id=item_id,
            kind=kind,
            source=source,
            priority=priority,
            metadata=merged_metadata,
            content_kind=content_kind,
        )
        if expires_at is not None:
            item = await self._manager.update_by_id(item.id, PromptAttachmentUpdate(expires_at=expires_at))
        return item

    async def upsert_from_section(
        self,
        *,
        section: Any,
        scope: PromptAttachmentScope | str,
        kind: PromptAttachmentKind | str,
        source: str,
        priority: int | None = None,
        language: str = "cn",
        metadata: dict[str, Any] | None = None,
        content_kind: str = "text/plain",
    ) -> PromptAttachment:
        """Transitional helper for moving an existing PromptSection to attachment."""

        section_name = _section_value(getattr(section, "name", "section"))
        render = getattr(section, "render", None)
        content = render(language) if callable(render) else str(getattr(section, "content", section))
        return await self.upsert_section(
            section=section_name,
            content=content,
            scope=scope,
            kind=kind,
            source=source,
            priority=getattr(section, "priority", 0) if priority is None else priority,
            metadata=metadata,
            content_kind=content_kind,
        )

    async def clear_section(
        self,
        *,
        section: str,
        scope: PromptAttachmentScope | str,
    ) -> int:
        """Remove the current context's prompt attachment for one section."""

        scope_value = PromptAttachmentScope(self._manager.scope_value(scope))
        session_id = self._require_session_id(scope_value)
        invoke_turn_id = self._require_invoke_turn_id(scope_value)
        item_id = self._make_section_id(
            section_id=_section_value(section),
            scope=scope_value,
            session_id=session_id,
            invoke_turn_id=invoke_turn_id,
        )
        return 1 if await self._manager.remove_by_id(item_id, session_id=session_id) else 0

    def _require_session_id(self, scope: PromptAttachmentScope) -> str:
        if not self.session_id:
            raise ValueError(f"{scope.value} prompt attachment context requires session_id")
        return self.session_id

    def _require_invoke_turn_id(self, scope: PromptAttachmentScope) -> str | None:
        if scope == PromptAttachmentScope.TURN and not self.invoke_turn_id:
            raise ValueError("turn prompt attachment context requires invoke_turn_id")
        return self.invoke_turn_id if scope == PromptAttachmentScope.TURN else None

    @staticmethod
    def _make_section_id(
        *,
        section_id: str,
        scope: PromptAttachmentScope,
        session_id: str,
        invoke_turn_id: str | None,
    ) -> str:
        safe_session_id = _safe_id_part(session_id, fallback="session")
        if scope == PromptAttachmentScope.SESSION:
            return f"session.{safe_session_id}.{section_id}"
        safe_turn_id = _safe_id_part(invoke_turn_id, fallback="turn")
        return f"turn.{safe_session_id}.{safe_turn_id}.{section_id}"


class PromptAttachmentManager:
    """DeepAgent-private in-memory prompt attachment manager.

    The manager stores prompt attachments only in the current DeepAgent process. It
    does not own a runtime directory, manifest, file hot reload, or cross-process
    persistence. Product layers such as jiuwenswarm decide which dynamic
    materials to add and when to clear them; agent-core renders and injects the
    current in-memory prompt attachments before the model call.
    """

    def __init__(self) -> None:
        self._items: dict[str, PromptAttachment] = {}
        self._lock = threading.RLock()

    def for_context(
        self,
        ctx: Any,
    ) -> PromptAttachmentContextWriter:
        """Return a context-aware writer for rail prompt attachment migration."""

        return PromptAttachmentContextWriter(self, ctx)

    @staticmethod
    def scope_value(scope: PromptAttachmentScope | str | None) -> str | None:
        """Normalize a prompt attachment scope to its stored string value."""

        if scope is None:
            return None
        return scope.value if isinstance(scope, PromptAttachmentScope) else PromptAttachmentScope(scope).value

    # ------------------------------------------------------------------
    # Basic management interfaces
    # ------------------------------------------------------------------

    async def add(self, prompt_attachment: PromptAttachment) -> PromptAttachment:
        """Create or replace one in-memory prompt attachment by logical id."""

        with self._lock:
            item = self._normalize_for_write(prompt_attachment, is_new=prompt_attachment.id not in self._items)
            self._items[item.id] = item
            return item.model_copy(deep=True)

    async def update_by_id(self, prompt_attachment_id: str, update: PromptAttachmentUpdate) -> PromptAttachment:
        """Update one prompt attachment by logical id using an explicit update schema."""

        with self._lock:
            current = self._items.get(prompt_attachment_id)
            if current is None:
                raise KeyError(f"prompt attachment not found: {prompt_attachment_id}")
            data = current.model_dump()
            data.update(self._update_data(update))
            data["id"] = prompt_attachment_id
            data["created_at"] = current.created_at
            updated = self._normalize_for_write(PromptAttachment(**data), is_new=False)
            self._items[prompt_attachment_id] = updated
            return updated.model_copy(deep=True)

    async def remove_by_id(self, prompt_attachment_id: str, *, session_id: str | None = None) -> bool:
        """Remove one prompt attachment by logical id from memory."""

        with self._lock:
            current = self._items.get(prompt_attachment_id)
            if current is None:
                return False
            if session_id is not None and current.session_id not in (None, session_id):
                return False
            self._items.pop(prompt_attachment_id, None)
            return True

    async def get_by_id(
        self,
        prompt_attachment_id: str,
        *,
        session_id: str | None = None,
    ) -> PromptAttachment | None:
        """Read one prompt attachment by logical id from memory."""

        item = self._items.get(prompt_attachment_id)
        if item is None:
            return None
        if session_id is not None and item.session_id not in (None, session_id):
            return None
        return item.model_copy(deep=True)

    async def list_by_filter(
        self,
        *,
        session_id: str | None = None,
        invoke_turn_id: str | None = None,
        scope: PromptAttachmentScope | str | None = None,
        kind: PromptAttachmentKind | str | None = None,
        source: str | None = None,
    ) -> list[PromptAttachment]:
        """List in-memory prompt attachments that exactly match the supplied filters."""

        scope_value = self.scope_value(scope)
        kind_value = _kind_value(kind) if kind is not None else None
        with self._lock:
            candidates = [item.model_copy(deep=True) for item in self._items.values()]

        items: list[PromptAttachment] = []
        for item in candidates:
            if session_id is not None and item.session_id != session_id:
                continue
            if invoke_turn_id is not None and item.invoke_turn_id != invoke_turn_id:
                continue
            if scope_value is not None and item.scope.value != scope_value:
                continue
            if kind_value is not None and _kind_value(item.kind) != kind_value:
                continue
            if source is not None and item.source != source:
                continue
            items.append(item)
        return self._stable_sort(items)

    async def update_by_filter(
        self,
        update: PromptAttachmentUpdate,
        *,
        session_id: str | None = None,
        invoke_turn_id: str | None = None,
        scope: PromptAttachmentScope | str | None = None,
        kind: PromptAttachmentKind | str | None = None,
        source: str | None = None,
        allow_all: bool = False,
        allow_all_turns: bool = False,
    ) -> list[PromptAttachment]:
        """Update all prompt attachments matching explicit filters."""

        self._validate_destructive_filter(
            session_id=session_id,
            invoke_turn_id=invoke_turn_id,
            scope=scope,
            kind=kind,
            source=source,
            allow_all=allow_all,
            allow_all_turns=allow_all_turns,
        )
        items = await self.list_by_filter(
            session_id=session_id,
            invoke_turn_id=invoke_turn_id,
            scope=scope,
            kind=kind,
            source=source,
        )
        updated: list[PromptAttachment] = []
        for item in items:
            updated.append(await self.update_by_id(item.id, update))
        return updated

    async def remove_by_filter(
        self,
        *,
        session_id: str | None = None,
        invoke_turn_id: str | None = None,
        scope: PromptAttachmentScope | str | None = None,
        kind: PromptAttachmentKind | str | None = None,
        source: str | None = None,
        allow_all: bool = False,
        allow_all_turns: bool = False,
    ) -> int:
        """Remove all prompt attachments matching explicit filters from memory."""

        self._validate_destructive_filter(
            session_id=session_id,
            invoke_turn_id=invoke_turn_id,
            scope=scope,
            kind=kind,
            source=source,
            allow_all=allow_all,
            allow_all_turns=allow_all_turns,
        )
        items = await self.list_by_filter(
            session_id=session_id,
            invoke_turn_id=invoke_turn_id,
            scope=scope,
            kind=kind,
            source=source,
        )
        count = 0
        for item in items:
            if await self.remove_by_id(item.id, session_id=item.session_id):
                count += 1
        return count

    async def clear_turn(self, session_id: str, invoke_turn_id: str) -> int:
        """Remove all turn-scope prompt attachments for one invoke turn."""

        return await self.remove_by_filter(
            session_id=session_id,
            invoke_turn_id=invoke_turn_id,
            scope=PromptAttachmentScope.TURN,
        )

    async def clear_all(self) -> int:
        """Remove every in-memory prompt attachment for this DeepAgent instance."""

        with self._lock:
            count = len(self._items)
            self._items.clear()
            return count

    # ------------------------------------------------------------------
    # Convenience interfaces built on top of the basic interfaces
    # ------------------------------------------------------------------

    async def update_content_by_id(
        self,
        prompt_attachment_id: str,
        *,
        content: str | None,
        session_id: str | None = None,
        content_kind: str | None = None,
    ) -> PromptAttachment:
        """Update one prompt attachment's content, optionally changing content_kind."""

        if session_id is not None:
            current = await self.get_by_id(prompt_attachment_id, session_id=session_id)
            if current is None:
                raise KeyError(f"prompt attachment not found: {prompt_attachment_id}")
        update_kwargs: dict[str, Any] = {"content": content}
        if content_kind is not None:
            update_kwargs["content_kind"] = content_kind
        update = PromptAttachmentUpdate(**update_kwargs)
        return await self.update_by_id(prompt_attachment_id, update)

    async def update_metadata_by_id(
        self,
        prompt_attachment_id: str,
        *,
        metadata: dict[str, Any],
        session_id: str | None = None,
        merge: bool = True,
    ) -> PromptAttachment:
        """Update one prompt attachment's metadata.

        By default the new metadata is merged into existing metadata. Set
        merge=False to replace the metadata dictionary.
        """

        current = await self.get_by_id(prompt_attachment_id, session_id=session_id)
        if current is None:
            raise KeyError(f"prompt attachment not found: {prompt_attachment_id}")
        next_metadata = {**current.metadata, **metadata} if merge else dict(metadata)
        return await self.update_by_id(prompt_attachment_id, PromptAttachmentUpdate(metadata=next_metadata))

    async def add_text(
        self,
        *,
        content: str,
        scope: PromptAttachmentScope | str,
        session_id: str | None = None,
        invoke_turn_id: str | None = None,
        prompt_attachment_id: str | None = None,
        kind: PromptAttachmentKind | str = PromptAttachmentKind.TEXT,
        source: str | None = None,
        priority: int = 0,
        metadata: dict[str, Any] | None = None,
        content_kind: str = "text/plain",
    ) -> PromptAttachment:
        """Convenience helper for creating a text prompt attachment."""

        scope_value = PromptAttachmentScope(self.scope_value(scope))
        item_id = prompt_attachment_id or self._make_logical_id(
            scope=scope_value,
            session_id=session_id,
            invoke_turn_id=invoke_turn_id,
            kind=kind,
            source=source,
            content=content,
        )
        return await self.add(PromptAttachment(
            id=item_id,
            scope=scope_value,
            kind=kind,
            content=content,
            priority=priority,
            source=source,
            session_id=session_id,
            invoke_turn_id=invoke_turn_id,
            metadata=metadata or {},
            content_kind=content_kind,
        ))

    async def upsert_text(
        self,
        *,
        content: str,
        scope: PromptAttachmentScope | str,
        session_id: str | None = None,
        invoke_turn_id: str | None = None,
        prompt_attachment_id: str | None = None,
        kind: PromptAttachmentKind | str = PromptAttachmentKind.TEXT,
        source: str | None = None,
        priority: int = 0,
        metadata: dict[str, Any] | None = None,
        content_kind: str = "text/plain",
    ) -> PromptAttachment:
        """Create or update a deterministic text prompt attachment."""

        scope_value = PromptAttachmentScope(self.scope_value(scope))
        item_id = prompt_attachment_id or self._make_logical_id(
            scope=scope_value,
            session_id=session_id,
            invoke_turn_id=invoke_turn_id,
            kind=kind,
            source=source,
            content=None,
        )
        current = await self.get_by_id(item_id, session_id=session_id)
        update = PromptAttachmentUpdate(
            content=content,
            scope=scope_value,
            kind=kind,
            priority=priority,
            source=source,
            session_id=session_id,
            invoke_turn_id=invoke_turn_id,
            metadata=metadata or {},
            content_kind=content_kind,
        )
        if current is None:
            return await self.add_text(
                content=content,
                scope=scope_value,
                session_id=session_id,
                invoke_turn_id=invoke_turn_id,
                prompt_attachment_id=item_id,
                kind=kind,
                source=source,
                priority=priority,
                metadata=metadata,
                content_kind=content_kind,
            )
        return await self.update_by_id(item_id, update)

    async def replace_source(
        self,
        *,
        source: str,
        prompt_attachments: Iterable[PromptAttachment],
        session_id: str | None = None,
        invoke_turn_id: str | None = None,
        scope: PromptAttachmentScope | str | None = None,
    ) -> list[PromptAttachment]:
        """Replace all prompt attachments from one source with new prompt attachments."""

        await self.remove_by_filter(
            source=source,
            session_id=session_id,
            invoke_turn_id=invoke_turn_id,
            scope=scope,
            allow_all_turns=invoke_turn_id is not None,
        )
        added: list[PromptAttachment] = []
        for prompt_attachment in prompt_attachments:
            data = prompt_attachment.model_copy(deep=True)
            data.source = source
            if session_id is not None:
                data.session_id = session_id
            if invoke_turn_id is not None:
                data.invoke_turn_id = invoke_turn_id
            added.append(await self.add(data))
        return added

    async def clear_source(
        self,
        *,
        source: str,
        session_id: str | None = None,
        invoke_turn_id: str | None = None,
        scope: PromptAttachmentScope | str | None = None,
    ) -> int:
        """Remove all prompt attachments from one source."""

        return await self.remove_by_filter(
            source=source,
            session_id=session_id,
            invoke_turn_id=invoke_turn_id,
            scope=scope,
            allow_all_turns=invoke_turn_id is not None,
        )

    async def add_file_reference(
        self,
        *,
        file_path: str,
        summary: str | None,
        scope: PromptAttachmentScope | str,
        session_id: str | None = None,
        invoke_turn_id: str | None = None,
        prompt_attachment_id: str | None = None,
        source: str | None = None,
        priority: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> PromptAttachment:
        """Convenience helper for creating a file-reference prompt attachment."""

        meta = dict(metadata or {})
        meta["file_path"] = file_path
        content = summary or f"File reference: {file_path}"
        return await self.add_text(
            content=content,
            scope=scope,
            session_id=session_id,
            invoke_turn_id=invoke_turn_id,
            prompt_attachment_id=prompt_attachment_id,
            kind=PromptAttachmentKind.FILE,
            source=source or "file_reference",
            priority=priority,
            metadata=meta,
            content_kind="text/markdown",
        )

    async def collect_for_turn(self, session_id: str, invoke_turn_id: str) -> list[PromptAttachment]:
        """Collect in-memory prompt attachments visible to one model call.

        Turn-scope attachments must match invoke_turn_id. This protects concurrent
        requests that share one DeepAgent instance and session.
        """

        result: list[PromptAttachment] = []
        now = _utc_now()
        for item in await self.list_by_filter():
            if self._is_expired(item, now):
                continue
            if item.scope == PromptAttachmentScope.SESSION and item.session_id == session_id:
                result.append(item)
            elif (
                item.scope == PromptAttachmentScope.TURN
                and item.session_id == session_id
                and item.invoke_turn_id == invoke_turn_id
            ):
                result.append(item)
        return self._stable_sort(result)

    def render(
        self,
        prompt_attachments: Iterable[PromptAttachment],
        *,
        max_prompt_attachment_chars: int = _DEFAULT_MAX_PROMPT_ATTACHMENT_CHARS,
        max_rendered_chars: int = _DEFAULT_MAX_RENDERED_CHARS,
    ) -> str:
        """Render prompt attachments as a user-role system-reminder block."""

        items = self._stable_sort(prompt_attachments)
        if not items:
            return ""

        truncated_ids: list[str] = []
        blocks = [
            "The following context is automatically attached for this model call only.",
            "It may or may not be relevant to your tasks. Do not respond to it unless it highly relevant to your task.",
            "",
        ]
        for item in items:
            content = item.content or ""
            if max_prompt_attachment_chars > 0 and len(content) > max_prompt_attachment_chars:
                content = (
                    content[:max_prompt_attachment_chars]
                    + "\n\n[Prompt attachment truncated: content exceeded max_prompt_attachment_chars.]"
                )
                truncated_ids.append(item.id)
            blocks.append(
                f'<prompt-attachment id="{_xml_attr(item.id)}" '
                f'scope="{_xml_attr(item.scope.value)}" '
                f'type="{_xml_attr(_kind_value(item.kind))}"'
                f'{self._render_source_attr(item)}>'
            )
            blocks.append(_xml_text(content))
            blocks.append("</prompt-attachment>")
            blocks.append("")

        body = "\n".join(blocks).rstrip()
        rendered = f"<system-reminder>\n{body}\n</system-reminder>"
        if max_rendered_chars > 0 and len(rendered) > max_rendered_chars:
            rendered = (
                rendered[:max_rendered_chars]
                + "\n\n[Prompt attachments truncated: rendered content exceeded max_rendered_chars.]\n"
                + "</system-reminder>"
            )
            truncated_ids = [item.id for item in items]

        if truncated_ids:
            logger.warning(
                "[PromptAttachmentManager] truncated prompt attachments while rendering: "
                f"ids={truncated_ids}, rendered_chars={len(rendered)}"
            )
        return rendered

    def inject_messages(
        self,
        messages: list[BaseMessage],
        rendered_prompt_attachments: str,
    ) -> list[BaseMessage]:
        """Append rendered prompt attachment text to the last user message copy."""

        if not rendered_prompt_attachments:
            return list(messages)

        new_messages = list(messages)
        for index in range(len(new_messages) - 1, -1, -1):
            message = new_messages[index]
            if not isinstance(message, UserMessage) and getattr(message, "role", None) != "user":
                continue
            content = getattr(message, "content", "")
            replacement = copy.deepcopy(message)
            if isinstance(content, str):
                replacement.content = (
                    f"{content}\n\n{rendered_prompt_attachments}" if content else rendered_prompt_attachments
                )
            elif isinstance(content, list):
                replacement.content = self._inject_into_content_blocks(content, rendered_prompt_attachments)
            else:
                logger.warning(
                    "[PromptAttachmentManager] unsupported user message content kind for prompt attachment injection: "
                    f"{type(content).__name__}"
                )
                return new_messages
            new_messages[index] = replacement
            return new_messages

        logger.warning("[PromptAttachmentManager] no user message found for prompt attachment injection")
        return new_messages

    def make_window_mutator(self, session_id: str, invoke_turn_id: str) -> WindowMutator:
        """Build the ContextEngine final-window mutator."""

        async def mutator(context: ModelContext, window: ContextWindow) -> ContextWindow:
            del context
            prompt_attachments = await self.collect_for_turn(session_id, invoke_turn_id)
            all_turn_attachments = [
                item
                for item in await self.list_by_filter(scope=PromptAttachmentScope.TURN)
                if item.session_id == session_id
            ]
            logger.info(
                "[PromptAttachmentManager] collect prompt attachments: "
                f"session_id={session_id}, invoke_turn_id={invoke_turn_id}, "
                f"collected_ids={[item.id for item in prompt_attachments]}, "
                f"visible_turns={[(item.id, item.invoke_turn_id) for item in all_turn_attachments]}"
            )
            rendered = self.render(prompt_attachments)
            if not rendered:
                return window
            messages = self.inject_messages(window.context_messages, rendered)
            logger.info(
                "[PromptAttachmentManager] injected prompt attachments: "
                f"session_id={session_id}, invoke_turn_id={invoke_turn_id}, "
                f"prompt_attachment_ids={[item.id for item in prompt_attachments]}, "
                f"rendered_prompt_attachment_hash={hash_rendered(rendered)}"
            )
            return window.model_copy(update={"context_messages": messages})

        return mutator

    def _normalize_for_write(self, prompt_attachment: PromptAttachment, *, is_new: bool) -> PromptAttachment:
        now = _utc_now()
        data = prompt_attachment.model_copy(deep=True)
        self._validate_scope(data)
        if is_new or not data.created_at:
            data.created_at = now
        data.updated_at = now
        data.content_sha256 = _content_sha256(data.content)
        return data

    @staticmethod
    def _validate_scope(prompt_attachment: PromptAttachment) -> None:
        if prompt_attachment.scope == PromptAttachmentScope.SESSION and not prompt_attachment.session_id:
            raise ValueError("session prompt attachment requires session_id")
        if prompt_attachment.scope == PromptAttachmentScope.TURN:
            if not prompt_attachment.session_id or not prompt_attachment.invoke_turn_id:
                raise ValueError("turn prompt attachment requires session_id and invoke_turn_id")

    @staticmethod
    def _update_data(update: PromptAttachmentUpdate) -> dict[str, Any]:
        return update.model_dump(mode="json", exclude_unset=True)

    @staticmethod
    def _stable_sort(prompt_attachments: Iterable[PromptAttachment]) -> list[PromptAttachment]:
        return sorted(
            prompt_attachments,
            key=lambda item: (
                _SCOPE_ORDER[item.scope],
                item.priority,
                _kind_value(item.kind),
                item.source or "",
                item.id,
            ),
        )

    @staticmethod
    def _is_expired(prompt_attachment: PromptAttachment, now: str) -> bool:
        return bool(prompt_attachment.expires_at and prompt_attachment.expires_at <= now)

    @staticmethod
    def _scope_value(scope: PromptAttachmentScope | str | None) -> str | None:
        return PromptAttachmentManager.scope_value(scope)

    def _validate_destructive_filter(
        self,
        *,
        session_id: str | None,
        invoke_turn_id: str | None,
        scope: PromptAttachmentScope | str | None,
        kind: PromptAttachmentKind | str | None,
        source: str | None,
        allow_all: bool,
        allow_all_turns: bool,
    ) -> None:
        has_filter = any(value is not None for value in (session_id, invoke_turn_id, scope, kind, source))
        if not has_filter and not allow_all:
            raise ValueError("destructive prompt attachment operation requires at least one filter")
        if self.scope_value(scope) == PromptAttachmentScope.TURN.value and not invoke_turn_id and not allow_all_turns:
            raise ValueError("turn-scope destructive operation requires invoke_turn_id")

    @staticmethod
    def _make_logical_id(
        *,
        scope: PromptAttachmentScope,
        session_id: str | None,
        invoke_turn_id: str | None,
        kind: PromptAttachmentKind | str,
        source: str | None,
        content: str | None,
    ) -> str:
        payload = {
            "scope": scope.value,
            "session_id": session_id,
            "invoke_turn_id": invoke_turn_id,
            "kind": _kind_value(kind),
            "source": source,
            "content": content,
        }
        return "ps_" + hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _render_source_attr(item: PromptAttachment) -> str:
        if not item.source:
            return ""
        return f' source="{_xml_attr(item.source)}"'

    @staticmethod
    def _inject_into_content_blocks(
        content: list[str | dict[str, Any]],
        rendered_prompt_attachments: str,
    ) -> list[str | dict[str, Any]]:
        blocks = copy.deepcopy(content)
        for index in range(len(blocks) - 1, -1, -1):
            block = blocks[index]
            if isinstance(block, str):
                blocks[index] = f"{block}\n\n{rendered_prompt_attachments}" if block else rendered_prompt_attachments
                return blocks
            if not isinstance(block, dict):
                continue
            if block.get("kind") == "text":
                text = str(block.get("text", ""))
                block["text"] = f"{text}\n\n{rendered_prompt_attachments}" if text else rendered_prompt_attachments
                return blocks
            if block.get("type") == "text":
                text = str(block.get("text", ""))
                block["text"] = f"{text}\n\n{rendered_prompt_attachments}" if text else rendered_prompt_attachments
                return blocks
        blocks.append({"type": "text", "text": rendered_prompt_attachments})
        return blocks


__all__ = [
    "PromptAttachment",
    "PromptAttachmentContextWriter",
    "PromptAttachmentKind",
    "PromptAttachmentManager",
    "PromptAttachmentScope",
    "PromptAttachmentUpdate",
    "hash_prompt_attachment",
    "hash_rendered",
]
