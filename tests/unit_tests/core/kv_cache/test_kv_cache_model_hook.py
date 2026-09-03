import asyncio
from types import SimpleNamespace

import pytest

from openjiuwen.core.kv_cache.kv_cache_model_hook import KVCacheModelHook
from openjiuwen.core.session import with_session
from openjiuwen.core.session.agent import Session


class RecordingRuntime:
    def __init__(self) -> None:
        self.events = []

    async def begin_inference(self, identity, model, *, model_name=None):
        self.events.append(("begin", identity, model, model_name))
        return "lease"

    async def end_inference(self, lease, *, succeeded):
        self.events.append(("end", lease, succeeded))


class AffinityModel:
    @staticmethod
    def supports_kv_cache_affinity() -> bool:
        return True


@pytest.mark.asyncio
async def test_model_hook_uses_current_session_without_mutating_request() -> None:
    runtime = RecordingRuntime()
    session = Session(session_id="session", kv_cache_runtime=runtime)
    model = AffinityModel()
    request_kwargs = {"session_id": "session", "parent_session_id": "session"}

    @with_session()
    async def call(*, session):
        runtime_lease = await KVCacheModelHook.begin(model, request_kwargs, "model")
        await KVCacheModelHook.end(runtime_lease, succeeded=True)

    await call(session=session)

    assert request_kwargs == {
        "session_id": "session",
        "parent_session_id": "session",
    }
    assert [event[0] for event in runtime.events] == ["begin", "end"]


@pytest.mark.asyncio
async def test_model_hook_is_noop_without_affinity_request_or_runtime() -> None:
    model = AffinityModel()

    assert await KVCacheModelHook.begin(model, {}, "model") is None

    session = SimpleNamespace(
        get_kv_cache_runtime=lambda: None,
        get_cache_identity=lambda: None,
    )

    @with_session()
    async def call(*, session):
        return await KVCacheModelHook.begin(
            model,
            {"session_id": "session"},
            "model",
        )

    assert await call(session=session) is None


@pytest.mark.asyncio
async def test_model_hook_capability_failure_is_fail_open() -> None:
    model = SimpleNamespace(supports_kv_cache_affinity=lambda: (_ for _ in ()).throw(RuntimeError("capability failed")))

    assert (
        await KVCacheModelHook.begin(
            model,
            {"session_id": "session"},
            "model",
        )
        is None
    )


@pytest.mark.asyncio
async def test_model_hook_runtime_failures_do_not_change_model_flow() -> None:
    class BrokenRuntime:
        async def begin_inference(self, *_args, **_kwargs):
            raise RuntimeError("binding failed")

        async def end_inference(self, *_args, **_kwargs):
            raise RuntimeError("cleanup failed")

    runtime = BrokenRuntime()
    session = Session(session_id="session", kv_cache_runtime=runtime)

    @with_session()
    async def call(*, session):
        return await KVCacheModelHook.begin(
            AffinityModel(),
            {"session_id": "session"},
            "model",
        )

    assert await call(session=session) is None
    await KVCacheModelHook.end((runtime, "lease"), succeeded=True)


@pytest.mark.asyncio
async def test_model_hook_preserves_caller_cancellation() -> None:
    started = asyncio.Event()

    class WaitingRuntime:
        async def begin_inference(self, *_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()

    session = Session(session_id="session", kv_cache_runtime=WaitingRuntime())

    @with_session()
    async def call(*, session):
        return await KVCacheModelHook.begin(
            AffinityModel(),
            {"session_id": "session"},
            "model",
        )

    task = asyncio.create_task(call(session=session))
    await asyncio.wait_for(started.wait(), timeout=0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
