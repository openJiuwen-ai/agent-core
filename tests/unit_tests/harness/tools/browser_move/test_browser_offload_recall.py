# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for session-scoped browser offload recall."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openjiuwen.harness.tools.browser_move.offload_recall import BrowserOffloadRecallTool


_HANDLE = "0123456789abcdef0123456789abcdef"


def _session(session_id: str):
    return SimpleNamespace(get_session_id=lambda: session_id)


def _write_offload(tmp_path, session_id: str, content: str, *, handle: str = _HANDLE):
    offload_dir = tmp_path / "context" / f"{session_id}_context" / "offload"
    offload_dir.mkdir(parents=True)
    path = offload_dir / f"ToolResultWindowProcessor_{handle}.json"
    path.write_text(
        json.dumps(
            {
                "offload_handle": handle,
                "messages": [
                    {
                        "role": "tool",
                        "name": "browser_snapshot",
                        "tool_call_id": "call-1",
                        "content": content,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.asyncio
async def test_recalls_bounded_chunk_from_current_session(tmp_path):
    content = "0123456789" * 30
    _write_offload(tmp_path, "browser-session", content)
    tool = BrowserOffloadRecallTool(str(tmp_path), language="en")

    result = await tool.invoke(
        {"handle": _HANDLE, "offset": 20, "limit": 40},
        session=_session("browser-session"),
    )

    assert result.success is True
    assert result.data["content"] == content[20:60]
    assert result.data["next_offset"] == 60
    assert result.data["has_more"] is True
    assert result.data["stale_interaction_refs"] is True


@pytest.mark.asyncio
async def test_query_returns_context_around_first_match(tmp_path):
    content = ("before-" * 100) + "UNIQUE TARGET" + ("-after" * 100)
    _write_offload(tmp_path, "browser-session", content)
    tool = BrowserOffloadRecallTool(tmp_path, language="en")

    result = await tool.invoke(
        {"handle": _HANDLE.upper(), "query": "unique target", "limit": 700},
        session=_session("browser-session"),
    )

    assert result.success is True
    assert result.data["found"] is True
    assert result.data["match_offset"] == content.index("UNIQUE TARGET")
    assert "UNIQUE TARGET" in result.data["content"]


@pytest.mark.asyncio
async def test_does_not_read_another_session_artifact(tmp_path):
    _write_offload(tmp_path, "other-session", "secret")
    tool = BrowserOffloadRecallTool(tmp_path, language="en")

    result = await tool.invoke(
        {"handle": _HANDLE},
        session=_session("browser-session"),
    )

    assert result.success is False
    assert "not found" in result.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handle",
    [
        "../0123456789abcdef0123456789abcd",
        "ToolResultWindowProcessor_0123456789abcdef",
        "not-a-handle",
    ],
)
async def test_rejects_non_hex_or_path_like_handles(tmp_path, handle):
    tool = BrowserOffloadRecallTool(tmp_path, language="en")

    result = await tool.invoke(
        {"handle": handle},
        session=_session("browser-session"),
    )

    assert result.success is False
    assert "32 hexadecimal" in result.error


@pytest.mark.asyncio
async def test_requires_runtime_session(tmp_path):
    tool = BrowserOffloadRecallTool(tmp_path, language="en")

    result = await tool.invoke({"handle": _HANDLE})

    assert result.success is False
    assert "runtime session" in result.error
