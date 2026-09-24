"""Schedule tick consumes SqliteImCorpus.count_eligible_since over im_context.db."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from openjiuwen.harness.personal_context.config import PersonalContextConfig
from openjiuwen.harness.personal_context.distill.runner import DistillRunResult
from openjiuwen.harness.personal_context.distill.schedule import (
    DistillScheduleConfig,
    tick_distill_schedule,
)
from openjiuwen.harness.personal_context.distill.sqlite_corpus import SqliteImCorpus
from openjiuwen.harness.personal_context.distill.store import set_last_attempt_at_ms
from openjiuwen.harness.personal_context.im.models import ImLearningMessage, ImLearningTarget
from openjiuwen.harness.personal_context.im.normalize import normalize_batch
from openjiuwen.harness.personal_context.im.persist import persist_batch
from openjiuwen.harness.personal_context.im.scheduler import open_im_context_db

BASE_MS = 1_700_000_000_000


class _FakeRunner:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, home: str, *, window_end_ms: int, **kwargs) -> DistillRunResult:
        self.calls.append({"home": home, "window_end_ms": window_end_ms, **kwargs})
        return DistillRunResult(
            job_id=f"job-{len(self.calls)}",
            status="success",
            window_start_ms=0,
            window_end_ms=window_end_ms,
            message_count=0,
            sampled=False,
        )


def _message(msg_id: str, *, sent_at: int, text: str) -> ImLearningMessage:
    return ImLearningMessage(
        channel_id="welink",
        msg_id=msg_id,
        conversation_external_id="g1",
        content_text=text,
        sent_at=sent_at,
        is_self=True,
        sender_account="me",
        sender_name="我",
    )


def _seed_home(home: Path, messages: list[ImLearningMessage], eligible_map: dict[str, int]) -> None:
    target = ImLearningTarget(
        channel_id="welink",
        kind="group",
        external_id="g1",
        title="项目群",
    )
    conn = open_im_context_db(home)
    try:
        batch = normalize_batch(
            target=target,
            messages=messages,
            fetched_at_ms=BASE_MS + 10_000,
            learning_eligible_map=eligible_map,
        )
        persist_batch(conn, batch)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_tick_volume_due_uses_sqlite_im_corpus_count(tmp_path: Path):
    home = tmp_path / "pc-home"
    home.mkdir()
    _seed_home(
        home,
        [
            _message("m1", sent_at=BASE_MS + 1_000, text="消息一"),
            _message("m2", sent_at=BASE_MS + 2_000, text="消息二"),
            _message("m3", sent_at=BASE_MS + 3_000, text="闲聊"),
        ],
        eligible_map={"m1": 1, "m2": 1, "m3": 0},
    )
    home_str = str(home)
    # Period not due; volume due via two eligible rows since cursor 0.
    set_last_attempt_at_ms(home_str, BASE_MS + 100_000)
    now_ms = BASE_MS + 5_000
    corpus = SqliteImCorpus(home_str)
    assert corpus.count_eligible_since(cursor_ms=0, until_ms=now_ms) == 2

    runner = _FakeRunner()
    config = DistillScheduleConfig(
        enabled=True,
        interval_ms=86_400_000,
        message_threshold=2,
        lease_ms=60_000,
    )
    result = await tick_distill_schedule(
        home_str,
        now_ms=now_ms,
        corpus=corpus,
        run_job=runner,
        config=config,
    )
    assert result.action == "ran"
    assert len(runner.calls) == 1
    assert runner.calls[0]["window_end_ms"] == now_ms
    assert (home / "im" / "distill" / "schedule.json").is_file()

    conn = sqlite3.connect(str(home / "im" / "im_context.db"))
    try:
        distill_stage = int(
            conn.execute(
                "SELECT COUNT(*) FROM im_stage_runs WHERE stage = ?",
                ("distill",),
            ).fetchone()[0]
        )
    finally:
        conn.close()
    assert distill_stage == 0


@pytest.mark.asyncio
async def test_tick_below_threshold_with_sqlite_im_corpus_skips(tmp_path: Path):
    home = tmp_path / "pc-home"
    home.mkdir()
    _seed_home(
        home,
        [_message("m1", sent_at=BASE_MS + 1_000, text="仅一条")],
        eligible_map={"m1": 1},
    )
    home_str = str(home)
    set_last_attempt_at_ms(home_str, BASE_MS + 100_000)
    runner = _FakeRunner()
    config = DistillScheduleConfig(
        enabled=True,
        interval_ms=86_400_000,
        message_threshold=5,
        lease_ms=60_000,
    )
    result = await tick_distill_schedule(
        home_str,
        now_ms=BASE_MS + 2_000,
        corpus=SqliteImCorpus(home_str),
        run_job=runner,
        config=config,
    )
    assert result.action == "skipped"
    assert result.reason == "not_due"
    assert runner.calls == []


def test_im_learning_and_distill_config_namespaces_are_independent():
    config = PersonalContextConfig.from_dict(
        {
            "collection_enabled": True,
            "agent_use_enabled": False,
            "strategy_profile": "rules",
            "model_client": None,
            "model_request": None,
            "fetch_services": [],
            "im_learning": {
                "enabled": True,
                "targets": [
                    {
                        "channel_id": "welink",
                        "kind": "group",
                        "external_id": "g1",
                        "title": "项目群",
                    }
                ],
                "fetch_interval_seconds": 600,
            },
            "distill": {
                "enabled": True,
                "interval_seconds": 86_400,
                "message_threshold": 50,
                "lease_seconds": 3_600,
            },
        }
    )
    assert config.im_learning.enabled is True
    assert config.im_learning.fetch_interval_seconds == 600
    assert config.distill.enabled is True
    assert config.distill.interval_seconds == 86_400
    assert config.distill.message_threshold == 50
