from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.proactive_context.config import PCSConfig
from openjiuwen.core.proactive_context.pcs import PCS
from openjiuwen.core.proactive_context.status_codes import StatusCode, build_error


def _config(
    *,
    enabled: bool = True,
    fetching_enabled: bool = True,
    service_enabled: bool = True,
) -> PCSConfig:
    return PCSConfig.from_dict(
        {
            "enabled": enabled,
            "fetching_enabled": fetching_enabled,
            "strategy_profile": "rules",
            "model_client": None,
            "model_request": None,
            "fetch_services": [
                {
                    "service_id": "notes",
                    "provider": "local_files",
                    "enabled": service_enabled,
                    "interval_seconds": 60,
                    "source": {"root_dir": str(Path.cwd())},
                    "credentials": {},
                }
            ],
        }
    )


@pytest.mark.asyncio
async def test_construct_does_not_start_and_activate_requires_configuration(tmp_path: Path) -> None:
    pcs = PCS(home=tmp_path)
    status = await pcs.snapshot()
    assert status.state == "CREATED"
    assert not status.configured
    with pytest.raises(BaseError):
        await pcs.activate_runtime()


@pytest.mark.asyncio
async def test_authorize_feishu_returns_ready_when_lark_cli_scope_is_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openjiuwen.core.proactive_context.pcs as pcs_module

    async def ready_auth_status(_scopes: tuple[str, ...]) -> tuple[bool, set[str]]:
        return _ready_auth_status()

    monkeypatch.setattr(pcs_module, "_lark_cli_auth_status", ready_auth_status)
    pcs = PCS(home=tmp_path)
    await pcs.set_configuration(
        PCSConfig.from_dict(
            {
                "enabled": True,
                "fetching_enabled": True,
                "strategy_profile": "rules",
                "fetch_services": [
                    {
                        "service_id": "feishu",
                        "provider": "feishu",
                        "enabled": True,
                        "interval_seconds": 60,
                        "source": {"mode": "wiki_space", "wiki_space_id": "space-1"},
                        "credentials": {},
                    }
                ],
            }
        )
    )

    result = await pcs.authorize_provider("feishu")

    assert result == {"provider": "feishu", "state": "ready", "verification_url": None, "expires_at": None}


def _ready_auth_status() -> tuple[bool, set[str]]:
    return True, {"wiki:node:retrieve", "docs:document.content:read", "drive:file:download"}


@pytest.mark.asyncio
async def test_disabled_configuration_does_not_create_runtime_tasks(tmp_path: Path) -> None:
    pcs = PCS(home=tmp_path)
    await pcs.set_configuration(_config(enabled=False))
    await pcs.activate_runtime()
    status = await pcs.snapshot()
    assert status.state == "CONFIGURED"
    assert not status.pipeline_running
    assert pcs._fetch_tasks == {}


@pytest.mark.asyncio
async def test_fetching_disabled_starts_pipeline_without_provider_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RunningPipeline:
        def __init__(self, **_kwargs: object) -> None:
            self.running = False

        async def start(self) -> None:
            self.running = True

        async def stop(self, *, timeout_seconds: float) -> None:
            del timeout_seconds
            self.running = False

        def is_running(self) -> bool:
            return self.running

    monkeypatch.setattr(
        "openjiuwen.core.proactive_context.pcs.ContextPipelineService",
        RunningPipeline,
    )
    pcs = PCS(home=tmp_path)
    await pcs.set_configuration(_config(fetching_enabled=False))
    await pcs.activate_runtime()

    status = await pcs.snapshot()
    assert status.state == "RUNNING"
    assert status.pipeline_running is True
    assert status.fetching_enabled is False
    assert status.fetch_service_states == {"notes": "STOPPED"}
    assert pcs._fetch_tasks == {}

    with pytest.raises(BaseError):
        await pcs.start_fetch_service("notes")
    await pcs.deactivate_runtime(timeout_seconds=1)


@pytest.mark.asyncio
async def test_concurrent_activate_waits_for_one_pipeline_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    instances = 0

    class FakePipeline:
        def __init__(self, **_kwargs: object) -> None:
            nonlocal instances
            instances += 1
            self.running = False

        async def start(self) -> None:
            started.set()
            await release.wait()
            self.running = True

        async def stop(self, *, timeout_seconds: float) -> None:
            del timeout_seconds

        def is_running(self) -> bool:
            return self.running

    monkeypatch.setattr("openjiuwen.core.proactive_context.pcs.ContextPipelineService", FakePipeline)
    pcs = PCS(home=tmp_path)
    await pcs.set_configuration(_config())
    first = asyncio.create_task(pcs.activate_runtime())
    await started.wait()
    second = asyncio.create_task(pcs.activate_runtime())
    await asyncio.sleep(0)
    assert instances == 1
    release.set()
    await asyncio.gather(first, second)
    assert (await pcs.snapshot()).state == "RUNNING"
    await pcs.deactivate_runtime(timeout_seconds=1)


@pytest.mark.asyncio
async def test_deactivate_stops_fetch_services_concurrently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pcs = PCS(home=tmp_path)
    await pcs.set_configuration(_config())
    pcs._state = "RUNNING"
    completed_tasks = [asyncio.create_task(asyncio.sleep(0)) for _ in range(2)]
    await asyncio.gather(*completed_tasks)
    pcs._fetch_tasks = {"first": completed_tasks[0], "second": completed_tasks[1]}

    release = asyncio.Event()
    both_started = asyncio.Event()
    active = 0
    peak = 0

    async def stop_fetch_service(service_id: str, *, timeout_seconds: float) -> None:
        del service_id, timeout_seconds
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            both_started.set()
        await release.wait()
        active -= 1

    monkeypatch.setattr(pcs, "stop_fetch_service", stop_fetch_service)
    stopping = asyncio.create_task(pcs.deactivate_runtime(timeout_seconds=1))
    await both_started.wait()
    assert peak == 2
    release.set()
    await stopping
    assert (await pcs.snapshot()).state == "STOPPED"


@pytest.mark.asyncio
async def test_snapshot_reports_failed_when_running_pipeline_has_stopped(tmp_path: Path) -> None:
    class StoppedPipeline:
        def is_running(self) -> bool:
            return False

    pcs = PCS(home=tmp_path)
    await pcs.set_configuration(_config())
    pcs._state = "RUNNING"
    pcs._pipeline_service = StoppedPipeline()  # type: ignore[assignment]
    status = await pcs.snapshot()
    assert status.state == "FAILED"
    assert status.last_error is not None
    assert status.last_error["operation"] == "snapshot"


@pytest.mark.asyncio
async def test_deactivate_during_starting_consumes_internal_activation_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()

    class DelayedPipeline:
        def __init__(self, **_kwargs: object) -> None:
            self.running = False

        async def start(self) -> None:
            started.set()
            await asyncio.Event().wait()

        async def stop(self, *, timeout_seconds: float) -> None:
            del timeout_seconds
            self.running = False

        def is_running(self) -> bool:
            return self.running

    monkeypatch.setattr("openjiuwen.core.proactive_context.pcs.ContextPipelineService", DelayedPipeline)
    pcs = PCS(home=tmp_path)
    await pcs.set_configuration(_config())
    activation = asyncio.create_task(pcs.activate_runtime())
    await started.wait()
    await pcs.deactivate_runtime(timeout_seconds=1)
    with pytest.raises(asyncio.CancelledError):
        await activation
    assert (await pcs.snapshot()).state == "STOPPED"


@pytest.mark.asyncio
async def test_deactivate_reports_activation_failure_instead_of_ignoring_it(tmp_path: Path) -> None:
    started = asyncio.Event()

    async def fail_during_cancellation() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            raise RuntimeError("activation cleanup failed") from exc

    pcs = PCS(home=tmp_path)
    await pcs.set_configuration(_config())
    activation = asyncio.create_task(fail_during_cancellation())
    await started.wait()
    pcs._state = "STARTING"
    pcs._activation_task = activation

    with pytest.raises(BaseError):
        await pcs.deactivate_runtime(timeout_seconds=1)

    status = await pcs.snapshot()
    assert status.state == "FAILED"
    assert status.last_error is not None
    assert status.last_error["operation"] == "deactivate_runtime"


@pytest.mark.asyncio
async def test_deactivate_retains_pipeline_when_stop_does_not_finish(tmp_path: Path) -> None:
    release = asyncio.Event()
    stop_started = asyncio.Event()

    class StubbornPipeline:
        def __init__(self) -> None:
            self.running = True

        def is_running(self) -> bool:
            return self.running

        async def stop(self, *, timeout_seconds: float) -> None:
            del timeout_seconds
            if release.is_set():
                self.running = False
                return
            stop_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
            self.running = False

    pcs = PCS(home=tmp_path)
    await pcs.set_configuration(_config())
    pcs._state = "RUNNING"
    pipeline = StubbornPipeline()
    pcs._pipeline_service = pipeline  # type: ignore[assignment]
    stopping = asyncio.create_task(pcs.deactivate_runtime(timeout_seconds=0.01))
    await stop_started.wait()
    with pytest.raises(BaseError):
        await stopping
    assert pcs._pipeline_service is pipeline
    assert (await pcs.snapshot()).state == "FAILED"

    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await pcs.deactivate_runtime(timeout_seconds=1)
    assert pcs._pipeline_service is None


@pytest.mark.asyncio
async def test_deactivate_stop_error_sets_failed_when_pipeline_reports_running(tmp_path: Path) -> None:
    class FailedPipeline:
        def is_running(self) -> bool:
            return True

        async def stop(self, *, timeout_seconds: float) -> None:
            del timeout_seconds
            raise build_error(StatusCode.CONTEXT_PROACTIVE_RUNTIME_TIMEOUT, error_msg="pipeline stop timed out")

    pcs = PCS(home=tmp_path)
    await pcs.set_configuration(_config())
    pcs._state = "RUNNING"
    pipeline = FailedPipeline()
    pcs._pipeline_service = pipeline  # type: ignore[assignment]
    with pytest.raises(BaseError):
        await pcs.deactivate_runtime(timeout_seconds=0.01)
    assert pcs._pipeline_service is pipeline
    assert (await pcs.snapshot()).state == "FAILED"


@pytest.mark.asyncio
async def test_deactivate_retains_activation_task_when_cancel_does_not_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class StubbornPipeline:
        def __init__(self, **_kwargs: object) -> None:
            self.running = True

        async def start(self) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()

        async def stop(self, *, timeout_seconds: float) -> None:
            del timeout_seconds
            if release.is_set():
                self.running = False

        def is_running(self) -> bool:
            return self.running

    monkeypatch.setattr("openjiuwen.core.proactive_context.pcs.ContextPipelineService", StubbornPipeline)
    pcs = PCS(home=tmp_path)
    empty_config = PCSConfig.from_dict(
        {
            "enabled": True,
            "fetching_enabled": True,
            "strategy_profile": "rules",
            "fetch_services": [],
        }
    )
    await pcs.set_configuration(empty_config)
    activation = asyncio.create_task(pcs.activate_runtime())
    await started.wait()
    with pytest.raises(BaseError):
        await pcs.deactivate_runtime(timeout_seconds=0.01)
    assert pcs._activation_task is not None
    assert (await pcs.snapshot()).state == "FAILED"

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await activation
    await pcs.deactivate_runtime(timeout_seconds=1)


@pytest.mark.asyncio
async def test_reactivation_replaces_pipeline_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queues: list[asyncio.Queue[object]] = []

    class QueuePipeline:
        def __init__(self, *, input_queue: asyncio.Queue[object], **_kwargs: object) -> None:
            queues.append(input_queue)
            self.running = False

        async def start(self) -> None:
            self.running = True

        async def stop(self, *, timeout_seconds: float) -> None:
            del timeout_seconds
            self.running = False

        def is_running(self) -> bool:
            return self.running

    monkeypatch.setattr("openjiuwen.core.proactive_context.pcs.ContextPipelineService", QueuePipeline)
    pcs = PCS(home=tmp_path)
    await pcs.set_configuration(
        PCSConfig.from_dict(
            {
                "enabled": True,
                "fetching_enabled": True,
                "strategy_profile": "rules",
                "fetch_services": [],
            }
        )
    )
    await pcs.activate_runtime()
    first_queue = pcs._pipeline_queue
    assert first_queue.maxsize == 8
    await pcs.deactivate_runtime(timeout_seconds=1)
    await pcs.activate_runtime()
    second_queue = pcs._pipeline_queue
    assert second_queue.maxsize == 8
    assert second_queue is not first_queue
    assert queues == [first_queue, second_queue]
    await pcs.deactivate_runtime(timeout_seconds=1)
