"""Offline public-lifecycle regression; no real Gateway or remote provider."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from openjiuwen.harness.personal_context import personal_context as core_module
from openjiuwen.harness.personal_context.config import PersonalContextConfig
from openjiuwen.harness.personal_context.fetch.local_files import LocalFilesFetchService
from openjiuwen.harness.personal_context.personal_context import PersonalContext


@pytest.mark.asyncio
async def test_failed_service_does_not_stop_healthy_real_pipeline_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    healthy_started = asyncio.Event()
    release_healthy = asyncio.Event()

    class ControlledLocalFiles(LocalFilesFetchService):
        async def prepare_run(
            self, *, run_id: str, run_started_at: datetime, cursor: dict[str, object] | None
        ) -> tuple[dict[str, object], ...]:
            if self._config.service_id == "failed":
                await healthy_started.wait()
                raise RuntimeError("injected service failure before healthy publication")
            healthy_started.set()
            await release_healthy.wait()
            return await super().prepare_run(run_id=run_id, run_started_at=run_started_at, cursor=cursor)

    source_root = tmp_path / "input"
    source_root.mkdir()
    body_marker = "HEALTHY_PUBLICATION_AFTER_OTHER_SERVICE_FAILURE"
    (source_root / "healthy.md").write_text(f"# 健康服务隔离验证\n\n{body_marker}\n", encoding="utf-8")
    config = PersonalContextConfig.from_dict(
        {
            "collection_enabled": True,
            "agent_use_enabled": False,
            "strategy_profile": "rules",
            "model_client": None,
            "model_request": None,
            "fetch_services": [
                {
                    "service_id": service_id,
                    "provider": "local_files",
                    "enabled": True,
                    "interval_seconds": 3600.0,
                    "time_range": {"mode": "all"},
                    "source": {"root_dir": str(source_root)},
                    "credentials": {},
                }
                for service_id in ("failed", "healthy")
            ],
        }
    )
    monkeypatch.setitem(core_module._PROVIDER_TYPES, "local_files", ControlledLocalFiles)
    runtime_home = tmp_path / "runtime"
    core = PersonalContext(home=runtime_home)
    await core.set_configuration(config)
    core._write_cursor("failed", {"_selection": {"completed": []}})
    failed_cursor = runtime_home / "state" / "cursors" / "failed.json"
    old_failed_cursor = failed_cursor.read_bytes()
    healthy_cursor = failed_cursor.with_name("healthy.json")
    await core.activate_runtime()
    try:
        accepted = await core.run_fetch()
        assert set(accepted["service_ids"]) == {"failed", "healthy"}
        tasks = dict(core._manual_fetch_tasks)
        await asyncio.wait_for(tasks["failed"], timeout=5)

        failed_snapshot = await core.snapshot()
        assert failed_snapshot.fetch_run_progress["failed"]["run_state"] == "failed"
        assert failed_snapshot.fetch_run_progress["healthy"]["run_state"] == "running"
        assert not tasks["healthy"].done()
        assert not healthy_cursor.exists()
        assert core._pipeline_service is not None and core._pipeline_service.is_running()

        release_healthy.set()
        await asyncio.wait_for(tasks["healthy"], timeout=10)
        completed_snapshot = await core.snapshot()
        assert completed_snapshot.fetch_run_progress["healthy"]["run_state"] == "succeeded"
        assert completed_snapshot.fetch_run_progress["failed"]["run_state"] == "failed"
        assert completed_snapshot.context_ready
        assert completed_snapshot.pipeline_queue_size == 0
        assert core._pipeline_service.is_running()
        assert failed_cursor.read_bytes() == old_failed_cursor
        assert healthy_cursor.exists()
        assert core._read_cursor("healthy")["_selection"]["completed"]
        pages = [
            path for path in (runtime_home / "workspace" / "context").rglob("*.md") if path.name != "description.md"
        ]
        assert len(pages) == 1
        assert body_marker in pages[0].read_text(encoding="utf-8")
        assert not list((runtime_home / "workspace" / "sandboxes").rglob("*"))
        assert (await core.get_graph())["context_ready"] is True
    finally:
        release_healthy.set()
        await core.deactivate_runtime(timeout_seconds=5)
