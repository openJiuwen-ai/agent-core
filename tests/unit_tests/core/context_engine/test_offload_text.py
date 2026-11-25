# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The offload ``.txt`` copy must stay readable by ``read_file`` paging and ``grep``.

The ``.json`` offload file escapes a whole tool result onto one physical line,
which neither tool can page or show. These tests pin the line-oriented copy
that the offload marker points at instead: real line breaks kept, over-long
lines wrapped within grep's column limit without splitting characters, and a
read-page suggestion that fits both the processor budget and ``read_file``.
"""

from openjiuwen.core.context_engine.processor.offload_text import (
    OFFLOAD_TEXT_HEADER_LINES,
    OFFLOAD_TEXT_WRAP_BYTES,
    READ_FILE_MAX_LINES,
    _TOOL_CALL_ARGUMENTS_PREVIEW_CHARS,
    format_offload_read_hint,
    offload_text_path,
    render_offload_text,
    suggest_read_limit,
)
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage


def _body_lines(text: str) -> list[str]:
    return text.splitlines()[OFFLOAD_TEXT_HEADER_LINES:]


def test_text_path_is_json_sibling() -> None:
    json_path = "/w/offload/MessageSummaryOffloader_ab.json"

    assert offload_text_path(json_path) == "/w/offload/MessageSummaryOffloader_ab.txt"


def test_render_keeps_real_line_breaks_after_header() -> None:
    lines = [f"2026-10-20 00:00:{index:02d}.000 [INFO] [order] Order {index} created" for index in range(50)]
    message = ToolMessage(content="\r\n".join(lines), tool_call_id="tc-log")

    rendered = render_offload_text([message], offload_handle="h1")

    header = rendered.text.splitlines()[:OFFLOAD_TEXT_HEADER_LINES]
    assert header[0] == "[OFFLOAD handle=h1 tool_call_id=tc-log]"
    assert header[1].startswith("[original_chars=")
    assert f"total_lines={len(lines)}" in header[1]
    assert header[2].startswith(f"[Body starts at line {OFFLOAD_TEXT_HEADER_LINES + 1}.")
    assert _body_lines(rendered.text) == lines
    assert rendered.body_lines == len(lines)
    assert rendered.wrapped_lines == 0


def test_render_wraps_long_line_within_grep_columns() -> None:
    content = "x" * (OFFLOAD_TEXT_WRAP_BYTES * 3 + 7)

    rendered = render_offload_text([ToolMessage(content=content, tool_call_id="tc")], offload_handle="h")

    body = _body_lines(rendered.text)
    assert len(body) == 4
    assert all(len(line.encode("utf-8")) <= OFFLOAD_TEXT_WRAP_BYTES for line in body)
    assert "".join(body) == content
    assert rendered.wrapped_lines == 1
    assert rendered.max_line_bytes == len(content)


def test_render_wraps_at_character_boundaries() -> None:
    content = "中文😀a" * 2_000

    rendered = render_offload_text([ToolMessage(content=content, tool_call_id="tc")], offload_handle="h")

    body = _body_lines(rendered.text)
    assert all(len(line.encode("utf-8")) <= OFFLOAD_TEXT_WRAP_BYTES for line in body)
    assert "".join(body) == content


def test_render_multiple_messages_with_tool_calls_and_non_text_parts() -> None:
    long_arguments = '{"command": "' + ("x" * (_TOOL_CALL_ARGUMENTS_PREVIEW_CHARS + 40)) + '"}'
    messages = [
        AssistantMessage(
            content="calling",
            tool_calls=[
                ToolCall(id="tc-1", type="function", name="bash", arguments='{"command": "ls"}'),
                ToolCall(id="tc-2", type="function", name="bash", arguments=long_arguments),
            ],
        ),
        UserMessage(content=["hello", {"type": "text", "text": "line1\nline2"}, {"type": "image_url"}]),
    ]

    rendered = render_offload_text(messages, offload_handle="h")

    assert rendered.text.splitlines()[0] == "[OFFLOAD handle=h]"
    assert _body_lines(rendered.text) == [
        "--- message 1/2 role=assistant ---",
        "calling",
        '[tool_call bash] {"command": "ls"}',
        (
            f"[tool_call bash] {long_arguments[:_TOOL_CALL_ARGUMENTS_PREVIEW_CHARS]}"
            "...(truncated)"
        ),
        "--- message 2/2 role=user ---",
        "hello",
        "line1",
        "line2",
        "[image_url part omitted]",
    ]


def test_suggest_read_limit_follows_page_budget() -> None:
    content = "\n".join(["y" * 60] * 1_000)
    rendered = render_offload_text([ToolMessage(content=content, tool_call_id="tc")], offload_handle="h")

    small_window = suggest_read_limit(rendered, content_tokens=20_000, page_budget_tokens=3_200)
    large_window = suggest_read_limit(rendered, content_tokens=20_000, page_budget_tokens=8_000)

    assert small_window == 160
    assert large_window == 400


def test_suggest_read_limit_respects_read_file_budget_and_bounds() -> None:
    wide = render_offload_text(
        [ToolMessage(content="\n".join(["z" * 400] * 100), tool_call_id="tc")],
        offload_handle="h",
    )
    narrow = render_offload_text(
        [ToolMessage(content="\n".join(["z"] * 100), tool_call_id="tc")],
        offload_handle="h",
    )

    assert suggest_read_limit(wide, content_tokens=1, page_budget_tokens=10**9) == 199
    assert suggest_read_limit(wide, content_tokens=10**9, page_budget_tokens=1) == 20
    assert suggest_read_limit(narrow, content_tokens=1, page_budget_tokens=10**9) == READ_FILE_MAX_LINES


def test_read_hint_reports_lines_tokens_and_limit() -> None:
    rendered = render_offload_text([ToolMessage(content="a\nb\nc", tool_call_id="tc")], offload_handle="h")

    hint = format_offload_read_hint(rendered, content_tokens=42, read_limit=280)

    assert hint.startswith("[Offloaded text: 3 lines, about 42 tokens.")
    assert "limit=280" in hint
