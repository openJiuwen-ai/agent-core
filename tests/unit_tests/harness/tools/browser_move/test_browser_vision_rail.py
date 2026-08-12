#!/usr/bin/env python
# coding: utf-8
# pylint: disable=protected-access
"""Tests for BrowserVisionRail screenshot retirement."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage
from openjiuwen.harness.tools.browser_move.playwright_runtime.vision_rail import (
    OUTDATED_VIEW_PLACEHOLDER,
    BrowserVisionRail,
)


def _capture_message(index: int = 0) -> UserMessage:
    return UserMessage(
        content=[
            {"type": "text", "text": f"Screenshot attached for: https://example.test/{index}"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QQ=="}},
        ]
    )


def _fake_context(messages: list[Any]) -> SimpleNamespace:
    class _MessageContext:
        def __init__(self, items: list[Any]) -> None:
            self._items = list(items)

        def get_messages(self) -> list[Any]:
            return self._items

        def set_messages(self, items: list[Any]) -> None:
            self._items = list(items)

    return SimpleNamespace(context=_MessageContext(messages))


def _image_blocks(msg) -> list[dict]:
    return [b for b in msg.content if isinstance(b, dict) and b.get("type") == "image_url"]


def test_rail_keeps_only_the_most_recent_capture() -> None:
    ctx = _fake_context([_capture_message(i) for i in range(4)])

    BrowserVisionRail(captures_to_keep=1)._retire_old_captures(ctx)

    messages = ctx.context.get_messages()
    for msg in messages[:-1]:
        assert not _image_blocks(msg)
        assert any(OUTDATED_VIEW_PLACEHOLDER in b.get("text", "") for b in msg.content)
    assert _image_blocks(messages[-1])


def test_rail_is_a_noop_at_or_below_the_budget() -> None:
    ctx = _fake_context([_capture_message(0), _capture_message(1)])

    BrowserVisionRail(captures_to_keep=2)._retire_old_captures(ctx)

    for msg in ctx.context.get_messages():
        assert _image_blocks(msg)


def test_rail_preserves_surrounding_text_and_other_messages() -> None:
    other = AssistantMessage(content="probing the page")
    ctx = _fake_context([_capture_message(0), other, _capture_message(1)])

    BrowserVisionRail(captures_to_keep=1)._retire_old_captures(ctx)

    messages = ctx.context.get_messages()
    # The retired turn keeps its descriptive text block; only the image goes.
    assert messages[0].content[0]["text"].startswith("Screenshot attached for:")
    assert len(messages[0].content) == 2
    assert messages[1] is other
    assert _image_blocks(messages[2])


def test_rail_can_retire_every_capture() -> None:
    ctx = _fake_context([_capture_message(0), _capture_message(1)])

    BrowserVisionRail(captures_to_keep=0)._retire_old_captures(ctx)

    for msg in ctx.context.get_messages():
        assert not _image_blocks(msg)


def test_rail_ignores_text_only_conversations() -> None:
    ctx = _fake_context([UserMessage(content="read the chart"), AssistantMessage(content="ok")])

    BrowserVisionRail(captures_to_keep=1)._retire_old_captures(ctx)

    assert ctx.context.get_messages()[0].content == "read the chart"


def test_rail_applies_the_multimodal_token_patch() -> None:
    from openjiuwen.core.context_engine.token.tiktoken_counter import TiktokenCounter

    BrowserVisionRail()

    counter = TiktokenCounter()
    big_data_url = "data:image/jpeg;base64," + ("A" * 200_000)
    message = UserMessage(
        content=[
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": big_data_url}},
        ]
    )

    # Unpatched counting repr()s the list and charges ~50k tokens for the blob.
    assert counter.count_messages([message]) < 5_000
