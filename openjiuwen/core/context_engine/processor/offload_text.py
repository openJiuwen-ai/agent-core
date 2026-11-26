# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Model-readable text copy of filesystem-offloaded messages.

The ``.json`` offload file keeps the exact messages for program reload
(``OffloadMessageBuffer.reload``), but JSON escapes every string value onto one
physical line. Line-based tools cannot consume that: ``read_file`` pages by
lines and rejects a page over its token budget, and ``grep`` omits lines wider
than ``--max-columns``. The sibling ``.txt`` copy rendered here keeps the real
line breaks and hard-wraps over-long lines, so the offload marker can point the
model at a file both tools can read.
"""

import os
from dataclasses import dataclass

from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage

# Limits of the harness read tools that the text copy must fit. Core must not
# import harness, so the values are mirrored here and pinned by unit tests.
GREP_MAX_COLUMNS = 500  # GrepTool.MAX_COLUMNS: ``rg --max-columns``, UTF-8 bytes per line
READ_FILE_MAX_LINES = 2000  # ReadFileTool.MAX_LINES_TO_READ
READ_FILE_MAX_TOKENS = 25_000  # ReadFileTool.MAX_TOKENS, estimated as ``len(text) // 4``

OFFLOAD_TEXT_SUFFIX = ".txt"
OFFLOAD_TEXT_HEADER_LINES = 3
OFFLOAD_TEXT_WRAP_BYTES = GREP_MAX_COLUMNS - 20
"""Maximum UTF-8 bytes per physical line, so ``grep`` shows every matching line in full."""

_READ_FILE_PAGE_CHARS = READ_FILE_MAX_TOKENS * 4 * 4 // 5  # 80% of read_file's character budget
_MIN_READ_LIMIT = 20
_TOOL_CALL_ARGUMENTS_PREVIEW_CHARS = 200
_UTF8_CONTINUATION_MASK = 0xC0
_UTF8_CONTINUATION_BITS = 0x80


@dataclass(frozen=True)
class OffloadText:
    """Rendered ``.txt`` copy and the line statistics used for read hints."""

    text: str
    body_lines: int
    body_chars: int
    original_chars: int
    max_line_bytes: int
    wrapped_lines: int


def offload_text_path(offload_path: str) -> str:
    """Return the ``.txt`` sibling path of an offload file path."""
    return os.path.splitext(offload_path)[0] + OFFLOAD_TEXT_SUFFIX


def render_offload_text(messages: list[BaseMessage], *, offload_handle: str) -> OffloadText:
    """Render offloaded messages as line-oriented text.

    Message text is rendered as is; each tool call becomes a one-line summary
    and each non-text content part a placeholder line. Lines follow
    ``str.splitlines`` boundaries and are joined with ``\\n``. A line wider than
    :data:`OFFLOAD_TEXT_WRAP_BYTES` is split at UTF-8 character boundaries
    without continuation markers; the exact original stays in the ``.json``
    file for program reload.
    """
    segments: list[str] = []
    original_chars = 0
    for index, message in enumerate(messages, start=1):
        if len(messages) > 1:
            segments.append(f"--- message {index}/{len(messages)} role={message.role} ---")
        text = _message_text(message)
        original_chars += len(text)
        segments.append(text)
        segments.extend(_tool_call_lines(message))

    body: list[str] = []
    max_line_bytes = 0
    wrapped_lines = 0
    for segment in segments:
        for line in segment.splitlines():
            encoded = line.encode("utf-8", errors="surrogatepass")
            max_line_bytes = max(max_line_bytes, len(encoded))
            if len(encoded) <= OFFLOAD_TEXT_WRAP_BYTES:
                body.append(line)
                continue
            wrapped_lines += 1
            body.extend(_wrap_encoded_line(encoded))

    header = [
        f"[OFFLOAD {_identity(messages, offload_handle)}]",
        (
            f"[original_chars={original_chars} total_lines={len(body)} max_line_bytes={max_line_bytes} "
            f"wrapped_lines={wrapped_lines} wrap_bytes={OFFLOAD_TEXT_WRAP_BYTES}]"
        ),
        (
            f"[Body starts at line {OFFLOAD_TEXT_HEADER_LINES + 1}. Lines over {OFFLOAD_TEXT_WRAP_BYTES} UTF-8 "
            "bytes are wrapped at character boundaries; wrapping only inserts line breaks. "
            "The sibling .json file is for program reload, not for reading.]"
        ),
    ]
    return OffloadText(
        text="\n".join([*header, *body]) + "\n",
        body_lines=len(body),
        body_chars=sum(len(line) for line in body),
        original_chars=original_chars,
        max_line_bytes=max_line_bytes,
        wrapped_lines=wrapped_lines,
    )


def suggest_read_limit(offload_text: OffloadText, *, content_tokens: int, page_budget_tokens: int) -> int:
    """Suggest a ``read_file`` ``limit`` for paging through the text copy.

    A page stays within ``page_budget_tokens`` (counted by the context token
    counter, so it follows the current model window) and within 80% of
    ``read_file``'s own ``len // 4`` token check.
    """
    lines = max(offload_text.body_lines, 1)
    tokens_per_line = max(content_tokens, 1) / lines
    chars_per_line = offload_text.body_chars / lines
    by_budget = int(page_budget_tokens / tokens_per_line)
    by_read_file = int(_READ_FILE_PAGE_CHARS / (chars_per_line + 1))
    return max(_MIN_READ_LIMIT, min(by_budget, by_read_file, READ_FILE_MAX_LINES))


def format_offload_read_hint(offload_text: OffloadText, *, content_tokens: int, read_limit: int) -> str:
    """Return the one-line read hint placed before the offload marker."""
    return (
        f"[Offloaded text: {offload_text.body_lines} lines, about {content_tokens} tokens. "
        "Locate content with grep first; when paging with read_file, "
        f"use limit={read_limit} and halve it if read_file reports the token limit.]"
    )


def _identity(messages: list[BaseMessage], offload_handle: str) -> str:
    identity = f"handle={offload_handle}"
    if len(messages) != 1 or not isinstance(messages[0], ToolMessage):
        return identity
    if messages[0].name:
        identity += f" tool={messages[0].name}"
    return f"{identity} tool_call_id={messages[0].tool_call_id}"


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif part.get("type") == "text":
            parts.append(str(part.get("text") or ""))
        else:
            parts.append(f"[{part.get('type', 'unknown')} part omitted]")
    return "\n".join(parts)


def _tool_call_lines(message: BaseMessage) -> list[str]:
    lines: list[str] = []
    for tool_call in getattr(message, "tool_calls", None) or []:
        arguments = tool_call.arguments
        if len(arguments) > _TOOL_CALL_ARGUMENTS_PREVIEW_CHARS:
            arguments = f"{arguments[:_TOOL_CALL_ARGUMENTS_PREVIEW_CHARS]}...(truncated)"
        lines.append(f"[tool_call {tool_call.name}] {arguments}")
    return lines


def _wrap_encoded_line(encoded: bytes) -> list[str]:
    parts: list[str] = []
    start = 0
    while start < len(encoded):
        end = min(start + OFFLOAD_TEXT_WRAP_BYTES, len(encoded))
        while end < len(encoded) and (encoded[end] & _UTF8_CONTINUATION_MASK) == _UTF8_CONTINUATION_BITS:
            end -= 1
        parts.append(encoded[start:end].decode("utf-8", errors="surrogatepass"))
        start = end
    return parts
