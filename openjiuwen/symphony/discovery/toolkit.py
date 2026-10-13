"""Result and session-delta helpers for structured Skill discovery."""

from __future__ import annotations

import hashlib
import textwrap
import threading
from collections import OrderedDict
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator


SKILL_INDEX_TOOL_NAME = "skill_index"
_MAX_DESCRIPTION_CHARS = 300
_MAX_SESSION_STATES = 1_024
_TOOL_ID_DOMAIN = b"openjiuwen.skill-index.v1\0"


class SkillDCICommandResult(str):
    """Concise model output with structured runtime diagnostics."""

    detailed_output: dict[str, Any]

    def __new__(
        cls,
        content: str,
        *,
        detailed_output: dict[str, Any],
        model_content: str | None = None,
    ) -> "SkillDCICommandResult":
        instance = super().__new__(cls, content)
        instance.detailed_output = detailed_output
        if model_content is not None:
            instance.data = {"content": model_content}
        return instance

    def __getnewargs_ex__(self) -> tuple[tuple[str], dict[str, Any]]:
        data = getattr(self, "data", None)
        model_content = data.get("content") if isinstance(data, Mapping) else None
        return (str(self),), {"detailed_output": self.detailed_output, "model_content": model_content}


@dataclass
class _PreparedNotice:
    added: list[tuple[str, str]]
    acknowledged: bool = False

    def acknowledge(self) -> None:
        self.acknowledged = True


class _NoticeState:
    def __init__(self, snapshot: dict[str, tuple[str, str]] | None) -> None:
        self._snapshot = snapshot
        self._lock = threading.Lock()

    @contextmanager
    def prepare(self, current: dict[str, tuple[str, str]] | None) -> Iterator[_PreparedNotice]:
        with self._lock:
            previous = self._snapshot
            added_ids = set() if previous is None or current is None else current.keys() - previous.keys()
            prepared = _PreparedNotice([] if current is None else [current[key] for key in added_ids])
            try:
                yield prepared
            finally:
                if current is not None:
                    self._snapshot = (
                        current
                        if prepared.acknowledged or not added_ids
                        else {key: value for key, value in current.items() if key not in added_ids}
                    )


_STATES_LOCK = threading.Lock()
_STATES: OrderedDict[str, _NoticeState] = OrderedDict()


def initialize_incremental_skill_notice_state(
    session_scope: str,
    raw_cards: Mapping[str, Mapping[str, str]],
    *,
    discovery_tool_name: str = SKILL_INDEX_TOOL_NAME,
) -> None:
    """Initialize a session baseline without consuming a delta."""

    _notice_state(_tool_id(session_scope, discovery_tool_name), _normalize_cards(raw_cards))


def consume_incremental_skill_reminder(
    session_scope: str,
    raw_cards: Mapping[str, Mapping[str, str]],
    *,
    max_chars: int = 4_000,
    discovery_tool_name: str = SKILL_INDEX_TOOL_NAME,
) -> str | None:
    """Consume a one-shot notice for Skills added since the session baseline."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    current = _normalize_cards(raw_cards)
    state = _notice_state(_tool_id(session_scope, discovery_tool_name), current)
    with state.prepare(current) as prepared:
        reminder = _render_reminder(prepared.added, max_chars=max_chars, tool_name=discovery_tool_name)
        prepared.acknowledge()
        return reminder


def clear_incremental_skill_notice_states() -> None:
    """Clear process-local notice cursors; intended for tests."""

    with _STATES_LOCK:
        _STATES.clear()


class IncrementalNoticeSession:
    """Append one pending notice only after it fits the delivered result."""

    def __init__(self, session_scope: str, raw_cards: Mapping[str, Mapping[str, str]], *, max_chars: int) -> None:
        if max_chars <= 0:
            raise ValueError("incremental_notice_max_chars must be positive")
        self._max_chars = max_chars
        self._state = _notice_state(_tool_id(session_scope, SKILL_INDEX_TOOL_NAME), _normalize_cards(raw_cards))

    def append(
        self,
        output: str,
        raw_cards: Mapping[str, Mapping[str, str]],
        *,
        output_budget: int | None,
    ) -> tuple[str, str | None]:
        current = _normalize_cards(raw_cards)
        with self._state.prepare(current) as prepared:
            reminder = _render_reminder(prepared.added, max_chars=self._max_chars, tool_name=SKILL_INDEX_TOOL_NAME)
            if not reminder:
                return output, None
            combined = f"{output}\n\n{reminder}" if output else reminder
            if output_budget is not None and len(combined) > output_budget:
                return output, None
            prepared.acknowledge()
            return combined, reminder


def _tool_id(session_scope: str, tool_name: str) -> str:
    scope = str(session_scope or "").strip()
    name = str(tool_name or "").strip()
    if not scope or not name:
        raise ValueError("session_scope and discovery_tool_name must be non-empty")
    digest = hashlib.sha256(_TOOL_ID_DOMAIN + name.encode() + b"\0" + scope.encode()).hexdigest()
    return f"{name}__{digest}"


def _notice_state(tool_id: str, snapshot: dict[str, tuple[str, str]] | None) -> _NoticeState:
    with _STATES_LOCK:
        state = _STATES.get(tool_id)
        if state is None:
            state = _NoticeState(snapshot)
            _STATES[tool_id] = state
        else:
            _STATES.move_to_end(tool_id)
        while len(_STATES) > _MAX_SESSION_STATES:
            _STATES.popitem(last=False)
        return state


def _normalize_cards(raw_cards: Mapping[str, Mapping[str, str]]) -> dict[str, tuple[str, str]]:
    cards: dict[str, tuple[str, str]] = {}
    for raw_id, raw_card in raw_cards.items():
        if not isinstance(raw_card, Mapping):
            continue
        worker_id = str(raw_id or "").strip()
        if not worker_id:
            continue
        name = _safe_text(raw_card.get("name") or worker_id)
        description = textwrap.shorten(
            _safe_text(raw_card.get("description") or name),
            width=_MAX_DESCRIPTION_CHARS,
            placeholder="...",
        )
        cards[worker_id] = (name, description)
    return cards


def _safe_text(value: Any) -> str:
    return " ".join(str(value or "").split()).replace("<", "&lt;").replace(">", "&gt;")


def _render_reminder(added: list[tuple[str, str]], *, max_chars: int, tool_name: str) -> str | None:
    if not added:
        return None
    rows = "\n".join(
        f"- {name}: {description}" for name, description in sorted(added, key=lambda row: row[0].casefold())
    )
    full = (
        "<system-reminder>\n"
        "The following skills are newly available for use with the Skill tool:\n\n"
        f"{rows}\n"
        "</system-reminder>"
    )
    if len(full) <= max_chars:
        return full
    return (
        "<system-reminder>\n"
        f"{len(added)} new skills are available. Use `{tool_name}` to search the current catalog.\n"
        "</system-reminder>"
    )


__all__ = [
    "IncrementalNoticeSession",
    "SKILL_INDEX_TOOL_NAME",
    "SkillDCICommandResult",
    "clear_incremental_skill_notice_states",
    "consume_incremental_skill_reminder",
    "initialize_incremental_skill_notice_state",
]
