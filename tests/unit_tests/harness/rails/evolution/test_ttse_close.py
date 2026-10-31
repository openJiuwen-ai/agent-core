# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TTSE close keeps real writers and both kinds of background work visible."""

import asyncio
from types import SimpleNamespace

import pytest

from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.ttse.config import TTSEConfig
from openjiuwen.core.common.background_tasks import BackgroundTask
from openjiuwen.harness.rails.evolution.ttse_rail import TTSERail

pytestmark = pytest.mark.level0


def _rail(tmp_path):
    return TTSERail(
        llm=object(), model="test",
        ttse_config=TTSEConfig(store_path=str(tmp_path / "bank.json")),
        trajectory_span_processor=TrajectorySpanProcessor(),
    )


@pytest.mark.asyncio
async def test_close_timeout_retains_induction_and_dream_until_they_really_exit(tmp_path):
    rail = _rail(tmp_path)
    released = asyncio.Event()
    writes = []

    async def writer(label):
        await released.wait()
        writes.append(label)

    induction = asyncio.create_task(writer("induce"))
    dream = asyncio.create_task(writer("dream"))
    handle = BackgroundTask.from_asyncio_task(induction, group="evolution")
    rail._bg_tasks.add(handle)
    rail._dream_task = dream
    with pytest.raises(TimeoutError, match="closing_timeout"):
        await rail.aclose(timeout=0.001, grace_timeout=0.001)
    assert rail.closing
    assert rail._bg_tasks == {handle}
    assert not induction.done() and not dream.done()
    assert not rail._allow_evolution_trigger(None, None)
    rail._schedule_dream()
    assert rail._dream_task is dream
    released.set()
    await rail.aclose(timeout=1)
    assert set(writes) == {"induce", "dream"}
    assert rail._bg_tasks == set() and rail._dream_task is None
    await rail.aclose(timeout=0)


@pytest.mark.asyncio
async def test_close_removes_only_tracked_ttse_catalog_sections(tmp_path):
    rail = _rail(tmp_path)
    removed = []

    async def clear_section(**kwargs):
        removed.append(kwargs)

    rail._attachment_manager = SimpleNamespace(clear_section=clear_section)
    rail._catalog_sessions.update({"session-a", "session-b"})
    await rail.aclose()
    assert {entry["session_id"] for entry in removed} == {"session-a", "session-b"}
    assert {entry["section"] for entry in removed} == {"ttse_catalog"}
