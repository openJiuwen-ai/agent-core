from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.core.proactive_context import context_pipeline
from openjiuwen.core.proactive_context import pcs as pcs_module
from openjiuwen.core.proactive_context.config import PCSConfig, PCSFetchServiceConfig
from openjiuwen.core.proactive_context.fetch.base import ContextFetchService
from openjiuwen.core.proactive_context.models import FetchBatch, RawChangeItem
from openjiuwen.core.proactive_context.pcs import PCS
from openjiuwen.core.proactive_context.source_metadata import read_source_metadata, source_id_for_locator


def _config(tmp_path: Path, *, interval: float = 0.01) -> PCSConfig:
    return PCSConfig.from_dict(
        {
            "enabled": True,
            "fetching_enabled": True,
            "strategy_profile": "rules",
            "model_client": None,
            "model_request": None,
            "fetch_services": [
                {
                    "service_id": "notes",
                    "provider": "local_files",
                    "enabled": True,
                    "interval_seconds": interval,
                    "source": {"root_dir": str(tmp_path)},
                    "credentials": {},
                }
            ],
        }
    )


def _manual_config(
    tmp_path: Path,
    *,
    enabled: bool = True,
    fetching_enabled: bool = True,
    services: dict[str, bool] | None = None,
) -> PCSConfig:
    service_states = services or {"notes": True}
    return PCSConfig.from_dict(
        {
            "enabled": enabled,
            "fetching_enabled": fetching_enabled,
            "strategy_profile": "rules",
            "model_client": None,
            "model_request": None,
            "fetch_services": [
                {
                    "service_id": service_id,
                    "provider": "local_files",
                    "enabled": service_enabled,
                    "interval_seconds": 3600.0,
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


class _BlockingManualProvider(ContextFetchService):
    instances: dict[str, "_BlockingManualProvider"] = {}

    def __init__(self, config: PCSFetchServiceConfig, *, home: Path) -> None:
        super().__init__(config, home=home)
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.fail = False
        self.commit_calls: list[str] = []
        self.abort_calls: list[str] = []
        type(self).instances[config.service_id] = self

    async def fetch(self, *, run_id: str, cursor: dict[str, object] | None):
        del run_id, cursor
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


async def _ready_manual_pcs(tmp_path: Path, config: PCSConfig) -> PCS:
    pcs = PCS(home=tmp_path)
    await pcs.set_configuration(config)
    pcs._state = "RUNNING"
    pcs._pipeline_service = _RunningPipeline()  # type: ignore[assignment]
    return pcs


async def _finish_manual_tasks(pcs: PCS, service_ids: tuple[str, ...]) -> None:
    tasks = [pcs._manual_fetch_tasks[service_id] for service_id in service_ids]
    for service_id in service_ids:
        provider = _BlockingManualProvider.instances[service_id]
        await asyncio.wait_for(provider.started.wait(), timeout=1.0)
        provider.release.set()
    await asyncio.gather(*tasks)


class _Provider(ContextFetchService):
    fetch_calls = 0
    commit_calls: list[str] = []
    abort_calls: list[str] = []

    async def fetch(self, *, run_id: str, cursor: dict[str, object] | None):
        del cursor
        type(self).fetch_calls += 1
        yield FetchBatch(
            batch_id=f"batch-{type(self).fetch_calls}", items=(), next_cursor={"n": type(self).fetch_calls}
        )

    async def commit_run(self, *, run_id: str) -> None:
        type(self).commit_calls.append(run_id)

    async def abort_run(self, *, run_id: str) -> None:
        type(self).abort_calls.append(run_id)


@pytest.mark.asyncio
async def test_run_fetch_all_accepts_only_enabled_services_and_returns_before_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _BlockingManualProvider.instances = {}
    monkeypatch.setitem(
        pcs_module._PROVIDER_TYPES,
        "local_files",
        _BlockingManualProvider,
    )
    pcs = await _ready_manual_pcs(
        tmp_path,
        _manual_config(
            tmp_path,
            services={"b": True, "disabled": False, "a": True},
        ),
    )

    result = await pcs.run_fetch()

    assert result == {"state": "accepted", "service_ids": ["a", "b"]}
    assert set(pcs._manual_fetch_tasks) == {"a", "b"}
    assert all(not task.done() for task in pcs._manual_fetch_tasks.values())
    assert "disabled" not in _BlockingManualProvider.instances
    await _finish_manual_tasks(pcs, ("a", "b"))


@pytest.mark.asyncio
async def test_run_fetch_one_ignores_service_and_global_fetch_switches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _BlockingManualProvider.instances = {}
    monkeypatch.setitem(
        pcs_module._PROVIDER_TYPES,
        "local_files",
        _BlockingManualProvider,
    )
    pcs = await _ready_manual_pcs(
        tmp_path,
        _manual_config(
            tmp_path,
            fetching_enabled=False,
            services={"disabled": False},
        ),
    )

    result = await pcs.run_fetch(service_id="disabled")

    assert result == {
        "state": "accepted",
        "service_ids": ["disabled"],
    }
    await _finish_manual_tasks(pcs, ("disabled",))


@pytest.mark.asyncio
async def test_run_fetch_rejects_disabled_core_stopped_runtime_or_dead_pipeline(
    tmp_path: Path,
) -> None:
    unconfigured = PCS(home=tmp_path / "unconfigured")
    with pytest.raises(PCS.Error):
        await unconfigured.run_fetch()

    disabled = PCS(home=tmp_path / "disabled")
    await disabled.set_configuration(
        _manual_config(tmp_path, enabled=False),
    )
    with pytest.raises(PCS.Error):
        await disabled.run_fetch()

    stopped = PCS(home=tmp_path / "stopped")
    await stopped.set_configuration(_manual_config(tmp_path))
    stopped._state = "STOPPED"
    stopped._pipeline_service = _RunningPipeline()  # type: ignore[assignment]
    with pytest.raises(PCS.Error):
        await stopped.run_fetch()

    dead_pipeline = PCS(home=tmp_path / "dead")
    await dead_pipeline.set_configuration(_manual_config(tmp_path))
    dead_pipeline._state = "RUNNING"
    dead_pipeline._pipeline_service = _RunningPipeline(  # type: ignore[assignment]
        running=False
    )
    with pytest.raises(PCS.Error):
        await dead_pipeline.run_fetch()


@pytest.mark.asyncio
async def test_run_fetch_rejects_unknown_service_or_empty_all_target(
    tmp_path: Path,
) -> None:
    pcs = await _ready_manual_pcs(
        tmp_path,
        _manual_config(tmp_path, services={"disabled": False}),
    )

    with pytest.raises(PCS.Error):
        await pcs.run_fetch()
    with pytest.raises(PCS.Error):
        await pcs.run_fetch(service_id="missing")


@pytest.mark.asyncio
async def test_run_fetch_rejects_same_service_when_manual_or_scheduled_round_is_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _BlockingManualProvider.instances = {}
    monkeypatch.setitem(
        pcs_module._PROVIDER_TYPES,
        "local_files",
        _BlockingManualProvider,
    )
    pcs = await _ready_manual_pcs(tmp_path, _manual_config(tmp_path))
    await pcs.run_fetch(service_id="notes")

    with pytest.raises(PCS.Error) as manual_error:
        await pcs.run_fetch(service_id="notes")
    assert "notes" in str(manual_error.value)
    await _finish_manual_tasks(pcs, ("notes",))

    pcs._fetch_running.add("notes")
    with pytest.raises(PCS.Error) as scheduled_error:
        await pcs.run_fetch(service_id="notes")
    assert "notes" in str(scheduled_error.value)
    pcs._fetch_running.clear()


@pytest.mark.asyncio
async def test_run_fetch_all_rejects_atomically_when_one_target_is_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _BlockingManualProvider.instances = {}
    monkeypatch.setitem(
        pcs_module._PROVIDER_TYPES,
        "local_files",
        _BlockingManualProvider,
    )
    pcs = await _ready_manual_pcs(
        tmp_path,
        _manual_config(tmp_path, services={"a": True, "b": True}),
    )
    pcs._fetch_running.add("a")

    with pytest.raises(PCS.Error) as caught:
        await pcs.run_fetch()

    assert "a" in str(caught.value)
    assert pcs._manual_fetch_tasks == {}
    assert _BlockingManualProvider.instances == {}
    assert pcs._fetch_running == {"a"}


@pytest.mark.asyncio
async def test_run_fetch_allows_different_services_to_fetch_concurrently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _BlockingManualProvider.instances = {}
    monkeypatch.setitem(
        pcs_module._PROVIDER_TYPES,
        "local_files",
        _BlockingManualProvider,
    )
    pcs = await _ready_manual_pcs(
        tmp_path,
        _manual_config(tmp_path, services={"a": True, "b": True}),
    )
    queue = pcs._pipeline_queue

    first = await pcs.run_fetch(service_id="a")
    second = await pcs.run_fetch(service_id="b")

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
    assert pcs._pipeline_queue is queue
    assert pcs._fetch_running == {"a", "b"}
    await _finish_manual_tasks(pcs, ("a", "b"))


@pytest.mark.asyncio
async def test_stop_fetch_service_waits_for_manual_round_before_returning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _BlockingManualProvider.instances = {}
    monkeypatch.setitem(
        pcs_module._PROVIDER_TYPES,
        "local_files",
        _BlockingManualProvider,
    )
    pcs = await _ready_manual_pcs(
        tmp_path,
        _manual_config(
            tmp_path,
            fetching_enabled=False,
            services={"notes": False},
        ),
    )
    await pcs.run_fetch(service_id="notes")
    manual_task = pcs._manual_fetch_tasks["notes"]
    provider = _BlockingManualProvider.instances["notes"]
    await asyncio.wait_for(provider.started.wait(), timeout=1.0)
    stop_task = asyncio.create_task(pcs.stop_fetch_service("notes", timeout_seconds=1.0))

    try:
        await asyncio.sleep(0)
        assert not stop_task.done()
    finally:
        provider.release.set()
        await asyncio.gather(manual_task, stop_task, return_exceptions=True)

    assert "notes" not in pcs._manual_fetch_tasks
    assert "notes" not in pcs._fetch_running
    assert pcs._fetch_states["notes"] == "STOPPED"
    assert (tmp_path / "state" / "cursors" / "notes.json").is_file()


@pytest.mark.asyncio
async def test_manual_fetch_stop_timeout_cancels_and_aborts_without_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _BlockingManualProvider.instances = {}
    monkeypatch.setitem(
        pcs_module._PROVIDER_TYPES,
        "local_files",
        _BlockingManualProvider,
    )
    pcs = await _ready_manual_pcs(
        tmp_path,
        _manual_config(
            tmp_path,
            fetching_enabled=False,
            services={"notes": False},
        ),
    )
    await pcs.run_fetch(service_id="notes")
    manual_task = pcs._manual_fetch_tasks["notes"]
    provider = _BlockingManualProvider.instances["notes"]
    await asyncio.wait_for(provider.started.wait(), timeout=1.0)

    try:
        with pytest.raises(PCS.Error):
            await pcs.stop_fetch_service("notes", timeout_seconds=0.01)
    finally:
        if not manual_task.done():
            manual_task.cancel()
        await asyncio.gather(manual_task, return_exceptions=True)

    assert provider.abort_calls
    assert not (tmp_path / "state" / "cursors" / "notes.json").exists()
    assert "notes" not in pcs._manual_fetch_tasks
    assert "notes" not in pcs._fetch_running


@pytest.mark.asyncio
async def test_deactivate_runtime_waits_for_manual_round_and_stops_pipeline_after_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _BlockingManualProvider.instances = {}
    monkeypatch.setitem(
        pcs_module._PROVIDER_TYPES,
        "local_files",
        _BlockingManualProvider,
    )
    pcs = await _ready_manual_pcs(
        tmp_path,
        _manual_config(
            tmp_path,
            fetching_enabled=False,
            services={"notes": False},
        ),
    )
    pipeline = pcs._pipeline_service
    assert isinstance(pipeline, _RunningPipeline)
    order: list[str] = []

    async def commit_run(*, run_id: str) -> None:
        del run_id
        order.append("fetch")

    async def stop_pipeline(*, timeout_seconds: float) -> None:
        del timeout_seconds
        order.append("pipeline")
        pipeline.running = False

    await pcs.run_fetch(service_id="notes")
    manual_task = pcs._manual_fetch_tasks["notes"]
    provider = _BlockingManualProvider.instances["notes"]
    provider.commit_run = commit_run  # type: ignore[method-assign]
    pipeline.stop = stop_pipeline  # type: ignore[method-assign]
    await asyncio.wait_for(provider.started.wait(), timeout=1.0)
    deactivate_task = asyncio.create_task(pcs.deactivate_runtime(timeout_seconds=1.0))

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
    assert pcs._state == "STOPPED"


@pytest.mark.asyncio
async def test_manual_fetch_failure_is_reported_without_task_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _BlockingManualProvider.instances = {}
    monkeypatch.setitem(
        pcs_module._PROVIDER_TYPES,
        "local_files",
        _BlockingManualProvider,
    )
    pcs = await _ready_manual_pcs(
        tmp_path,
        _manual_config(
            tmp_path,
            fetching_enabled=False,
            services={"notes": False},
        ),
    )
    await pcs.run_fetch(service_id="notes")
    manual_task = pcs._manual_fetch_tasks["notes"]
    provider = _BlockingManualProvider.instances["notes"]
    provider.fail = True
    provider.release.set()

    await manual_task

    status = await pcs.snapshot()
    assert status.fetch_service_errors["notes"]
    assert status.fetch_service_states["notes"] == "FAILED"
    assert "notes" not in pcs._manual_fetch_tasks
    assert "notes" not in pcs._fetch_running


class _TwoBatchProvider(ContextFetchService):
    def __init__(self, config: PCSFetchServiceConfig, *, home: Path) -> None:
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

    async def fetch(self, *, run_id: str, cursor: dict[str, object] | None):
        del run_id, cursor
        for batch in self.batches:
            yield batch

    async def commit_run(self, *, run_id: str) -> None:
        self.commit_calls.append(run_id)

    async def abort_run(self, *, run_id: str) -> None:
        self.abort_calls.append(run_id)


async def _two_batch_run(tmp_path: Path) -> tuple[PCS, _TwoBatchProvider]:
    pcs = PCS(home=tmp_path)
    await pcs.set_configuration(_config(tmp_path))
    assert pcs._config is not None
    provider = _TwoBatchProvider(pcs._config.fetch_services[0], home=tmp_path)
    pcs._write_cursor("notes", {"n": 0})
    return pcs, provider


async def _next_pipeline_event(pcs: PCS) -> tuple[tuple[object, ...], asyncio.Future[None]]:
    event = await pcs._pipeline_queue.get()
    assert isinstance(event, tuple) and len(event) == 5
    completion = event[4]
    assert isinstance(completion, asyncio.Future)
    return event, completion


def _record_abort_order(pcs: PCS, provider: _TwoBatchProvider) -> list[str]:
    order: list[str] = []

    async def pipeline_abort(_service_id: str, _run_id: str) -> None:
        order.append("pipeline")

    async def provider_abort(*, run_id: str) -> None:
        del run_id
        order.append("provider")

    pcs._abort_pipeline_run = pipeline_abort  # type: ignore[method-assign]
    provider.abort_run = provider_abort  # type: ignore[method-assign]
    return order


def _record_pipeline_calls(
    pcs: PCS,
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
        assert pcs._read_cursor("notes") == {"n": 0}
        if failure_stage == "finish":
            raise RuntimeError("finish failed")

    async def abort(service_id: str, run_id: str) -> None:
        events.append(("abort", service_id, run_id))

    pcs._submit_batch = submit  # type: ignore[method-assign]
    pcs._finish_pipeline_run = finish  # type: ignore[method-assign]
    pcs._abort_pipeline_run = abort  # type: ignore[method-assign]
    return events


@pytest.mark.asyncio
async def test_first_fetch_waits_for_interval_and_commits_atomic_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Provider.fetch_calls = 0
    _Provider.commit_calls = []
    _Provider.abort_calls = []
    monkeypatch.setitem(pcs_module._PROVIDER_TYPES, "local_files", _Provider)
    pcs = PCS(home=tmp_path)
    await pcs.set_configuration(_config(tmp_path))
    pcs._state = "RUNNING"
    pcs._pipeline_service = type("Pipeline", (), {"is_running": lambda self: True})()
    submitted: list[FetchBatch] = []

    async def submit(_service_id: str, _run_id: str, _batch: FetchBatch) -> None:
        submitted.append(_batch)

    pcs._submit_batch = submit  # type: ignore[assignment]
    await pcs.start_fetch_service("notes")
    await asyncio.sleep(0.003)
    assert _Provider.fetch_calls == 0
    await asyncio.sleep(0.03)
    assert _Provider.fetch_calls >= 1
    assert submitted == []
    await pcs.stop_fetch_service("notes", timeout_seconds=1)
    cursor = tmp_path / "state" / "cursors" / "notes.json"
    assert cursor.is_file()
    assert cursor.read_text(encoding="utf-8").find('"source_fingerprint"') >= 0


@pytest.mark.asyncio
async def test_cancelled_round_aborts_without_cursor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class BlockingProvider(ContextFetchService):
        async def fetch(self, *, run_id: str, cursor: dict[str, object] | None):
            del cursor
            yield FetchBatch(batch_id="batch-1", items=(), next_cursor={"n": 1})
            await asyncio.Event().wait()

        async def abort_run(self, *, run_id: str) -> None:
            self.aborted = run_id

    pcs = PCS(home=tmp_path)
    await pcs.set_configuration(_config(tmp_path))
    assert pcs._config is not None
    provider = BlockingProvider(pcs._config.fetch_services[0], home=tmp_path)
    task = asyncio.create_task(pcs._run_fetch_once("notes", provider))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (tmp_path / "state" / "cursors" / "notes.json").exists()


@pytest.mark.asyncio
async def test_multi_batch_run_finishes_pipeline_before_provider_commit_and_cursor(tmp_path: Path) -> None:
    pcs, provider = await _two_batch_run(tmp_path)
    events = _record_pipeline_calls(pcs, provider)

    await pcs._run_fetch_once("notes", provider)

    run_id = provider.commit_calls[0]
    batches = [event[3] for event in events if event[0] == "batch"]
    assert events == [
        ("batch", "notes", run_id, batches[0]),
        ("batch", "notes", run_id, batches[1]),
        ("finish", "notes", run_id),
    ]
    assert provider.abort_calls == []
    assert pcs._read_cursor("notes") == {"n": 2}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["second_batch", "finish"])
async def test_multi_batch_failure_aborts_pipeline_and_preserves_old_cursor(
    tmp_path: Path,
    failure_stage: str,
) -> None:
    pcs, provider = await _two_batch_run(tmp_path)
    events = _record_pipeline_calls(pcs, provider, failure_stage=failure_stage)

    with pytest.raises(Exception):
        await pcs._run_fetch_once("notes", provider)

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
    assert pcs._read_cursor("notes") == {"n": 0}


@pytest.mark.asyncio
async def test_empty_only_run_commits_cursor_without_pipeline_or_sandbox(tmp_path: Path) -> None:
    class EmptyProvider(ContextFetchService):
        def __init__(self, config: PCSFetchServiceConfig, *, home: Path) -> None:
            super().__init__(config, home=home)
            self.commit_calls: list[str] = []

        async def fetch(self, *, run_id: str, cursor: dict[str, object] | None):
            del run_id, cursor
            yield FetchBatch(batch_id="empty", items=(), next_cursor={"n": 1})

        async def commit_run(self, *, run_id: str) -> None:
            self.commit_calls.append(run_id)

    pcs = PCS(home=tmp_path)
    await pcs.set_configuration(_config(tmp_path))
    assert pcs._config is not None
    provider = EmptyProvider(pcs._config.fetch_services[0], home=tmp_path)
    pipeline_calls: list[str] = []

    async def unexpected(*_args: object) -> None:
        pipeline_calls.append("called")

    pcs._submit_batch = unexpected  # type: ignore[method-assign]
    pcs._finish_pipeline_run = unexpected  # type: ignore[attr-defined]
    pcs._abort_pipeline_run = unexpected  # type: ignore[attr-defined]

    await pcs._run_fetch_once("notes", provider)

    assert len(provider.commit_calls) == 1
    assert pipeline_calls == []
    assert pcs._read_cursor("notes") == {"n": 1}
    assert not (tmp_path / "workspace" / "sandboxes").exists()


@pytest.mark.asyncio
async def test_private_pipeline_helpers_submit_one_tagged_queue_contract(tmp_path: Path) -> None:
    pcs, provider = await _two_batch_run(tmp_path)

    for submit, expected_tag, expected_payload in (
        (lambda: pcs._submit_batch("notes", "run-1", provider.batches[0]), "batch", provider.batches[0]),
        (lambda: pcs._finish_pipeline_run("notes", "run-1"), "finish", None),
        (lambda: pcs._abort_pipeline_run("notes", "run-1"), "abort", None),
    ):
        task = asyncio.create_task(submit())
        event, completion = await _next_pipeline_event(pcs)
        tag, service_id, run_id, payload, _ = event
        assert (tag, service_id, run_id, payload) == (expected_tag, "notes", "run-1", expected_payload)
        completion.set_result(None)
        await task


@pytest.mark.asyncio
async def test_cancelled_before_first_batch_is_enqueued_does_not_submit_pipeline_abort(tmp_path: Path) -> None:
    pcs, provider = await _two_batch_run(tmp_path)
    pcs._pipeline_queue = asyncio.Queue(maxsize=1)
    sentinel = object()
    await pcs._pipeline_queue.put(sentinel)
    pipeline_abort_calls: list[tuple[str, str]] = []

    async def pipeline_abort(service_id: str, run_id: str) -> None:
        pipeline_abort_calls.append((service_id, run_id))

    pcs._abort_pipeline_run = pipeline_abort  # type: ignore[method-assign]
    task = asyncio.create_task(pcs._run_fetch_once("notes", provider))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.5)

    assert pipeline_abort_calls == []
    assert len(provider.abort_calls) == 1
    assert pcs._read_cursor("notes") == {"n": 0}
    assert pcs._pipeline_queue.get_nowait() is sentinel
    assert pcs._pipeline_queue.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize("waiting_for", ["batch", "finish"])
async def test_cancelled_after_pipeline_enqueue_aborts_in_order(tmp_path: Path, waiting_for: str) -> None:
    pcs, provider = await _two_batch_run(tmp_path)
    abort_order = _record_abort_order(pcs, provider)
    task = asyncio.create_task(pcs._run_fetch_once("notes", provider))
    run_id: str | None = None
    completed_batches = provider.batches if waiting_for == "finish" else ()
    for batch in completed_batches:
        event, completion = await _next_pipeline_event(pcs)
        if run_id is None:
            assert isinstance(event[2], str)
            run_id = event[2]
        assert event[:4] == ("batch", "notes", run_id, batch)
        completion.set_result(None)

    pending_event, pending_completion = await _next_pipeline_event(pcs)
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
    assert pcs._read_cursor("notes") == {"n": 0}


@pytest.mark.asyncio
async def test_stop_timeout_keeps_uncancellable_task_for_follow_up_cleanup(tmp_path: Path) -> None:
    release = asyncio.Event()

    async def stubborn_task() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    pcs = PCS(home=tmp_path)
    await pcs.set_configuration(_config(tmp_path))
    assert pcs._config is not None
    pcs._state = "RUNNING"
    task = asyncio.create_task(stubborn_task())
    await asyncio.sleep(0)
    pcs._fetch_tasks["notes"] = task
    pcs._fetch_stop_events["notes"] = asyncio.Event()
    pcs._fetch_providers["notes"] = _Provider(pcs._config.fetch_services[0], home=tmp_path)
    pcs._fetch_states["notes"] = "RUNNING"

    with pytest.raises(Exception):
        await pcs.stop_fetch_service("notes", timeout_seconds=0.01)
    assert pcs._fetch_tasks["notes"] is task
    assert (await pcs.snapshot()).fetch_service_states["notes"] == "FAILED"

    release.set()
    await pcs.stop_fetch_service("notes", timeout_seconds=1)
    assert "notes" not in pcs._fetch_tasks


@pytest.mark.asyncio
async def test_second_stop_cleans_fetch_task_cancelled_after_pipeline_abort(tmp_path: Path) -> None:
    pcs, provider = await _two_batch_run(tmp_path)
    pcs._state = "RUNNING"
    task = asyncio.create_task(pcs._run_fetch_once("notes", provider))
    pcs._fetch_tasks["notes"] = task
    pcs._fetch_stop_events["notes"] = asyncio.Event()
    pcs._fetch_providers["notes"] = provider
    pcs._fetch_states["notes"] = "RUNNING"

    batch_event, batch_completion = await _next_pipeline_event(pcs)
    assert batch_event[0] == "batch"

    with pytest.raises(Exception):
        await pcs.stop_fetch_service("notes", timeout_seconds=0.01)
    assert pcs._fetch_tasks["notes"] is task
    assert batch_completion.cancelled()

    abort_event, abort_completion = await asyncio.wait_for(_next_pipeline_event(pcs), timeout=0.5)
    assert abort_event[:4] == ("abort", "notes", batch_event[2], None)
    abort_completion.set_result(None)
    done, pending = await asyncio.wait({task}, timeout=0.5)
    assert pending == set()
    assert done == {task}
    assert task.cancelled()
    assert len(provider.abort_calls) == 1
    assert pcs._read_cursor("notes") == {"n": 0}

    await pcs.stop_fetch_service("notes", timeout_seconds=1)
    assert "notes" not in pcs._fetch_tasks


@pytest.mark.asyncio
async def test_failed_finish_keeps_old_context_cursor_and_source_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SingleBatchProvider(ContextFetchService):
        def __init__(
            self,
            config: PCSFetchServiceConfig,
            *,
            home: Path,
            batch: FetchBatch,
        ) -> None:
            super().__init__(config, home=home)
            self.batch = batch
            self.commit_calls: list[str] = []
            self.abort_calls: list[str] = []

        async def fetch(self, *, run_id: str, cursor: dict[str, object] | None):
            del run_id, cursor
            yield self.batch

        async def commit_run(self, *, run_id: str) -> None:
            self.commit_calls.append(run_id)

        async def abort_run(self, *, run_id: str) -> None:
            self.abort_calls.append(run_id)

    pcs = PCS(home=tmp_path)
    await pcs.set_configuration(_config(tmp_path, interval=3600))
    assert pcs._config is not None
    service_config = pcs._config.fetch_services[0]
    monkeypatch.setattr(pcs_module, "uuid4", lambda: SimpleNamespace(hex="run"))
    await pcs.activate_runtime()
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
        await pcs._run_fetch_once("notes", upsert)
        source_metadata = next((tmp_path / "workspace" / "source-meta").glob("src_*.md"))
        old_page = next(
            path for path in (tmp_path / "workspace" / "context").rglob("*.md") if path.name != "description.md"
        )
        assert pcs._read_cursor("notes") == {"n": 1}

        original_publish = context_pipeline._copy_and_publish_tree

        def fail_context_publish(candidate: Path, target: Path, *, skip_relative: str | None = None) -> set[str]:
            if target == tmp_path / "workspace" / "context":
                raise OSError("injected context publication failure")
            return original_publish(candidate, target, skip_relative=skip_relative)

        monkeypatch.setattr(context_pipeline, "_copy_and_publish_tree", fail_context_publish)
        delete = SingleBatchProvider(
            service_config,
            home=tmp_path,
            batch=FetchBatch(
                batch_id="delete",
                items=(
                    RawChangeItem(
                        logical_id="notes/one",
                        revision_id="rev-2",
                        operation="delete",
                        title=None,
                        content=None,
                        original_ref="file:///notes/one",
                    ),
                ),
                next_cursor={"n": 2},
            ),
        )

        with pytest.raises(Exception):
            await pcs._run_fetch_once("notes", delete)

        assert pcs._read_cursor("notes") == {"n": 1}
        assert delete.commit_calls == []
        assert len(delete.abort_calls) == 1
        assert old_page.is_file()
        assert source_metadata.is_file()
        assert not (tmp_path / "workspace" / "source-proofs").exists()
    finally:
        await pcs.deactivate_runtime(timeout_seconds=1)


@pytest.mark.asyncio
async def test_retry_replaces_failed_same_revision_context_without_persistent_source_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SingleBatchProvider(ContextFetchService):
        def __init__(
            self,
            config: PCSFetchServiceConfig,
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

        async def fetch(self, *, run_id: str, cursor: dict[str, object] | None):
            del run_id, cursor
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

    config = PCSConfig.from_dict(
        {
            "enabled": True,
            "fetching_enabled": True,
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
    monkeypatch.setattr(context_pipeline, "run_pcs_agent", filesystem_agent)
    monkeypatch.setattr(pcs_module, "uuid4", lambda: SimpleNamespace(hex="run"))

    pcs = PCS(home=tmp_path)
    await pcs.set_configuration(config)
    pcs._write_cursor("notes", {"n": 0})
    assert pcs._config is not None
    service_config = pcs._config.fetch_services[0]
    await pcs.activate_runtime()
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
            await pcs._run_fetch_once("notes", first)
        assert pcs._read_cursor("notes") == {"n": 0}
        assert list((tmp_path / "workspace" / "source-meta").glob("src_*.md"))
        assert not (tmp_path / "workspace" / "source-proofs").exists()
        assert not list((tmp_path / "workspace" / "sandboxes").rglob("content.md"))

        second = SingleBatchProvider(
            service_config,
            home=tmp_path,
            content="Second raw body.",
            next_cursor=2,
        )
        await pcs._run_fetch_once("notes", second)

        workspace = tmp_path / "workspace"
        page = workspace / "context" / "topics" / "page-2.md"
        assert "Agent body 2." in page.read_text(encoding="utf-8")
        assert not (workspace / "context" / "topics" / "page-1.md").exists()
        assert not (workspace / "source-proofs").exists()
        assert not list((workspace / "sandboxes").rglob("content.md"))
        assert pcs._read_cursor("notes") == {"n": 2}
        assert second.commit_calls
        assert second.abort_calls == []
    finally:
        await pcs.deactivate_runtime(timeout_seconds=1)


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
    home = tmp_path / "pcs-home"
    config = PCSConfig.from_dict(
        {
            "enabled": True,
            "fetching_enabled": False,
            "strategy_profile": "rules",
            "model_client": None,
            "model_request": None,
            "fetch_services": [
                {
                    "service_id": service_id,
                    "provider": "local_files",
                    "enabled": True,
                    "interval_seconds": 3600.0,
                    "source": {"root_dir": str(source_root)},
                    "credentials": {},
                }
                for service_id in ("notes-a", "notes-b")
            ],
        }
    )
    pcs = PCS(home=home)
    await pcs.set_configuration(config)
    await pcs.activate_runtime()
    try:
        assert await pcs.run_fetch() == {
            "state": "accepted",
            "service_ids": ["notes-a", "notes-b"],
        }
        for _attempt in range(500):
            status = await pcs.snapshot()
            cursor_files = sorted((home / "state" / "cursors").glob("*.json"))
            if (
                status.context_ready
                and len(cursor_files) == 2
                and status.fetch_service_states == {"notes-a": "STOPPED", "notes-b": "STOPPED"}
            ):
                break
            assert status.fetch_service_errors == {}
            await asyncio.sleep(0.01)
        else:
            pytest.fail("public PCS fetch lifecycle did not finish")

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
            assert "pcs_logical_ids" not in text
            assert not text.startswith("---\n")

        graph = await pcs.get_graph()
        nodes = {str(node["id"]): node for node in graph["nodes"]}
        edges = [(str(edge["source"]), str(edge["target"]), str(edge["kind"])) for edge in graph["edges"]]
        assert graph["context_ready"] is True
        assert {node_id for node_id in nodes if node_id.startswith("source:")} == {
            f"source:{source_id}" for source_id in expected_source_ids
        }
        assert all(nodes[f"source:{source_id}"]["kind"] == "source" for source_id in expected_source_ids)
        assert all(nodes[f"source:{source_id}"]["subkind"] == "source.0" for source_id in expected_source_ids)
        assert all("source-meta" not in node_id for node_id in nodes)

        adjacency: dict[str, set[str]] = {}
        for source, target, _kind in edges:
            adjacency.setdefault(source, set()).add(target)
        for page_id in (node_id for node_id in nodes if node_id.startswith("page:")):
            pending = [page_id]
            visited: set[str] = set()
            while pending:
                current = pending.pop()
                if current in visited:
                    continue
                visited.add(current)
                pending.extend(adjacency.get(current, set()) - visited)
            assert any(node_id.startswith("source:") for node_id in visited), page_id

        for source_id in expected_source_ids:
            detail = await pcs.get_graph_page(f"source:{source_id}")
            assert detail["node_id"] == f"source:{source_id}"
            assert detail["path"] == f"{source_id}.md"
            assert detail["markdown"] == (home / "workspace" / "source-meta" / f"{source_id}.md").read_text(
                encoding="utf-8"
            )

        cursor_files = sorted((home / "state" / "cursors").glob("*.json"))
        assert {path.stem for path in cursor_files} == {"notes-a", "notes-b"}
        assert all(json.loads(path.read_text(encoding="utf-8"))["cursor"]["files"] for path in cursor_files)
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
        await pcs.deactivate_runtime(timeout_seconds=5)
