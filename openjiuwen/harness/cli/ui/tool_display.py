"""Tool display formatting — name mapping, args, and result summary.

Provides Claude Code–style tool display:

- ``● Read(src/main.py)`` — tool call header
- ``⎿  Read 42 lines`` — result summary
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional, Union

# ---------------------------------------------------------------------------
# Tool name mapping: internal SDK name → friendly display name
# ---------------------------------------------------------------------------

_TOOL_DISPLAY_NAMES: Dict[str, str] = {
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Edit",
    "bash": "Bash",
    "grep": "Grep",
    "glob": "Glob",
    "ls": "LS",
    "list_dir": "LS",
    "todo_create": "TodoWrite",
    "todo_modify": "TodoWrite",
    "todo_list": "TodoList",
    "web_search": "WebSearch",
    "web_free_search": "WebSearch",
    "web_fetch": "WebFetch",
    "web_fetch_webpage": "WebFetch",
    "image_ocr": "ImageOCR",
    "visual_question_answering": "VisionQA",
    "audio_transcription": "AudioTranscribe",
    "audio_question_answering": "AudioQA",
    "audio_metadata": "AudioMetadata",
}

# Tools whose results should be rendered as todo checkboxes
TODO_TOOLS = {"todo_create", "todo_modify", "todo_list"}


def get_display_name(tool_name: str) -> str:
    """Map an internal tool name to a friendly display name.

    Args:
        tool_name: Internal SDK tool name (e.g. ``"read_file"``).

    Returns:
        Friendly display name (e.g. ``"Read"``).
    """
    return _TOOL_DISPLAY_NAMES.get(
        tool_name,
        tool_name.replace("_", " ").title(),
    )


def _parse_args(
    tool_args: Union[str, Dict[str, Any], None],
) -> Dict[str, Any]:
    """Ensure tool_args is a dict."""
    if tool_args is None:
        return {}
    if isinstance(tool_args, dict):
        return tool_args
    if isinstance(tool_args, str):
        try:
            parsed = json.loads(tool_args)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def format_tool_args(
    tool_name: str,
    tool_args: Union[str, Dict[str, Any], None],
) -> str:
    """Format tool arguments for display.

    Args:
        tool_name: Internal tool name.
        tool_args: Raw tool arguments (str or dict).

    Returns:
        Formatted argument string for display.
    """
    args = _parse_args(tool_args)

    if tool_name in ("read_file",):
        path = args.get("file_path", "")
        if path:
            path = _short_path(path)
            limit = args.get("limit")
            if limit:
                return f"{path}, limit={limit}"
            return path

    if tool_name in ("write_file",):
        path = args.get("file_path", "")
        return _short_path(path) if path else ""

    if tool_name in ("edit_file",):
        path = args.get("file_path", "")
        return _short_path(path) if path else ""

    if tool_name == "bash":
        cmd = args.get("command", "")
        if len(cmd) > 60:
            return cmd[:57] + "..."
        return cmd

    if tool_name in ("grep",):
        pattern = args.get("pattern", "")
        path = args.get("path", "")
        return f'"{pattern}" {path}'.strip()

    if tool_name in ("glob",):
        return args.get("pattern", "")

    if tool_name in ("ls", "list_dir"):
        return args.get("path", ".")

    if tool_name in TODO_TOOLS:
        return ""  # Task-list tools omit args in display

    if tool_name in (
        "web_search",
        "web_free_search",
    ):
        return args.get("query", "")

    if tool_name in (
        "web_fetch",
        "web_fetch_webpage",
    ):
        return args.get("url", "")

    # Fallback: show first arg value
    if args:
        first_val = str(next(iter(args.values())))
        if len(first_val) > 60:
            return first_val[:57] + "..."
        return first_val
    return ""


def format_tool_result(
    tool_name: str,
    tool_result: str,
    tool_args: Optional[
        Union[str, Dict[str, Any]]
    ] = None,
    tool_meta: Optional[Dict[str, Any]] = None,
) -> str:
    """Format a tool result into a concise summary.

    Args:
        tool_name: Internal tool name.
        tool_result: Raw tool result string.
        tool_args: Raw tool arguments (for context).

    Returns:
        Formatted result summary string.
    """
    if not tool_result:
        return "Done"

    if tool_name in ("read_file",):
        lines = _extract_tool_result_line_count(
            tool_result, tool_meta
        )
        return f"Read {lines} lines"

    if tool_name in ("write_file",):
        args = _parse_args(tool_args)
        path = _short_path(
            args.get("file_path", "")
        )
        lines = tool_result.count("\n")
        if lines > 0:
            return f"Wrote {lines} lines to {path}"
        return f"Wrote to {path}"

    if tool_name in ("edit_file",):
        # Show first line of result or "Edited file"
        first_line = tool_result.split("\n", 1)[0]
        if first_line and len(first_line) <= 80:
            return first_line
        return "Edited file"

    if tool_name == "bash":
        lines = tool_result.strip().split("\n")
        if len(lines) == 1 and len(lines[0]) <= 80:
            return lines[0]
        first = lines[0][:60] if lines[0] else ""
        return f"{first}... (+{len(lines) - 1} lines)"

    if tool_name in ("grep",):
        lines = tool_result.strip().split("\n")
        count = len(
            [ln for ln in lines if ln.strip()]
        )
        if count == 0:
            return "No matches found"
        return f"Found {count} matches"

    if tool_name in ("glob",):
        lines = tool_result.strip().split("\n")
        count = len(
            [ln for ln in lines if ln.strip()]
        )
        if count == 0:
            return "No files found"
        return f"Found {count} files"

    if tool_name in ("ls", "list_dir"):
        lines = tool_result.strip().split("\n")
        count = len(
            [ln for ln in lines if ln.strip()]
        )
        return f"Listed {count} items"

    if tool_name in TODO_TOOLS:
        return ""  # Handled by todo_render

    # Default: first line truncated
    first_line = tool_result.split("\n", 1)[0]
    if len(first_line) > 80:
        return first_line[:77] + "..."
    return first_line


def format_write_preview(tool_result: str) -> str:
    """Format a write/edit result as a content preview.

    Shows the first 5 lines with line numbers, plus
    ``… +N lines`` if truncated.

    Args:
        tool_result: Raw tool result string.

    Returns:
        Formatted preview string (may be multi-line).
    """
    lines = tool_result.split("\n")
    preview_lines = lines[:5]
    parts = []
    for i, line in enumerate(preview_lines, 1):
        truncated = (
            line[:77] + "..." if len(line) > 80 else line
        )
        parts.append(f"     {i} {truncated}")
    if len(lines) > 5:
        parts.append(f"     … +{len(lines) - 5} lines")
    return "\n".join(parts)


def _count_text_lines(text: str) -> int:
    """Count rendered text lines without undercounting single-line content."""
    return len(text.splitlines())


def _extract_tool_result_line_count(
    tool_result: str,
    tool_meta: Optional[Dict[str, Any]],
) -> int:
    """Prefer structured line counts over re-counting rendered text."""
    if isinstance(tool_meta, dict):
        line_count = tool_meta.get("line_count")
        if line_count is not None:
            try:
                return int(line_count)
            except (TypeError, ValueError):
                pass
    return _count_text_lines(tool_result)


def _short_path(path: str) -> str:
    """Shorten a file path for display.

    Strips the CWD prefix if the path starts with it.
    """
    cwd = os.getcwd()
    if path.startswith(cwd):
        rel = os.path.relpath(path, cwd)
        return rel
    return path
