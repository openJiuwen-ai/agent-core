# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for ExperienceTracker presentation accounting."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionPatch,
    EvolutionRecord,
    EvolutionTarget,
    UsageStats,
)
from openjiuwen.agent_evolving.experience.tracker import ExperienceTracker


def _make_body_record(record_id: str = "ev_body") -> EvolutionRecord:
    record = EvolutionRecord.make(
        source="test",
        context="ctx",
        change=EvolutionPatch(
            section="Troubleshooting",
            action="append",
            content="useful tip",
            target=EvolutionTarget.BODY,
        ),
        score=0.8,
    )
    record.id = record_id
    record.usage_stats = UsageStats()
    return record


@pytest.mark.asyncio
async def test_record_presented_records_counts_repeat_without_queue_dup():
    """Repeat presentation increments times_presented but keeps one queue entry."""
    body_record = _make_body_record()
    store = Mock()
    store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[body_record]))
    store.update_record_scores = AsyncMock()

    tracker = ExperienceTracker(store=store, scorer=Mock(), eval_interval=5)
    session = SimpleNamespace()

    await tracker.record_presented_records(
        session=session,
        skill_name="sk",
        presentation_snippet="### [ev_body]",
        record_ids=["ev_body"],
    )
    await tracker.record_presented_records(
        session=session,
        skill_name="sk",
        presentation_snippet="### [ev_body] again",
        record_ids=["ev_body"],
    )

    assert store.update_record_scores.await_count == 2
    second_updates = store.update_record_scores.await_args_list[1].args[1]
    assert second_updates["ev_body"]["usage_stats"]["times_presented"] == 2

    entries = tracker.get_session_presented_records(session)
    assert len(entries) == 1
    assert entries[0][1].id == "ev_body"
    assert entries[0][1].usage_stats is not None
    assert entries[0][1].usage_stats.times_presented == 2
