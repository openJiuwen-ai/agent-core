# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""In-memory prompt attachment management for DeepAgent prompt assembly."""
from __future__ import annotations

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


class PromptAttachmentKind(str, Enum):
    """Built-in prompt attachment kinds."""

    GENERIC = "generic"
    TEXT = "text"
    RUNTIME = "runtime"
    MEMORY = "memory"
    FILE = "file"
    TOOL = "tool"
    SKILL = "skill"
    DIAGNOSTIC = "diagnostic"
    TODO_REMINDER = "todo_reminder"
    WORKSPACE_DELTA = "workspace_delta"


class PromptAttachment(BaseModel):
    """Structured dynamic prompt fragment injected into one model call."""

    id: str
    section: str
    kind: PromptAttachmentKind | str = PromptAttachmentKind.GENERIC
    content: str | None = None
    priority: int = 100
    source: str | None = None
    session_id: str
    created_at: str | None = None
    updated_at: str | None = None
    expires_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_kind: str = "text/plain"
    content_path: str | None = None
    content_sha256: str | None = None


class PromptAttachmentUpdate(BaseModel):
    """Explicit update schema for fields callers are allowed to modify."""

    kind: PromptAttachmentKind | str | None = None
    content: str | None = None
    priority: int | None = None
    source: str | None = None
    expires_at: str | None = None
    metadata: dict[str, Any] | None = None
    content_kind: str | None = None


WindowMutator = Callable[[ModelContext, ContextWindow], Awaitable[ContextWindow]]

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


class PromptAttachmentContextWriter:
    """Context-aware prompt attachment writer for rail migration."""

    def __init__(self, manager: "PromptAttachmentManager", ctx: Any) -> None:
        self._manager = manager
        self.session_id = _resolve_session_id_from_context(ctx)

    async def add_section(
        self,
        section: str,
        content: str,
        kind: PromptAttachmentKind | str,
        source: str,
        *,
        priority: int = 100,
        metadata: dict[str, Any] | None = None,
        content_kind: str = "text/plain",
        expires_at: str | None = None,
    ) -> PromptAttachment:
        """Add or replace one section in the bound session."""

        return await self._manager.add_section(
            session_id=self._require_session_id(),
            section=section,
            content=content,
            kind=kind,
            source=source,
            priority=priority,
            metadata=metadata,
            content_kind=content_kind,
            expires_at=expires_at,
        )

    async def add_from_prompt_section(
        self,
        prompt_section: Any,
        kind: PromptAttachmentKind | str,
        source: str,
        *,
        priority: int | None = None,
        language: str = "cn",
        metadata: dict[str, Any] | None = None,
        content_kind: str = "text/plain",
        expires_at: str | None = None,
    ) -> PromptAttachment | None:
        """Add or replace an attachment section from an existing PromptSection."""

        if prompt_section is None:
            return None
        section_name = _section_value(getattr(prompt_section, "name", "section"))
        render = getattr(prompt_section, "render", None)
        content = render(language) if callable(render) else str(getattr(prompt_section, "content", prompt_section))
        if not str(content).strip():
            return None
        return await self.add_section(
            section=section_name,
            content=content,
            kind=kind,
            source=source,
            priority=getattr(prompt_section, "priority", 100) if priority is None else priority,
            metadata=metadata,
            content_kind=content_kind,
            expires_at=expires_at,
        )

    async def clear_section(self, section: str) -> int:
        """Remove one section from the bound session."""

        return await self._manager.clear_section(session_id=self._require_session_id(), section=section)

    def _require_session_id(self) -> str:
        if not self.session_id:
            raise ValueError("prompt attachment context requires session_id")
        return self.session_id


class PromptAttachmentManager:
    """DeepAgent-private in-memory prompt attachment manager."""

    def __init__(self) -> None:
        self._items: dict[str, dict[str, PromptAttachment]] = {}
        self._lock = threading.RLock()

    def bind_context(self, ctx: Any) -> PromptAttachmentContextWriter:
        """Return a context-aware writer for rail prompt attachment migration."""

        return PromptAttachmentContextWriter(self, ctx)

    async def add_section(
        self,
        *,
        session_id: str,
        section: str,
        content: str,
        kind: PromptAttachmentKind | str,
        source: str,
        priority: int = 100,
        metadata: dict[str, Any] | None = None,
        content_kind: str = "text/plain",
        expires_at: str | None = None,
    ) -> PromptAttachment:
        """Add or replace one section in a session."""

        section_id = _section_value(section)
        merged_metadata = {
            **(metadata or {}),
            "section": section_id,
            "source": source,
        }
        item = PromptAttachment(
            id=self._make_section_id(session_id=session_id, section=section_id),
            section=section_id,
            kind=kind,
            content=content,
            priority=priority,
            source=source,
            session_id=session_id,
            expires_at=expires_at,
            metadata=merged_metadata,
            content_kind=content_kind,
        )
        return await self._add(item)

    async def clear_section(self, *, session_id: str, section: str) -> int:
        section_id = _section_value(section)
        with self._lock:
            bucket = self._items.get(session_id)
            if not bucket or section_id not in bucket:
                return 0
            del bucket[section_id]
            if not bucket:
                self._items.pop(session_id, None)
            return 1

    async def get_by_id(self, prompt_attachment_id: str, *, session_id: str | None = None) -> PromptAttachment | None:
        item = self._find_by_id(prompt_attachment_id, session_id=session_id)
        return item.model_copy(deep=True) if item is not None else None

    async def update_by_id(self, prompt_attachment_id: str, update: PromptAttachmentUpdate) -> PromptAttachment:
        with self._lock:
            location = self._find_location_by_id_unlocked(prompt_attachment_id)
            if location is None:
                raise KeyError(f"prompt attachment not found: {prompt_attachment_id}")
            session_id, section = location
            current = self._items[session_id][section]
            data = current.model_dump()
            data.update(self._update_data(update))
            data["id"] = current.id
            data["section"] = current.section
            data["session_id"] = current.session_id
            data["created_at"] = current.created_at
            updated = self._normalize_for_write(PromptAttachment(**data), is_new=False)
            if updated.source != current.source:
                logger.warning(
                    "[PromptAttachmentManager] prompt attachment section source changed: "
                    "session_id=%s, section=%s, old_source=%s, new_source=%s",
                    session_id,
                    section,
                    current.source,
                    updated.source,
                )
            self._items[session_id][section] = updated
            return updated.model_copy(deep=True)

    async def remove_by_id(self, prompt_attachment_id: str, *, session_id: str | None = None) -> bool:
        with self._lock:
            location = self._find_location_by_id_unlocked(prompt_attachment_id)
            if location is None:
                return False
            found_session_id, section = location
            if session_id is not None and found_session_id != session_id:
                return False
            del self._items[found_session_id][section]
            if not self._items[found_session_id]:
                self._items.pop(found_session_id, None)
            return True

    async def list_by_filter(
        self,
        *,
        session_id: str | None = None,
        section: str | None = None,
        kind: PromptAttachmentKind | str | None = None,
        source: str | None = None,
    ) -> list[PromptAttachment]:
        section_id = _section_value(section) if section is not None else None
        kind_value = _kind_value(kind) if kind is not None else None
        with self._lock:
            candidates = [item.model_copy(deep=True) for item in self._iter_items_unlocked(session_id=session_id)]
        items = []
        for item in candidates:
            if section_id is not None and item.section != section_id:
                continue
            if kind_value is not None and _kind_value(item.kind) != kind_value:
                continue
            if source is not None and item.source != source:
                continue
            items.append(item)
        return self._stable_sort(items)

    async def remove_by_filter(
        self,
        *,
        session_id: str | None = None,
        section: str | None = None,
        kind: PromptAttachmentKind | str | None = None,
        source: str | None = None,
        allow_all: bool = False,
    ) -> int:
        self._validate_destructive_filter(
            session_id=session_id,
            section=section,
            kind=kind,
            source=source,
            allow_all=allow_all,
        )
        items = await self.list_by_filter(session_id=session_id, section=section, kind=kind, source=source)
        count = 0
        for item in items:
            if await self.remove_by_id(item.id, session_id=item.session_id):
                count += 1
        return count

    async def clear_session(self, session_id: str) -> int:
        with self._lock:
            bucket = self._items.pop(session_id, {})
            return len(bucket)

    async def clear_all(self) -> int:
        with self._lock:
            count = sum(len(bucket) for bucket in self._items.values())
            self._items.clear()
            return count

    async def update_content_by_id(
        self,
        prompt_attachment_id: str,
        *,
        content: str | None,
        session_id: str | None = None,
        content_kind: str | None = None,
    ) -> PromptAttachment:
        current = await self.get_by_id(prompt_attachment_id, session_id=session_id)
        if current is None:
            raise KeyError(f"prompt attachment not found: {prompt_attachment_id}")
        update_kwargs: dict[str, Any] = {"content": content}
        if content_kind is not None:
            update_kwargs["content_kind"] = content_kind
        return await self.update_by_id(prompt_attachment_id, PromptAttachmentUpdate(**update_kwargs))

    async def update_metadata_by_id(
        self,
        prompt_attachment_id: str,
        *,
        metadata: dict[str, Any],
        session_id: str | None = None,
        merge: bool = True,
    ) -> PromptAttachment:
        current = await self.get_by_id(prompt_attachment_id, session_id=session_id)
        if current is None:
            raise KeyError(f"prompt attachment not found: {prompt_attachment_id}")
        next_metadata = {**current.metadata, **metadata} if merge else dict(metadata)
        return await self.update_by_id(prompt_attachment_id, PromptAttachmentUpdate(metadata=next_metadata))

    async def replace_source(
        self,
        *,
        source: str,
        prompt_attachments: Iterable[PromptAttachment],
        session_id: str | None = None,
    ) -> list[PromptAttachment]:
        await self.remove_by_filter(source=source, session_id=session_id, allow_all=session_id is None)
        added: list[PromptAttachment] = []
        for prompt_attachment in prompt_attachments:
            data = prompt_attachment.model_copy(deep=True)
            data.source = source
            if session_id is not None:
                data.session_id = session_id
            data.id = self._make_section_id(session_id=data.session_id, section=data.section)
            added.append(await self._add(data))
        return added

    async def clear_source(self, *, source: str, session_id: str | None = None) -> int:
        return await self.remove_by_filter(source=source, session_id=session_id)

    async def add_file_reference(
        self,
        *,
        file_path: str,
        summary: str | None,
        session_id: str,
        section: str | None = None,
        source: str | None = None,
        priority: int = 100,
        metadata: dict[str, Any] | None = None,
    ) -> PromptAttachment:
        meta = dict(metadata or {})
        meta["file_path"] = file_path
        return await self.add_section(
            session_id=session_id,
            section=section or f"file_{_safe_id_part(file_path, fallback='file')}",
            content=summary or f"File reference: {file_path}",
            kind=PromptAttachmentKind.FILE,
            source=source or "file_reference",
            priority=priority,
            metadata=meta,
            content_kind="text/markdown",
        )

    async def collect_for_session(self, session_id: str) -> list[PromptAttachment]:
        """Collect in-memory prompt attachments visible to one session."""

        result: list[PromptAttachment] = []
        now = _utc_now()
        expired: list[tuple[str, str]] = []
        with self._lock:
            for item in self._iter_items_unlocked(session_id=session_id):
                if self._is_expired(item, now):
                    expired.append((item.session_id, item.section))
                    continue
                result.append(item.model_copy(deep=True))
            for expired_session_id, expired_section in expired:
                bucket = self._items.get(expired_session_id)
                if bucket is not None:
                    bucket.pop(expired_section, None)
                    if not bucket:
                        self._items.pop(expired_session_id, None)
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
            blocks.append(f'<prompt-attachment type="{_xml_attr(_kind_value(item.kind))}">')
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

    @staticmethod
    def inject_messages(
        messages: list[BaseMessage],
        rendered_prompt_attachments: str,
    ) -> list[BaseMessage]:
        """Append rendered prompt attachment text as a standalone user message."""

        if not rendered_prompt_attachments:
            return list(messages)

        new_messages = list(messages)
        new_messages.append(UserMessage(content=rendered_prompt_attachments))
        return new_messages

    def make_window_mutator(self, session_id: str) -> WindowMutator:
        """Build the ContextEngine final-window mutator."""

        async def mutator(context: ModelContext, window: ContextWindow) -> ContextWindow:
            del context
            prompt_attachments = await self.collect_for_session(session_id)
            logger.info(
                "[PromptAttachmentManager] collect prompt attachments: "
                f"session_id={session_id}, collected_ids={[item.id for item in prompt_attachments]}"
            )
            rendered = self.render(prompt_attachments)
            if not rendered:
                return window
            messages = self.inject_messages(window.context_messages, rendered)
            logger.info(
                "[PromptAttachmentManager] injected prompt attachments: "
                f"session_id={session_id}, "
                f"prompt_attachment_ids={[item.id for item in prompt_attachments]}, "
                f"rendered_prompt_attachment_hash={hash_rendered(rendered)}"
            )
            return window.model_copy(update={"context_messages": messages})

        return mutator

    async def _add(self, prompt_attachment: PromptAttachment) -> PromptAttachment:
        with self._lock:
            section = _section_value(prompt_attachment.section)
            item = prompt_attachment.model_copy(deep=True)
            item.section = section
            item.id = self._make_section_id(session_id=item.session_id, section=section)
            existing = self._items.get(item.session_id, {}).get(section)
            if existing is not None and existing.source != item.source:
                logger.warning(
                    "[PromptAttachmentManager] prompt attachment section overwritten by different source: "
                    "session_id=%s, section=%s, old_source=%s, new_source=%s",
                    item.session_id,
                    section,
                    existing.source,
                    item.source,
                )
            normalized = self._normalize_for_write(item, is_new=existing is None)
            self._items.setdefault(normalized.session_id, {})[section] = normalized
            return normalized.model_copy(deep=True)

    @staticmethod
    def _normalize_for_write(prompt_attachment: PromptAttachment, *, is_new: bool) -> PromptAttachment:
        now = _utc_now()
        data = prompt_attachment.model_copy(deep=True)
        if not data.session_id:
            raise ValueError("prompt attachment requires session_id")
        if not data.section:
            raise ValueError("prompt attachment requires section")
        if is_new or not data.created_at:
            data.created_at = now
        data.updated_at = now
        data.content_sha256 = _content_sha256(data.content)
        data.metadata = {**(data.metadata or {}), "section": data.section}
        if data.source is not None:
            data.metadata.setdefault("source", data.source)
        return data

    @staticmethod
    def _update_data(update: PromptAttachmentUpdate) -> dict[str, Any]:
        return update.model_dump(mode="json", exclude_unset=True)

    @staticmethod
    def _stable_sort(prompt_attachments: Iterable[PromptAttachment]) -> list[PromptAttachment]:
        return sorted(
            prompt_attachments,
            key=lambda item: (
                item.priority,
                item.source or "",
                item.section,
            ),
        )

    @staticmethod
    def _is_expired(prompt_attachment: PromptAttachment, now: str) -> bool:
        return bool(prompt_attachment.expires_at and prompt_attachment.expires_at <= now)

    @staticmethod
    def _make_section_id(*, session_id: str, section: str) -> str:
        return f"session.{_safe_id_part(session_id, fallback='session')}.{_section_value(section)}"

    def _find_by_id(self, prompt_attachment_id: str, *, session_id: str | None = None) -> PromptAttachment | None:
        with self._lock:
            location = self._find_location_by_id_unlocked(prompt_attachment_id)
            if location is None:
                return None
            found_session_id, section = location
            if session_id is not None and found_session_id != session_id:
                return None
            return self._items[found_session_id][section]

    def _find_location_by_id_unlocked(self, prompt_attachment_id: str) -> tuple[str, str] | None:
        for session_id, bucket in self._items.items():
            for section, item in bucket.items():
                if item.id == prompt_attachment_id:
                    return session_id, section
        return None

    def _iter_items_unlocked(self, *, session_id: str | None = None) -> Iterable[PromptAttachment]:
        if session_id is not None:
            yield from self._items.get(session_id, {}).values()
            return
        for bucket in self._items.values():
            yield from bucket.values()

    @staticmethod
    def _validate_destructive_filter(
        *,
        session_id: str | None,
        section: str | None,
        kind: PromptAttachmentKind | str | None,
        source: str | None,
        allow_all: bool,
    ) -> None:
        has_filter = any(value is not None for value in (session_id, section, kind, source))
        if not has_filter and not allow_all:
            raise ValueError("destructive prompt attachment operation requires at least one filter")

__all__ = [
    "PromptAttachment",
    "PromptAttachmentContextWriter",
    "PromptAttachmentKind",
    "PromptAttachmentManager",
    "PromptAttachmentUpdate",
    "hash_prompt_attachment",
    "hash_rendered",
]
