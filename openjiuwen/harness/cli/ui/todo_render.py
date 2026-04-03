"""Todo checkbox rendering for CLI output.

Renders todo items with visual checkboxes and progress summaries:

- ``☑ task``  — completed (green)
- ``◐ task``  — in_progress (yellow)
- ``☐ task``  — pending (dim)
- ``✓2 ◐1 ☐3`` — progress summary
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

# Status → (icon, Rich style)
_STATUS_STYLE = {
    "completed": ("☑", "green"),
    "in_progress": ("◐", "yellow"),
    "pending": ("☐", "dim"),
    "cancelled": ("☒", "dim strike"),
}

# SDK STATUS_ICONS → our status name
_SDK_ICON_TO_STATUS = {
    "[>]": "in_progress",
    "[ ]": "pending",
    "[√]": "completed",
    "[×]": "cancelled",
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
    """Parse todo items from the SDK's human-readable text format.

    The SDK todo tools return text like::

        Successfully created 3 task(s):
          [>] task_id: abc , content: Write tests
          [ ] task_id: def , content: Deploy
          [ ] task_id: ghi , content: Review

    Or for ``todo_list``::

        Todo List (Total: 3 items):

        [>] In Progress Task
         [uuid] Write tests

        [ ] Pending Tasks
         [uuid] Deploy
         [uuid] Review

    Args:
        tool_result: Raw string from ``str(tool_result)``.

    Returns:
        List of item dicts, or ``None``.
    """
    items: List[Dict[str, Any]] = []

    # Normalize: unescape \\n → real newlines for parsing
    # (str() on a dict produces Python repr with literal \\n)
    normalized = tool_result.replace("\\n", "\n")

    # Pattern 1: TodoCreateTool format
    #   [>] task_id: uuid , content: description
    create_pattern = re.compile(
        r"\[([>√× ])\]\s+task_id:\s*\S+\s*,\s*content:\s*(.+)"
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

    if items:
        return items

    # Pattern 2: TodoListTool format
    #   Section headers: [>] In Progress Task  /  [ ] Pending Tasks
    #   Items under them:  [uuid] description
    current_status = "pending"
    section_pattern = re.compile(
        r"^\[([>√× ])\]\s+(?:In Progress|Pending|Completed|Cancelled)",
    )
    item_pattern = re.compile(
        r"^\s+\[[\w-]+\]\s+(.+)",
    )
    for line in normalized.split("\n"):
        section_match = section_pattern.match(line)
        if section_match:
            icon_char = section_match.group(1)
            icon_key = f"[{icon_char}]"
            current_status = _SDK_ICON_TO_STATUS.get(
                icon_key, "pending"
            )
            continue
        item_match = item_pattern.match(line)
        if item_match:
            content = item_match.group(1).strip()
            items.append(
                {
                    "content": content,
                    "status": current_status,
                }
            )

    return items if items else None


def parse_todo_result(
    tool_result: str,
) -> Optional[List[Dict[str, Any]]]:
    """Parse a todo tool result into a list of items.

    Tries multiple strategies:

    1. JSON (structured data)
    2. SDK text format with ``[>]``/``[ ]`` icons

    Args:
        tool_result: Raw string result from a todo tool.

    Returns:
        List of item dicts, or ``None``.
    """
    if not tool_result:
        return None

    # Strategy 1: Try JSON
    try:
        data = json.loads(tool_result)
    except (json.JSONDecodeError, TypeError):
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
