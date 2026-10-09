"""Unit tests for distill schedule: due, lease, tick, corpus count."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from openjiuwen.harness.personal_context.distill.corpus import FixtureCorpus
from openjiuwen.harness.personal_context.distill.runner import DistillRunResult
from openjiuwen.harness.personal_context.distill.schedule import (
    DistillScheduleConfig,
    evaluate_distill_due,
    run_distill_scheduler_loop,
    tick_distill_schedule,
)
from openjiuwen.harness.personal_context.distill.store import (
    get_cursor_ms,
    get_last_attempt_at_ms,
    set_last_attempt_at_ms,
    try_claim_distill_lease,
)
from openjiuwen.harness.personal_context.distill.types import CorpusMessage

BASE_MS = 1_700_000_000_000


def _msg(msg_id: str, offset_ms: int, *, eligible: int = 1, text: str = "hello") -> CorpusMessage:
    return CorpusMessage(
        id=msg_id,
        channel_id="welink",
        conversation_id="c1",
        content_text=text,
        sent_at_ms=BASE_MS + offset_ms,
        is_self=True,
        learning_eligible=eligible,
    )


class _FakeRunner:
    def __init__(self, *, status: str = "success", message_count: int = 0) -> None:
        self.calls: list[dict] = []
        self.status = status
        self.message_count = message_count

    async def __call__(self, home: str, *, window_end_ms: int, **kwargs) -> DistillRunResult:
        self.calls.append({"home": home, "window_end_ms": window_end_ms, **kwargs})
        return DistillRunResult(
            job_id=f"job-{len(self.calls)}",
            status=self.status,
            window_start_ms=0,
            window_end_ms=window_end_ms,
            message_count=self.message_count,
            sampled=False,
            error="boom" if self.status == "failed" else None,
        )


def test_fixture_corpus_count_eligible_since():
    corpus = FixtureCorpus(
        [
            _msg("a", 1_000),
            _msg("b", 2_000, eligible=0),
            _msg("c", 3_000, text="   "),
            _msg("d", 4_000),
            _msg("e", 10_000),
        ]
    )
    assert corpus.count_eligible_since(cursor_ms=BASE_MS + 1_500, until_ms=BASE_MS + 5_000) == 1
    assert corpus.count_eligible_since(cursor_ms=0, until_ms=BASE_MS + 5_000) == 2


def test_evaluate_period_and_volume_due():
    config = DistillScheduleConfig(
        enabled=True,
        interval_ms=10_000,
        message_threshold=3,
        lease_ms=60_000,
    )
    # Cool-down satisfied but below threshold → not due
    due = evaluate_distill_due(
        enabled=config.enabled,
        now_ms=BASE_MS + 10_000,
        last_attempt_at_ms=BASE_MS,
        interval_ms=config.interval_ms,
        pending_count=0,
        message_threshold=config.message_threshold,
    )
    assert due.due is False
    assert due.period_due is True
    assert due.volume_due is False

    # Volume ok but cool-down not → not due
    due2 = evaluate_distill_due(
        enabled=True,
        now_ms=BASE_MS + 1_000,
        last_attempt_at_ms=BASE_MS,
        interval_ms=10_000,
        pending_count=3,
        message_threshold=3,
    )
    assert due2.due is False
    assert due2.period_due is False
    assert due2.volume_due is True

    # Both ok → due
    due_both = evaluate_distill_due(
        enabled=True,
        now_ms=BASE_MS + 10_000,
        last_attempt_at_ms=BASE_MS,
        interval_ms=10_000,
        pending_count=3,
        message_threshold=3,
    )
    assert due_both.due is True
    assert due_both.period_due is True
    assert due_both.volume_due is True

    # Never attempted: cool-down ok; still needs volume
    due_first = evaluate_distill_due(
        enabled=True,
        now_ms=BASE_MS,
        last_attempt_at_ms=0,
        interval_ms=10_000,
        pending_count=3,
        message_threshold=3,
    )
    assert due_first.due is True
    assert due_first.period_due is True

    due3 = evaluate_distill_due(
        enabled=False,
        now_ms=BASE_MS + 100_000,
        last_attempt_at_ms=0,
        interval_ms=1,
        pending_count=100,
        message_threshold=1,
    )
    assert due3.due is False


@pytest.mark.asyncio
async def test_tick_period_due_calls_runner_once(tmp_path: Path):
    """Cooldown + volume both ok → ran; immediate re-tick skips (cooldown)."""
    home = str(tmp_path)
    set_last_attempt_at_ms(home, BASE_MS)
    runner = _FakeRunner(message_count=3)
    config = DistillScheduleConfig(
        enabled=True,
        interval_ms=5_000,
        message_threshold=3,
        lease_ms=60_000,
    )
    corpus = FixtureCorpus([_msg("a", 1_000), _msg("b", 2_000), _msg("c", 3_000)])

    first = await tick_distill_schedule(
        home,
        now_ms=BASE_MS + 5_000,
        corpus=corpus,
        run_job=runner,
        config=config,
    )
    assert first.action == "ran"
    assert len(runner.calls) == 1

    second = await tick_distill_schedule(
        home,
        now_ms=BASE_MS + 6_000,
        corpus=corpus,
        run_job=runner,
        config=config,
    )
    assert second.action == "skipped"
    assert second.reason == "cooldown"
    assert len(runner.calls) == 1
    assert get_last_attempt_at_ms(home) == BASE_MS + 5_000


@pytest.mark.asyncio
async def test_tick_volume_due_calls_runner(tmp_path: Path):
    home = str(tmp_path)
    # Cool-down must be satisfied (AND); never-attempted is enough.
    set_last_attempt_at_ms(home, 0)
    runner = _FakeRunner(message_count=2)
    config = DistillScheduleConfig(
        enabled=True,
        interval_ms=86_400_000,
        message_threshold=2,
        lease_ms=60_000,
    )
    corpus = FixtureCorpus([_msg("a", 1_000), _msg("b", 2_000)])
    result = await tick_distill_schedule(
        home,
        now_ms=BASE_MS + 3_000,
        corpus=corpus,
        run_job=runner,
        config=config,
    )
    assert result.action == "ran"
    assert len(runner.calls) == 1
    assert runner.calls[0]["window_end_ms"] == BASE_MS + 3_000


@pytest.mark.asyncio
async def test_tick_below_threshold_skips(tmp_path: Path):
    home = str(tmp_path)
    set_last_attempt_at_ms(home, 0)  # cool-down ok; volume fails
    runner = _FakeRunner()
    config = DistillScheduleConfig(
        enabled=True,
        interval_ms=86_400_000,
        message_threshold=5,
        lease_ms=60_000,
    )
    corpus = FixtureCorpus([_msg("a", 1_000)])
    result = await tick_distill_schedule(
        home,
        now_ms=BASE_MS + 2_000,
        corpus=corpus,
        run_job=runner,
        config=config,
    )
    assert result.action == "skipped"
    assert result.reason == "below_threshold"
    assert runner.calls == []


@pytest.mark.asyncio
async def test_tick_cooldown_skips_even_if_volume_ok(tmp_path: Path):
    home = str(tmp_path)
    set_last_attempt_at_ms(home, BASE_MS)
    runner = _FakeRunner()
    config = DistillScheduleConfig(
        enabled=True,
        interval_ms=86_400_000,
        message_threshold=2,
        lease_ms=60_000,
    )
    corpus = FixtureCorpus([_msg("a", 1_000), _msg("b", 2_000)])
    result = await tick_distill_schedule(
        home,
        now_ms=BASE_MS + 3_000,
        corpus=corpus,
        run_job=runner,
        config=config,
    )
    assert result.action == "skipped"
    assert result.reason == "cooldown"
    assert runner.calls == []
    assert get_last_attempt_at_ms(home) == BASE_MS


@pytest.mark.asyncio
async def test_tick_lease_blocks_second_run_until_expired(tmp_path: Path):
    home = str(tmp_path)
    runner = _FakeRunner()
    config = DistillScheduleConfig(
        enabled=True,
        interval_ms=1,
        message_threshold=1,
        lease_ms=10_000,
    )
    corpus = FixtureCorpus([_msg("a", 500)])
    claimed = try_claim_distill_lease(home, now_ms=BASE_MS, lease_ms=10_000)
    assert claimed is not None

    blocked = await tick_distill_schedule(
        home,
        now_ms=BASE_MS + 1_000,
        corpus=corpus,
        run_job=runner,
        config=config,
    )
    assert blocked.action == "skipped"
    assert blocked.reason == "lease_held"
    assert runner.calls == []

    after_expire = await tick_distill_schedule(
        home,
        now_ms=BASE_MS + 11_000,
        corpus=corpus,
        run_job=runner,
        config=config,
    )
    assert after_expire.action == "ran"
    assert len(runner.calls) == 1


@pytest.mark.asyncio
async def test_tick_failed_runner_does_not_write_cursor(tmp_path: Path):
    home = str(tmp_path)
    runner = _FakeRunner(status="failed")
    config = DistillScheduleConfig(
        enabled=True,
        interval_ms=1,
        message_threshold=1,
        lease_ms=60_000,
    )
    await tick_distill_schedule(
        home,
        now_ms=BASE_MS + 2_000,
        corpus=FixtureCorpus([_msg("a", 1_000)]),
        run_job=runner,
        config=config,
    )
    assert get_cursor_ms(home) == 0
    cursor_path = Path(home) / "im" / "distill" / "cursor.json"
    assert not cursor_path.is_file()
    schedule_path = Path(home) / "im" / "distill" / "schedule.json"
    payload = json.loads(schedule_path.read_text(encoding="utf-8"))
    assert "persona_id" not in payload
    assert payload["last_attempt_at_ms"] == BASE_MS + 2_000


class _BoomThenOkRunner:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, home: str, *, window_end_ms: int, **kwargs) -> DistillRunResult:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient boom")
        return DistillRunResult(
            job_id=f"job-{self.calls}",
            status="success",
            window_start_ms=0,
            window_end_ms=window_end_ms,
            message_count=0,
            sampled=False,
        )


@pytest.mark.asyncio
async def test_scheduler_loop_continues_after_tick_exception(tmp_path: Path):
    home = str(tmp_path)
    stop = asyncio.Event()
    runner = _BoomThenOkRunner()
    config = DistillScheduleConfig(
        enabled=True,
        interval_ms=1,
        message_threshold=1,
        lease_ms=60_000,
        poll_seconds=0.02,
    )
    clock = {"t": BASE_MS}
    corpus = FixtureCorpus([_msg("a", 5_000)])  # eligible for all tick now_ms > BASE_MS+5k

    def now_ms() -> int:
        clock["t"] += 10_000
        return clock["t"]

    task = asyncio.create_task(
        run_distill_scheduler_loop(
            home,
            stop,
            get_corpus=lambda: corpus,
            get_runner=lambda: runner,
            config=config,
            now_ms=now_ms,
        )
    )
    try:
        for _ in range(50):
            if runner.calls >= 2:
                break
            await asyncio.sleep(0.05)
        assert runner.calls >= 2
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)
