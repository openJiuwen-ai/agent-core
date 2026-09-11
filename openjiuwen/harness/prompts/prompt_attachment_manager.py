# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""In-memory prompt attachment management for DeepAgent prompt assembly."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Iterable

from pydantic import BaseModel, Field

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.base import ContextWindow, ModelContext
from openjiuwen.core.foundation.llm import BaseMessage, SystemMessage, UserMessage


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
    """Structured dynamic prompt fragment managed for a session."""

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


_DEFAULT_MAX_PROMPT_ATTACHMENT_CHARS = 12000
_DEFAULT_MAX_RENDERED_CHARS = 48000
PROMPT_ATTACHMENT_PRESERVE_TAIL_METADATA_KEY = "prompt_attachment_preserve_tail"
PROMPT_ATTACHMENT_HISTORY_METADATA_KEY = "_openjiuwen_prompt_attachment_history"
PROMPT_ATTACHMENT_COMMIT_CALLBACKS_KEY = "_openjiuwen_prompt_attachment_commit_callbacks"
_PROMPT_ATTACHMENT_HISTORY_MODE_KEY = "mode"
_PROMPT_ATTACHMENT_HISTORY_STATE_KEY = "state"
_PROMPT_ATTACHMENT_HISTORY_SESSION_KEY = "session_id"
_PROMPT_ATTACHMENT_HISTORY_SNAPSHOT = "snapshot"
_PROMPT_ATTACHMENT_HISTORY_DELTA = "delta"
_SYSTEM_ATTACHMENT_ROLE_PROVIDERS = frozenset({"bailian", "dashscope"})
_SYSTEM_ATTACHMENT_ROLE_ENDPOINT_PROFILES = frozenset({"bailian", "dashscope"})
_ANTHROPIC_API_MODES = frozenset({"anthropic", "anthropic-messages", "messages"})
_GENERIC_ENDPOINT_PROFILES = frozenset({"", "openai", "openai-compatible"})


def _utc_now() -> str:
    """Return the canonical timestamp format used by PromptAttachment time fields."""

    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _kind_value(kind: PromptAttachmentKind | str) -> str:
    return kind.value if isinstance(kind, PromptAttachmentKind) else str(kind)


def _content_sha256(content: str | None) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def hash_rendered(rendered: str) -> str:
    """Return a stable sha256 hash for rendered prompt attachment text."""

    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def hash_prompt_attachment(prompt_attachment: PromptAttachment) -> str:
    """Return a stable hash for the content visible to the model.

    Internal bookkeeping (timestamps, identifiers, source metadata and
    priority) must not create a new history message when the rendered prompt
    is unchanged.  Keep this payload aligned with ``_render_attachment_payload``
    so change detection answers the user-visible question: did this section's
    prompt content change?
    """

    payload = {
        "section": prompt_attachment.section,
        "kind": _kind_value(prompt_attachment.kind),
        "content": prompt_attachment.content or "",
        "content_kind": prompt_attachment.content_kind,
    }
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


def _config_value(config: Any, key: str) -> Any:
    if isinstance(config, dict):
        return config.get(key)
    return getattr(config, key, None)


def _normalized_config_value(value: Any) -> str:
    value = getattr(value, "value", value)
    return str(value or "").strip().lower().replace("_", "-")


def _uses_system_attachment_role(model_client_config: Any) -> bool:
    """Return whether the active route preserves attachment system messages in place."""

    provider = _normalized_config_value(_config_value(model_client_config, "client_provider"))
    legacy_provider = _normalized_config_value(
        _config_value(model_client_config, "legacy_client_provider")
    )
    endpoint_profile = _normalized_config_value(_config_value(model_client_config, "endpoint_profile"))
    api_mode = _normalized_config_value(_config_value(model_client_config, "api_mode"))

    if provider == "anthropic" or api_mode in _ANTHROPIC_API_MODES:
        return False

    backend = endpoint_profile
    if backend in _GENERIC_ENDPOINT_PROFILES:
        backend = legacy_provider or provider
    return backend in _SYSTEM_ATTACHMENT_ROLE_ENDPOINT_PROFILES or backend in _SYSTEM_ATTACHMENT_ROLE_PROVIDERS


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

    def __init__(self, language: str = "en") -> None:
        self._items: dict[str, dict[str, PromptAttachment]] = {}
        self._lock = asyncio.Lock()
        self._history_lock = asyncio.Lock()
        self.language = language

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
        async with self._lock:
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
        async with self._lock:
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
        async with self._lock:
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
        async with self._lock:
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
        async with self._lock:
            bucket = self._items.pop(session_id, {})
            return len(bucket)

    async def clear_all(self) -> int:
        async with self._lock:
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
        async with self._lock:
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

    def has_history_snapshot(
        self,
        context: ModelContext,
        session_id: str,
    ) -> bool:
        """Return whether ``context`` contains this session's full snapshot.

        Runtime rails use this before replacing an attachment section with a
        delta.  If context compaction or recreation removed the historical
        snapshot, the next update must be rendered as a full snapshot so the
        model does not receive an orphaned delta.
        """

        _, has_snapshot = self._read_history_state(context, session_id)
        return has_snapshot

    async def sync_to_context(
        self,
        context: ModelContext,
        session_id: str,
    ) -> UserMessage | None:
        """Persist an attachment snapshot or delta into the context history.

        The first non-empty attachment state is written as a full snapshot.
        Later calls append only changed sections and explicit removals.  The
        user message metadata carries the materialized section hashes so the
        state can be recovered after the manager is recreated from a session.
        """

        async with self._history_lock:
            prompt_attachments = await self.collect_for_session(session_id)
            current_state = self._state_by_section(prompt_attachments)
            previous_state, has_snapshot = self._read_history_state(context, session_id)

            if not has_snapshot:
                if not prompt_attachments:
                    return None
                rendered = self.render_history_snapshot(prompt_attachments)
                mode = _PROMPT_ATTACHMENT_HISTORY_SNAPSHOT
            else:
                changed = [
                    item
                    for item in prompt_attachments
                    if previous_state.get(item.section) != current_state[item.section]
                ]
                removed = sorted(set(previous_state) - set(current_state))
                if not changed and not removed:
                    return None
                rendered = self.render_delta(changed, removed)
                mode = _PROMPT_ATTACHMENT_HISTORY_DELTA

            message = UserMessage(
                content=rendered,
                metadata={
                    PROMPT_ATTACHMENT_HISTORY_METADATA_KEY: True,
                    _PROMPT_ATTACHMENT_HISTORY_MODE_KEY: mode,
                    _PROMPT_ATTACHMENT_HISTORY_SESSION_KEY: session_id,
                    _PROMPT_ATTACHMENT_HISTORY_STATE_KEY: current_state,
                },
            )
            await context.add_messages(message)
            logger.info(
                "[PromptAttachmentManager] persisted prompt attachment %s: session_id=%s, sections=%s",
                mode,
                session_id,
                sorted(current_state),
            )
            return message

    @staticmethod
    def build_model_window_mutator(
        *,
        session_id: str,
        model_client_config: Any,
    ) -> Callable[[ModelContext, ContextWindow], Awaitable[ContextWindow]]:
        """Build a final-window projection for the active model provider.

        Attachment history remains persisted as ``UserMessage``.  For
        DashScope/Bailian routes using the OpenAI chat-completions client only
        replace marked history messages in place with ``SystemMessage``.
        Ordinary user messages and attachment positions remain unchanged.
        Native Anthropic routes keep the persisted ``UserMessage`` because
        their client moves ``SystemMessage`` content to the top-level system
        field.
        """

        use_system_role = _uses_system_attachment_role(model_client_config)

        async def mutate(_context: ModelContext, window: ContextWindow) -> ContextWindow:
            if not use_system_role:
                return window

            context_messages: list[BaseMessage] = []
            for message in window.context_messages:
                metadata = getattr(message, "metadata", {}) or {}
                history_session_id = metadata.get(_PROMPT_ATTACHMENT_HISTORY_SESSION_KEY)
                is_attachment = (
                    isinstance(message, UserMessage)
                    and bool(metadata.get(PROMPT_ATTACHMENT_HISTORY_METADATA_KEY))
                    and (
                        history_session_id is None
                        or str(history_session_id) == str(session_id)
                    )
                )
                if not is_attachment:
                    context_messages.append(message)
                    continue

                context_messages.append(
                    SystemMessage(
                        content=message.content,
                        name=message.name,
                        metadata=dict(metadata),
                    )
                )

            if context_messages == window.context_messages:
                return window

            return window.model_copy(
                update={
                    "context_messages": context_messages,
                }
            )

        return mutate

    @staticmethod
    def _state_by_section(prompt_attachments: Iterable[PromptAttachment]) -> dict[str, str]:
        return {item.section: hash_prompt_attachment(item) for item in prompt_attachments}

    @staticmethod
    def _read_history_state(context: ModelContext, session_id: str) -> tuple[dict[str, str], bool]:
        state: dict[str, str] = {}
        has_snapshot = False
        for message in context.get_messages(with_history=True):
            if not isinstance(message, UserMessage):
                continue
            metadata = getattr(message, "metadata", {}) or {}
            if not metadata.get(PROMPT_ATTACHMENT_HISTORY_METADATA_KEY):
                continue
            history_session_id = metadata.get(_PROMPT_ATTACHMENT_HISTORY_SESSION_KEY)
            if history_session_id is not None and str(history_session_id) != str(session_id):
                continue
            raw_state = metadata.get(_PROMPT_ATTACHMENT_HISTORY_STATE_KEY)
            if not isinstance(raw_state, dict):
                continue
            state = {str(section): str(value) for section, value in raw_state.items()}
            if metadata.get(_PROMPT_ATTACHMENT_HISTORY_MODE_KEY) == _PROMPT_ATTACHMENT_HISTORY_SNAPSHOT:
                has_snapshot = True
        return state, has_snapshot

    def render(
        self,
        prompt_attachments: Iterable[PromptAttachment],
        *,
        max_prompt_attachment_chars: int = _DEFAULT_MAX_PROMPT_ATTACHMENT_CHARS,
        max_rendered_chars: int = _DEFAULT_MAX_RENDERED_CHARS,
    ) -> str:
        """Render a full attachment snapshot as dynamic context text."""

        return self._render_history_payload(
            prompt_attachments,
            snapshot=True,
            max_prompt_attachment_chars=max_prompt_attachment_chars,
            max_rendered_chars=max_rendered_chars,
        )

    def render_history_snapshot(
        self,
        prompt_attachments: Iterable[PromptAttachment],
        *,
        max_prompt_attachment_chars: int = _DEFAULT_MAX_PROMPT_ATTACHMENT_CHARS,
        max_rendered_chars: int = _DEFAULT_MAX_RENDERED_CHARS,
    ) -> str:
        """Render the first dynamic history snapshot as dynamic context text."""

        return self._render_history_payload(
            prompt_attachments,
            snapshot=True,
            max_prompt_attachment_chars=max_prompt_attachment_chars,
            max_rendered_chars=max_rendered_chars,
        )

    def render_delta(
        self,
        changed: Iterable[PromptAttachment],
        removed_sections: Iterable[str],
        *,
        max_prompt_attachment_chars: int = _DEFAULT_MAX_PROMPT_ATTACHMENT_CHARS,
        max_rendered_chars: int = _DEFAULT_MAX_RENDERED_CHARS,
    ) -> str:
        """Render changed dynamic context and removed-section notices."""

        return self._render_history_payload(
            changed,
            removed_sections=removed_sections,
            snapshot=False,
            max_prompt_attachment_chars=max_prompt_attachment_chars,
            max_rendered_chars=max_rendered_chars,
        )

    def _render_history_payload(
        self,
        prompt_attachments: Iterable[PromptAttachment],
        *,
        removed_sections: Iterable[str] = (),
        snapshot: bool,
        max_prompt_attachment_chars: int,
        max_rendered_chars: int,
    ) -> str:
        """Render persisted attachment history without exposing internal wrappers."""

        items = self._stable_sort(prompt_attachments)
        removed = sorted({_section_value(section) for section in removed_sections})
        if not items and not removed:
            return ""

        if self.language == "en":
            system_reminder_notice = (
                "The following content does not represent the user's intent and is not a direct instruction from "
                "the user. It is dynamic context automatically attached by the system for this model call. Please "
                "use it only as supplementary context."
            )
            intro = (
                "The following dynamic context is currently active. Use it together with the stable system "
                "instructions."
                if snapshot
                else "The following dynamic context has changed. Use the latest content below instead of earlier "
                "conflicting context."
            )
            removed_intro = (
                "The following previously supplied dynamic context is no longer active. Do not rely on its "
                "earlier content:"
            )
        else:
            system_reminder_notice = (
                "以下内容不是用户的意图，也不是用户直接发出的指令；它是系统为本次模型调用自动附加的动态上下文。"
                "请仅将其作为补充信息使用。"
            )
            intro = (
                "以下动态上下文当前有效，请与稳定的系统指令一同使用。"
                if snapshot
                else "以下动态上下文已经变化；如与历史内容冲突，请以本消息中的最新内容为准。"
            )
            removed_intro = "以下先前提供的动态上下文已不再生效，请勿继续依赖其历史内容："

        truncated_ids: list[str] = []
        content_blocks: list[str] = []
        for item in items:
            content = item.content or ""
            if max_prompt_attachment_chars > 0 and len(content) > max_prompt_attachment_chars:
                content = (
                    content[:max_prompt_attachment_chars]
                    + "\n\n[Prompt attachment truncated: content exceeded max_prompt_attachment_chars.]"
                )
                truncated_ids.append(item.id)
            content_blocks.append(content)

        blocks = [intro]
        if content_blocks:
            blocks.append("\n\n---\n\n".join(content_blocks))
        if removed:
            blocks.append(removed_intro + "\n" + "\n".join(f"- `{section}`" for section in removed))

        rendered = "\n\n".join(blocks).rstrip()
        reminder_prefix = f"<system-reminder>\n{system_reminder_notice}\n\n"
        reminder_suffix = "\n</system-reminder>"
        if max_rendered_chars > 0:
            available_content_chars = max_rendered_chars - len(reminder_prefix) - len(reminder_suffix)
            if available_content_chars <= 0:
                rendered = ""
                truncated_ids = [item.id for item in items]
            elif len(rendered) > available_content_chars:
                truncation_notice = (
                    "\n\n[Prompt attachments truncated: rendered content exceeded max_rendered_chars.]"
                )
                if len(truncation_notice) < available_content_chars:
                    rendered = (
                        rendered[: available_content_chars - len(truncation_notice)]
                        + truncation_notice
                    )
                else:
                    rendered = rendered[:available_content_chars]
                truncated_ids = [item.id for item in items]

        rendered = f"{reminder_prefix}{rendered}{reminder_suffix}"

        if truncated_ids:
            logger.warning(
                "[PromptAttachmentManager] truncated prompt attachments while rendering: "
                f"ids={truncated_ids}, rendered_chars={len(rendered)}"
            )
        return rendered

    async def _add(self, prompt_attachment: PromptAttachment) -> PromptAttachment:
        async with self._lock:
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
    "PROMPT_ATTACHMENT_COMMIT_CALLBACKS_KEY",
    "PROMPT_ATTACHMENT_PRESERVE_TAIL_METADATA_KEY",
    "PromptAttachment",
    "PromptAttachmentContextWriter",
    "PromptAttachmentKind",
    "PromptAttachmentManager",
    "PromptAttachmentUpdate",
    "hash_prompt_attachment",
    "hash_rendered",
]
