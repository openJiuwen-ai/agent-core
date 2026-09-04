# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Ephemeral current-browser-state injection for browser-agent model calls."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, Field

from openjiuwen.core.context_engine import ContextEngine, ContextWindow, ModelContext
from openjiuwen.core.context_engine.processor.base import ContextEvent, ContextProcessor
from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage, ToolMessage, UserMessage
from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PROMPT_ATTACHMENT_PRESERVE_TAIL_METADATA_KEY,
)

from .browser_logging import browser_agent_log_warning
from .browser_working_context import BrowserWorkingContextStore

_BROWSER_STATE_MESSAGE_NAME = "current_browser_state"
_BROWSER_STATE_METADATA_KEY = "browser_state_context"
_BROWSER_STATE_MESSAGE_ID = "openjiuwen:browser-state"
_BROWSER_PROGRESS_MESSAGE_NAME = "browser_state_progress"
_BROWSER_PROGRESS_METADATA_KEY = "browser_state_progress_context"
_PAGE_CHANGE_INITIAL = "initial"
_PAGE_CHANGE_CHANGED = "changed"
_PAGE_CHANGE_UNCHANGED = "unchanged"
_PAGE_CHANGE_UNKNOWN = "unknown"
_BROWSER_STATE_REFRESH_TOOL_NAMES = frozenset(
    {
        # Runtime interaction helpers.
        "browser_batch_interact",
        "browser_click",
        "browser_close",
        "browser_custom_action",
        "browser_drag",
        "browser_drop",
        "browser_evaluate",
        "browser_file_upload",
        "browser_fill_form",
        "browser_handle_dialog",
        "browser_hover",
        "browser_navigate",
        "browser_navigate_back",
        "browser_press_key",
        "browser_run_code",
        "browser_run_code_unsafe",
        "browser_select_option",
        "browser_tabs",
        "browser_type",
        "browser_wait_for",
        # Coordinate-based interactions can click, drag, hover, or scroll.
        "browser_mouse_click_xy",
        "browser_mouse_down",
        "browser_mouse_drag_xy",
        "browser_mouse_move_xy",
        "browser_mouse_up",
        "browser_mouse_wheel",
        # Devtools actions that can alter rendering or resume page activity.
        "browser_annotate",
        "browser_hide_highlight",
        "browser_highlight",
        "browser_resize",
        "browser_resume",
        "browser_video_hide_actions",
        "browser_video_show_actions",
        # Network and storage mutations can affect the current application.
        "browser_network_state_set",
        "browser_route",
        "browser_unroute",
        "browser_cookie_clear",
        "browser_cookie_delete",
        "browser_cookie_set",
        "browser_localstorage_clear",
        "browser_localstorage_delete",
        "browser_localstorage_set",
        "browser_sessionstorage_clear",
        "browser_sessionstorage_delete",
        "browser_sessionstorage_set",
        "browser_set_storage_state",
    }
)
_BROWSER_STATE_OBSERVATION_TOOL_NAMES = frozenset(
    {
        "browser_find",
        "browser_probe_cards",
        "browser_probe_interactives",
        "browser_snapshot",
    }
)


class BrowserStateContextProcessorConfig(BaseModel):
    """Configuration for capturing browser state initially and after mutations."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: Any = Field(exclude=True, repr=False)
    max_dom_chars: int = Field(default=12_000, ge=2_000)


@ContextEngine.register_processor()
class BrowserStateContextProcessor(ContextProcessor):
    """Inject cached browser state, refreshing it after browser mutations."""

    def __init__(self, config: BrowserStateContextProcessorConfig):
        super().__init__(config)
        self._cached_state: Dict[str, Any] | None = None
        self._cached_state_message: UserMessage | None = None
        self._seen_refresh_tool_call_ids: set[str] = set()
        self._seen_action_group_ids: set[str] = set()
        self._last_successful_state_digest: str | None = None
        self._page_change = _PAGE_CHANGE_INITIAL
        self._consecutive_no_progress = 0

    @property
    def config(self) -> BrowserStateContextProcessorConfig:
        return self._config

    async def trigger_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs: Any,
    ) -> bool:
        del context, context_window, kwargs
        # The hook must run for every model call because this processor's state
        # message is ephemeral. The expensive browser capture inside the hook is
        # gated on the initial call and completed browser-mutating tool calls.
        return True

    async def on_get_context_window(
        self,
        context: ModelContext,
        context_window: ContextWindow,
        **kwargs: Any,
    ) -> tuple[ContextEvent | None, ContextWindow]:
        del kwargs
        source_messages = context.get_messages() if context is not None else context_window.context_messages
        action_group_id, refresh_tool_call_ids, observation_only = self._completed_state_action_group(source_messages)
        reconciliation_only = self._requires_reconciliation(source_messages, refresh_tool_call_ids)
        should_refresh = self._cached_state is None or bool(
            action_group_id and action_group_id not in self._seen_action_group_ids
        )
        if should_refresh:
            if reconciliation_only and action_group_id:
                captured_state = await self._capture_reconciliation_state(action_group_id=action_group_id)
            elif observation_only and action_group_id:
                captured_state = await self._capture_compact_state(action_group_id=action_group_id)
            else:
                captured_state = await self._capture_state(action_group_id=action_group_id or "initial")
            self._page_change = self._classify_page_change(captured_state)
            semantic_progress = captured_state.get("semantic_progress")
            if isinstance(semantic_progress, dict):
                self._consecutive_no_progress = int(semantic_progress.get("consecutive_no_progress") or 0)
                session = context.get_session_ref() if context is not None else None
                recovered = BrowserWorkingContextStore.sync_semantic_progress(session, semantic_progress)
                if recovered:
                    acknowledge_replan = getattr(self.config.provider, "acknowledge_semantic_replan", None)
                    if callable(acknowledge_replan):
                        acknowledge_replan()
            elif self._page_change == _PAGE_CHANGE_UNCHANGED:
                self._consecutive_no_progress += 1
            else:
                self._consecutive_no_progress = 0
            self._cached_state = captured_state
            self._cached_state_message = self._build_state_message(captured_state)
        self._seen_refresh_tool_call_ids.update(refresh_tool_call_ids)
        if action_group_id:
            self._seen_action_group_ids.add(action_group_id)

        context_window.context_messages = [
            message
            for message in context_window.context_messages
            if not self._is_browser_state_message(message) and not self._is_browser_progress_message(message)
        ]
        if self._cached_state_message is None:
            self._cached_state_message = self._build_state_message(self._cached_state or {})
        context_window.context_messages.append(self._cached_state_message)
        return None, context_window

    @classmethod
    def _completed_refresh_action_group(
        cls,
        messages: list[BaseMessage],
    ) -> tuple[str, set[str]]:
        """Return the latest completed model action group that mutates browser state."""

        action_group_id, refresh_ids, observation_only = cls._completed_state_action_group(messages)
        if observation_only:
            return "", set()
        mutation_ids: set[str] = set()
        for message in messages:
            if not isinstance(message, AssistantMessage):
                continue
            for tool_call in message.tool_calls or []:
                call_id = str(tool_call.id or "")
                if not call_id or call_id not in refresh_ids:
                    continue
                if cls._is_refresh_tool_name(tool_call.name):
                    mutation_ids.add(call_id)
        return action_group_id, mutation_ids

    @classmethod
    def _completed_state_action_group(
        cls,
        messages: list[BaseMessage],
    ) -> tuple[str, set[str], bool]:
        """Return one completed browser group, merging concurrent read-only observations."""

        completed_call_ids, tool_messages, executed_call_ids = cls._completed_tool_message_index(messages)
        refresh_tool_call_ids: set[str] = set()
        latest_group_id = ""
        latest_observation_only = False
        for message in messages:
            if not isinstance(message, AssistantMessage):
                continue
            group = cls._classify_completed_action_group(
                list(message.tool_calls or []),
                completed_call_ids=completed_call_ids,
                executed_call_ids=executed_call_ids,
                tool_messages=tool_messages,
            )
            if group is None:
                continue
            call_ids, group_refresh_ids, group_observation_ids = group
            group_browser_ids = group_refresh_ids | group_observation_ids
            if not group_browser_ids:
                continue
            refresh_tool_call_ids.update(group_browser_ids)
            latest_group_id = hashlib.sha256("\x1f".join(call_ids).encode("utf-8")).hexdigest()[:16]
            latest_observation_only = not group_refresh_ids and bool(group_observation_ids)
        return latest_group_id, refresh_tool_call_ids, latest_observation_only

    @classmethod
    def _requires_reconciliation(
        cls,
        messages: list[BaseMessage],
        refresh_tool_call_ids: set[str],
    ) -> bool:
        for message in messages:
            if not isinstance(message, ToolMessage):
                continue
            if str(message.tool_call_id or "") not in refresh_tool_call_ids:
                continue
            if cls._tool_message_changed_state(message) and not cls._tool_message_succeeded(message):
                return True
        return False

    @classmethod
    def _classify_completed_action_group(
        cls,
        tool_calls: list[Any],
        *,
        completed_call_ids: set[str],
        executed_call_ids: set[str],
        tool_messages: dict[str, ToolMessage],
    ) -> tuple[list[str], set[str], set[str]] | None:
        call_ids = [str(tool_call.id) for tool_call in tool_calls if tool_call.id]
        if not call_ids or not all(call_id in completed_call_ids for call_id in call_ids):
            return None
        mutation_ids: set[str] = set()
        observation_ids: set[str] = set()
        for tool_call in tool_calls:
            call_id = str(tool_call.id or "")
            if not call_id or call_id not in executed_call_ids:
                continue
            tool_message = tool_messages.get(call_id)
            if cls._is_refresh_tool_name(tool_call.name) and cls._tool_message_changed_state(tool_message):
                mutation_ids.add(call_id)
            if cls._is_observation_tool_name(tool_call.name) and cls._tool_message_succeeded(tool_message):
                observation_ids.add(call_id)
        return call_ids, mutation_ids, observation_ids

    @classmethod
    def _completed_tool_message_index(
        cls,
        messages: list[BaseMessage],
    ) -> tuple[set[str], dict[str, ToolMessage], set[str]]:
        tool_messages = {
            str(message.tool_call_id): message
            for message in messages
            if isinstance(message, ToolMessage) and message.tool_call_id
        }
        completed_call_ids = set(tool_messages)
        executed_call_ids = {
            call_id
            for call_id, message in tool_messages.items()
            if cls._tool_message_was_executed(message)
        }
        return completed_call_ids, tool_messages, executed_call_ids

    @staticmethod
    def _tool_message_was_executed(message: ToolMessage) -> bool:
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        if metadata.get("denied") is True or metadata.get("executed") is False:
            return False
        content = message.content
        if isinstance(content, str):
            try:
                payload = json.loads(content)
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict) and (payload.get("denied") is True or payload.get("executed") is False):
                return False
        return True

    @staticmethod
    def _tool_message_changed_state(message: ToolMessage | None) -> bool:
        if message is None:
            return False
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        if metadata.get("state_changed") is False:
            return False
        content = message.content
        if isinstance(content, str):
            try:
                payload = json.loads(content)
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict) and payload.get("state_changed") is False:
                return False
        return True

    @staticmethod
    def _tool_message_succeeded(message: ToolMessage | None) -> bool:
        if message is None:
            return False
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        if metadata.get("success") is False:
            return False
        content = message.content
        if isinstance(content, str):
            try:
                payload = json.loads(content)
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict) and (
                payload.get("ok") is False or payload.get("success") is False
            ):
                return False
        return True

    @classmethod
    def _completed_refresh_tool_call_ids(cls, messages: list[BaseMessage]) -> set[str]:
        """Compatibility projection for callers that only need completed IDs."""
        _, refresh_tool_call_ids = cls._completed_refresh_action_group(messages)
        return refresh_tool_call_ids

    @staticmethod
    def _is_refresh_tool_name(tool_name: str) -> bool:
        return any(
            tool_name == expected or tool_name.endswith(f".{expected}") or tool_name.endswith(f"_{expected}")
            for expected in _BROWSER_STATE_REFRESH_TOOL_NAMES
        )

    @staticmethod
    def _is_observation_tool_name(tool_name: str) -> bool:
        return any(
            tool_name == expected or tool_name.endswith(f".{expected}") or tool_name.endswith(f"_{expected}")
            for expected in _BROWSER_STATE_OBSERVATION_TOOL_NAMES
        )

    async def _capture_state(self, *, action_group_id: str) -> Dict[str, Any]:
        try:
            capture = self.config.provider.capture_browser_state
            if action_group_id == "initial":
                state = await capture()
            else:
                try:
                    state = await capture(action_group_id=action_group_id)
                except TypeError as exc:
                    if "action_group_id" not in str(exc):
                        raise
                    state = await capture()
        except Exception as exc:
            browser_agent_log_warning(
                "[BrowserStateContextProcessor] browser state capture failed: %s",
                exc,
            )
            return {
                "ok": False,
                "error": f"browser state capture failed: {exc}",
                "url": "",
                "title": "",
                "tabs": [],
                "page_position": {},
                "dom": "",
            }

        if not isinstance(state, dict):
            return {
                "ok": False,
                "error": "browser state capture returned a non-object result",
                "url": "",
                "title": "",
                "tabs": [],
                "page_position": {},
                "dom": "",
            }
        return state

    async def _capture_compact_state(self, *, action_group_id: str) -> Dict[str, Any]:
        capture = getattr(self.config.provider, "capture_compact_browser_state", None)
        if not callable(capture):
            return await self._capture_state(action_group_id=action_group_id)
        try:
            state = await capture(action_group_id=action_group_id)
        except Exception as exc:
            browser_agent_log_warning(
                "[BrowserStateContextProcessor] compact browser state merge failed: %s",
                exc,
            )
            return await self._capture_state(action_group_id=action_group_id)
        if not isinstance(state, dict):
            return await self._capture_state(action_group_id=action_group_id)
        if self._cached_state:
            if not state.get("tabs"):
                state["tabs"] = self._cached_state.get("tabs") or []
            if not state.get("page_position"):
                state["page_position"] = self._cached_state.get("page_position") or {}
        return state

    async def _capture_reconciliation_state(self, *, action_group_id: str) -> Dict[str, Any]:
        capture = getattr(self.config.provider, "capture_reconciliation_browser_state", None)
        if not callable(capture):
            return await self._capture_state(action_group_id=action_group_id)
        try:
            state = await capture(action_group_id=action_group_id)
        except Exception as exc:
            browser_agent_log_warning(
                "[BrowserStateContextProcessor] browser state reconciliation failed: %s",
                exc,
            )
            return await self._capture_state(action_group_id=action_group_id)
        if not isinstance(state, dict):
            return await self._capture_state(action_group_id=action_group_id)
        return state

    def _classify_page_change(self, state: Dict[str, Any]) -> str:
        """Compare a successful capture with the previous successful capture."""
        if not bool(state.get("ok")):
            return _PAGE_CHANGE_UNKNOWN

        state_digest = self._state_digest(state)
        if self._last_successful_state_digest is None:
            page_change = _PAGE_CHANGE_INITIAL
        elif state_digest == self._last_successful_state_digest:
            page_change = _PAGE_CHANGE_UNCHANGED
        else:
            page_change = _PAGE_CHANGE_CHANGED
        self._last_successful_state_digest = state_digest
        return page_change

    @staticmethod
    def _state_digest(state: Dict[str, Any]) -> str:
        comparable_state = {
            "url": state.get("url") or "",
            "title": state.get("title") or "",
            "tabs": state.get("tabs") or [],
            "page_position": state.get("page_position") or {},
            "page_state": state.get("page_state") or {},
            "semantic_state": state.get("semantic_state") or {},
        }
        serialized_state = json.dumps(
            comparable_state,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(serialized_state.encode("utf-8")).hexdigest()

    def _build_state_message(self, state: Dict[str, Any]) -> UserMessage:
        text = self._format_state_text(state)
        return UserMessage(
            name=_BROWSER_STATE_MESSAGE_NAME,
            metadata={
                _BROWSER_STATE_METADATA_KEY: True,
                "context_message_id": _BROWSER_STATE_MESSAGE_ID,
                PROMPT_ATTACHMENT_PRESERVE_TAIL_METADATA_KEY: True,
            },
            content=text,
        )

    def _format_state_text(self, state: Dict[str, Any]) -> str:
        page_state = state.get("page_state")
        if not isinstance(page_state, dict):
            page_state = {}

        state_header = self._fit_state_header({
            "ok": bool(state.get("ok")),
            "error": state.get("error"),
            "url": state.get("url") or "",
            "title": state.get("title") or "",
            "tabs": state.get("tabs") or [],
            "page_position": state.get("page_position") or {},
            "semantic_state": state.get("semantic_state") or {},
            "dom_error": state.get("dom_error"),
            "page_state": page_state,
        })
        return (
            "<browser_state>\n"
            "This observation was captured initially or after the latest detected browser mutation and "
            "replaces any previous browser state. It is reused until another state-invalidating browser "
            "tool completes; element references may become stale if the page changes independently. "
            "Change status is provided separately after this compact observation. Raw AX/Card data is "
            "available only in the browser audit trace.\n"
            f"{json.dumps(state_header, ensure_ascii=False, separators=(',', ':'))}\n"
            "</browser_state>"
        )

    def _fit_state_header(self, value: Dict[str, Any]) -> Dict[str, Any]:
        """Bound browser state structurally so the rendered JSON stays valid."""

        payload = json.loads(json.dumps(value, ensure_ascii=False, default=str))
        page_state = payload.get("page_state")
        semantic_state = payload.get("semantic_state")
        if isinstance(semantic_state, dict):
            semantic_state.pop("field_coverage", None)
            semantic_state.pop("blockers", None)
        if isinstance(page_state, dict) and page_state:
            page_state.pop("field_coverage", None)
            page_blockers = page_state.pop("blockers", None)
            if page_blockers:
                page_state["page_blockers"] = page_blockers
            page_state["interactives"] = list(page_state.get("interactives") or [])[:20]
            page_state["cards"] = list(page_state.get("cards") or [])[:8]
        payload["tabs"] = list(payload.get("tabs") or [])[:6]
        if self._serialized_size(payload) <= self.config.max_dom_chars:
            return payload

        if isinstance(page_state, dict):
            collections = (
                page_state.get("interactives"),
                page_state.get("cards"),
            )
            while self._serialized_size(payload) > self.config.max_dom_chars:
                largest = max(
                    (items for items in collections if isinstance(items, list) and items),
                    key=lambda items: len(json.dumps(items, ensure_ascii=False, default=str)),
                    default=None,
                )
                if largest is None:
                    break
                largest.pop()

        if self._serialized_size(payload) <= self.config.max_dom_chars:
            payload["truncated"] = True
            return payload

        semantic_state = payload.get("semantic_state")
        if isinstance(semantic_state, dict):
            compact_semantic = {}
            for key in (
                "url",
                "form_values",
                "selected_filters",
                "result_count",
                "first_result_text",
            ):
                value = semantic_state.get(key)
                if value not in (None, "", [], {}):
                    compact_semantic[key] = value
            for key in ("form_values", "selected_filters"):
                values = compact_semantic.get(key)
                if isinstance(values, dict):
                    compact_semantic[key] = {
                        str(item_key)[:80]: str(item_value)[:160]
                        for item_key, item_value in list(values.items())[:8]
                    }
                elif isinstance(values, list):
                    compact_semantic[key] = [str(item)[:160] for item in values[:8]]
            if "first_result_text" in compact_semantic:
                compact_semantic["first_result_text"] = str(compact_semantic["first_result_text"])[:300]
            payload["semantic_state"] = compact_semantic
        payload["tabs"] = payload["tabs"][:3]
        payload["truncated"] = True
        if self._serialized_size(payload) <= self.config.max_dom_chars:
            return payload

        compact_page = {
            key: page_state.get(key)
            for key in ("page_id", "generation_id")
            if isinstance(page_state, dict) and page_state.get(key) not in (None, "", [], {})
        }
        if isinstance(page_state, dict):
            for key, limit in (("url", 600), ("title", 200)):
                if page_state.get(key) not in (None, ""):
                    compact_page[key] = str(page_state[key])[:limit]
        fallback = {
            "ok": payload.get("ok"),
            "error": payload.get("error"),
            "url": str(payload.get("url") or "")[:1_000],
            "title": str(payload.get("title") or "")[:300],
            "tabs": payload.get("tabs") or [],
            "semantic_state": payload.get("semantic_state") or {},
            "dom_error": payload.get("dom_error"),
            "page_state": compact_page,
            "truncated": True,
        }
        if self._serialized_size(fallback) <= self.config.max_dom_chars:
            return fallback
        final_semantic: Dict[str, Any] = {}
        fallback_semantic = fallback.get("semantic_state")
        if isinstance(fallback_semantic, dict):
            if fallback_semantic.get("url"):
                final_semantic["url"] = str(fallback_semantic["url"])[:400]
            if fallback_semantic.get("result_count") not in (None, ""):
                final_semantic["result_count"] = fallback_semantic["result_count"]
        return {
            "ok": payload.get("ok"),
            "error": str(payload.get("error") or "")[:300] or None,
            "url": str(payload.get("url") or "")[:600],
            "title": str(payload.get("title") or "")[:200],
            "semantic_state": final_semantic,
            "page_state": compact_page,
            "truncated": True,
        }

    @staticmethod
    def _serialized_size(value: Dict[str, Any]) -> int:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))

    @staticmethod
    def _is_browser_state_message(message: BaseMessage) -> bool:
        return message.name == _BROWSER_STATE_MESSAGE_NAME or bool(message.metadata.get(_BROWSER_STATE_METADATA_KEY))

    @staticmethod
    def _is_browser_progress_message(message: BaseMessage) -> bool:
        return message.name == _BROWSER_PROGRESS_MESSAGE_NAME or bool(
            message.metadata.get(_BROWSER_PROGRESS_METADATA_KEY)
        )

    def load_state(self, state: Dict[str, Any]) -> None:
        del state
        self._cached_state = None
        self._cached_state_message = None
        self._seen_refresh_tool_call_ids = set()
        self._seen_action_group_ids = set()
        self._last_successful_state_digest = None
        self._page_change = _PAGE_CHANGE_INITIAL
        self._consecutive_no_progress = 0

    def save_state(self) -> Dict[str, Any]:
        return {}


__all__ = [
    "BrowserStateContextProcessor",
    "BrowserStateContextProcessorConfig",
]
