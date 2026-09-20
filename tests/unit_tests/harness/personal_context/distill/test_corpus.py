"""Unit tests for distill FixtureCorpus filtering and downsampling."""

from __future__ import annotations

from openjiuwen.harness.personal_context.distill.corpus import (
    FixtureCorpus,
    default_fixture_messages,
)
from openjiuwen.harness.personal_context.distill.types import CorpusMessage

FIXTURE_END_MS = 1_700_000_010_000
BASE_MS = 1_700_000_000_000


def test_fixture_corpus_filters_ineligible_empty_and_window():
    corpus = FixtureCorpus(
        [
            *default_fixture_messages(),
            CorpusMessage(
                id="m-empty",
                channel_id="welink",
                conversation_id="conv-a",
                content_text="   ",
                sent_at_ms=BASE_MS + 6_000,
                is_self=True,
                learning_eligible=1,
            ),
            CorpusMessage(
                id="m-out",
                channel_id="welink",
                conversation_id="conv-a",
                content_text="窗外消息",
                sent_at_ms=FIXTURE_END_MS + 1,
                is_self=True,
                learning_eligible=1,
            ),
        ]
    )
    messages, sampled = corpus.list_messages(
        window_start_ms=0,
        window_end_ms=FIXTURE_END_MS,
        max_messages=800,
    )
    assert sampled is False
    assert len(messages) == 4
    assert all(message.learning_eligible == 1 for message in messages)
    assert all(message.content_text.strip() for message in messages)
    assert all(0 <= message.sent_at_ms < FIXTURE_END_MS for message in messages)
    ids = {message.id for message in messages}
    assert "m5" not in ids
    assert "m-empty" not in ids
    assert "m-out" not in ids


def test_fixture_corpus_downsamples_uniformly():
    corpus = FixtureCorpus(default_fixture_messages())
    sampled_messages, sampled = corpus.list_messages(
        window_start_ms=0,
        window_end_ms=FIXTURE_END_MS,
        max_messages=2,
    )
    assert sampled is True
    assert len(sampled_messages) == 2
    assert sampled_messages[0].id == "m1"
    assert sampled_messages[1].id == "m3"
