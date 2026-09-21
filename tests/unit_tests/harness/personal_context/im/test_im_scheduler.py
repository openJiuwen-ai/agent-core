"""Unit tests for the IM learning scheduler + PersonalContext integration."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from openjiuwen.harness.personal_context.config import PersonalContextConfig
from openjiuwen.harness.personal_context.im.models import (
    ImLearningMessage,
    ImLearningTarget,
    ImMessageBatch,
)
from openjiuwen.harness.personal_context.im.scheduler import (
    ImLearningScheduler,
    open_im_context_db,
)
from openjiuwen.harness.personal_context.im.stage_runs import (
    begin_stage_run,
    source_key_for,
)
from openjiuwen.harness.personal_context.personal_context import PersonalContext

BASE_MS = 1_700_000_000_000


class ScriptedSource:
    """Returns fresh messages per call to exercise steady-state watermarking."""

    def __init__(self) -> None:
        self.calls = 0

    async def fetch_messages(self, target, cursor=None):
        self.calls += 1
        messages = tuple(
            ImLearningMessage(
                channel_id=target.channel_id,
                msg_id=f"m{self.calls}-{i}",
                conversation_external_id=target.external_id,
                content_text=f"消息 {self.calls}-{i}",
                sent_at=BASE_MS + self.calls * 100 + i,
            )
            for i in range(2)
        )
        return ImMessageBatch(messages=messages, next_cursor=None)


def _target() -> ImLearningTarget:
    return ImLearningTarget(channel_id="welink", kind="group", external_id="g1", title="项目群")


class TestImLearningScheduler:
    @pytest.mark.asyncio
    async def test_start_cycle_stop_and_status(self, tmp_path) -> None:
        source = ScriptedSource()
        scheduler = ImLearningScheduler(
            source=source,
            home=tmp_path,
            targets=(_target(),),
            since_ms=0,
            fetch_interval_seconds=3600,
            index_fallback_seconds=3600,
        )
        await scheduler.start()
        assert scheduler.is_running()
        await asyncio.sleep(0.3)  # first cycle fires immediately
        assert source.calls == 1
        status = await scheduler.read_status()
        assert status["running"] is True
        assert status["last_cycle_targets"] == 1
        assert status["last_cycle_persisted"] == 2
        assert status["last_cycle_errors"] == 0
        assert status["backfill"]["ready"] is True

        await scheduler.stop()
        assert not scheduler.is_running()
        # status remains readable after stop (connection reopened read-only)
        status2 = await scheduler.read_status()
        assert status2["running"] is False
        assert status2["last_cycle_persisted"] == 2

    @pytest.mark.asyncio
    async def test_trigger_now_runs_extra_cycle(self, tmp_path) -> None:
        source = ScriptedSource()
        scheduler = ImLearningScheduler(
            source=source,
            home=tmp_path,
            targets=(_target(),),
            since_ms=0,
            fetch_interval_seconds=3600,
            index_fallback_seconds=3600,
        )
        await scheduler.start()
        await asyncio.sleep(0.3)
        assert source.calls == 1
        assert await scheduler.trigger_now() is True
        await asyncio.sleep(0.3)
        assert source.calls == 2
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_lease_conflict_skips_target(self, tmp_path) -> None:
        source = ScriptedSource()
        scheduler = ImLearningScheduler(
            source=source,
            home=tmp_path,
            targets=(_target(),),
            since_ms=0,
            fetch_interval_seconds=3600,
        )
        await scheduler.start()
        await asyncio.sleep(0.2)
        # Simulate another process holding a valid fetch lease for the target.
        conn = open_im_context_db(tmp_path)
        key = source_key_for("welink", "group", "g1")
        begin_stage_run(conn, stage="fetch", source_key=key, lease_ttl_ms=3_600_000)
        try:
            calls_before = source.calls
            assert await scheduler.trigger_now() is True
            await asyncio.sleep(0.4)
            assert source.calls == calls_before  # skipped: lease held
            status = await scheduler.read_status()
            assert status["last_cycle_errors"] == 0
        finally:
            conn.close()
            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_persists_to_dedicated_db(self, tmp_path) -> None:
        source = ScriptedSource()
        scheduler = ImLearningScheduler(source=source, home=tmp_path, targets=(_target(),), since_ms=0)
        await scheduler.start()
        await asyncio.sleep(0.3)
        await scheduler.stop()
        db = tmp_path / "im" / "im_context.db"
        assert db.is_file()
        conn = sqlite3.connect(str(db))
        try:
            count = conn.execute("SELECT COUNT(*) FROM im_messages").fetchone()[0]
            assert count == 2
        finally:
            conn.close()


class TestPersonalContextIntegration:
    @staticmethod
    def _config() -> PersonalContextConfig:
        return PersonalContextConfig.from_dict(
            {
                "strategy_profile": "rules",
                "fetch_services": [],
                "im_learning": {
                    "enabled": True,
                    "targets": [{"channel_id": "welink", "kind": "group", "external_id": "g1"}],
                },
            }
        )

    @pytest.mark.asyncio
    async def test_disabled_without_source_or_config(self, tmp_path) -> None:
        pc = PersonalContext(home=tmp_path)  # no im_learning_source
        await pc.set_configuration(self._config())
        await pc.start_collection()
        try:
            status = await pc.get_im_learning_status()
            assert status["running"] is False
            assert status["enabled"] is True
            assert status["source_injected"] is False
            assert await pc.run_im_learning_now() is False
        finally:
            await pc.stop_collection()

    @pytest.mark.asyncio
    async def test_full_lifecycle_with_source(self, tmp_path) -> None:
        source = ScriptedSource()
        pc = PersonalContext(home=tmp_path, im_learning_source=source)
        await pc.set_configuration(self._config())
        await pc.start_collection()
        try:
            snapshot = await pc.snapshot()
            assert snapshot.state == "RUNNING"
            await asyncio.sleep(0.3)
            status = await pc.get_im_learning_status()
            assert status["running"] is True
            assert status["last_cycle_persisted"] == 2
            assert await pc.run_im_learning_now() is True
            await asyncio.sleep(0.3)
            assert source.calls == 2
        finally:
            await pc.stop_collection()
        status = await pc.get_im_learning_status()
        assert status["running"] is False
        assert (await pc.snapshot()).state == "STOPPED"

    @pytest.mark.asyncio
    async def test_default_config_keeps_im_disabled(self, tmp_path) -> None:
        source = ScriptedSource()
        pc = PersonalContext(home=tmp_path, im_learning_source=source)
        await pc.set_configuration(PersonalContextConfig.from_dict({"strategy_profile": "rules", "fetch_services": []}))
        await pc.start_collection()
        try:
            status = await pc.get_im_learning_status()
            assert status["enabled"] is False
            assert status["running"] is False
        finally:
            await pc.stop_collection()
