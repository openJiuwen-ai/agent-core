"""Durable production request wiring with local HTTP transport and real checkpoints."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from examples.adaptive_multi_agent_collab.infrastructure import persistence
from examples.adaptive_multi_agent_collab.operations.experiment import (
    wire_durable_journal,
    validate_journal_resume,
)
from examples.adaptive_multi_agent_collab.providers.gateway import build_openrouter_model
from examples.adaptive_multi_agent_collab.infrastructure.persistence import (
    DurableJournal,
    PersistenceFailure,
    read_journal,
)
from examples.adaptive_multi_agent_collab.providers.resources import AccountingFailure
from examples.adaptive_multi_agent_collab.learning.training import CheckpointManager
from examples.adaptive_multi_agent_collab.tests.test_failure_policy_resources import (
    controller,
)
from examples.adaptive_multi_agent_collab.tests.test_gateway import binding, frame, sse
from examples.adaptive_multi_agent_collab.tests.test_training_schedule import (
    config_for,
    make_trainer,
)


def wired(tmp_path):
    ctl = controller()
    trainer = make_trainer(config_for())
    trainer.hooks = SimpleNamespace(
        expenditure_ledger=ctl.ledger,
        state_dict=lambda: {"expenditure_ledger": ctl.ledger.state_dict()},
    )
    trainer.checkpoint_path = tmp_path / "checkpoint.pt"
    wire_durable_journal(trainer, trainer.hooks, tmp_path)
    trainer._checkpoint()
    return ctl, trainer


def native_metadata():
    return {
        "id": "generation-fixture",
        "model": binding().model,
        "provider_name": "Fixture Provider",
        "native_tokens_prompt": 20,
        "native_tokens_completion": 10,
        "native_tokens_cached": 0,
        "native_tokens_reasoning": 0,
        "total_cost": 0.0001,
        "is_byok": False,
        "finish_reason": "stop",
        "native_finish_reason": "stop",
    }


async def invoke(ctl, model):
    return await ctl.invoke(
        model,
        messages=[{"role": "user", "content": "synthetic input"}],
        kind="role",
        deadline=time.monotonic() + 5,
        settings={"seed": 42},
    )


def test_three_production_streams_have_durable_post_get_intents_and_two_checkpoints(
    tmp_path, monkeypatch
):
    ctl, trainer = wired(tmp_path)
    checkpoint_writes = []
    original = CheckpointManager.write_payload

    def save(path, payload, **kwargs):
        checkpoint_writes.append(path)
        return original(path, payload, **kwargs)

    monkeypatch.setattr(CheckpointManager, "write_payload", save)
    requests = []

    async def handle(request):
        journal = read_journal(tmp_path)
        assert journal["boundary"] is None
        if request.method == "POST":
            assert any(
                item["status"] == "dispatched" and item["charged"] is None
                for item in journal["state"]["entries"]
            )
            requests.append("POST")
            return httpx.Response(
                200,
                text=sse(
                    [
                        *[frame("x") for _ in range(16)],
                        frame("x", finish="stop", native="stop"),
                    ]
                ),
            )
        assert any(
            event["event"] == "metadata-lookup-intent"
            for item in journal["state"]["entries"]
            for event in item.get("recovery_events", [])
        )
        requests.append("GET")
        return httpx.Response(200, json={"data": native_metadata()})

    model = build_openrouter_model(
        binding(),
        "offline-fake-key",
        temperature=0.7,
        transport=httpx.MockTransport(handle),
    )

    async def scenario():
        await asyncio.gather(*(invoke(ctl, model) for _ in range(3)))
        await ctl.ledger.flush_durable()
        assert checkpoint_writes == []
        assert (
            read_journal(tmp_path)["boundary"] is None
        )  # All bills known != workflow safe.
        with pytest.raises(ValueError, match="unfinished workflow"):
            validate_journal_resume(trainer.checkpoint_path, tmp_path)
        trainer._checkpoint()
        assert len(checkpoint_writes) == 1
        rebuilt = read_journal(tmp_path)
        assert json.dumps(rebuilt["state"], sort_keys=True) == json.dumps(
            ctl.ledger.state_dict(), sort_keys=True
        )
        assert validate_journal_resume(trainer.checkpoint_path, tmp_path) is not None
        assert requests.count("POST") == requests.count("GET") == 3
        assert trainer.durable_journal.metrics["invariant_serializations"] <= 18
        await trainer.durable_journal.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["reserved", "dispatched", "received"])
def test_crash_window_retains_unknown_holds_or_known_charge_and_blocks_resume(
    tmp_path, phase
):
    ctl, trainer = wired(tmp_path)

    async def scenario():
        entry = ctl.ledger.reserve(0.01, kind="role", model="fixture", metadata={})
        if phase != "reserved":
            entry["status"] = "dispatched"
            ctl.ledger.persist(entry)
        if phase == "received":
            ctl.ledger.settle(
                entry, {"total_cost": 0.004}, status="completed", elapsed=0.1
            )
        await ctl.ledger.flush_durable()
        saved = read_journal(tmp_path)["state"]["entries"][0]
        assert saved["reserved"] == 0.01
        assert saved["charged"] == (0.004 if phase == "received" else None)
        with pytest.raises(ValueError, match="unfinished workflow"):
            validate_journal_resume(trainer.checkpoint_path, tmp_path)
        with pytest.raises(PersistenceFailure, match="another accounting writer"):
            DurableJournal([tmp_path], ctl.ledger.state_dict())
        await trainer.durable_journal.aclose()
        trainer.durable_journal._thread.join(timeout=1)
        with pytest.raises(PersistenceFailure, match="unfinished workflow"):
            DurableJournal([tmp_path], ctl.ledger.state_dict())

    asyncio.run(scenario())


def test_checkpoint_failure_never_publishes_safe_boundary(tmp_path, monkeypatch):
    ctl, trainer = wired(tmp_path)

    async def scenario():
        ctl.ledger.reserve(0.01, kind="role", model="fixture", metadata={})
        await ctl.ledger.flush_durable()

        def failed(*args, **kwargs):
            raise OSError("fixture checkpoint publication failure")

        monkeypatch.setattr(CheckpointManager, "write_payload", failed)
        with pytest.raises(PersistenceFailure):
            trainer._checkpoint()
        assert trainer._request_boundary_pending
        assert read_journal(tmp_path)["boundary"] is None
        with pytest.raises(ValueError, match="unfinished workflow"):
            validate_journal_resume(trainer.checkpoint_path, tmp_path)
        with pytest.raises(PersistenceFailure):
            await trainer.durable_journal.aclose()

    asyncio.run(scenario())


def test_writer_failure_prevents_post_and_retains_original_durable_hold(
    tmp_path, monkeypatch
):
    ctl, trainer = wired(tmp_path)
    posts = []

    async def scenario():
        entry = ctl.ledger.reserve(0.01, kind="role", model="fixture", metadata={})
        await ctl.ledger.flush_durable()

        def failed(*args):
            raise OSError("fixture disk failure")

        monkeypatch.setattr(trainer.durable_journal, "_write", failed)
        entry["status"] = "dispatched"
        ctl.ledger.persist(entry)
        with pytest.raises(AccountingFailure, match="persistence failed"):
            await ctl.ledger.flush_durable()
        with pytest.raises(AccountingFailure, match="persistence"):
            ctl.ledger.reserve(0.01, kind="role", model="fixture", metadata={})
        assert read_journal(tmp_path)["state"]["entries"][0]["charged"] is None
        assert not posts

    asyncio.run(scenario())


def test_slow_writer_is_off_loop_bounded_and_stops_dispatch(tmp_path, monkeypatch):
    ctl, trainer = wired(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = trainer.durable_journal._write

    def delayed(*args):
        entered.set()
        release.wait(2)
        return original(*args)

    monkeypatch.setattr(trainer.durable_journal, "_write", delayed)
    monkeypatch.setattr(persistence, "PERSISTENCE_DEADLINE", 0.03)
    ticks = []

    async def scenario():
        ctl.ledger.reserve(0.01, kind="role", model="fixture", metadata={})

        async def heartbeat():
            for _ in range(5):
                await asyncio.sleep(0.002)
                ticks.append(True)

        pulse = asyncio.create_task(heartbeat())
        with pytest.raises(AccountingFailure):
            await ctl.ledger.flush_durable()
        await pulse
        assert len(ticks) == 5 and entered.is_set()
        with pytest.raises(AccountingFailure):
            ctl.ledger.assert_dispatch_allowed()

    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_progress_roundtrip_and_torn_tail_block_resume_without_rewriting(tmp_path):
    ctl, trainer = wired(tmp_path)

    async def scenario():
        await ctl.ledger.record_progress(
            "completed-session", {"session": 1, "raw": "unchanged evidence"}
        )
        state = read_journal(tmp_path)
        assert state["progress"][-1]["payload"] == {
            "session": 1,
            "raw": "unchanged evidence",
        }
        assert state["boundary"] is None
        await trainer.durable_journal.aclose()

    asyncio.run(scenario())
    path = tmp_path / "accounting_progress.jsonl"
    with path.open("ab") as stream:
        stream.write(b'{"seq":')
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(PersistenceFailure, match="unfinished accounting journal"):
        validate_journal_resume(trainer.checkpoint_path, tmp_path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_restart_at_safe_boundary_preserves_spending_and_contiguous_dispatch(tmp_path):
    ctl, trainer = wired(tmp_path)

    async def first():
        entry = ctl.ledger.reserve(0.01, kind="role", model="fixture", metadata={})
        ctl.ledger.settle(entry, {"total_cost": 0.003}, status="completed", elapsed=0.1)
        await ctl.ledger.flush_durable()
        trainer._checkpoint()
        await trainer.durable_journal.aclose()

    asyncio.run(first())
    saved = validate_journal_resume(trainer.checkpoint_path, tmp_path)
    restored = controller()
    restored.ledger.load_state_dict(saved)
    fresh = make_trainer(config_for())
    fresh.hooks = SimpleNamespace(
        expenditure_ledger=restored.ledger,
        state_dict=lambda: {"expenditure_ledger": restored.ledger.state_dict()},
    )
    fresh.checkpoint_path = trainer.checkpoint_path
    wire_durable_journal(fresh, fresh.hooks, tmp_path)

    async def second():
        # Restore requires the existing process-local audited policy guard.
        restored.ledger._failure_policy_guard = lambda: True
        item = restored.ledger.reserve(0.01, kind="role", model="fixture", metadata={})
        assert item["dispatch_id"] == 1
        await restored.ledger.flush_durable()
        assert read_journal(tmp_path)["state"]["entries"][0]["charged"] == 0.003
        await fresh.durable_journal.aclose()

    asyncio.run(second())


def test_call_deadline_during_durable_ack_is_not_provider_failure(
    tmp_path, monkeypatch
):
    from examples.adaptive_multi_agent_collab.infrastructure.failure_policy import (
        CALL_TIMING,
        deadline_scope,
        classify_failure,
        FailureKind,
    )

    ctl, trainer = wired(tmp_path)
    release = threading.Event()
    original = trainer.durable_journal._write

    def delayed(*args):
        release.wait(2)
        return original(*args)

    monkeypatch.setattr(trainer.durable_journal, "_write", delayed)
    timing = {"phase": "generation"}

    async def scenario():
        token = CALL_TIMING.set(timing)
        try:
            entry = ctl.ledger.reserve(0.01, kind="role", model="fixture", metadata={})
            with pytest.raises(AccountingFailure):
                async with deadline_scope("call-deadline", timeout=0.02):
                    await ctl.ledger.flush_durable()
            assert timing["phase"] == "accounting-persistence"
            assert classify_failure(timing["hard_failure"]) == FailureKind.INTEGRATION
            assert entry["charged"] is None and entry["reserved"] == 0.01
            with pytest.raises(AccountingFailure):
                ctl.ledger.assert_dispatch_allowed()
        finally:
            CALL_TIMING.reset(token)

    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_production_post_is_not_sent_when_dispatch_intent_cannot_be_committed(
    tmp_path, monkeypatch
):
    ctl, trainer = wired(tmp_path)
    posted = []
    original = trainer.durable_journal._write

    def failed(operation, value):
        if operation in {"ledger", "ledger-delta-v1"} and any(
            item.get("status") == "dispatched" for item in value.get("entries", [])
        ):
            raise OSError("fixture dispatch-intent fsync failed")
        return original(operation, value)

    monkeypatch.setattr(trainer.durable_journal, "_write", failed)

    def handle(request):
        posted.append(request.method)
        return httpx.Response(500)

    model = build_openrouter_model(
        binding(),
        "offline-fake-key",
        temperature=0.7,
        transport=httpx.MockTransport(handle),
    )
    with pytest.raises(AccountingFailure):
        asyncio.run(invoke(ctl, model))
    assert not posted
    state = read_journal(tmp_path)["state"]
    assert state["entries"][0]["status"] == "reserved"
    assert state["entries"][0]["charged"] is None
    assert read_journal(tmp_path)["boundary"] is None


def test_raw_values_cannot_collide_with_storage_reference_markers(tmp_path):
    ctl, trainer = wired(tmp_path)

    async def scenario():
        payload = {
            "raw": {"$object": "literal model output"},
            "other": {"$literal": [["raw", "value"]]},
        }
        await ctl.ledger.record_progress("partial-call", payload)
        assert read_journal(tmp_path)["progress"][-1]["payload"] == payload
        await trainer.durable_journal.aclose()

    asyncio.run(scenario())


def test_slow_real_checkpoint_serialization_is_bounded_off_loop_and_cannot_publish_late(
    tmp_path, monkeypatch
):
    import torch

    ctl, trainer = wired(tmp_path)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = torch.save
    original_hash = hashlib.sha256(trainer.checkpoint_path.read_bytes()).hexdigest()

    def delayed(*args, **kwargs):
        entered.set()
        release.wait(2)
        try:
            return original(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(torch, "save", delayed)
    monkeypatch.setattr(persistence, "PERSISTENCE_DEADLINE", 0.03)
    ticks = []

    async def scenario():
        ctl.ledger.reserve(0.01, kind="role", model="fixture", metadata={})
        await ctl.ledger.flush_durable()

        async def heartbeat():
            for _ in range(5):
                await asyncio.sleep(0.002)
                ticks.append(True)

        task = asyncio.create_task(heartbeat())
        with pytest.raises(PersistenceFailure, match="acknowledgement deadline"):
            await trainer.checkpoint_async(safe_boundary=False)
        await task
        assert entered.is_set() and len(ticks) == 5
        assert trainer._request_boundary_pending
        assert read_journal(tmp_path)["boundary"] is None
        assert read_journal(tmp_path)["state"]["entries"][0]["charged"] is None
        with pytest.raises(ValueError, match="unfinished workflow"):
            validate_journal_resume(trainer.checkpoint_path, tmp_path)

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        assert finished.wait(1)
    # The expired writer may finish serialization but its publication guard
    # rejects a late rename and leaves the former checkpoint byte-identical.
    trainer.durable_journal._thread.join(0.02)
    assert (
        hashlib.sha256(trainer.checkpoint_path.read_bytes()).hexdigest()
        == original_hash
    )


def test_async_boundary_freezes_tensors_and_publishes_reports_off_loop(
    tmp_path, monkeypatch
):
    import torch

    ctl, trainer = wired(tmp_path)
    original = torch.save
    entered, release = threading.Event(), threading.Event()
    before = {name: value.clone() for name, value in trainer.alpha.state_dict().items()}

    def delayed(*args, **kwargs):
        entered.set()
        release.wait(2)
        return original(*args, **kwargs)

    monkeypatch.setattr(torch, "save", delayed)

    async def scenario():
        task = asyncio.create_task(trainer.checkpoint_async())
        while not entered.is_set():
            await asyncio.sleep(0.001)
        with torch.no_grad():
            next(trainer.alpha.parameters()).add_(1)
        release.set()
        await task
        from examples.adaptive_multi_agent_collab.infrastructure.storage import load_checkpoint
        saved = load_checkpoint(trainer.checkpoint_path)
        assert all(
            torch.equal(saved["alpha"][name], value) for name, value in before.items()
        )
        assert read_journal(tmp_path)["boundary"] is not None
        await ctl.ledger.write_artifact(
            tmp_path / "result.json", {"status": "complete", "synthetic": True}
        )
        assert (
            json.loads((tmp_path / "result.json").read_text())["status"] == "complete"
        )
        await trainer.durable_journal.aclose()

    asyncio.run(scenario())
