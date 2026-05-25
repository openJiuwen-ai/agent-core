# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Smart output truncation and large-output persistence."""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path


def truncate_output(text: str, max_chars: int, *, head_ratio: float = 0.8) -> str:
    """Truncate long output while preserving both the beginning and end.

    When *text* fits within *max_chars* it is returned unchanged.
    Otherwise the first ~80 % of the budget keeps the head (context /
    setup messages) and the last ~20 % keeps the tail (typically the
    error or final status).  A gap indicator line shows how many lines
    were omitted.

    Args:
        text: Raw output text.
        max_chars: Maximum character budget. 0 means no limit.
        head_ratio: Fraction of the budget allocated to the head.

    Returns:
        The original or truncated text.
    """
    if max_chars == 0 or len(text) <= max_chars:
        return text

    head_budget = int(max_chars * head_ratio)
    tail_budget = max_chars - head_budget

    head = text[:head_budget]
    tail = text[-tail_budget:] if tail_budget > 0 else ""
    omitted = text[head_budget: len(text) - tail_budget] if tail_budget > 0 else text[head_budget:]
    omitted_lines = omitted.count("\n")

    return f"{head}\n\n... [{omitted_lines} lines omitted] ...\n\n{tail}"


# ── large output persistence ─────────────────────────────────

_OUTPUT_DIR: Path = Path(tempfile.gettempdir()) / "openjiuwen_bash_outputs"


def persist_large_output(stdout: str, stderr: str) -> tuple[str, int]:
    """Write raw command output to a temp file for later retrieval.

    The file is named by a content hash so identical outputs reuse the
    same file.  The caller should include the returned path in the tool
    result so the model can reference it.

    Args:
        stdout: Raw standard output.
        stderr: Raw standard error.

    Returns:
        (file_path, total_bytes) of the persisted file.
    """
    combined = stdout
    if stderr:
        combined += f"\n--- stderr ---\n{stderr}"

    content_bytes = combined.encode("utf-8", errors="replace")
    digest = hashlib.sha256(content_bytes).hexdigest()[:12]

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = _OUTPUT_DIR / f"bash_{digest}.txt"

    if not path.exists():
        path.write_bytes(content_bytes)

    return str(path), len(content_bytes)


# ── model-facing result rendering ────────────────────────────
#
# Renders command output into the Anthropic tool_result shape (a single content
# string + is_error). Standalone reimplementation -- intentionally independent
# of the anyshell engine -- covering the two paths the shell tools actually
# take: a semantic error and a normal (data) result.

_PERSISTED_OUTPUT_TAG = "<persisted-output>"
_PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"
_PREVIEW_SIZE_BYTES = 2000
_LEADING_BLANK_LINES = re.compile(r"^(\s*\n)+")


def _format_file_size(size_in_bytes: float) -> str:
    """Render a byte count as bytes / KB / MB / GB (trailing .0 stripped)."""
    kb = size_in_bytes / 1024
    if kb < 1:
        return f"{size_in_bytes} bytes"
    if kb < 1024:
        text = f"{kb:.1f}"
        return f"{text[:-2] if text.endswith('.0') else text}KB"
    mb = kb / 1024
    if mb < 1024:
        text = f"{mb:.1f}"
        return f"{text[:-2] if text.endswith('.0') else text}MB"
    gb = mb / 1024
    text = f"{gb:.1f}"
    return f"{text[:-2] if text.endswith('.0') else text}GB"


def _generate_preview(content: str, max_bytes: int) -> tuple[str, bool]:
    """Return (preview, has_more), cutting on a line boundary when possible."""
    if len(content) <= max_bytes:
        return content, False
    truncated = content[:max_bytes]
    last_newline = truncated.rfind("\n")
    cut = last_newline if last_newline > max_bytes * 0.5 else max_bytes
    return content[:cut], True


def _build_persisted_message(filepath: str, original_size: int, preview: str, has_more: bool) -> str:
    """Wrap an oversized-output preview in a <persisted-output> block."""
    msg = f"{_PERSISTED_OUTPUT_TAG}\n"
    msg += f"Output too large ({_format_file_size(original_size)}). Full output saved to: {filepath}\n\n"
    msg += f"Preview (first {_format_file_size(_PREVIEW_SIZE_BYTES)}):\n"
    msg += preview
    msg += "\n...\n" if has_more else "\n"
    msg += _PERSISTED_OUTPUT_CLOSING_TAG
    return msg


def _prepend_warning(content: str, warning: str | None) -> str:
    """Prepend a destructive-command warning to content, keeping it visible."""
    if not warning:
        return content
    return f"{warning}\n{content}" if content else warning


def _merge(first: str, second: str) -> str:
    """Join two output streams with a newline, dropping empty ones."""
    if not first:
        return second
    if not second:
        return first
    return f"{first}\n{second}"


@dataclass(frozen=True)
class CommandOutput:
    """Bundled command execution result and rendering configuration."""

    stdout: str
    stderr: str
    exit_code: int
    warning: str | None
    max_output_chars: int


def render_tool_content(
    output: CommandOutput,
    is_error: bool,
) -> tuple[str, bool]:
    """Render command output into the model-facing (content, is_error) standard.

    Mirrors the bash_v2 post-processing without depending on the anyshell engine:
    stdout and stderr are merged (the error path surfaces the merged stream after
    an ``Exit code N`` header), oversized output is persisted to disk and shown as
    a ``<persisted-output>`` preview, and any destructive-command warning is
    prepended so it stays visible to the model.

    Args:
        output: Bundled command result and rendering configuration.
        is_error: Whether the exit code is semantically an error, as decided by
            the command-aware ``interpret_exit_code``.

    Returns:
        The ``(content, is_error)`` pair for the Anthropic tool_result shape.
    """
    if is_error:
        # Error path: stderr leads so the failure detail comes first.
        merged = _merge(output.stderr, output.stdout)
        parts = [f"Exit code {output.exit_code}", merged]
        return _prepend_warning("\n".join(p for p in parts if p), output.warning), True

    # Data path: stdout leads.
    merged = _merge(output.stdout, output.stderr)
    processed = _LEADING_BLANK_LINES.sub("", merged).rstrip() if merged else merged
    if output.max_output_chars > 0 and len(merged) > output.max_output_chars:
        path, size = persist_large_output(output.stdout, output.stderr)
        preview, has_more = _generate_preview(processed, _PREVIEW_SIZE_BYTES)
        processed = _build_persisted_message(path, size, preview, has_more)
    return _prepend_warning(processed, output.warning), False


def render_partial_on_failure(
    output: CommandOutput,
    failure_message: str,
) -> str | None:
    """Render output collected before a post-launch failure (e.g. a timeout).

    The shell layer kills the process on timeout but still collects whatever was
    written before the kill. This surfaces that output to the model with the
    failure reason as a header, instead of dropping it.

    Args:
        output: Bundled command result and rendering configuration.
        failure_message: Why the command failed (e.g. the timeout message).

    Returns:
        The model-facing content string, or None when nothing was collected (the
        caller should fall back to the bare failure message).
    """
    if not output.stdout and not output.stderr:
        return None
    content, _ = render_tool_content(output, True)
    return f"{failure_message}\n{content}" if content else failure_message
