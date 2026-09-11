from __future__ import annotations

import asyncio
import errno
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.harness.personal_context import context_pipeline
from openjiuwen.harness.personal_context import personal_context as personal_context_module
from openjiuwen.harness.personal_context.config import PersonalContextConfig, PersonalContextFetchServiceConfig
from openjiuwen.harness.personal_context.fetch import local_files, retry as retry_module
from openjiuwen.harness.personal_context.fetch.base import ContextFetchService
from openjiuwen.harness.personal_context.fetch.cursor_selection import record_completed_candidates
from openjiuwen.harness.personal_context.fetch.local_files import LocalFilesFetchService
from openjiuwen.harness.personal_context.models import FetchBatch, RawChangeItem
from openjiuwen.harness.personal_context.personal_context import PersonalContext
from openjiuwen.harness.personal_context.source_metadata import read_source_metadata, source_id_for_locator


def _config(tmp_path: Path, *, interval: float = 0.01) -> PersonalContextConfig:
    return PersonalContextConfig.from_dict(
        {
            "collection_enabled": True,
            "agent_use_enabled": False,
            "strategy_profile": "rules",
            "model_client": None,
            "model_request": None,
            "fetch_services": [
                {
                    "service_id": "notes",
                    "provider": "local_files",
                    "enabled": True,
                    "interval_seconds": interval,
                    "time_range": {"mode": "all"},
                    "source": {"root_dir": str(tmp_path)},
                    "credentials": {},
                }
            ],
        }
    )


def _manual_config(
    tmp_path: Path,
    *,
    collection_enabled: bool = True,
    services: dict[str, bool] | None = None,
) -> PersonalContextConfig:
    service_states = services or {"notes": True}
    return PersonalContextConfig.from_dict(
        {
            "collection_enabled": collection_enabled,
            "agent_use_enabled": False,
            "strategy_profile": "rules",
            "model_client": None,
            "model_request": None,
            "fetch_services": [
                {
                    "service_id": service_id,
                    "provider": "local_files",
                    "enabled": service_enabled,
                    "interval_seconds": 3600.0,
                    "time_range": {"mode": "all"},
                    "source": {"root_dir": str(tmp_path / service_id)},
                    "credentials": {},
                }
                for service_id, service_enabled in service_states.items()
            ],
        }
    )


class _RunningPipeline:
    def __init__(self, *, running: bool = True) -> None:
        self.running = running

    def is_running(self) -> bool:
        return self.running

    async def stop(self, *, timeout_seconds: float) -> None:
        del timeout_seconds
        self.running = False


class _EmptyPreparedProvider(ContextFetchService):
    async def prepare_run(
        self,
        *,
        run_id: str,
        run_started_at: datetime,
        cursor: dict[str, object] | None,
    ) -> tuple[dict[str, object], ...]:
        del run_id, run_started_at, cursor
        return ()


class _BlockingManualProvider(_EmptyPreparedProvider):
    instances: dict[str, "_BlockingManualProvider"] = {}

    def __init__(self, config: PersonalContextFetchServiceConfig, *, home: Path) -> None:
        super().__init__(config, home=home)
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.fail = False
        self.commit_calls: list[str] = []
        self.abort_calls: list[str] = []
        type(self).instances[config.service_id] = self

    async def fetch(
        self,
        *,
        run_id: str,
        cursor: dict[str, object] | None,
        candidates: tuple[dict[str, object], ...],
    ):
        del run_id, cursor, candidates
        self.started.set()
        await self.release.wait()
        if self.fail:
            raise RuntimeError("manual fetch failed")
        yield FetchBatch(
            batch_id="manual",
            items=(),
            next_cursor={"done": True},
        )

    async def commit_run(self, *, run_id: str) -> None:
        self.commit_calls.append(run_id)

    async def abort_run(self, *, run_id: str) -> None:
        self.abort_calls.append(run_id)


async def _ready_manual_personal_context(tmp_path: Path, config: PersonalContextConfig) -> PersonalContext:
    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(config)
    personal_context._state = "RUNNING"
    personal_context._pipeline_service = _RunningPipeline()  # type: ignore[assignment]
    return personal_context


async def _finish_manual_tasks(personal_context: PersonalContext, service_ids: tuple[str, ...]) -> None:
    tasks = [personal_context._manual_fetch_tasks[service_id] for service_id in service_ids]
    for service_id in service_ids:
        provider = _BlockingManualProvider.instances[service_id]
        await asyncio.wait_for(provider.started.wait(), timeout=1.0)
        provider.release.set()
    await asyncio.gather(*tasks)


class _Provider(_EmptyPreparedProvider):
    fetch_calls = 0
    commit_calls: list[str] = []
    abort_calls: list[str] = []

    async def fetch(
        self,
        *,
        run_id: str,
        cursor: dict[str, object] | None,
        candidates: tuple[dict[str, object], ...],
    ):
        del cursor, candidates
        type(self).fetch_calls += 1
        yield FetchBatch(
            batch_id=f"batch-{type(self).fetch_calls}", items=(), next_cursor={"n": type(self).fetch_calls}
        )

    async def commit_run(self, *, run_id: str) -> None:
        type(self).commit_calls.append(run_id)

    async def abort_run(self, *, run_id: str) -> None:
        type(self).abort_calls.append(run_id)


def _run_candidate(index: int) -> dict[str, object]:
    return {
        "stable_id": f"item-{index}",
        "revision_id": f"revision-{index}",
        "candidate_time": "2026-08-25T00:00:00Z",
        "resource_lane": "document",
        "locator": f"file:///notes/item-{index}",
    }


def _run_item(index: int) -> RawChangeItem:
    return RawChangeItem(
        logical_id=f"notes/item-{index}",
        revision_id=f"revision-{index}",
        operation="upsert",
        title=f"Item {index}",
        content=f"Body {index}",
        original_ref=f"file:///notes/item-{index}",
    )


def _item_candidate(item: RawChangeItem) -> dict[str, object]:
    return {
        "stable_id": item.logical_id,
        "revision_id": item.revision_id,
        "candidate_time": "2026-08-25T00:00:00Z",
        "resource_lane": "test",
        "locator": item.original_ref,
    }


class _ProgressProvider(ContextFetchService):
    def __init__(self, config: PersonalContextFetchServiceConfig, *, home: Path) -> None:
        super().__init__(config, home=home)
        self.prepare_started = asyncio.Event()
        self.release_prepare = asyncio.Event()
        self.events: list[str] = []
        self.received_candidates: tuple[dict[str, object], ...] | None = None

    async def prepare_run(
        self,
        *,
        run_id: str,
        run_started_at: datetime,
        cursor: dict[str, object] | None,
    ) -> tuple[dict[str, object], ...]:
        del run_id, run_started_at, cursor
        self.events.append("prepare_started")
        self.prepare_started.set()
        await self.release_prepare.wait()
        self.events.append("prepare_returned")
        return tuple(_run_candidate(index) for index in range(20))

    async def fetch(
        self,
        *,
        run_id: str,
        cursor: dict[str, object] | None,
        candidates: tuple[dict[str, object], ...],
    ):
        del run_id, cursor
        self.events.append("fetch_started")
        self.received_candidates = candidates
        for batch_index, indexes in enumerate((range(0, 2), range(2, 3), range(3, 20)), start=1):
            yield FetchBatch(
                batch_id=f"progress-{batch_index}",
                items=tuple(_run_item(index) for index in indexes),
                next_cursor={"batch": batch_index},
            )


@pytest.mark.asyncio
async def test_two_stage_run_freezes_candidates_and_reports_processing_progress(tmp_path: Path) -> None:
    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_config(tmp_path))
    assert personal_context._config is not None
    provider = _ProgressProvider(personal_context._config.fetch_services[0], home=tmp_path)
    submit_entered = [asyncio.Event() for _ in range(3)]
    submit_release = [asyncio.Event() for _ in range(3)]
    finish_entered = asyncio.Event()
    finish_release = asyncio.Event()
    submit_index = 0

    async def submit_batch(
        service_id: str,
        run_id: str,
        batch: FetchBatch,
        *,
        enqueued: asyncio.Event | None = None,
    ) -> None:
        nonlocal submit_index
        del service_id, run_id, batch
        if enqueued is not None:
            enqueued.set()
        current = submit_index
        submit_index += 1
        submit_entered[current].set()
        await submit_release[current].wait()

    async def finish_run(service_id: str, run_id: str) -> None:
        del service_id, run_id
        finish_entered.set()
        await finish_release.wait()

    personal_context._submit_batch = submit_batch  # type: ignore[method-assign]
    personal_context._finish_pipeline_run = finish_run  # type: ignore[method-assign]
    task = asyncio.create_task(personal_context._run_fetch_once("notes", provider))

    await asyncio.wait_for(provider.prepare_started.wait(), timeout=1)
    assert (await personal_context.snapshot()).fetch_run_progress["notes"] == {
        "service_id": "notes",
        "run_state": "running",
        "progress_percent": 0,
        "total_items": 0,
        "completed_items": 0,
        "last_error": None,
    }
    provider.release_prepare.set()
    await asyncio.wait_for(submit_entered[0].wait(), timeout=1)
    assert provider.events == ["prepare_started", "prepare_returned", "fetch_started"]
    assert isinstance(provider.received_candidates, tuple)
    assert len(provider.received_candidates) == 20
    assert (await personal_context.snapshot()).fetch_run_progress["notes"]["total_items"] == 20

    submit_release[0].set()
    await asyncio.wait_for(submit_entered[1].wait(), timeout=1)
    first = (await personal_context.snapshot()).fetch_run_progress["notes"]
    assert (first["completed_items"], first["progress_percent"]) == (2, 10)

    submit_release[1].set()
    await asyncio.wait_for(submit_entered[2].wait(), timeout=1)
    second = (await personal_context.snapshot()).fetch_run_progress["notes"]
    assert (second["completed_items"], second["progress_percent"]) == (3, 15)

    submit_release[2].set()
    await asyncio.wait_for(finish_entered.wait(), timeout=1)
    publishing = (await personal_context.snapshot()).fetch_run_progress["notes"]
    assert (publishing["completed_items"], publishing["progress_percent"]) == (20, 99)

    finish_release.set()
    await asyncio.wait_for(task, timeout=1)
    assert (await personal_context.snapshot()).fetch_run_progress["notes"] == {
        "service_id": "notes",
        "run_state": "succeeded",
        "progress_percent": 100,
        "total_items": 20,
        "completed_items": 20,
        "last_error": None,
    }
    committed_cursor = personal_context._read_cursor("notes")
    assert committed_cursor is not None
    assert committed_cursor["batch"] == 3
    selection = committed_cursor["_selection"]
    assert isinstance(selection, dict)
    assert len(selection["completed"]) == 20


@pytest.mark.asyncio
async def test_run_progress_preserves_counts_for_failure_and_cancellation(tmp_path: Path) -> None:
    class FailingProvider(_ProgressProvider):
        async def fetch(
            self,
            *,
            run_id: str,
            cursor: dict[str, object] | None,
            candidates: tuple[dict[str, object], ...],
        ):
            del run_id, cursor, candidates
            yield FetchBatch(batch_id="completed", items=tuple(_run_item(index) for index in range(2)))
            raise RuntimeError("injected provider failure")

    class CancelledProvider(_ProgressProvider):
        def __init__(self, config: PersonalContextFetchServiceConfig, *, home: Path) -> None:
            super().__init__(config, home=home)
            self.blocked = asyncio.Event()

        async def fetch(
            self,
            *,
            run_id: str,
            cursor: dict[str, object] | None,
            candidates: tuple[dict[str, object], ...],
        ):
            del run_id, cursor, candidates
            yield FetchBatch(batch_id="completed", items=tuple(_run_item(index) for index in range(2)))
            self.blocked.set()
            await asyncio.Event().wait()
            if False:
                yield FetchBatch(batch_id="unreachable")

    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_config(tmp_path))
    assert personal_context._config is not None
    service_config = personal_context._config.fetch_services[0]

    async def submit_batch(*_args: object, **_kwargs: object) -> None:
        return None

    personal_context._submit_batch = submit_batch  # type: ignore[method-assign]

    failed = FailingProvider(service_config, home=tmp_path)
    failed.release_prepare.set()
    with pytest.raises(Exception):
        await personal_context._run_fetch_once("notes", failed)
    failed_status = (await personal_context.snapshot()).fetch_run_progress["notes"]
    assert failed_status["run_state"] == "failed"
    assert (failed_status["completed_items"], failed_status["progress_percent"]) == (2, 10)
    assert isinstance(failed_status["last_error"], str)

    cancelled = CancelledProvider(service_config, home=tmp_path)
    cancelled.release_prepare.set()
    task = asyncio.create_task(personal_context._run_fetch_once("notes", cancelled))
    await asyncio.wait_for(cancelled.blocked.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cancelled_status = (await personal_context.snapshot()).fetch_run_progress["notes"]
    assert cancelled_status["run_state"] == "cancelled"
    assert (cancelled_status["completed_items"], cancelled_status["progress_percent"]) == (2, 10)
    assert cancelled_status["last_error"] is None


@pytest.mark.asyncio
async def test_empty_run_succeeds_and_next_run_replaces_retained_progress(tmp_path: Path) -> None:
    class EmptyProvider(ContextFetchService):
        async def prepare_run(
            self,
            *,
            run_id: str,
            run_started_at: datetime,
            cursor: dict[str, object] | None,
        ) -> tuple[dict[str, object], ...]:
            del run_id, run_started_at, cursor
            return ()

        async def fetch(
            self,
            *,
            run_id: str,
            cursor: dict[str, object] | None,
            candidates: tuple[dict[str, object], ...],
        ):
            del run_id, cursor, candidates
            yield FetchBatch(batch_id="empty", next_cursor={"checked": True})

    class BlockingPrepareProvider(EmptyProvider):
        def __init__(self, config: PersonalContextFetchServiceConfig, *, home: Path) -> None:
            super().__init__(config, home=home)
            self.started = asyncio.Event()

        async def prepare_run(
            self,
            *,
            run_id: str,
            run_started_at: datetime,
            cursor: dict[str, object] | None,
        ) -> tuple[dict[str, object], ...]:
            del run_id, run_started_at, cursor
            self.started.set()
            await asyncio.Event().wait()
            return ()

    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_config(tmp_path))
    initial = (await personal_context.snapshot()).fetch_run_progress["notes"]
    assert initial["run_state"] == "idle"
    assert initial["progress_percent"] == 0
    assert personal_context._config is not None
    service_config = personal_context._config.fetch_services[0]

    await personal_context._run_fetch_once("notes", EmptyProvider(service_config, home=tmp_path))
    assert (await personal_context.snapshot()).fetch_run_progress["notes"] == {
        "service_id": "notes",
        "run_state": "succeeded",
        "progress_percent": 100,
        "total_items": 0,
        "completed_items": 0,
        "last_error": None,
    }

    blocking = BlockingPrepareProvider(service_config, home=tmp_path)
    task = asyncio.create_task(personal_context._run_fetch_once("notes", blocking))
    await asyncio.wait_for(blocking.started.wait(), timeout=1)
    replacement = (await personal_context.snapshot()).fetch_run_progress["notes"]
    assert replacement["run_state"] == "running"
    assert replacement["progress_percent"] == 0
    assert replacement["total_items"] == 0
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_write_cursor_compacts_selection_and_atomically_preserves_old_bytes_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    personal_context = PersonalContext(home=tmp_path)
    asyncio.run(personal_context.set_configuration(_config(tmp_path)))
    base_time = datetime(2026, 8, 1, tzinfo=UTC)
    candidates = tuple(
        {
            **_run_candidate(index),
            "candidate_time": (base_time + timedelta(minutes=index // 20)).isoformat().replace("+00:00", "Z"),
        }
        for index in range(6000)
    )
    cursor = record_completed_candidates(None, candidates)
    replace_calls: list[tuple[Path, Path]] = []
    real_replace = personal_context_module.os.replace

    def recording_replace(source: str | Path, target: str | Path) -> None:
        replace_calls.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(personal_context_module.os, "replace", recording_replace)

    personal_context._write_cursor("notes", cursor)

    cursor_path = tmp_path / "state" / "cursors" / "notes.json"
    payload = cursor_path.read_bytes()
    stored = json.loads(payload)
    compacted_cursor = stored["cursor"]
    compacted_bytes = json.dumps(
        compacted_cursor,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(payload) <= 512 * 1024
    assert len(compacted_bytes) <= 384 * 1024
    assert len(replace_calls) == 1
    assert replace_calls[0][1] == cursor_path

    before = cursor_path.read_bytes()
    with pytest.raises(Exception):
        personal_context._write_cursor("notes", {"provider_state": "x" * (512 * 1024)})
    assert cursor_path.read_bytes() == before
    assert len(replace_calls) == 1


@pytest.mark.asyncio
async def test_run_fetch_all_accepts_only_enabled_services_and_returns_before_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _BlockingManualProvider.instances = {}
    monkeypatch.setitem(
        personal_context_module._PROVIDER_TYPES,
        "local_files",
        _BlockingManualProvider,
    )
    personal_context = await _ready_manual_personal_context(
        tmp_path,
        _manual_config(
            tmp_path,
            services={"b": True, "disabled": False, "a": True},
        ),
    )

    result = await personal_context.run_fetch()

    assert result == {"state": "accepted", "service_ids": ["a", "b"]}
    assert set(personal_context._manual_fetch_tasks) == {"a", "b"}
    assert all(not task.done() for task in personal_context._manual_fetch_tasks.values())
    assert "disabled" not in _BlockingManualProvider.instances
    await _finish_manual_tasks(personal_context, ("a", "b"))


@pytest.mark.asyncio
async def test_run_fetch_one_ignores_service_switch_when_collection_is_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _BlockingManualProvider.instances = {}
    monkeypatch.setitem(
        personal_context_module._PROVIDER_TYPES,
        "local_files",
        _BlockingManualProvider,
    )
    personal_context = await _ready_manual_personal_context(
        tmp_path,
        _manual_config(
            tmp_path,
            services={"disabled": False},
        ),
    )

    result = await personal_context.run_fetch(service_id="disabled")

    assert result == {
        "state": "accepted",
        "service_ids": ["disabled"],
    }
    await _finish_manual_tasks(personal_context, ("disabled",))


@pytest.mark.asyncio
async def test_run_fetch_rejects_disabled_core_stopped_runtime_or_dead_pipeline(
    tmp_path: Path,
) -> None:
    unconfigured = PersonalContext(home=tmp_path / "unconfigured")
    with pytest.raises(PersonalContext.Error):
        await unconfigured.run_fetch()

    disabled = PersonalContext(home=tmp_path / "disabled")
    await disabled.set_configuration(
        _manual_config(tmp_path, collection_enabled=False),
    )
    with pytest.raises(PersonalContext.Error):
        await disabled.run_fetch()

    stopped = PersonalContext(home=tmp_path / "stopped")
    await stopped.set_configuration(_manual_config(tmp_path))
    stopped._state = "STOPPED"
    stopped._pipeline_service = _RunningPipeline()  # type: ignore[assignment]
    with pytest.raises(PersonalContext.Error):
        await stopped.run_fetch()

    dead_pipeline = PersonalContext(home=tmp_path / "dead")
    await dead_pipeline.set_configuration(_manual_config(tmp_path))
    dead_pipeline._state = "RUNNING"
    dead_pipeline._pipeline_service = _RunningPipeline(  # type: ignore[assignment]
        running=False
    )
    with pytest.raises(PersonalContext.Error):
        await dead_pipeline.run_fetch()


@pytest.mark.asyncio
async def test_run_fetch_rejects_unknown_service_or_empty_all_target(
    tmp_path: Path,
) -> None:
    personal_context = await _ready_manual_personal_context(
        tmp_path,
        _manual_config(tmp_path, services={"disabled": False}),
    )

    with pytest.raises(PersonalContext.Error):
        await personal_context.run_fetch()
    with pytest.raises(PersonalContext.Error):
        await personal_context.run_fetch(service_id="missing")


@pytest.mark.asyncio
async def test_run_fetch_rejects_same_service_when_manual_or_scheduled_round_is_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _BlockingManualProvider.instances = {}
    monkeypatch.setitem(
        personal_context_module._PROVIDER_TYPES,
        "local_files",
        _BlockingManualProvider,
    )
    personal_context = await _ready_manual_personal_context(tmp_path, _manual_config(tmp_path))
    await personal_context.run_fetch(service_id="notes")

    with pytest.raises(PersonalContext.Error) as manual_error:
        await personal_context.run_fetch(service_id="notes")
    assert "notes" in str(manual_error.value)
    await _finish_manual_tasks(personal_context, ("notes",))

    personal_context._fetch_running.add("notes")
    with pytest.raises(PersonalContext.Error) as scheduled_error:
        await personal_context.run_fetch(service_id="notes")
    assert "notes" in str(scheduled_error.value)
    personal_context._fetch_running.clear()


@pytest.mark.asyncio
async def test_run_fetch_all_rejects_atomically_when_one_target_is_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _BlockingManualProvider.instances = {}
    monkeypatch.setitem(
        personal_context_module._PROVIDER_TYPES,
        "local_files",
        _BlockingManualProvider,
    )
    personal_context = await _ready_manual_personal_context(
        tmp_path,
        _manual_config(tmp_path, services={"a": True, "b": True}),
    )
    personal_context._fetch_running.add("a")

    with pytest.raises(PersonalContext.Error) as caught:
        await personal_context.run_fetch()

    assert "a" in str(caught.value)
    assert personal_context._manual_fetch_tasks == {}
    assert _BlockingManualProvider.instances == {}
    assert personal_context._fetch_running == {"a"}


@pytest.mark.asyncio
async def test_run_fetch_allows_different_services_to_fetch_concurrently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _BlockingManualProvider.instances = {}
    monkeypatch.setitem(
        personal_context_module._PROVIDER_TYPES,
        "local_files",
        _BlockingManualProvider,
    )
    personal_context = await _ready_manual_personal_context(
        tmp_path,
        _manual_config(tmp_path, services={"a": True, "b": True}),
    )
    queue = personal_context._pipeline_queue

    first = await personal_context.run_fetch(service_id="a")
    second = await personal_context.run_fetch(service_id="b")

    assert first["service_ids"] == ["a"]
    assert second["service_ids"] == ["b"]
    await asyncio.wait_for(
        _BlockingManualProvider.instances["a"].started.wait(),
        timeout=1.0,
    )
    await asyncio.wait_for(
        _BlockingManualProvider.instances["b"].started.wait(),
        timeout=1.0,
    )
    assert personal_context._pipeline_queue is queue
    assert personal_context._fetch_running == {"a", "b"}
    await _finish_manual_tasks(personal_context, ("a", "b"))


@pytest.mark.asyncio
async def test_stop_fetch_service_waits_for_manual_round_before_returning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _BlockingManualProvider.instances = {}
    monkeypatch.setitem(
        personal_context_module._PROVIDER_TYPES,
        "local_files",
        _BlockingManualProvider,
    )
    personal_context = await _ready_manual_personal_context(
        tmp_path,
        _manual_config(
            tmp_path,
            collection_enabled=True,
            services={"notes": False},
        ),
    )
    await personal_context.run_fetch(service_id="notes")
    manual_task = personal_context._manual_fetch_tasks["notes"]
    provider = _BlockingManualProvider.instances["notes"]
    await asyncio.wait_for(provider.started.wait(), timeout=1.0)
    stop_task = asyncio.create_task(personal_context.stop_fetch_service("notes", timeout_seconds=1.0))

    try:
        await asyncio.sleep(0)
        assert not stop_task.done()
    finally:
        provider.release.set()
        await asyncio.gather(manual_task, stop_task, return_exceptions=True)

    assert "notes" not in personal_context._manual_fetch_tasks
    assert "notes" not in personal_context._fetch_running
    assert personal_context._fetch_states["notes"] == "STOPPED"
    assert (tmp_path / "state" / "cursors" / "notes.json").is_file()


@pytest.mark.asyncio
async def test_stop_collection_cancels_active_round_without_cursor_or_context_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _BlockingManualProvider.instances = {}
    monkeypatch.setitem(
        personal_context_module._PROVIDER_TYPES,
        "local_files",
        _BlockingManualProvider,
    )
    personal_context = await _ready_manual_personal_context(
        tmp_path,
        _manual_config(tmp_path, services={"notes": False}),
    )
    cursor_path = tmp_path / "state" / "cursors" / "notes.json"
    description_path = tmp_path / "workspace" / "context" / "description.md"
    description_path.parent.mkdir(parents=True)
    description_path.write_text("last complete context", encoding="utf-8")

    await personal_context.run_fetch(service_id="notes")
    provider = _BlockingManualProvider.instances["notes"]
    manual_task = personal_context._manual_fetch_tasks["notes"]
    await asyncio.wait_for(provider.started.wait(), timeout=1.0)

    await personal_context.stop_collection(timeout_seconds=1.0)

    assert manual_task.done()
    assert provider.commit_calls == []
    assert len(provider.abort_calls) == 1
    assert not cursor_path.exists()
    assert description_path.read_text(encoding="utf-8") == "last complete context"
    status = await personal_context.snapshot()
    assert status.collection_enabled is False
    assert status.pipeline_running is False
    assert status.state == "STOPPED"


@pytest.mark.asyncio
async def test_disabling_scheduled_service_lets_active_round_finish_and_stops_next_timer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _BlockingManualProvider.instances = {}
    monkeypatch.setitem(
        personal_context_module._PROVIDER_TYPES,
        "local_files",
        _BlockingManualProvider,
    )
    personal_context = await _ready_manual_personal_context(
        tmp_path,
        _config(tmp_path, interval=0.01),
    )
    await personal_context.start_fetch_service("notes")
    scheduler = personal_context._fetch_tasks["notes"]
    provider = _BlockingManualProvider.instances["notes"]
    await asyncio.wait_for(provider.started.wait(), timeout=1.0)

    await personal_context.set_fetch_service_enabled("notes", False)

    assert not scheduler.done()
    assert provider.abort_calls == []
    provider.release.set()
    await asyncio.wait_for(scheduler, timeout=1.0)
    assert len(provider.commit_calls) == 1
    assert personal_context._fetch_states["notes"] == "STOPPED"
    assert "notes" not in personal_context._fetch_tasks


@pytest.mark.asyncio
async def test_manual_fetch_stop_timeout_cancels_and_aborts_without_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _BlockingManualProvider.instances = {}
    monkeypatch.setitem(
        personal_context_module._PROVIDER_TYPES,
        "local_files",
        _BlockingManualProvider,
    )
    personal_context = await _ready_manual_personal_context(
        tmp_path,
        _manual_config(
            tmp_path,
            collection_enabled=True,
            services={"notes": False},
        ),
    )
    await personal_context.run_fetch(service_id="notes")
    manual_task = personal_context._manual_fetch_tasks["notes"]
    provider = _BlockingManualProvider.instances["notes"]
    await asyncio.wait_for(provider.started.wait(), timeout=1.0)

    try:
        with pytest.raises(PersonalContext.Error):
            await personal_context.stop_fetch_service("notes", timeout_seconds=0.01)
    finally:
        if not manual_task.done():
            manual_task.cancel()
        await asyncio.gather(manual_task, return_exceptions=True)

    assert provider.abort_calls
    assert not (tmp_path / "state" / "cursors" / "notes.json").exists()
    assert "notes" not in personal_context._manual_fetch_tasks
    assert "notes" not in personal_context._fetch_running


@pytest.mark.asyncio
async def test_deactivate_runtime_waits_for_manual_round_and_stops_pipeline_after_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _BlockingManualProvider.instances = {}
    monkeypatch.setitem(
        personal_context_module._PROVIDER_TYPES,
        "local_files",
        _BlockingManualProvider,
    )
    personal_context = await _ready_manual_personal_context(
        tmp_path,
        _manual_config(
            tmp_path,
            collection_enabled=True,
            services={"notes": False},
        ),
    )
    pipeline = personal_context._pipeline_service
    assert isinstance(pipeline, _RunningPipeline)
    order: list[str] = []

    async def commit_run(*, run_id: str) -> None:
        del run_id
        order.append("fetch")

    async def stop_pipeline(*, timeout_seconds: float) -> None:
        del timeout_seconds
        order.append("pipeline")
        pipeline.running = False

    await personal_context.run_fetch(service_id="notes")
    manual_task = personal_context._manual_fetch_tasks["notes"]
    provider = _BlockingManualProvider.instances["notes"]
    provider.commit_run = commit_run  # type: ignore[method-assign]
    pipeline.stop = stop_pipeline  # type: ignore[method-assign]
    await asyncio.wait_for(provider.started.wait(), timeout=1.0)
    deactivate_task = asyncio.create_task(personal_context.deactivate_runtime(timeout_seconds=1.0))

    try:
        await asyncio.sleep(0)
        assert not deactivate_task.done()
        assert pipeline.running is True
    finally:
        provider.release.set()
        await asyncio.gather(
            manual_task,
            deactivate_task,
            return_exceptions=True,
        )

    assert order == ["fetch", "pipeline"]
    assert personal_context._state == "STOPPED"


@pytest.mark.asyncio
async def test_manual_fetch_failure_is_reported_without_task_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _BlockingManualProvider.instances = {}
    monkeypatch.setitem(
        personal_context_module._PROVIDER_TYPES,
        "local_files",
        _BlockingManualProvider,
    )
    personal_context = await _ready_manual_personal_context(
        tmp_path,
        _manual_config(
            tmp_path,
            collection_enabled=True,
            services={"notes": False},
        ),
    )
    await personal_context.run_fetch(service_id="notes")
    manual_task = personal_context._manual_fetch_tasks["notes"]
    provider = _BlockingManualProvider.instances["notes"]
    provider.fail = True
    provider.release.set()

    await manual_task

    status = await personal_context.snapshot()
    assert status.fetch_service_errors["notes"]
    assert status.fetch_service_states["notes"] == "FAILED"
    assert "notes" not in personal_context._manual_fetch_tasks
    assert "notes" not in personal_context._fetch_running


class _TwoBatchProvider(ContextFetchService):
    def __init__(self, config: PersonalContextFetchServiceConfig, *, home: Path) -> None:
        super().__init__(config, home=home)
        self.commit_calls: list[str] = []
        self.abort_calls: list[str] = []
        self.batches = tuple(
            FetchBatch(
                batch_id=f"batch-{number}",
                items=(
                    RawChangeItem(
                        logical_id=f"notes/{number}",
                        revision_id=f"rev-{number}",
                        operation="upsert",
                        title=f"Note {number}",
                        content=f"Body {number}",
                        original_ref=f"file:///notes/{number}",
                    ),
                ),
                next_cursor={"n": number},
            )
            for number in (1, 2)
        )

    async def prepare_run(
        self,
        *,
        run_id: str,
        run_started_at: datetime,
        cursor: dict[str, object] | None,
    ) -> tuple[dict[str, object], ...]:
        del run_id, run_started_at, cursor
        return tuple(_item_candidate(item) for batch in self.batches for item in batch.items)

    async def fetch(
        self,
        *,
        run_id: str,
        cursor: dict[str, object] | None,
        candidates: tuple[dict[str, object], ...],
    ):
        del run_id, cursor, candidates
        for batch in self.batches:
            yield batch

    async def commit_run(self, *, run_id: str) -> None:
        self.commit_calls.append(run_id)

    async def abort_run(self, *, run_id: str) -> None:
        self.abort_calls.append(run_id)


async def _two_batch_run(tmp_path: Path) -> tuple[PersonalContext, _TwoBatchProvider]:
    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_config(tmp_path))
    assert personal_context._config is not None
    provider = _TwoBatchProvider(personal_context._config.fetch_services[0], home=tmp_path)
    personal_context._write_cursor("notes", {"n": 0})
    return personal_context, provider


async def _local_retry_run(tmp_path: Path) -> tuple[PersonalContext, LocalFilesFetchService, Path]:
    source = tmp_path / "source"
    source.mkdir()
    note = source / "note.md"
    note.write_text("# Note\n\nBody", encoding="utf-8")
    home = tmp_path / "home"
    personal_context = PersonalContext(home=home)
    await personal_context.set_configuration(_config(source))
    assert personal_context._config is not None
    provider = LocalFilesFetchService(personal_context._config.fetch_services[0], home=home)
    return personal_context, provider, home / "state" / "cursors" / "notes.json"


@pytest.mark.asyncio
async def test_transient_provider_read_recovery_submits_once_and_commits_cursor_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    personal_context, provider, cursor_path = await _local_retry_run(tmp_path)
    original_materialize = local_files._materialize_candidate
    attempts = 0
    pipeline_events: list[tuple[str, str]] = []

    def flaky_materialize(candidate: dict[str, object]) -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError(errno.EBUSY, "temporarily busy")
        return original_materialize(candidate)

    async def no_sleep(_delay: float) -> None:
        return None

    async def submit(
        _service_id: str,
        run_id: str,
        batch: FetchBatch,
        *,
        enqueued: asyncio.Event | None = None,
    ) -> None:
        assert len(batch.items) == 1
        if enqueued is not None:
            enqueued.set()
        pipeline_events.append(("batch", run_id))

    async def finish(_service_id: str, run_id: str) -> None:
        pipeline_events.append(("finish", run_id))

    monkeypatch.setattr(local_files, "_materialize_candidate", flaky_materialize)
    monkeypatch.setattr(retry_module, "_sleep", no_sleep)
    monkeypatch.setattr(retry_module, "_jitter_seconds", lambda: 0.0)
    personal_context._submit_batch = submit  # type: ignore[method-assign]
    personal_context._finish_pipeline_run = finish  # type: ignore[method-assign]

    await personal_context._run_fetch_once("notes", provider)

    assert attempts == 2
    assert [event[0] for event in pipeline_events] == ["batch", "finish"]
    assert pipeline_events[0][1] == pipeline_events[1][1]
    assert json.loads(cursor_path.read_text(encoding="utf-8"))["service_id"] == "notes"
    committed_cursor = personal_context._read_cursor("notes")
    assert committed_cursor is not None
    assert len(committed_cursor["_selection"]["completed"]) == 1


@pytest.mark.asyncio
async def test_exhausted_provider_read_does_not_submit_or_commit_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    personal_context, provider, cursor_path = await _local_retry_run(tmp_path)
    attempts = 0
    pipeline_events: list[str] = []

    def always_busy(_candidate: dict[str, object]) -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        raise OSError(errno.EBUSY, "still busy")

    async def no_sleep(_delay: float) -> None:
        return None

    async def unexpected(*_args: object, **_kwargs: object) -> None:
        pipeline_events.append("called")

    monkeypatch.setattr(local_files, "_materialize_candidate", always_busy)
    monkeypatch.setattr(retry_module, "_sleep", no_sleep)
    monkeypatch.setattr(retry_module, "_jitter_seconds", lambda: 0.0)
    personal_context._submit_batch = unexpected  # type: ignore[method-assign]
    personal_context._finish_pipeline_run = unexpected  # type: ignore[method-assign]
    personal_context._abort_pipeline_run = unexpected  # type: ignore[method-assign]

    with pytest.raises(Exception):
        await personal_context._run_fetch_once("notes", provider)

    assert attempts == 3
    assert pipeline_events == []
    assert not cursor_path.exists()


@pytest.mark.asyncio
async def test_cancelling_provider_retry_wait_stops_without_submit_or_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    personal_context, provider, cursor_path = await _local_retry_run(tmp_path)
    attempts = 0
    sleep_started = asyncio.Event()
    pipeline_events: list[str] = []

    def always_busy(_candidate: dict[str, object]) -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        raise OSError(errno.EBUSY, "still busy")

    async def blocking_sleep(_delay: float) -> None:
        sleep_started.set()
        await asyncio.Event().wait()

    async def unexpected(*_args: object, **_kwargs: object) -> None:
        pipeline_events.append("called")

    monkeypatch.setattr(local_files, "_materialize_candidate", always_busy)
    monkeypatch.setattr(retry_module, "_sleep", blocking_sleep)
    monkeypatch.setattr(retry_module, "_jitter_seconds", lambda: 0.0)
    personal_context._submit_batch = unexpected  # type: ignore[method-assign]
    personal_context._finish_pipeline_run = unexpected  # type: ignore[method-assign]
    personal_context._abort_pipeline_run = unexpected  # type: ignore[method-assign]

    task = asyncio.create_task(personal_context._run_fetch_once("notes", provider))
    await asyncio.wait_for(sleep_started.wait(), timeout=1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert attempts == 1
    assert pipeline_events == []
    assert not cursor_path.exists()


async def _next_pipeline_event(personal_context: PersonalContext) -> tuple[tuple[object, ...], asyncio.Future[None]]:
    event = await personal_context._pipeline_queue.get()
    assert isinstance(event, tuple) and len(event) == 5
    completion = event[4]
    assert isinstance(completion, asyncio.Future)
    return event, completion


def _record_abort_order(personal_context: PersonalContext, provider: _TwoBatchProvider) -> list[str]:
    order: list[str] = []

    async def pipeline_abort(_service_id: str, _run_id: str) -> None:
        order.append("pipeline")

    async def provider_abort(*, run_id: str) -> None:
        del run_id
        order.append("provider")

    personal_context._abort_pipeline_run = pipeline_abort  # type: ignore[method-assign]
    provider.abort_run = provider_abort  # type: ignore[method-assign]
    return order


def _record_pipeline_calls(
    personal_context: PersonalContext,
    provider: _TwoBatchProvider,
    *,
    failure_stage: str | None = None,
) -> list[tuple[object, ...]]:
    events: list[tuple[object, ...]] = []

    async def submit(service_id: str, run_id: str, batch: FetchBatch, *, enqueued: asyncio.Event | None = None) -> None:
        if enqueued is not None:
            enqueued.set()
        events.append(("batch", service_id, run_id, batch))
        if failure_stage == "second_batch" and batch.batch_id == "batch-2":
            raise RuntimeError("second batch failed")

    async def finish(service_id: str, run_id: str) -> None:
        events.append(("finish", service_id, run_id))
        assert provider.commit_calls == []
        assert personal_context._read_cursor("notes") == {"n": 0}
        if failure_stage == "finish":
            raise RuntimeError("finish failed")

    async def abort(service_id: str, run_id: str) -> None:
        events.append(("abort", service_id, run_id))

    personal_context._submit_batch = submit  # type: ignore[method-assign]
    personal_context._finish_pipeline_run = finish  # type: ignore[method-assign]
    personal_context._abort_pipeline_run = abort  # type: ignore[method-assign]
    return events


@pytest.mark.asyncio
async def test_first_fetch_waits_for_interval_and_commits_atomic_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Provider.fetch_calls = 0
    _Provider.commit_calls = []
    _Provider.abort_calls = []
    monkeypatch.setitem(personal_context_module._PROVIDER_TYPES, "local_files", _Provider)
    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_config(tmp_path))
    personal_context._state = "RUNNING"
    personal_context._pipeline_service = type("Pipeline", (), {"is_running": lambda self: True})()
    submitted: list[FetchBatch] = []

    async def submit(_service_id: str, _run_id: str, _batch: FetchBatch) -> None:
        submitted.append(_batch)

    personal_context._submit_batch = submit  # type: ignore[assignment]
    await personal_context.start_fetch_service("notes")
    await asyncio.sleep(0.003)
    assert _Provider.fetch_calls == 0
    await asyncio.sleep(0.03)
    assert _Provider.fetch_calls >= 1
    assert submitted == []
    await personal_context.stop_fetch_service("notes", timeout_seconds=1)
    cursor = tmp_path / "state" / "cursors" / "notes.json"
    assert cursor.is_file()
    assert cursor.read_text(encoding="utf-8").find('"source_fingerprint"') >= 0


@pytest.mark.asyncio
async def test_scheduler_remains_alive_after_fetch_state_becomes_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempted = asyncio.Event()

    class FailingProvider(_EmptyPreparedProvider):
        async def fetch(
            self,
            *,
            run_id: str,
            cursor: dict[str, object] | None,
            candidates: tuple[dict[str, object], ...],
        ):
            del run_id, cursor, candidates
            attempted.set()
            if False:
                yield FetchBatch(batch_id="unreachable")
            raise RuntimeError("controlled fetch failure")

    monkeypatch.setitem(
        personal_context_module._PROVIDER_TYPES,
        "local_files",
        FailingProvider,
    )
    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_config(tmp_path))
    personal_context._state = "RUNNING"
    personal_context._pipeline_service = _RunningPipeline()  # type: ignore[assignment]

    await personal_context.start_fetch_service("notes")
    scheduler = personal_context._fetch_tasks["notes"]
    try:
        await asyncio.wait_for(attempted.wait(), timeout=1.0)
        async with asyncio.timeout(1.0):
            while (await personal_context.snapshot()).fetch_service_states["notes"] != "FAILED":
                await asyncio.sleep(0)

        assert not scheduler.done()
        assert personal_context._fetch_tasks["notes"] is scheduler
    finally:
        await personal_context.stop_fetch_service("notes", timeout_seconds=1.0)


@pytest.mark.asyncio
async def test_cancelled_round_aborts_without_cursor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class BlockingProvider(_EmptyPreparedProvider):
        async def fetch(
            self,
            *,
            run_id: str,
            cursor: dict[str, object] | None,
            candidates: tuple[dict[str, object], ...],
        ):
            del cursor, candidates
            yield FetchBatch(batch_id="batch-1", items=(), next_cursor={"n": 1})
            await asyncio.Event().wait()

        async def abort_run(self, *, run_id: str) -> None:
            self.aborted = run_id

    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_config(tmp_path))
    assert personal_context._config is not None
    provider = BlockingProvider(personal_context._config.fetch_services[0], home=tmp_path)
    task = asyncio.create_task(personal_context._run_fetch_once("notes", provider))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (tmp_path / "state" / "cursors" / "notes.json").exists()


@pytest.mark.asyncio
async def test_multi_batch_run_finishes_pipeline_before_provider_commit_and_cursor(tmp_path: Path) -> None:
    personal_context, provider = await _two_batch_run(tmp_path)
    events = _record_pipeline_calls(personal_context, provider)

    await personal_context._run_fetch_once("notes", provider)

    run_id = provider.commit_calls[0]
    batches = [event[3] for event in events if event[0] == "batch"]
    assert events == [
        ("batch", "notes", run_id, batches[0]),
        ("batch", "notes", run_id, batches[1]),
        ("finish", "notes", run_id),
    ]
    assert provider.abort_calls == []
    committed_cursor = personal_context._read_cursor("notes")
    assert committed_cursor is not None
    assert committed_cursor["n"] == 2
    assert len(committed_cursor["_selection"]["completed"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["second_batch", "finish"])
async def test_multi_batch_failure_aborts_pipeline_and_preserves_old_cursor(
    tmp_path: Path,
    failure_stage: str,
) -> None:
    personal_context, provider = await _two_batch_run(tmp_path)
    events = _record_pipeline_calls(personal_context, provider, failure_stage=failure_stage)
    cursor_path = tmp_path / "state" / "cursors" / "notes.json"
    before = cursor_path.read_bytes()

    with pytest.raises(Exception):
        await personal_context._run_fetch_once("notes", provider)

    run_id = provider.abort_calls[0]
    expected = [
        ("batch", "notes", run_id, provider.batches[0]),
        ("batch", "notes", run_id, provider.batches[1]),
    ]
    if failure_stage == "finish":
        expected.append(("finish", "notes", run_id))
    expected.append(("abort", "notes", run_id))
    assert events == expected
    assert provider.commit_calls == []
    assert provider.abort_calls == [run_id]
    assert personal_context._read_cursor("notes") == {"n": 0}
    assert cursor_path.read_bytes() == before


@pytest.mark.asyncio
async def test_empty_only_run_commits_cursor_without_pipeline_or_sandbox(tmp_path: Path) -> None:
    class EmptyProvider(_EmptyPreparedProvider):
        def __init__(self, config: PersonalContextFetchServiceConfig, *, home: Path) -> None:
            super().__init__(config, home=home)
            self.commit_calls: list[str] = []

        async def fetch(
            self,
            *,
            run_id: str,
            cursor: dict[str, object] | None,
            candidates: tuple[dict[str, object], ...],
        ):
            del run_id, cursor, candidates
            yield FetchBatch(batch_id="empty", items=(), next_cursor={"n": 1})

        async def commit_run(self, *, run_id: str) -> None:
            self.commit_calls.append(run_id)

    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_config(tmp_path))
    assert personal_context._config is not None
    provider = EmptyProvider(personal_context._config.fetch_services[0], home=tmp_path)
    pipeline_calls: list[str] = []

    async def unexpected(*_args: object) -> None:
        pipeline_calls.append("called")

    personal_context._submit_batch = unexpected  # type: ignore[method-assign]
    personal_context._finish_pipeline_run = unexpected  # type: ignore[attr-defined]
    personal_context._abort_pipeline_run = unexpected  # type: ignore[attr-defined]

    await personal_context._run_fetch_once("notes", provider)

    assert len(provider.commit_calls) == 1
    assert pipeline_calls == []
    committed_cursor = personal_context._read_cursor("notes")
    assert committed_cursor is not None
    assert committed_cursor["n"] == 1
    assert committed_cursor["_selection"]["completed"] == []
    assert not (tmp_path / "workspace" / "sandboxes").exists()


@pytest.mark.asyncio
async def test_private_pipeline_helpers_submit_one_tagged_queue_contract(tmp_path: Path) -> None:
    personal_context, provider = await _two_batch_run(tmp_path)

    for submit, expected_tag, expected_payload in (
        (lambda: personal_context._submit_batch("notes", "run-1", provider.batches[0]), "batch", provider.batches[0]),
        (lambda: personal_context._finish_pipeline_run("notes", "run-1"), "finish", None),
        (lambda: personal_context._abort_pipeline_run("notes", "run-1"), "abort", None),
    ):
        task = asyncio.create_task(submit())
        event, completion = await _next_pipeline_event(personal_context)
        tag, service_id, run_id, payload, _ = event
        assert (tag, service_id, run_id, payload) == (expected_tag, "notes", "run-1", expected_payload)
        completion.set_result(None)
        await task


@pytest.mark.asyncio
async def test_cancelled_before_first_batch_is_enqueued_does_not_submit_pipeline_abort(tmp_path: Path) -> None:
    personal_context, provider = await _two_batch_run(tmp_path)
    personal_context._pipeline_queue = asyncio.Queue(maxsize=1)
    sentinel = object()
    await personal_context._pipeline_queue.put(sentinel)
    pipeline_abort_calls: list[tuple[str, str]] = []

    async def pipeline_abort(service_id: str, run_id: str) -> None:
        pipeline_abort_calls.append((service_id, run_id))

    personal_context._abort_pipeline_run = pipeline_abort  # type: ignore[method-assign]
    task = asyncio.create_task(personal_context._run_fetch_once("notes", provider))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.5)

    assert pipeline_abort_calls == []
    assert len(provider.abort_calls) == 1
    assert personal_context._read_cursor("notes") == {"n": 0}
    assert personal_context._pipeline_queue.get_nowait() is sentinel
    assert personal_context._pipeline_queue.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize("waiting_for", ["batch", "finish"])
async def test_cancelled_after_pipeline_enqueue_aborts_in_order(tmp_path: Path, waiting_for: str) -> None:
    personal_context, provider = await _two_batch_run(tmp_path)
    abort_order = _record_abort_order(personal_context, provider)
    cursor_path = tmp_path / "state" / "cursors" / "notes.json"
    before = cursor_path.read_bytes()
    task = asyncio.create_task(personal_context._run_fetch_once("notes", provider))
    run_id: str | None = None
    completed_batches = provider.batches if waiting_for == "finish" else ()
    for batch in completed_batches:
        event, completion = await _next_pipeline_event(personal_context)
        if run_id is None:
            assert isinstance(event[2], str)
            run_id = event[2]
        assert event[:4] == ("batch", "notes", run_id, batch)
        completion.set_result(None)

    pending_event, pending_completion = await _next_pipeline_event(personal_context)
    if run_id is None:
        assert isinstance(pending_event[2], str)
        run_id = pending_event[2]
    expected_payload = provider.batches[0] if waiting_for == "batch" else None
    assert pending_event[:4] == (waiting_for, "notes", run_id, expected_payload)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert abort_order == ["pipeline", "provider"]
    assert pending_completion.cancelled()
    assert not pending_completion.cancel()
    assert provider.commit_calls == []
    assert personal_context._read_cursor("notes") == {"n": 0}
    assert cursor_path.read_bytes() == before


@pytest.mark.asyncio
async def test_stop_timeout_keeps_uncancellable_task_for_follow_up_cleanup(tmp_path: Path) -> None:
    release = asyncio.Event()

    async def stubborn_task() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_config(tmp_path))
    assert personal_context._config is not None
    personal_context._state = "RUNNING"
    task = asyncio.create_task(stubborn_task())
    await asyncio.sleep(0)
    personal_context._fetch_tasks["notes"] = task
    personal_context._fetch_stop_events["notes"] = asyncio.Event()
    personal_context._fetch_providers["notes"] = _Provider(personal_context._config.fetch_services[0], home=tmp_path)
    personal_context._fetch_states["notes"] = "RUNNING"

    with pytest.raises(Exception):
        await personal_context.stop_fetch_service("notes", timeout_seconds=0.01)
    assert personal_context._fetch_tasks["notes"] is task
    assert (await personal_context.snapshot()).fetch_service_states["notes"] == "FAILED"

    release.set()
    await personal_context.stop_fetch_service("notes", timeout_seconds=1)
    assert "notes" not in personal_context._fetch_tasks


@pytest.mark.asyncio
async def test_second_stop_cleans_fetch_task_cancelled_after_pipeline_abort(tmp_path: Path) -> None:
    personal_context, provider = await _two_batch_run(tmp_path)
    personal_context._state = "RUNNING"
    task = asyncio.create_task(personal_context._run_fetch_once("notes", provider))
    personal_context._fetch_tasks["notes"] = task
    personal_context._fetch_stop_events["notes"] = asyncio.Event()
    personal_context._fetch_providers["notes"] = provider
    personal_context._fetch_states["notes"] = "RUNNING"

    batch_event, batch_completion = await _next_pipeline_event(personal_context)
    assert batch_event[0] == "batch"

    with pytest.raises(Exception):
        await personal_context.stop_fetch_service("notes", timeout_seconds=0.01)
    assert personal_context._fetch_tasks["notes"] is task
    assert batch_completion.cancelled()

    abort_event, abort_completion = await asyncio.wait_for(_next_pipeline_event(personal_context), timeout=0.5)
    assert abort_event[:4] == ("abort", "notes", batch_event[2], None)
    abort_completion.set_result(None)
    done, pending = await asyncio.wait({task}, timeout=0.5)
    assert pending == set()
    assert done == {task}
    assert task.cancelled()
    assert len(provider.abort_calls) == 1
    assert personal_context._read_cursor("notes") == {"n": 0}

    await personal_context.stop_fetch_service("notes", timeout_seconds=1)
    assert "notes" not in personal_context._fetch_tasks


@pytest.mark.asyncio
async def test_failed_finish_keeps_old_context_cursor_and_source_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SingleBatchProvider(ContextFetchService):
        def __init__(
            self,
            config: PersonalContextFetchServiceConfig,
            *,
            home: Path,
            batch: FetchBatch,
        ) -> None:
            super().__init__(config, home=home)
            self.batch = batch
            self.commit_calls: list[str] = []
            self.abort_calls: list[str] = []

        async def prepare_run(
            self,
            *,
            run_id: str,
            run_started_at: datetime,
            cursor: dict[str, object] | None,
        ) -> tuple[dict[str, object], ...]:
            del run_id, run_started_at, cursor
            return tuple(_item_candidate(item) for item in self.batch.items)

        async def fetch(
            self,
            *,
            run_id: str,
            cursor: dict[str, object] | None,
            candidates: tuple[dict[str, object], ...],
        ):
            del run_id, cursor, candidates
            yield self.batch

        async def commit_run(self, *, run_id: str) -> None:
            self.commit_calls.append(run_id)

        async def abort_run(self, *, run_id: str) -> None:
            self.abort_calls.append(run_id)

    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(_config(tmp_path, interval=3600))
    assert personal_context._config is not None
    service_config = personal_context._config.fetch_services[0]
    monkeypatch.setattr(personal_context_module, "uuid4", lambda: SimpleNamespace(hex="run"))
    await personal_context.activate_runtime()
    try:
        upsert = SingleBatchProvider(
            service_config,
            home=tmp_path,
            batch=FetchBatch(
                batch_id="upsert",
                items=(
                    RawChangeItem(
                        logical_id="notes/one",
                        revision_id="rev-1",
                        operation="upsert",
                        title="One",
                        content="Original body.",
                        original_ref="file:///notes/one",
                    ),
                ),
                next_cursor={"n": 1},
            ),
        )
        await personal_context._run_fetch_once("notes", upsert)
        source_metadata = next((tmp_path / "workspace" / "source-meta").glob("src_*.md"))
        old_page = next(
            path for path in (tmp_path / "workspace" / "context").rglob("*.md") if path.name != "description.md"
        )
        committed_cursor = personal_context._read_cursor("notes")
        assert committed_cursor is not None
        assert committed_cursor["n"] == 1
        assert len(committed_cursor["_selection"]["completed"]) == 1

        original_publish = context_pipeline._copy_and_publish_tree

        def fail_context_publish(candidate: Path, target: Path, *, skip_relative: str | None = None) -> set[str]:
            if target == tmp_path / "workspace" / "context":
                raise OSError("injected context publication failure")
            return original_publish(candidate, target, skip_relative=skip_relative)

        monkeypatch.setattr(context_pipeline, "_copy_and_publish_tree", fail_context_publish)
        update = SingleBatchProvider(
            service_config,
            home=tmp_path,
            batch=FetchBatch(
                batch_id="update",
                items=(
                    RawChangeItem(
                        logical_id="notes/one",
                        revision_id="rev-2",
                        operation="upsert",
                        title="One updated",
                        content="Updated body.",
                        original_ref="file:///notes/one",
                    ),
                ),
                next_cursor={"n": 2},
            ),
        )

        with pytest.raises(Exception):
            await personal_context._run_fetch_once("notes", update)

        assert personal_context._read_cursor("notes") == committed_cursor
        assert update.commit_calls == []
        assert len(update.abort_calls) == 1
        assert old_page.is_file()
        assert source_metadata.is_file()
        assert not (tmp_path / "workspace" / "source-proofs").exists()
    finally:
        await personal_context.deactivate_runtime(timeout_seconds=1)


@pytest.mark.asyncio
async def test_retry_replaces_failed_same_revision_context_without_persistent_source_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SingleBatchProvider(ContextFetchService):
        def __init__(
            self,
            config: PersonalContextFetchServiceConfig,
            *,
            home: Path,
            content: str,
            next_cursor: int,
        ) -> None:
            super().__init__(config, home=home)
            self.content = content
            self.next_cursor = next_cursor
            self.commit_calls: list[str] = []
            self.abort_calls: list[str] = []

        async def prepare_run(
            self,
            *,
            run_id: str,
            run_started_at: datetime,
            cursor: dict[str, object] | None,
        ) -> tuple[dict[str, object], ...]:
            del run_id, run_started_at, cursor
            return (
                {
                    "stable_id": "notes/one",
                    "revision_id": "rev-1",
                    "candidate_time": "2026-08-25T00:00:00Z",
                    "resource_lane": "test",
                    "locator": "file:///notes/one",
                },
            )

        async def fetch(
            self,
            *,
            run_id: str,
            cursor: dict[str, object] | None,
            candidates: tuple[dict[str, object], ...],
        ):
            del run_id, cursor, candidates
            yield FetchBatch(
                batch_id=f"batch-{self.next_cursor}",
                items=(
                    RawChangeItem(
                        logical_id="notes/one",
                        revision_id="rev-1",
                        operation="upsert",
                        title="One",
                        content=self.content,
                        original_ref="file:///notes/one",
                    ),
                ),
                next_cursor={"n": self.next_cursor},
            )

        async def commit_run(self, *, run_id: str) -> None:
            self.commit_calls.append(run_id)

        async def abort_run(self, *, run_id: str) -> None:
            self.abort_calls.append(run_id)

    class DirectModel:
        outputs: list[str] = []

        def __init__(self, **_kwargs: object) -> None:
            pass

        async def invoke(self, _messages: list[object]) -> str:
            return self.outputs.pop(0)

    def processing_output(markdown: str) -> str:
        return json.dumps({"summaries": [{"item_index": 0, "markdown": markdown}]})

    agent_calls = 0

    async def filesystem_agent(*, sandbox_path: Path, **_kwargs: object) -> str:
        nonlocal agent_calls
        agent_calls += 1
        relative = f"topics/page-{agent_calls}.md"
        page = sandbox_path / "context" / relative
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(f"# Page {agent_calls}\n\nAgent body {agent_calls}. [[ref:0]]\n", encoding="utf-8")
        (page.parent / "description.md").write_text(
            f"# Topics\n\n- [Page {agent_calls}](page-{agent_calls}.md)\n",
            encoding="utf-8",
        )
        (sandbox_path / "context" / "description.md").write_text(
            "# Context\n\n- [Topics](topics/description.md)\n",
            encoding="utf-8",
        )
        return "done"

    config = PersonalContextConfig.from_dict(
        {
            "collection_enabled": True,
            "agent_use_enabled": False,
            "strategy_profile": "agent",
            "model_client": {
                "client_provider": "OpenAI",
                "api_key": "secret",
                "api_base": "https://example.test",
            },
            "model_request": {"model": "test"},
            "fetch_services": [
                {
                    "service_id": "notes",
                    "provider": "local_files",
                    "enabled": True,
                    "interval_seconds": 3600,
                    "time_range": {"mode": "all"},
                    "source": {"root_dir": str(tmp_path)},
                    "credentials": {},
                }
            ],
        }
    )
    DirectModel.outputs = [
        processing_output("First processed body."),
        processing_output("Second processed body."),
    ]
    monkeypatch.setattr(context_pipeline, "Model", DirectModel)
    monkeypatch.setattr(context_pipeline, "run_personal_context_agent", filesystem_agent)
    monkeypatch.setattr(personal_context_module, "uuid4", lambda: SimpleNamespace(hex="run"))

    personal_context = PersonalContext(home=tmp_path)
    await personal_context.set_configuration(config)
    personal_context._write_cursor("notes", {"n": 0})
    assert personal_context._config is not None
    service_config = personal_context._config.fetch_services[0]
    await personal_context.activate_runtime()
    try:
        original_publish = context_pipeline._copy_and_publish_tree
        fail_context_once = True

        def publish_with_one_failure(candidate: Path, target: Path, *, skip_relative: str | None = None) -> set[str]:
            nonlocal fail_context_once
            if fail_context_once and target == tmp_path / "workspace" / "context":
                fail_context_once = False
                raise OSError("injected first context publication failure")
            return original_publish(candidate, target, skip_relative=skip_relative)

        monkeypatch.setattr(context_pipeline, "_copy_and_publish_tree", publish_with_one_failure)
        first = SingleBatchProvider(
            service_config,
            home=tmp_path,
            content="First raw body.",
            next_cursor=1,
        )
        with pytest.raises(Exception):
            await personal_context._run_fetch_once("notes", first)
        assert personal_context._read_cursor("notes") == {"n": 0}
        assert list((tmp_path / "workspace" / "source-meta").glob("src_*.md"))
        assert not (tmp_path / "workspace" / "source-proofs").exists()
        assert not list((tmp_path / "workspace" / "sandboxes").rglob("content.md"))

        second = SingleBatchProvider(
            service_config,
            home=tmp_path,
            content="Second raw body.",
            next_cursor=2,
        )
        await personal_context._run_fetch_once("notes", second)

        workspace = tmp_path / "workspace"
        page = workspace / "context" / "topics" / "page-2.md"
        assert "Agent body 2." in page.read_text(encoding="utf-8")
        assert not (workspace / "context" / "topics" / "page-1.md").exists()
        assert not (workspace / "source-proofs").exists()
        assert not list((workspace / "sandboxes").rglob("content.md"))
        committed_cursor = personal_context._read_cursor("notes")
        assert committed_cursor is not None
        assert committed_cursor["n"] == 2
        assert len(committed_cursor["_selection"]["completed"]) == 1
        assert second.commit_calls
        assert second.abort_calls == []
    finally:
        await personal_context.deactivate_runtime(timeout_seconds=1)


@pytest.mark.asyncio
async def test_public_lifecycle_publishes_deduplicated_atomic_sources_without_source_content(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "user-input"
    source_root.mkdir()
    shared = source_root / "shared.md"
    distinct = source_root / "distinct.md"
    shared.write_text("# Shared\n\nSHARED_SOURCE_BODY_SENTINEL\n", encoding="utf-8")
    distinct.write_text("# Distinct\n\nDISTINCT_SOURCE_BODY_SENTINEL\n", encoding="utf-8")
    input_snapshot = {path.name: path.read_bytes() for path in source_root.iterdir()}
    home = tmp_path / "personal-context-home"
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
                for service_id in ("notes-a", "notes-b")
            ],
        }
    )
    personal_context = PersonalContext(home=home)
    await personal_context.set_configuration(config)
    await personal_context.activate_runtime()
    try:
        assert await personal_context.run_fetch() == {
            "state": "accepted",
            "service_ids": ["notes-a", "notes-b"],
        }
        for _attempt in range(500):
            status = await personal_context.snapshot()
            cursor_files = sorted((home / "state" / "cursors").glob("*.json"))
            if (
                status.context_ready
                and len(cursor_files) == 2
                and status.fetch_service_states == {"notes-a": "RUNNING", "notes-b": "RUNNING"}
            ):
                break
            assert status.fetch_service_errors == {}
            await asyncio.sleep(0.01)
        else:
            pytest.fail(
                f"public PersonalContext fetch lifecycle did not finish: status={status!r}, cursors={cursor_files!r}"
            )

        expected_source_ids = {
            source_id_for_locator(str(shared.resolve())),
            source_id_for_locator(str(distinct.resolve())),
        }
        source_files = sorted((home / "workspace" / "source-meta").glob("src_*.md"))
        assert {path.stem for path in source_files} == expected_source_ids
        assert len(source_files) == 2
        for source_file in source_files:
            metadata = read_source_metadata(source_file)
            assert metadata["locator"] in {str(shared.resolve()), str(distinct.resolve())}
            source_markdown = source_file.read_text(encoding="utf-8")
            assert "SHARED_SOURCE_BODY_SENTINEL" not in source_markdown
            assert "DISTINCT_SOURCE_BODY_SENTINEL" not in source_markdown

        context_root = home / "workspace" / "context"
        context_files = sorted(context_root.rglob("*.md"))
        assert context_files
        for context_file in context_files:
            text = context_file.read_text(encoding="utf-8")
            assert "[[ref:" not in text
            assert "Source / Evidence" not in text
            assert "personal_context_logical_ids" not in text
            assert not text.startswith("---\n")

        graph = await personal_context.get_graph()
        nodes = {str(node["id"]): node for node in graph["nodes"]}
        edges = [(str(edge["source"]), str(edge["target"]), str(edge["kind"])) for edge in graph["edges"]]
        assert graph["context_ready"] is True
        assert all(node["kind"] in {"directory", "document"} for node in nodes.values())
        assert all(not node_id.startswith("source:") for node_id in nodes)
        assert all(kind in {"contains", "references"} for _source, _target, kind in edges)
        assert all(not source.startswith("source:") and not target.startswith("source:") for source, target, _ in edges)

        context_markdown = "\n".join(path.read_text(encoding="utf-8") for path in context_files)
        assert all(source_id in context_markdown for source_id in expected_source_ids)

        for source_id in expected_source_ids:
            detail = await personal_context.get_source(source_id)
            assert detail["source_id"] == source_id
            assert detail["locator"] in {str(shared.resolve()), str(distinct.resolve())}
            assert "markdown" not in detail

        cursor_files = sorted((home / "state" / "cursors").glob("*.json"))
        assert {path.stem for path in cursor_files} == {"notes-a", "notes-b"}
        assert all(
            json.loads(path.read_text(encoding="utf-8"))["cursor"]["_selection"]["completed"] for path in cursor_files
        )
        persistent_files = {path.relative_to(home).as_posix() for path in home.rglob("*") if path.is_file()}
        assert all(
            path.startswith(("workspace/context/", "workspace/source-meta/", "state/cursors/"))
            for path in persistent_files
        )
        assert not list((home / "workspace" / "sandboxes").rglob("*"))
        assert not (home / "workspace" / "source-proofs").exists()
        assert not (home / "materialized-sources").exists()
        assert not any(
            path.name in {"content.md", "context-document.md", "blocks.jsonl", "manifest.json"}
            for path in home.rglob("*")
        )
        assert {path.name: path.read_bytes() for path in source_root.iterdir()} == input_snapshot
    finally:
        await personal_context.deactivate_runtime(timeout_seconds=5)
