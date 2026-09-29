"""PersonalContext distill scheduler mount tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openjiuwen.harness.personal_context.config import PersonalContextConfig
from openjiuwen.harness.personal_context.distill.corpus import FixtureCorpus
from openjiuwen.harness.personal_context.distill.runner import DistillRunResult
from openjiuwen.harness.personal_context.personal_context import PersonalContext


def _pc_config(*, distill_enabled: bool, poll_seconds: float = 0.05) -> PersonalContextConfig:
    return PersonalContextConfig.from_dict(
        {
            "collection_enabled": True,
            "agent_use_enabled": False,
            "strategy_profile": "rules",
            "model_client": None,
            "model_request": None,
            "fetch_services": [],
            "distill": {
                "enabled": distill_enabled,
                "interval_seconds": 0.001,
                "message_threshold": 100,
                "lease_seconds": 60,
                "poll_seconds": poll_seconds,
            },
        }
    )


class _FakeRunner:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, home: str, *, window_end_ms: int, **kwargs) -> DistillRunResult:
        self.calls += 1
        return DistillRunResult(
            job_id=f"job-{self.calls}",
            status="success",
            window_start_ms=0,
            window_end_ms=window_end_ms,
            message_count=0,
            sampled=False,
        )


@pytest.mark.asyncio
async def test_personal_context_starts_distill_loop_when_enabled_and_injected(tmp_path: Path):
    pc = PersonalContext(home=tmp_path)
    await pc.set_configuration(_pc_config(distill_enabled=True))
    runner = _FakeRunner()
    pc.set_distill_corpus(FixtureCorpus([]))
    pc.set_distill_runner(runner)

    await pc.activate_runtime()
    try:
        for _ in range(40):
            if runner.calls >= 1:
                break
            await asyncio.sleep(0.05)
        assert runner.calls >= 1
        assert pc._distill_task is not None
        assert not pc._distill_task.done()
    finally:
        await pc.deactivate_runtime(timeout_seconds=5.0)
    assert pc._distill_task is None


@pytest.mark.asyncio
async def test_personal_context_skips_distill_loop_without_corpus(tmp_path: Path):
    pc = PersonalContext(home=tmp_path)
    await pc.set_configuration(_pc_config(distill_enabled=True))
    await pc.activate_runtime()
    try:
        assert pc._distill_task is None
    finally:
        await pc.deactivate_runtime(timeout_seconds=5.0)


@pytest.mark.asyncio
async def test_personal_context_skips_distill_loop_when_disabled(tmp_path: Path):
    pc = PersonalContext(home=tmp_path)
    await pc.set_configuration(_pc_config(distill_enabled=False))
    pc.set_distill_corpus(FixtureCorpus([]))
    pc.set_distill_runner(_FakeRunner())
    await pc.activate_runtime()
    try:
        assert pc._distill_task is None
    finally:
        await pc.deactivate_runtime(timeout_seconds=5.0)


@pytest.mark.asyncio
async def test_personal_context_rejects_distill_inject_while_running(tmp_path: Path):
    pc = PersonalContext(home=tmp_path)
    await pc.set_configuration(_pc_config(distill_enabled=False))
    await pc.activate_runtime()
    try:
        with pytest.raises(Exception, match="while stopped"):
            pc.set_distill_corpus(FixtureCorpus([]))
        with pytest.raises(Exception, match="while stopped"):
            pc.set_distill_runner(_FakeRunner())
    finally:
        await pc.deactivate_runtime(timeout_seconds=5.0)
