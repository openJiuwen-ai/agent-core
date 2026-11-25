"""Todo checkbox rendering for CLI output.

Renders todo items with visual checkboxes and progress summaries:

- ``☑ task``  — completed (green)
- ``◐ task``  — in_progress (yellow)
- ``☐ task``  — pending (dim)
- ``✓2 ◐1 ☐3`` — progress summary
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any, Dict, List, Optional

from openjiuwen.harness.schema.task import PROGRESS_RANK, STATUS_ICONS

# Status → (icon, Rich style)
_STATUS_STYLE = {
    "completed": ("☑", "green"),
    "in_progress": ("◐", "yellow"),
    "pending": ("☐", "dim"),
    "cancelled": ("☒", "dim strike"),
}

# SDK STATUS_ICONS → our status name. Derived from the SDK so an icon
# change there cannot silently break parsing here.
_SDK_ICON_TO_STATUS = {
    icon: status.value for status, icon in STATUS_ICONS.items()
}
# Characters inside the brackets, for the parsing regexes below.
_ICON_CHAR_CLASS = "".join(
    re.escape(icon[1:-1]) for icon in _SDK_ICON_TO_STATUS
)
# Status name → saved-order rank, keyed by plain strings for cached dict items.
_PROGRESS_RANK_BY_NAME = {
    status.value: rank for status, rank in PROGRESS_RANK.items()
}


def render_todo_item(
    content: str,
    status: str,
) -> str:
    """Render a single todo item with a checkbox.

    Args:
        content: Task description text.
        status: One of ``"completed"``, ``"in_progress"``,
            ``"pending"``, ``"cancelled"``.

    Returns:
        Rich-formatted string like ``[green]☑ task[/green]``.
    """
    icon, style = _STATUS_STYLE.get(
        status, ("☐", "dim")
    )
    return f"[{style}]{icon} {content}[/{style}]"


def render_todo_list(
    items: List[Dict[str, Any]],
) -> List[str]:
    """Render a list of todo items as checkbox lines.

    Each line is prefixed with ``⎿`` for Claude Code style.

    Args:
        items: List of dicts with ``"content"`` and ``"status"``
            keys (matching TodoItem fields).

    Returns:
        List of Rich-formatted strings.
    """
    lines = []
    for item in items:
        content = item.get(
            "content", item.get("activeForm", "")
        )
        status = item.get("status", "pending")
        checkbox = render_todo_item(content, status)
        lines.append(f"  ⎿  {checkbox}")
    return lines


def render_todo_summary(
    items: List[Dict[str, Any]],
) -> str:
    """Render a compact progress summary.

    Args:
        items: List of dicts with ``"status"`` keys.

    Returns:
        String like ``✓2 ◐1 ☐3``.
    """
    counts: Dict[str, int] = {
        "completed": 0,
        "in_progress": 0,
        "pending": 0,
        "cancelled": 0,
    }
    for item in items:
        status = item.get("status", "pending")
        if status in counts:
            counts[status] += 1

    parts = []
    if counts["completed"]:
        parts.append(f"✓{counts['completed']}")
    if counts["in_progress"]:
        parts.append(f"◐{counts['in_progress']}")
    if counts["pending"]:
        parts.append(f"☐{counts['pending']}")
    return " ".join(parts) if parts else "No tasks"


def _parse_todo_text(tool_result: str) -> Optional[List[Dict[str, Any]]]:
    """Parse todo items from ``todo_create``'s human-readable text.

    ``todo_create`` returns text like::

        Successfully created 3 task(s):
          [→] task_id: abc , content: Write tests
          [ ] task_id: def , content: Deploy
          [ ] task_id: ghi , content: Review

    Args:
        tool_result: Raw string from ``str(tool_result)``.

    Returns:
        List of item dicts, or ``None``.
    """
    items: List[Dict[str, Any]] = []

    # Normalize: unescape \\n → real newlines for parsing
    # (str() on a dict produces Python repr with literal \\n)
    normalized = tool_result.replace("\\n", "\n")

    #   [→] task_id: uuid , content: description
    create_pattern = re.compile(
        rf"\[([{_ICON_CHAR_CLASS}])\]\s+task_id:\s*\S+\s*,\s*content:\s*(.+)"
    )
    for match in create_pattern.finditer(normalized):
        icon_char = match.group(1)
        content = match.group(2).strip()
        icon_key = f"[{icon_char}]"
        status = _SDK_ICON_TO_STATUS.get(
            icon_key, "pending"
        )
        items.append(
            {"content": content, "status": status}
        )

    return items if items else None


def parse_todo_result(
    tool_result: str,
) -> Optional[List[Dict[str, Any]]]:
    """Parse a todo tool result into a list of items.

    Tries multiple strategies:

    1. Structured data: JSON, or the Python dict repr the CLI tool
       tracker produces with ``str()`` (e.g. ``todo_list``'s
       ``{'tasks': [...]}``)
    2. ``todo_create`` text format with ``STATUS_ICONS`` icons

    Args:
        tool_result: Raw string result from a todo tool.

    Returns:
        List of item dicts, or ``None``.
    """
    if not tool_result:
        return None

    # Strategy 1: Try JSON, then Python literal repr
    try:
        data = json.loads(tool_result)
    except (json.JSONDecodeError, TypeError):
        try:
            data = ast.literal_eval(tool_result)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            data = None

    if data is not None:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in (
                "items",
                "tasks",
                "todos",
                "result",
            ):
                if key in data and isinstance(
                    data[key], list
                ):
                    return data[key]
            if "content" in data or "status" in data:
                return [data]

    # Strategy 2: Parse SDK text format
    # Clean trailing dict repr artifacts (e.g. "'}") before parsing
    cleaned = tool_result.rstrip()
    for suffix in ("'}", '"}', "}", "'", '"'):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
    return _parse_todo_text(cleaned)


def parse_todo_tool_args(
    tool_args: Any,
) -> Dict[str, Any]:
    """Parse raw tool args into a dict."""
    if isinstance(tool_args, dict):
        return tool_args
    if isinstance(tool_args, str):
        try:
            data = json.loads(tool_args)
        except (json.JSONDecodeError, TypeError):
            return {}
        if isinstance(data, dict):
            return data
    return {}


def apply_todo_modify_args(
    items: List[Dict[str, Any]],
    tool_args: Any,
) -> Optional[List[Dict[str, Any]]]:
    """Apply a ``todo_modify`` call to rendered todo items.

    This lets the CLI refresh the visible todo checklist even when the
    tool result only contains a summary like ``Successfully updated``.
    The result follows the saved order (closed, in progress, pending).
    """
    updated = _replay_todo_modify(items, tool_args)
    if updated is None:
        return None
    return sorted(
        updated,
        key=lambda item: _PROGRESS_RANK_BY_NAME.get(item.get("status"), 2),
    )


def _replay_todo_modify(
    items: List[Dict[str, Any]],
    tool_args: Any,
) -> Optional[List[Dict[str, Any]]]:
    args = parse_todo_tool_args(tool_args)
    action = args.get("action")
    if not action:
        return None

    current_items = [
        dict(item) for item in items
    ]

    if action == "update":
        updates = args.get("todos")
        if not isinstance(updates, list):
            return None
        item_map = {
            item.get("id"): item
            for item in current_items
            if item.get("id")
        }
        if not item_map:
            return None
        for update in updates:
            if not isinstance(update, dict):
                continue
            todo_id = update.get("id")
            if todo_id not in item_map:
                continue
            item = item_map[todo_id]
            item.update(update)
            _sync_display_content(item)
        return current_items

    if action == "delete":
        ids = args.get("ids")
        if not isinstance(ids, list):
            return None
        delete_ids = set(ids)
        return [
            item
            for item in current_items
            if item.get("id") not in delete_ids
        ]

    if action == "cancel":
        ids = args.get("ids")
        if not isinstance(ids, list):
            return None
        cancel_ids = set(ids)
        for item in current_items:
            if item.get("id") in cancel_ids:
                item["status"] = "cancelled"
                _sync_display_content(item)
        return current_items

    if action == "append":
        todos = args.get("todos")
        if not isinstance(todos, list):
            return None
        current_items.extend(
            _normalize_todo_item(todo)
            for todo in todos
            if isinstance(todo, dict)
        )
        return current_items

    if action in ("insert_after", "insert_before"):
        todo_data = args.get("todo_data")
        if (
            not isinstance(todo_data, list)
            or len(todo_data) != 2
        ):
            return None
        target_id, insert_todos = todo_data
        if not isinstance(target_id, str) or not isinstance(insert_todos, list):
            return None
        target_index = next(
            (
                idx
                for idx, item in enumerate(current_items)
                if item.get("id") == target_id
            ),
            -1,
        )
        if target_index < 0:
            return None
        new_items = [
            _normalize_todo_item(todo)
            for todo in insert_todos
            if isinstance(todo, dict)
        ]
        insert_index = (
            target_index + 1
            if action == "insert_after"
            else target_index
        )
        return (
            current_items[:insert_index]
            + new_items
            + current_items[insert_index:]
        )

    return None


def _normalize_todo_item(
    item: Dict[str, Any],
) -> Dict[str, Any]:
    normalized = dict(item)
    _sync_display_content(normalized)
    return normalized


def _sync_display_content(
    item: Dict[str, Any],
) -> None:
    """Keep the rendered ``content`` aligned with todo status."""
    status = item.get("status", "pending")
    content = item.get("content", "")
    active_form = item.get("activeForm", "")
    if status == "in_progress" and active_form:
        item["content"] = active_form
    elif content:
        item["content"] = content
