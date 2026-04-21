"""Stream renderer — maps OutputSchema chunks to terminal output.

Supports eight chunk types:

- ``llm_output``      — LLM generated text (accumulated into result)
- ``llm_reasoning``   — reasoning / thinking (hidden by default)
- ``answer``          — final answer (skipped to avoid duplication)
- ``__interaction__`` — HITL interaction request
- ``message``         — system / status messages
- ``tool_call``       — tool execution start (● ToolName)
- ``tool_result``     — tool execution result (⎿ summary)
- ``todo.updated``    — todo list change (checkbox rendering)
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from rich.console import Console

from openjiuwen.harness.cli.ui.tool_display import (
    TODO_TOOLS,
    format_tool_args,
    format_tool_result,
    format_write_preview,
    get_display_name,
)
from openjiuwen.harness.cli.ui.todo_render import (
    apply_todo_modify_args,
    parse_todo_result,
    parse_todo_tool_args,
    render_todo_list,
)


def _write_terminal(text: str) -> None:
    """Write *text* directly to the terminal (stdout).

    This is intentional CLI user-facing output, not
    diagnostic logging. Uses the active stdout encoding
    while writing directly to file descriptor 1.
    """
    stdout = sys.stdout
    encoding = stdout.encoding or "utf-8"
    errors = stdout.errors or "strict"
    os.write(1, text.encode(encoding, errors=errors))

# Chunk type constants (aligned with SDK OutputSchema.type)
CHUNK_LLM_OUTPUT = "llm_output"
CHUNK_LLM_REASONING = "llm_reasoning"
CHUNK_ANSWER = "answer"
CHUNK_INTERACTION = "__interaction__"
CHUNK_MESSAGE = "message"
CHUNK_TOOL_CALL = "tool_call"
CHUNK_TOOL_RESULT = "tool_result"
CHUNK_TODO_UPDATED = "todo.updated"
CHUNK_CONTROLLER_OUTPUT = "controller_output"


@dataclass
class PendingInteraction:
    """An interaction request awaiting user input.

    Attributes:
        interaction_id: The tool-call ID that must be used
            as the key in ``InteractiveInput.update()``.
        request: The raw ``ToolCallInterruptRequest``
            payload for the interaction.
    """

    interaction_id: str
    request: Any


@dataclass
class RenderResult:
    """Value returned by :func:`render_stream`.

    Attributes:
        text: Accumulated LLM output text.
        pending_interactions: Interactions that need user
            answers before the agent can continue.
    """

    text: str = ""
    pending_interactions: list[PendingInteraction] = field(
        default_factory=list
    )


def _extract_content(chunk: Any) -> str:
    """Extract text content from a chunk's payload."""
    payload = chunk.payload
    if isinstance(payload, dict):
        return (
            payload.get("content", "")
            or payload.get("output", "")
        )
    if isinstance(payload, str):
        return payload
    # InteractionOutput or other objects
    return str(payload)


def _extract_controller_output_error(payload: Any) -> str:
    """Extract a readable controller task-failure message."""
    if isinstance(payload, dict):
        payload_type = str(payload.get("type", "")).lower()
        if "task_failed" in payload_type:
            data = payload.get("data", [])
            texts: list[str] = []
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        text = str(item.get("text", "")).strip()
                        if text:
                            texts.append(text)
            if texts:
                return "\n".join(texts)
        return ""

    raw = str(payload)
    if "task_failed" not in raw.lower():
        return ""

    matches = re.findall(r'text="(.*?)"', raw)
    if matches:
        return "\n".join(
            m.encode("utf-8").decode("unicode_escape")
            for m in matches
        )
    return raw


def _extract_todo_message(tool_result: str) -> str:
    """Extract the 'message' value from a Python dict repr.

    Todo tools return ``{'message': 'text...'}``.  When
    ``str()`` is applied, it becomes a Python repr string.
    This helper extracts the message value for fallback display.
    """
    # Try to find 'message': '...' in the repr
    match = re.search(
        r"['\"]message['\"]\s*:\s*['\"](.+?)['\"]}\s*$",
        tool_result,
        re.DOTALL,
    )
    if match:
        msg = match.group(1)
        # Unescape \\n → \n
        msg = msg.replace("\\n", "\n")
        first_line = msg.split("\n", 1)[0].strip()
        if first_line:
            return first_line
    return ""


def _render_tool_call(
    payload: dict, console: Console
) -> None:
    """Render a tool call in Claude Code style.

    Example::

        ● Read(src/main.py)
    """
    tool_name = payload.get("tool_name", "")
    tool_args = payload.get("tool_args", "")
    display_name = get_display_name(tool_name)
    args_str = format_tool_args(tool_name, tool_args)

    if args_str:
        console.print(
            f"[cyan]● {display_name}[/cyan]"
            f"[dim]({args_str})[/dim]"
        )
    else:
        console.print(f"[cyan]● {display_name}[/cyan]")


def _render_tool_result(
    payload: dict,
    console: Console,
    todo_items: Optional[list[dict[str, Any]]] = None,
) -> Optional[list[dict[str, Any]]]:
    """Render a tool result in Claude Code style.

    Example::

        ⎿  Read 42 lines
    """
    tool_name = payload.get("tool_name", "")
    tool_result = payload.get("tool_result", "")
    tool_args = payload.get("tool_args", "")

    # Task-list tools get special checkbox rendering
    if tool_name in TODO_TOOLS:
        items = parse_todo_result(tool_result)
        if items:
            for line in render_todo_list(items):
                console.print(line)
            console.print()  # blank line after
            return items
        if (
            tool_name == "todo_modify"
            and todo_items
        ):
            items = apply_todo_modify_args(
                todo_items,
                parse_todo_tool_args(tool_args),
            )
            if items:
                for line in render_todo_list(items):
                    console.print(line)
                console.print()
                return items
        # Fallback: show raw message if parsing failed
        # Extract 'message' value from Python dict repr
        msg = _extract_todo_message(tool_result)
        if msg:
            console.print(f"[dim]  ⎿  {msg}[/dim]")
            console.print()
            return todo_items

    summary = format_tool_result(
        tool_name, tool_result, tool_args, payload
    )
    if summary:
        console.print(f"[dim]  ⎿  {summary}[/dim]")

    # Write/edit content preview
    if tool_name in ("write_file",) and tool_result:
        preview = format_write_preview(tool_result)
        if preview:
            console.print(f"[dim]{preview}[/dim]")

    console.print()  # blank line after tool result
    return todo_items


async def render_stream(
    stream: AsyncIterator[Any],
    console: Console,
    *,
    on_interaction: Optional[
        Callable[[str, Any], Awaitable[str]]
    ] = None,
    show_reasoning: bool = False,
) -> RenderResult:
    """Consume an OutputSchema stream and render to terminal.

    Args:
        stream: Async iterator of OutputSchema chunks from the SDK.
        console: Rich :class:`Console` instance for output.
        on_interaction: Callback for ``__interaction__`` chunks.
            Receives ``(interaction_id, question)`` and must return
            the user's answer string.  ``None`` means skip interaction.
        show_reasoning: If ``True``, display reasoning chunks in dim
            style.  Defaults to ``False`` (hidden).

    Returns:
        A :class:`RenderResult` containing the accumulated response
        text and any pending interactions that need user answers.
    """
    result_parts: list[str] = []
    chunk_count = 0
    has_llm_output = False
    in_llm_output = False
    pending_interactions: list[PendingInteraction] = []
    todo_items: Optional[list[dict[str, Any]]] = None
    visible_chunk_seen = False
    seen_chunk_types: set[str] = set()

    async for chunk in stream:
        chunk_count += 1
        chunk_type = getattr(chunk, "type", "")
        if chunk_type:
            seen_chunk_types.add(chunk_type)

        if chunk_type != CHUNK_LLM_OUTPUT and in_llm_output:
            _write_terminal("\n")
            in_llm_output = False

        if chunk_type == CHUNK_LLM_OUTPUT:
            text = _extract_content(chunk)
            if text:
                # Print green bullet prefix on first
                # llm_output token of a new response
                if not in_llm_output:
                    _write_terminal("\033[92m● \033[0m")
                    in_llm_output = True
                has_llm_output = True
                result_parts.append(text)
                _write_terminal(text)
                visible_chunk_seen = True

        elif chunk_type == CHUNK_ANSWER:
            # The answer chunk duplicates llm_output content.
            # Only use it if no llm_output was received (fallback).
            if not has_llm_output:
                text = _extract_content(chunk)
                if text:
                    result_parts.append(text)
                    _write_terminal(text)
                    visible_chunk_seen = True

        elif chunk_type == CHUNK_LLM_REASONING:
            # Reasoning is hidden by default; only shown with
            # --verbose or show_reasoning=True.
            if show_reasoning:
                text = _extract_content(chunk)
                if text:
                    console.print(
                        f"[dim]{text}[/dim]", end=""
                    )

        elif chunk_type == CHUNK_MESSAGE:
            text = _extract_content(chunk)
            if text:
                console.print(f"[dim]  \u2699 {text}[/dim]")
                visible_chunk_seen = True

        elif chunk_type == CHUNK_TOOL_CALL:
            payload = (
                chunk.payload
                if isinstance(chunk.payload, dict)
                else {}
            )
            _render_tool_call(payload, console)
            visible_chunk_seen = True

        elif chunk_type == CHUNK_TOOL_RESULT:
            payload = (
                chunk.payload
                if isinstance(chunk.payload, dict)
                else {}
            )
            todo_items = _render_tool_result(
                payload,
                console,
                todo_items=todo_items,
            )
            visible_chunk_seen = True

        elif chunk_type == CHUNK_TODO_UPDATED:
            payload = (
                chunk.payload
                if isinstance(chunk.payload, dict)
                else {}
            )
            items = parse_todo_result(
                str(payload.get("items", "[]"))
            )
            if items:
                todo_items = items
                for line in render_todo_list(items):
                    console.print(line)
                visible_chunk_seen = True

        elif chunk_type == CHUNK_CONTROLLER_OUTPUT:
            error_text = _extract_controller_output_error(
                chunk.payload
            )
            if error_text:
                console.print(f"[red]✗ {error_text}[/red]")
                visible_chunk_seen = True

        elif chunk_type == CHUNK_INTERACTION:
            payload = chunk.payload
            iid = getattr(payload, "id", "unknown")
            value = getattr(payload, "value", payload)

            # Render the interaction question to terminal
            if on_interaction is not None:
                await on_interaction(iid, value)

            # Collect for resume — the REPL must send
            # these back via InteractiveInput.
            pending_interactions.append(
                PendingInteraction(
                    interaction_id=iid,
                    request=value,
                )
            )
            visible_chunk_seen = True

        # Silently skip unknown types (controller_output, etc.)

    # Ensure trailing newline after streaming output
    if in_llm_output:
        _write_terminal("\n")

    # Integrity warnings
    result = "".join(result_parts)
    if chunk_count == 0:
        console.print(
            "[dim]\u26a0 No output received. "
            "Check your API configuration.[/dim]"
        )
    elif not visible_chunk_seen and not pending_interactions:
        chunk_types = ", ".join(sorted(seen_chunk_types)) or "unknown"
        console.print(
            "[dim]\u26a0 No visible output received. "
            f"Chunk types: {chunk_types}[/dim]"
        )

    return RenderResult(
        text=result,
        pending_interactions=pending_interactions,
    )
