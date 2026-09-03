import asyncio
from types import SimpleNamespace

import pytest

from openjiuwen.core.kv_cache import KVCacheRuntime, KVCacheRuntimeConfig
from openjiuwen.core.kv_cache.kv_cache_types import KVCacheIdentity
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.agent_team import Session as TeamSession


class FakeModel:
    def __init__(self) -> None:
        self.model_client_config = SimpleNamespace(
            client_provider="AscendAffinity",
            api_base="http://model/v1",
            extensions=None,
        )
        self.model_config = SimpleNamespace(model_name="model")
        self.calls: list[tuple[str, str, str]] = []
        self.offload_started = asyncio.Event()
        self.release_offload = asyncio.Event()

    async def offload_kvc(self, **kwargs) -> bool:
        self.calls.append(("offload", kwargs["session_id"], kwargs["parent_session_id"]))
        self.offload_started.set()
        await self.release_offload.wait()
        return True

    async def prefetch_kvc(self, **kwargs) -> bool:
        self.calls.append(("prefetch", kwargs["session_id"], kwargs["parent_session_id"]))
        return True

    async def evict_kvc(self, **kwargs) -> bool:
        self.calls.append(("evict", kwargs["session_id"], kwargs["parent_session_id"]))
        return True


def runtime() -> KVCacheRuntime:
    return KVCacheRuntime(
        KVCacheRuntimeConfig(
            action_timeout=0.5,
            evict_timeout=0.5,
            close_timeout=0.5,
        )
    )


@pytest.mark.asyncio
async def test_session_kvc_api_is_noop_without_runtime() -> None:
    session = Session(session_id="session")

    assert await session.prepare_kvc() is False
    assert await session.suspend_kvc() is False
    assert await session.release_kvc() is False


@pytest.mark.asyncio
async def test_inference_waits_for_offload_then_only_for_prefetch_start() -> None:
    kvc_runtime = runtime()
    model = FakeModel()
    identity = KVCacheIdentity("session", "session")
    lease = await kvc_runtime.begin_inference(identity, model)
    await kvc_runtime.end_inference(lease, succeeded=True)

    assert await kvc_runtime.suspend(identity) is True
    await asyncio.wait_for(model.offload_started.wait(), timeout=0.5)

    prepare = asyncio.create_task(kvc_runtime.prepare(identity))
    await asyncio.sleep(0)
    assert prepare.done() is False

    model.release_offload.set()
    assert await asyncio.wait_for(prepare, timeout=0.5) is True
    assert [call[0] for call in model.calls] == ["offload", "prefetch"]

    lease = await asyncio.wait_for(
        kvc_runtime.begin_inference(identity, model),
        timeout=0.5,
    )
    assert lease is not None
    await kvc_runtime.end_inference(lease, succeeded=True)
    await kvc_runtime.close()


@pytest.mark.asyncio
async def test_team_root_action_cascades_once_per_control_domain() -> None:
    kvc_runtime = runtime()
    model = FakeModel()
    model.release_offload.set()
    child_a = KVCacheIdentity("team/agent-a", "team")
    child_b = KVCacheIdentity("team/agent-b", "team")
    for identity in (child_a, child_b):
        lease = await kvc_runtime.begin_inference(identity, model)
        await kvc_runtime.end_inference(lease, succeeded=True)

    team = TeamSession(session_id="team", kv_cache_runtime=kvc_runtime)
    assert await team.suspend_kvc() is True
    await asyncio.wait_for(model.offload_started.wait(), timeout=0.5)

    offloads = [call for call in model.calls if call[0] == "offload"]
    assert offloads == [("offload", "team", "team")]

    assert await team.release_kvc() is True
    assert ("evict", "team", "team") in model.calls
    assert await team.prepare_kvc() is False


@pytest.mark.asyncio
async def test_root_offload_orders_new_child_inference_after_prefetch_start() -> None:
    kvc_runtime = runtime()
    model = FakeModel()
    first = KVCacheIdentity("team/agent-a", "team")
    second = KVCacheIdentity("team/agent-b", "team")
    lease = await kvc_runtime.begin_inference(first, model)
    await kvc_runtime.end_inference(lease, succeeded=True)

    assert await kvc_runtime.suspend(KVCacheIdentity("team", "team")) is True
    await asyncio.wait_for(model.offload_started.wait(), timeout=0.5)

    inference = asyncio.create_task(kvc_runtime.begin_inference(second, model))
    await asyncio.sleep(0)
    assert inference.done() is False

    model.release_offload.set()
    lease = await asyncio.wait_for(inference, timeout=0.5)
    assert lease is not None
    assert [call[0] for call in model.calls] == ["offload", "prefetch"]
    await kvc_runtime.end_inference(lease, succeeded=True)
    await kvc_runtime.close()


@pytest.mark.asyncio
async def test_started_offload_is_cancelled_before_inference_fails_open() -> None:
    class HangingOffloadModel(FakeModel):
        def __init__(self) -> None:
            super().__init__()
            self.offload_active = False
            self.offload_cancelled = asyncio.Event()

        async def offload_kvc(self, **kwargs) -> bool:
            self.calls.append(("offload", kwargs["session_id"], kwargs["parent_session_id"]))
            self.offload_active = True
            self.offload_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.offload_active = False
                self.offload_cancelled.set()

        async def prefetch_kvc(self, **kwargs) -> bool:
            assert self.offload_active is False
            return await super().prefetch_kvc(**kwargs)

    kvc_runtime = KVCacheRuntime(
        KVCacheRuntimeConfig(
            action_timeout=0.05,
            evict_timeout=0.5,
            close_timeout=0.5,
        )
    )
    model = HangingOffloadModel()
    identity = KVCacheIdentity("session", "session")
    lease = await kvc_runtime.begin_inference(identity, model)
    await kvc_runtime.end_inference(lease, succeeded=True)
    assert await kvc_runtime.suspend(identity) is True
    await asyncio.wait_for(model.offload_started.wait(), timeout=0.5)

    lease = await asyncio.wait_for(
        kvc_runtime.begin_inference(identity, model),
        timeout=0.5,
    )

    assert lease is not None
    assert model.offload_cancelled.is_set()
    assert [call[0] for call in model.calls] == ["offload", "prefetch"]
    await kvc_runtime.end_inference(lease, succeeded=True)
    await kvc_runtime.close()


@pytest.mark.asyncio
async def test_root_prepare_waits_for_child_offload_before_cascade() -> None:
    kvc_runtime = runtime()
    model = FakeModel()
    child = KVCacheIdentity("team/agent-a", "team")
    root = KVCacheIdentity("team", "team")
    lease = await kvc_runtime.begin_inference(child, model)
    await kvc_runtime.end_inference(lease, succeeded=True)
    assert await kvc_runtime.suspend(child) is True
    await asyncio.wait_for(model.offload_started.wait(), timeout=0.5)

    prepare = asyncio.create_task(kvc_runtime.prepare(root))
    await asyncio.sleep(0)
    assert prepare.done() is False
    assert [call[0] for call in model.calls] == ["offload"]

    model.release_offload.set()
    assert await asyncio.wait_for(prepare, timeout=0.5) is True
    assert model.calls == [
        ("offload", "team/agent-a", "team"),
        ("prefetch", "team", "team"),
    ]
    await kvc_runtime.close()


@pytest.mark.asyncio
async def test_duplicate_suspend_is_coalesced() -> None:
    kvc_runtime = runtime()
    model = FakeModel()
    identity = KVCacheIdentity("session", "session")
    lease = await kvc_runtime.begin_inference(identity, model)
    await kvc_runtime.end_inference(lease, succeeded=True)

    assert await kvc_runtime.suspend(identity) is True
    assert await kvc_runtime.suspend(identity) is True
    await asyncio.wait_for(model.offload_started.wait(), timeout=0.5)
    model.release_offload.set()
    await asyncio.sleep(0)

    assert [call[0] for call in model.calls] == ["offload"]
    await kvc_runtime.close()


@pytest.mark.asyncio
async def test_completed_suspend_is_not_sent_again_while_still_offloaded() -> None:
    class ImmediateOffloadModel(FakeModel):
        async def offload_kvc(self, **kwargs) -> bool:
            self.calls.append(
                ("offload", kwargs["session_id"], kwargs["parent_session_id"])
            )
            self.offload_started.set()
            return True

    kvc_runtime = runtime()
    model = ImmediateOffloadModel()
    identity = KVCacheIdentity("session", "session")
    lease = await kvc_runtime.begin_inference(identity, model)
    await kvc_runtime.end_inference(lease, succeeded=True)

    assert await kvc_runtime.suspend(identity) is True
    await asyncio.wait_for(model.offload_started.wait(), timeout=0.5)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert await kvc_runtime.suspend(identity) is False
    assert [call[0] for call in model.calls] == ["offload"]
    await kvc_runtime.close()


@pytest.mark.asyncio
async def test_failed_action_disables_later_offload_without_blocking_inference() -> None:
    class FailingOffloadModel(FakeModel):
        async def offload_kvc(self, **kwargs) -> bool:
            self.calls.append(
                ("offload", kwargs["session_id"], kwargs["parent_session_id"])
            )
            self.offload_started.set()
            return False

    kvc_runtime = runtime()
    model = FailingOffloadModel()
    identity = KVCacheIdentity("session", "session")
    lease = await kvc_runtime.begin_inference(identity, model)
    await kvc_runtime.end_inference(lease, succeeded=True)

    assert await kvc_runtime.suspend(identity) is True
    await asyncio.wait_for(model.offload_started.wait(), timeout=0.5)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    lease = await kvc_runtime.begin_inference(identity, model)
    assert lease is not None
    await kvc_runtime.end_inference(lease, succeeded=True)
    assert await kvc_runtime.suspend(identity) is False
    assert [call[0] for call in model.calls].count("offload") == 1
    await kvc_runtime.close()


@pytest.mark.asyncio
async def test_released_session_id_can_be_registered_by_a_new_session() -> None:
    kvc_runtime = runtime()
    model = FakeModel()
    model.release_offload.set()
    identity = KVCacheIdentity("session", "session")
    lease = await kvc_runtime.begin_inference(identity, model)
    await kvc_runtime.end_inference(lease, succeeded=True)

    assert await kvc_runtime.release(identity) is True
    new_lease = await kvc_runtime.begin_inference(identity, model)

    assert new_lease is not None
    await kvc_runtime.end_inference(new_lease, succeeded=True)
    await kvc_runtime.close()


@pytest.mark.asyncio
async def test_release_failure_never_escapes_session_cleanup() -> None:
    class BrokenRuntime:
        async def release(self, _identity):
            raise RuntimeError("provider failed")

    session = Session(session_id="session", kv_cache_runtime=BrokenRuntime())

    assert await session.release_kvc() is False
    assert await session.prepare_kvc() is False


@pytest.mark.asyncio
async def test_historical_session_uses_fallback_binding() -> None:
    model = FakeModel()
    model.release_offload.set()
    kvc_runtime = KVCacheRuntime(
        KVCacheRuntimeConfig(
            action_timeout=0.5,
            evict_timeout=0.5,
            close_timeout=0.5,
        ),
        binding_provider=lambda: model,
    )
    session = Session(session_id="history", kv_cache_runtime=kvc_runtime)

    assert await session.prepare_kvc() is True
    assert model.calls == [("prefetch", "history", "history")]
    assert await session.release_kvc() is True
    assert model.calls[-1] == ("evict", "history", "history")


@pytest.mark.asyncio
async def test_prepare_reports_not_sent_when_model_has_no_prefetch_method() -> None:
    model = FakeModel()
    model.prefetch_kvc = None
    kvc_runtime = KVCacheRuntime(
        KVCacheRuntimeConfig(
            action_timeout=0.5,
            evict_timeout=0.5,
            close_timeout=0.5,
        ),
        binding_provider=lambda: model,
    )

    assert await kvc_runtime.prepare(KVCacheIdentity("history", "history")) is False
    assert model.calls == []
    await kvc_runtime.close()


@pytest.mark.asyncio
async def test_prepare_returns_after_model_call_starts_not_after_response() -> None:
    class BlockingPrefetchModel(FakeModel):
        def __init__(self) -> None:
            super().__init__()
            self.prefetch_started = asyncio.Event()
            self.release_prefetch = asyncio.Event()

        async def prefetch_kvc(self, **kwargs) -> bool:
            self.calls.append(
                ("prefetch", kwargs["session_id"], kwargs["parent_session_id"])
            )
            self.prefetch_started.set()
            await self.release_prefetch.wait()
            return True

    model = BlockingPrefetchModel()
    kvc_runtime = KVCacheRuntime(
        KVCacheRuntimeConfig(
            action_timeout=0.5,
            evict_timeout=0.5,
            close_timeout=0.5,
        ),
        binding_provider=lambda: model,
    )

    prepare = asyncio.create_task(
        kvc_runtime.prepare(KVCacheIdentity("history", "history"))
    )
    await asyncio.wait_for(model.prefetch_started.wait(), timeout=0.5)
    assert await asyncio.wait_for(prepare, timeout=0.5) is True
    assert model.release_prefetch.is_set() is False

    model.release_prefetch.set()
    await kvc_runtime.close()


@pytest.mark.asyncio
async def test_live_child_binding_replaces_root_fallback_in_same_domain() -> None:
    fallback_model = FakeModel()
    fallback_model.release_offload.set()
    live_model = FakeModel()
    live_model.release_offload.set()
    kvc_runtime = KVCacheRuntime(
        KVCacheRuntimeConfig(
            action_timeout=0.5,
            evict_timeout=0.5,
            close_timeout=0.5,
        ),
        binding_provider=lambda: fallback_model,
    )
    root = KVCacheIdentity("team", "team")
    child = KVCacheIdentity("team/agent-a", "team")
    assert await kvc_runtime.prepare(root) is True

    lease = await kvc_runtime.begin_inference(child, live_model)
    assert lease is not None
    await kvc_runtime.end_inference(lease, succeeded=True)
    assert await kvc_runtime.suspend(root) is True
    await asyncio.wait_for(live_model.offload_started.wait(), timeout=0.5)

    assert [call[0] for call in fallback_model.calls] == ["prefetch"]
    assert live_model.calls == [("offload", "team", "team")]
    await kvc_runtime.close()


@pytest.mark.asyncio
async def test_live_binding_preserves_fallback_root_offload_admission() -> None:
    fallback_model = FakeModel()
    live_model = FakeModel()
    kvc_runtime = KVCacheRuntime(
        KVCacheRuntimeConfig(
            action_timeout=0.5,
            evict_timeout=0.5,
            close_timeout=0.5,
        ),
        binding_provider=lambda: fallback_model,
    )
    root = KVCacheIdentity("team", "team")
    child = KVCacheIdentity("team/agent-a", "team")
    assert await kvc_runtime.prepare(root) is True
    assert await kvc_runtime.suspend(root) is True
    await asyncio.wait_for(fallback_model.offload_started.wait(), timeout=0.5)

    inference = asyncio.create_task(kvc_runtime.begin_inference(child, live_model))
    await asyncio.sleep(0)
    assert inference.done() is False

    fallback_model.release_offload.set()
    lease = await asyncio.wait_for(inference, timeout=0.5)
    assert lease is not None
    assert [call[0] for call in fallback_model.calls] == ["prefetch", "offload"]
    assert live_model.calls == [("prefetch", "team", "team")]
    await kvc_runtime.end_inference(lease, succeeded=True)
    await kvc_runtime.close()
