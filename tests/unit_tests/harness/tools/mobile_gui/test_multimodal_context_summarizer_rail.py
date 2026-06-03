# coding: utf-8
"""Tests for MultimodalContextSummarizerRail screenshot archiving."""

from __future__ import annotations

from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.harness.tools.mobile_gui.rails.multimodal_context_summarizer_rail import (
    ARCHIVED_SCREEN_PLACEHOLDER,
    MultimodalContextSummarizerRail,
)
from openjiuwen.harness.tools.mobile_gui.rails.multimodal_skill_read_rail import (
    MULTIMODAL_SKILL_USER_MESSAGE_NAME,
)

from tests.unit_tests.harness.tools.mobile_gui.conftest import fake_message_context


def _minimal_image_user(*, name: str | None) -> UserMessage:
    return UserMessage(
        name=name,
        content=[
            {"type": "text", "text": "stub"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,QQ=="}},
        ],
    )


def test_summarizer_does_not_archive_protected_skill_reference_users():
    """``multimodal_skill`` user turns are excluded from the screenshot retention budget."""
    messages = [_minimal_image_user(name=MULTIMODAL_SKILL_USER_MESSAGE_NAME) for _ in range(8)]
    ctx = fake_message_context(messages)

    rail = MultimodalContextSummarizerRail(screenshots_to_keep=3)
    rail._archive_old_screenshot_images(ctx)

    for msg in ctx.context.get_messages():
        assert msg.name == MULTIMODAL_SKILL_USER_MESSAGE_NAME
        image_block = msg.content[1]
        assert image_block.get("type") == "image_url"
        assert "base64" in image_block["image_url"]["url"]


def test_summarizer_archives_oldest_unnamed_screenshots_keeps_recent_and_protected():
    """Unnamed live screenshots beyond ``screenshots_to_keep`` become text placeholders."""
    unnamed = [_minimal_image_user(name=None) for _ in range(5)]
    protected = [_minimal_image_user(name=MULTIMODAL_SKILL_USER_MESSAGE_NAME)]
    ctx = fake_message_context(unnamed + protected)

    rail = MultimodalContextSummarizerRail(screenshots_to_keep=3)
    rail._archive_old_screenshot_images(ctx)

    updated = ctx.context.get_messages()
    assert len(updated) == 6

    # Indices 0–1 archived (5 unnamed, keep last 3 → archive first 2)
    for idx in (0, 1):
        archived_block = updated[idx].content[1]
        assert archived_block.get("type") == "text"
        assert ARCHIVED_SCREEN_PLACEHOLDER in archived_block["text"]

    # Indices 2–4 still carry image_url
    for idx in (2, 3, 4):
        assert updated[idx].content[1].get("type") == "image_url"

    # Protected tail unchanged
    assert updated[-1].name == MULTIMODAL_SKILL_USER_MESSAGE_NAME
    assert updated[-1].content[1].get("type") == "image_url"


def test_summarizer_noop_when_unnamed_count_at_limit():
    """Exactly ``screenshots_to_keep`` unnamed images are all retained."""
    unnamed = [_minimal_image_user(name=None) for _ in range(3)]
    ctx = fake_message_context(unnamed)
    rail = MultimodalContextSummarizerRail(screenshots_to_keep=3)
    rail._archive_old_screenshot_images(ctx)

    for msg in ctx.context.get_messages():
        assert msg.content[1].get("type") == "image_url"


def test_summarizer_noop_when_fewer_than_limit():
    messages = [_minimal_image_user(name=None) for _ in range(2)]
    ctx = fake_message_context(messages)
    rail = MultimodalContextSummarizerRail(screenshots_to_keep=3)
    rail._archive_old_screenshot_images(ctx)

    for msg in ctx.context.get_messages():
        assert msg.content[1].get("type") == "image_url"


def test_summarizer_ignores_assistant_messages_with_images():
    """Only user-role messages participate in screenshot retention."""
    from openjiuwen.core.foundation.llm import AssistantMessage

    assistant_img = AssistantMessage(
        content=[
            {"type": "text", "text": "thinking"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
        ]
    )
    users = [_minimal_image_user(name=None) for _ in range(5)]
    ctx = fake_message_context([assistant_img, *users])
    rail = MultimodalContextSummarizerRail(screenshots_to_keep=2)
    rail._archive_old_screenshot_images(ctx)

    updated = ctx.context.get_messages()
    assert updated[0].content[1].get("type") == "image_url"
    for idx in (1, 2, 3):
        assert updated[idx].content[1].get("type") == "text"
    for idx in (4, 5):
        assert updated[idx].content[1].get("type") == "image_url"
