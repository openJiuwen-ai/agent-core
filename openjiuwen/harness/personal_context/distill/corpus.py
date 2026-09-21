"""Corpus ports: fixture for tests; production adapters inject CorpusPort."""

from __future__ import annotations

from typing import Protocol

from openjiuwen.harness.personal_context.distill.types import CorpusMessage


class CorpusPort(Protocol):
    def list_messages(
        self,
        *,
        window_start_ms: int,
        window_end_ms: int,
        max_messages: int,
    ) -> tuple[list[CorpusMessage], bool]:
        """Return (messages, sampled) in [window_start_ms, window_end_ms)."""

    def count_eligible_since(self, *, cursor_ms: int, until_ms: int) -> int:
        """Count eligible non-empty messages in [cursor_ms, until_ms)."""


def _eligible_in_window(
    messages: list[CorpusMessage],
    *,
    window_start_ms: int,
    window_end_ms: int,
) -> list[CorpusMessage]:
    selected = [
        message
        for message in messages
        if window_start_ms <= message.sent_at_ms < window_end_ms
        and message.learning_eligible == 1
        and str(message.content_text or "").strip()
    ]
    selected.sort(key=lambda item: item.sent_at_ms)
    return selected


def _downsample(
    selected: list[CorpusMessage],
    max_messages: int,
) -> tuple[list[CorpusMessage], bool]:
    if max_messages <= 0 or len(selected) <= max_messages:
        return selected, False
    step = len(selected) / max_messages
    picked = [selected[int(index * step)] for index in range(max_messages)]
    return picked, True


class FixtureCorpus:
    """In-memory corpus for unit tests."""

    def __init__(self, messages: list[CorpusMessage]):
        self._messages = list(messages)

    def list_messages(
        self,
        *,
        window_start_ms: int,
        window_end_ms: int,
        max_messages: int,
    ) -> tuple[list[CorpusMessage], bool]:
        selected = _eligible_in_window(
            self._messages,
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
        )
        return _downsample(selected, max_messages)

    def count_eligible_since(self, *, cursor_ms: int, until_ms: int) -> int:
        return len(
            _eligible_in_window(
                self._messages,
                window_start_ms=cursor_ms,
                window_end_ms=until_ms,
            )
        )


def default_fixture_messages() -> list[CorpusMessage]:
    """Small Chinese office-chat fixture for distill unit tests."""
    base = 1_700_000_000_000
    return [
        CorpusMessage(
            id="m1",
            channel_id="welink",
            conversation_id="conv-a",
            content_text="这块前端页面我来改，接口找后端对齐。",
            sent_at_ms=base + 1_000,
            is_self=True,
            sender_account="me",
            sender_name="我",
        ),
        CorpusMessage(
            id="m2",
            channel_id="welink",
            conversation_id="conv-a",
            content_text="帮我 review 一下这个 PR，重点看列表性能。",
            sent_at_ms=base + 2_000,
            is_self=False,
            sender_account="alice",
            sender_name="Alice",
        ),
        CorpusMessage(
            id="m3",
            channel_id="welink",
            conversation_id="conv-a",
            content_text="好的，我先看现有实现再给建议，今晚前回复。",
            sent_at_ms=base + 3_000,
            is_self=True,
            sender_account="me",
            sender_name="我",
        ),
        CorpusMessage(
            id="m4",
            channel_id="welink",
            conversation_id="conv-b",
            content_text="发版时间别口头承诺，等产品确认后再同步群里。",
            sent_at_ms=base + 4_000,
            is_self=True,
            sender_account="me",
            sender_name="我",
        ),
        CorpusMessage(
            id="m5",
            channel_id="welink",
            conversation_id="conv-b",
            content_text="闲聊：午饭吃啥",
            sent_at_ms=base + 5_000,
            is_self=True,
            sender_account="me",
            sender_name="我",
            learning_eligible=0,
        ),
    ]
