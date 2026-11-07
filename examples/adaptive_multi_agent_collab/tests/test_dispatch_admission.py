"""Recheck durable admission at the final audited generation boundary."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from examples.adaptive_multi_agent_collab.infrastructure.failure_policy import logical_summary
from examples.adaptive_multi_agent_collab.providers.gateway import build_openrouter_model
from examples.adaptive_multi_agent_collab.infrastructure.persistence import read_journal
from examples.adaptive_multi_agent_collab.providers.resources import (
    AccountingFailure,
    PolicyStopped,
)
from examples.adaptive_multi_agent_collab.tests.test_deadline_causes import (
    complete_frame,
)
from examples.adaptive_multi_agent_collab.tests.test_gateway import binding, sse
from examples.adaptive_multi_agent_collab.tests.test_persistence import (
    invoke,
    native_metadata,
    wired,
)


def stop(ledger, kind):
    if kind == "pause":
        ledger._policy_drain("synthetic concurrent pause")
    else:
        ledger.halt("synthetic concurrent hard halt")


@pytest.mark.parametrize("kind", ["pause", "halt"])
@pytest.mark.parametrize("boundary", ["durable-ack", "gateway-post", "httpx-send"])
def test_admission_changes_before_post_are_not_physical_attempts(
    tmp_path, monkeypatch, kind, boundary
):
    ctl, trainer = wired(tmp_path)
    requests = []

    async def handle(request):
        requests.append(request.method)
        return httpx.Response(200, text=sse([complete_frame()]))

    class Transport(httpx.MockTransport):
        async def __aenter__(self):
            result = await super().__aenter__()
            if boundary == "gateway-post":
                # The SDK and client setup occur after the resource dispatch
                # acknowledgement, but before any physical POST.
                await asyncio.sleep(0)
                stop(ctl.ledger, kind)
            return result

    model = build_openrouter_model(
        binding(), "offline-key", temperature=0.7, transport=Transport(handle)
    )
    original_send = httpx.AsyncClient.send

    async def send(client, request, **kwargs):
        if boundary == "httpx-send":
            await asyncio.sleep(0)
            stop(ctl.ledger, kind)
        return await original_send(client, request, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    original = ctl.ledger.flush_durable
    stopped = False

    async def flush():
        nonlocal stopped
        await original()
        if (
            boundary == "durable-ack"
            and not stopped
            and any(entry["status"] == "dispatched" for entry in ctl.ledger.entries)
        ):
            stopped = True
            await asyncio.sleep(0)
            stop(ctl.ledger, kind)

    monkeypatch.setattr(ctl.ledger, "flush_durable", flush)

    async def scenario():
        try:
            with pytest.raises(PolicyStopped if kind == "pause" else AccountingFailure):
                await invoke(ctl, model)
            assert requests == []
            receipt = ctl.ledger.entries[0]
            assert receipt["status"] == "not-dispatched"
            assert receipt["charged"] == 0
            assert receipt["usage"]["model_calls"] == 0
            assert receipt["usage"]["reference_cost"] == 0
            assert [
                event["outcome"]
                for event in ctl.ledger.failure_policy["events"]
                if event["event"] == "logical-finish"
            ] == ["not-dispatched"]
            assert all(
                value == 0
                for value in logical_summary(ctl.ledger.failure_policy)[
                    "backend_streaks"
                ].values()
            )
            durable = read_journal(tmp_path)
            assert durable["state"]["entries"][0] == receipt
            assert durable["boundary"] is None
            assert ctl.ledger.dispatch_stop_reason is not None
        finally:
            await trainer.durable_journal.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["pause", "halt"])
def test_already_dispatched_generation_may_drain_and_recover_receipt(tmp_path, kind):
    ctl, trainer = wired(tmp_path)
    requests = []

    async def handle(request):
        requests.append(request.method)
        if request.method == "POST":
            stop(ctl.ledger, kind)
            return httpx.Response(200, text=sse([complete_frame(paid=None)]))
        assert request.method == "GET"
        metadata = native_metadata()
        metadata["is_byok"] = True
        return httpx.Response(200, json={"data": metadata})

    model = build_openrouter_model(
        binding(), "offline-key", temperature=0.7, transport=httpx.MockTransport(handle)
    )

    async def scenario():
        try:
            response = await invoke(ctl, model)
            assert response.metadata["input_tokens"] == 20
            assert response.metadata["total_cost"] == 0.0001
            assert requests == ["POST", "GET"]
            assert ctl.ledger.entries[0]["usage"].get("model_calls", 1) == 1
            assert ctl.ledger.entries[0]["status"] == "completed"
            assert ctl.ledger.dispatch_stop_reason is not None
        finally:
            await trainer.durable_journal.aclose()

    asyncio.run(scenario())
