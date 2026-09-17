"""Live "what just happened" hint for a running module attempt.

A module (especially code_implementation and reporting) can run for tens of
minutes with dozens of tool calls, while the frontend only ever sees the
static module-level label ("正在实现代码") the whole time -- indistinguishable
from actually being stuck. `ObservabilityRail` already logs every tool call
to that attempt's own `agent_trace.jsonl`; this module tails that file while
the module is running and turns the latest event into a short line, pushed
through the existing `on_stage(module, note)` channel. It does not touch any
module's own logic, and does not add any new field the frontend must learn
to read -- the caller folds `note` into the same summary string the frontend
already renders (see `PaperTreeOrchestrator._emit`).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import agent_trace_path

_POLL_SECONDS = 1.5
_COMMAND_CHARS = 60
# Recognized script paths get a friendlier phrase than the generic "executed
# a command" fallback. Order matters only in that the first match wins.
_COMMAND_PATTERNS: tuple[tuple[str, str], ...] = (
    ("ts-write", "撰写论文正文"),
    ("ts-figure", "生成图表"),
    ("ts-review", "审校论文"),
    ("ts-latex", "编译 PDF"),
)


def _extract_bash_command(arguments: Any) -> str | None:
    """Best-effort pull of the shell command string out of a sanitized
    tool_call_start.arguments payload -- same shape ambiguity handled in
    pipeline/subagents.py's `_extract_bash_command`, kept as a separate,
    shorter-clipping copy here since this is for a one-line live hint, not
    a retry-repair prompt."""
    try:
        payload = arguments
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            return None
        command = payload.get("command")
        if command is None and isinstance(payload.get("text"), str):
            try:
                inner = json.loads(payload["text"])
            except (TypeError, ValueError, json.JSONDecodeError):
                inner = None
            command = inner.get("command") if isinstance(inner, dict) else payload.get("text")
        if not isinstance(command, str):
            return None
        command = " ".join(command.split()).strip()
        return command or None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _extract_path(arguments: Any) -> str | None:
    try:
        payload = arguments if isinstance(arguments, dict) else json.loads(arguments)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    path = payload.get("path") or payload.get("file_path")
    return path if isinstance(path, str) and path else None


def _describe_bash(command: str, seen_patterns: dict[str, int]) -> str:
    for needle, phrase in _COMMAND_PATTERNS:
        if needle in command:
            seen_patterns[needle] = seen_patterns.get(needle, 0) + 1
            count = seen_patterns[needle]
            suffix = f"（第 {count} 次）" if count > 1 else ""
            return f"最近动作：{phrase}{suffix}"
    if "pytest" in command or "smoke" in command:
        return "最近动作：运行验证测试"
    clipped = command if len(command) <= _COMMAND_CHARS else command[: _COMMAND_CHARS - 1] + "…"
    return f"最近动作：执行命令 `{clipped}`"


def _describe_event(event: dict[str, Any], seen_patterns: dict[str, int]) -> str | None:
    kind = event.get("event")
    tool_name = event.get("tool_name")
    if kind == "tool_call_error":
        # No "正在": jiuwenswarm's frontend filter (isStageDescription in
        # rsiPresentation.ts) hides any description containing that word,
        # which would silently swallow this note along with the stage label
        # it gets concatenated with.
        return "最近动作：上一步失败，尝试修正"
    if kind == "model_call_start":
        return "最近动作：等待模型响应中"
    if kind != "tool_call_start" or not tool_name:
        return None
    if tool_name == "bash":
        command = _extract_bash_command(event.get("arguments"))
        return _describe_bash(command, seen_patterns) if command else "最近动作：执行命令"
    lowered = str(tool_name).lower()
    if "read" in lowered or "write" in lowered or "edit" in lowered:
        verb = "读取" if "read" in lowered else "编写"
        path = _extract_path(event.get("arguments"))
        return f"最近动作：{verb} `{path}`" if path else f"最近动作：{verb}文件"
    return f"最近动作：调用 {tool_name}"


async def tail_activity(
    *,
    run_id: str,
    module: str,
    round_index: int,
    attempt: int,
    on_note: Callable[[str], Awaitable[None]],
    poll_seconds: float = _POLL_SECONDS,
) -> None:
    """Background loop: watch this attempt's own trace file and push a short
    human-readable hint via `on_note` whenever new tool activity appears.

    Cheap by design: most ticks are a single `os.stat` and nothing else --
    real parsing only happens when the file has actually grown. Intended to
    be wrapped in `asyncio.create_task` and cancelled by the caller once the
    module's own `adapter.ainvoke(...)` call returns (see
    `ManagerRuntime._execute_contract`); this function never returns on its
    own and relies entirely on `CancelledError` to stop.
    """
    trace_path = agent_trace_path(run_id, module, round_index, attempt)
    offset = 0
    last_size = -1
    seen_patterns: dict[str, int] = {}
    while True:
        await asyncio.sleep(poll_seconds)
        try:
            size = trace_path.stat().st_size
        except OSError:
            continue
        if size == last_size:
            continue
        last_size = size
        try:
            with trace_path.open(encoding="utf-8", errors="replace") as handle:
                handle.seek(offset)
                lines = handle.readlines()
                offset = handle.tell()
        except OSError:
            continue
        note: str | None = None
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            described = _describe_event(event, seen_patterns)
            if described:
                note = described
        if note:
            await on_note(note)
