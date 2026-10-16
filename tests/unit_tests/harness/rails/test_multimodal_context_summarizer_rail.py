# coding: utf-8
"""Tests for the shared MultimodalContextSummarizerRail."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.harness.rails.multimodal_context_summarizer_rail import (
    ARCHIVED_SCREEN_PLACEHOLDER,
    MultimodalContextSummarizerRail,
)


def _image_user(*, name: str | None = None) -> UserMessage:
    return UserMessage(
        name=name,
        content=[
            {"type": "text", "text": "stub"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,QQ=="}},
        ],
    )


def _fake_message_context(messages: list[Any]) -> SimpleNamespace:
    class _MessageContext:
        def __init__(self, items: list[Any]) -> None:
            self._items = list(items)

        def get_messages(self) -> list[Any]:
            return self._items

        def set_messages(self, items: list[Any]) -> None:
            self._items = list(items)

    return SimpleNamespace(context=_MessageContext(messages))


def test_keeps_last_n_screenshots_and_archives_older_in_place():
    messages = [_image_user() for _ in range(5)]
    ctx = _fake_message_context(messages)

    rail = MultimodalContextSummarizerRail(screenshots_to_keep=3)
    rail._archive_old_screenshot_images(ctx)

    updated = ctx.context.get_messages()
    for idx in (0, 1):
        archived_block = updated[idx].content[1]
        assert archived_block == {"type": "text", "text": ARCHIVED_SCREEN_PLACEHOLDER}
    for idx in (2, 3, 4):
        assert updated[idx].content[1].get("type") == "image_url"


def test_keep_one_retains_only_the_newest_screenshot():
    messages = [_image_user() for _ in range(3)]
    ctx = _fake_message_context(messages)

    rail = MultimodalContextSummarizerRail(1)
    rail._archive_old_screenshot_images(ctx)

    updated = ctx.context.get_messages()
    assert updated[0].content[1].get("type") == "text"
    assert updated[1].content[1].get("type") == "text"
    assert updated[2].content[1].get("type") == "image_url"


def test_default_protects_no_message_names():
    messages = [_image_user(name="multimodal_skill") for _ in range(4)]
    ctx = _fake_message_context(messages)

    rail = MultimodalContextSummarizerRail(screenshots_to_keep=1)
    rail._archive_old_screenshot_images(ctx)

    updated = ctx.context.get_messages()
    assert [m.content[1].get("type") for m in updated] == ["text", "text", "text", "image_url"]


def test_protected_names_are_excluded_from_retention_budget():
    protected = [_image_user(name="protected_turn") for _ in range(4)]
    unnamed = [_image_user() for _ in range(2)]
    ctx = _fake_message_context(protected + unnamed)

    rail = MultimodalContextSummarizerRail(
        screenshots_to_keep=1,
        protected_user_message_names={"protected_turn"},
    )
    rail._archive_old_screenshot_images(ctx)

    updated = ctx.context.get_messages()
    for idx in range(4):
        assert updated[idx].content[1].get("type") == "image_url"
    assert updated[4].content[1].get("type") == "text"
    assert updated[5].content[1].get("type") == "image_url"


def test_mobile_shim_subclasses_shared_rail_and_protects_skill_turns():
    from openjiuwen.harness.tools.mobile_gui.rails.multimodal_context_summarizer_rail import (
        ARCHIVED_SCREEN_PLACEHOLDER as shim_placeholder,
    )
    from openjiuwen.harness.tools.mobile_gui.rails.multimodal_context_summarizer_rail import (
        MultimodalContextSummarizerRail as MobileRail,
    )
    from openjiuwen.harness.tools.mobile_gui.rails.multimodal_skill_read_rail import (
        USER_MESSAGES_PROTECTED_FROM_SCREENSHOT_ARCHIVE,
    )

    assert shim_placeholder is ARCHIVED_SCREEN_PLACEHOLDER
    assert issubclass(MobileRail, MultimodalContextSummarizerRail)
    rail = MobileRail(screenshots_to_keep=3)
    assert rail._protected_user_message_names == frozenset(USER_MESSAGES_PROTECTED_FROM_SCREENSHOT_ARCHIVE)
