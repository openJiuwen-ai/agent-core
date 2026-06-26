# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import json

from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.context_engine import ContextEngineConfig
from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.qa_artifact.catalog import CatalogBuilder
from openjiuwen.core.context_engine.qa_artifact.manager import QAArtifactManager
from openjiuwen.core.context_engine.qa_artifact.schema import QAArtifactConfig
from openjiuwen.core.context_engine.qa_artifact.store import QAArtifactStore
from openjiuwen.core.context_engine.qa_block.history_buffer import HistoryQABuffer
from openjiuwen.core.context_engine.qa_block.layer import QABlockLayer, message_qa_id
from openjiuwen.core.context_engine.qa_block.registry import load_registry, save_registry
from openjiuwen.core.context_engine.qa_block.schema import L0Store, QABlockEntry, QABlockRegistry
from openjiuwen.core.context_engine.qa_block.store import QABlockStore
from openjiuwen.core.context_engine.qa_ref import QARef
from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage


class FakeSession:
    def __init__(self, session_id: str):
        self._session_id = session_id
        self.state = {}

    def get_session_id(self) -> str:
        return self._session_id

    def get_state(self, key=None):
        if key is None:
            return self.state
        return self.state.get(key)

    def update_state(self, updates):
        self.state.update(updates)


class FakeWorkspace:
    def __init__(self, root: str):
        self.root_path = root


class FakeCtx:
    def __init__(self, session, workspace, messages=None):
        self.session = session
        self.workspace = workspace
        self.context = None
        self.sys_operation = None
        self.inputs = type("Inputs", (), {"messages": messages or []})()


class _CompactTestManager(QAArtifactManager):
    def __init__(self, session: FakeSession, tmp_path, config: QAArtifactConfig):
        super().__init__(config, None, CatalogBuilder(config))
        self._test_session = session
        self._test_tmp_path = tmp_path

    async def _produce(self, ctx, workspace, qa, *, mark_ready):
        store = QAArtifactStore(self._test_session, str(self._test_tmp_path))
        state = store.get_or_init(qa.qa_id)
        state.products_ready = mark_ready
        state.is_extracting = False
        store.save(qa.qa_id, state)


def _history_registry(session_id: str = "session-a") -> QABlockRegistry:
    return QABlockRegistry(
        session_id=session_id,
        next_qa_index=3,
        current_qa_id=None,
        blocks={
            "qa_001": QABlockEntry(
                qa_id="qa_001",
                qa_index=1,
                status="completed",
                is_history=True,
            ),
        },
    )


async def _seed_artifact(
    tmp_path,
    session: FakeSession,
    qa_id: str = "qa_001",
    *,
    state: str = "COMPACTED",
    products_ready: bool = False,
    overview: str = "QA overview text",
) -> QAArtifactStore:
    store = QAArtifactStore(session, str(tmp_path))
    artifact_state = store.get_or_init(qa_id)
    artifact_state.state = state
    artifact_state.products_ready = products_ready
    store.save(qa_id, artifact_state)

    await store.write_atomic(
        artifact_state.overview_path,
        overview,
        artifact_state.pending_path,
    )
    await store.write_atomic(
        artifact_state.catalog_path,
        json.dumps({"qa_id": qa_id, "entries": [{"preview": "round one", "handle": "h1"}]}, ensure_ascii=False),
        f"{artifact_state.pending_path}.catalog",
    )
    return store


@pytest.mark.asyncio
async def test_store_round_trips_qa_memory_state(tmp_path):
    session = FakeSession("session-a")
    store = QAArtifactStore(session, str(tmp_path))

    state = store.get_or_init("qa_001")
    state.products_ready = True
    store.save("qa_001", state)

    reloaded = store.load("qa_001")
    assert reloaded is not None
    assert reloaded.products_ready is True
    assert reloaded.overview_path.endswith("qa_001.md")


@pytest.mark.asyncio
async def test_render_index_returns_overview_and_catalog(tmp_path):
    session = FakeSession("session-c")
    store = QAArtifactStore(session, str(tmp_path))
    state = store.get_or_init("qa_001")
    await store.write_atomic(state.overview_path, "# Overview\nDone.", state.pending_path)
    catalog_body = json.dumps(
        {"qa_id": "qa_001", "entries": [{"preview": "short msg"}]},
        ensure_ascii=False,
    )
    await store.write_atomic(state.catalog_path, catalog_body, f"{state.pending_path}.catalog")
    state.products_ready = True
    store.save("qa_001", state)

    mgr = QAArtifactManager(QAArtifactConfig(), None, CatalogBuilder(QAArtifactConfig()))
    ctx = FakeCtx(session, FakeWorkspace(str(tmp_path)))
    qa_ref = QARef(qa_id="qa_001", tokens=10, is_history=True, get_messages=lambda: [])

    text = await mgr.render_index(ctx, workspace=ctx.workspace, qa=qa_ref)
    assert "Overview" in text
    assert "[QA qa_001 catalog]" in text
    assert "short msg" in text


def test_registry_round_trips_through_session_state():
    session = FakeSession("session-a")

    registry = load_registry(session)
    assert registry.session_id == "session-a"
    assert registry.next_qa_index == 1

    registry.current_qa_id = "qa_001"
    registry.next_qa_index = 2
    registry.blocks["qa_001"] = QABlockEntry(
        qa_id="qa_001",
        qa_index=1,
        status="completed",
        l0_store=L0Store(path="qa_blocks/qa_001/messages.json", handle="qa_001"),
        message_count=2,
    )

    save_registry(session, registry)

    reloaded = load_registry(session)
    assert reloaded.current_qa_id == "qa_001"
    assert reloaded.next_qa_index == 2
    assert reloaded.blocks["qa_001"].l0_store.handle == "qa_001"


@pytest.mark.asyncio
async def test_maybe_compact_history_block_claims_one_qa(tmp_path):
    session = FakeSession("session-f")
    config = QAArtifactConfig(history_block_compact_tokens=50)
    mgr = _CompactTestManager(session, tmp_path, config)

    ctx = FakeCtx(session, FakeWorkspace(str(tmp_path)))
    window = [
        QARef(qa_id="qa_001", tokens=100, is_history=True, get_messages=lambda: []),
        QARef(qa_id="qa_002", tokens=30, is_history=True, get_messages=lambda: []),
    ]
    started = await mgr.maybe_compact_history_block(
        ctx,
        workspace=ctx.workspace,
        window_qas=window,
        window_tokens=200,
    )
    assert started is True
    await mgr._bg[("session-f", "qa_001")]


@pytest.mark.asyncio
async def test_hydrate_injects_compact_when_products_ready(tmp_path):
    session = FakeSession("session-a")
    history = HistoryQABuffer(max_blocks=3)
    long_raw = [UserMessage(content="q"), AssistantMessage(content="a" * 5000)]
    history.push("qa_001", long_raw)

    l0_store = QABlockStore(str(tmp_path), "session-a")
    artifact_store = await _seed_artifact(
        tmp_path,
        session,
        products_ready=True,
        state="RAW",
    )

    context = SessionModelContext(
        "default_context_id",
        "session-a",
        ContextEngineConfig(),
        history_messages=[UserMessage(content="follow up")],
    )
    layer = QABlockLayer(
        _history_registry(),
        history,
        l0_store,
        artifact_store=artifact_store,
    )

    injected = await layer.hydrate_history_into_window(context)
    messages = context.get_messages()

    assert injected == 1
    assert len(messages) == 2
    assert "QA overview text" in messages[0].content
    assert messages[0].metadata.get("qa_artifact_compacted") is True
    assert message_qa_id(messages[0]) == "qa_001"


@pytest.mark.asyncio
async def test_apply_artifact_force_reapply_replaces_rolling_active_qa(tmp_path):
    from openjiuwen.core.context_engine.qa_artifact.window import (
        apply_artifact_to_context,
        compact_replacement_message,
    )
    from openjiuwen.core.context_engine.qa_artifact.schema import QAArtifacts, CatalogEntry

    session = FakeSession("session-roll")
    context = SessionModelContext(
        "default_context_id",
        "session-roll",
        ContextEngineConfig(),
        history_messages=[],
    )
    old_compact = compact_replacement_message("qa_001", "old overview")
    tail_one = AssistantMessage(content="tool output " * 200, metadata={"qa_id": "qa_001"})
    tail_two = UserMessage(content="latest user", metadata={"qa_id": "qa_001"})
    context.set_messages([old_compact, tail_one, tail_two])
    stored = context.get_messages()
    tail_one_id = (getattr(stored[1], "metadata", None) or {}).get("context_message_id")

    artifacts = QAArtifacts(
        overview="new overview",
        entries=[CatalogEntry(preview="round-1", handle="h1")],
    )
    skipped = apply_artifact_to_context(context, "qa_001", artifacts)
    assert skipped == 0

    reduced = apply_artifact_to_context(
        context,
        "qa_001",
        artifacts,
        force_reapply=True,
        covers_upto_message_id=tail_one_id,
    )
    assert reduced > 0
    messages = context.get_messages()
    assert len(messages) == 2
    assert "new overview" in messages[0].content
    assert messages[-1].content == "latest user"


@pytest.mark.asyncio
async def test_compact_to_target_includes_active_qa_with_growing_tail(tmp_path, monkeypatch):
    from openjiuwen.core.context_engine.qa_artifact.window import compact_replacement_message

    session = FakeSession("session-active")
    config = QAArtifactConfig(
        qa_min_worth_tokens=10,
        full_compact_target_tokens=100,
    )
    mgr = QAArtifactManager(config, None, CatalogBuilder(config))
    store = QAArtifactStore(session, str(tmp_path))
    state = store.get_or_init("qa_001")
    state.state = "COMPACTED"
    state.products_ready = False
    state.covers_upto_message_id = "msg-old"
    store.save("qa_001", state)
    await store.write_atomic(state.overview_path, "overview", state.pending_path)
    await store.write_atomic(
        state.catalog_path,
        json.dumps({"qa_id": "qa_001", "entries": []}),
        f"{state.pending_path}.catalog",
    )

    compact = compact_replacement_message("qa_001", "compact block")
    tail = AssistantMessage(
        content="growing tail " * 80,
        metadata={"qa_id": "qa_001"},
    )
    context = SessionModelContext(
        "default_context_id",
        "session-active",
        ContextEngineConfig(),
        history_messages=[compact, tail],
    )
    stored = context.get_messages()
    state.covers_upto_message_id = (getattr(stored[0], "metadata", None) or {}).get("context_message_id")
    store.save("qa_001", state)

    qa_ref = QARef(
        qa_id="qa_001",
        tokens=500,
        is_history=False,
        get_messages=lambda: context.get_messages(),
    )
    ctx = FakeCtx(session, FakeWorkspace(str(tmp_path)))
    ctx.context = context

    refreshed = False

    async def _fake_refresh(_ctx, _workspace, _qa_ref):
        nonlocal refreshed
        refreshed = True
        return True

    async def _fake_produce(_ctx, _workspace, qa, *, mark_ready, messages=None):
        store_local = QAArtifactStore(session, str(tmp_path))
        st = store_local.get_or_init(qa.qa_id)
        st.state = "STAGED"
        st.products_ready = False
        tail_id = (getattr(context.get_messages()[-1], "metadata", None) or {}).get("context_message_id")
        st.covers_upto_message_id = tail_id
        store_local.save(qa.qa_id, st)

    monkeypatch.setattr(mgr, "_refresh_active_qa_products", _fake_refresh)
    monkeypatch.setattr(mgr, "_produce_impl", _fake_produce)
    monkeypatch.setattr(mgr, "_claim", lambda *args, **kwargs: True)

    handled = await mgr.compact_to_target(
        ctx,
        workspace=ctx.workspace,
        window_qas=[qa_ref],
        total_tokens=500,
        context=context,
        fallback=AsyncMock(return_value=False),
    )

    assert refreshed is True
    assert len(context.get_messages()) <= 2


@pytest.mark.asyncio
async def test_compact_to_target_refreshes_active_qa_when_covered_id_left_live_context(tmp_path, monkeypatch):
    from openjiuwen.core.context_engine.qa_artifact.window import compact_replacement_message

    session = FakeSession("session-active-stale-cover")
    config = QAArtifactConfig(
        qa_min_worth_tokens=10,
        full_compact_target_tokens=600,
        active_qa_rolling_keep_tail=1,
    )
    mgr = QAArtifactManager(config, None, CatalogBuilder(config))
    store = QAArtifactStore(session, str(tmp_path))
    state = store.get_or_init("qa_001")
    state.state = "STAGED"
    state.products_ready = False
    state.covers_upto_message_id = "raw-message-that-was-replaced"
    store.save("qa_001", state)
    await store.write_atomic(state.overview_path, "old overview", state.pending_path)
    await store.write_atomic(
        state.catalog_path,
        json.dumps({"qa_id": "qa_001", "entries": []}),
        f"{state.pending_path}.catalog",
    )

    compact = compact_replacement_message("qa_001", "old compact block")
    tail_one = AssistantMessage(
        content="growing tail one " * 80,
        metadata={"qa_id": "qa_001"},
    )
    tail_two = AssistantMessage(
        content="growing tail two " * 80,
        metadata={"qa_id": "qa_001"},
    )
    latest = UserMessage(content="latest user", metadata={"qa_id": "qa_001"})
    context = SessionModelContext(
        "default_context_id",
        "session-active-stale-cover",
        ContextEngineConfig(),
        history_messages=[compact, tail_one, tail_two, latest],
    )
    qa_ref = QARef(
        qa_id="qa_001",
        tokens=1000,
        is_history=False,
        get_messages=lambda: context.get_messages(),
    )
    ctx = FakeCtx(session, FakeWorkspace(str(tmp_path)))
    ctx.context = context

    produced_messages = []

    async def _fake_produce_impl(_ctx, _workspace, qa, *, mark_ready, messages=None):
        produced_messages.append(list(messages or []))
        store_local = QAArtifactStore(session, str(tmp_path))
        st = store_local.get_or_init(qa.qa_id)
        st.state = "STAGED"
        st.products_ready = False
        st.covers_upto_message_id = (getattr((messages or [])[-1], "metadata", None) or {}).get(
            "context_message_id"
        )
        store_local.save(qa.qa_id, st)
        await store_local.write_atomic(st.overview_path, "refreshed overview", st.pending_path)
        await store_local.write_atomic(
            st.catalog_path,
            json.dumps({"qa_id": qa.qa_id, "entries": []}),
            f"{st.pending_path}.catalog",
        )

    fallback = AsyncMock(return_value=False)
    monkeypatch.setattr(mgr, "_produce_impl", _fake_produce_impl)
    monkeypatch.setattr(mgr, "_claim", lambda *args, **kwargs: True)

    handled = await mgr.compact_to_target(
        ctx,
        workspace=ctx.workspace,
        window_qas=[qa_ref],
        total_tokens=1000,
        context=context,
        fallback=fallback,
    )

    assert handled is True
    fallback.assert_not_awaited()
    assert produced_messages
    assert len(produced_messages[0]) == 3
    messages = context.get_messages()
    assert len(messages) == 2
    assert "refreshed overview" in messages[0].content
    assert messages[-1].content == "latest user"


def test_needs_history_artifact_work_false_when_history_already_compact(tmp_path):
    from openjiuwen.core.context_engine.qa_artifact.window import compact_replacement_message

    session = FakeSession("session-settled")
    store = QAArtifactStore(session, str(tmp_path))
    state = store.get_or_init("qa_001")
    state.state = "COMPACTED"
    state.products_ready = False
    store.save("qa_001", state)

    context = SessionModelContext(
        "default_context_id",
        "session-settled",
        ContextEngineConfig(),
        history_messages=[
            compact_replacement_message("qa_001", "already compact overview"),
            UserMessage(content="current question", metadata={"qa_id": "qa_012"}),
        ],
    )
    window_qas = [
        QARef(qa_id="qa_001", tokens=10, is_history=True, get_messages=lambda: []),
        QARef(qa_id="qa_012", tokens=5, is_history=False, get_messages=lambda: context.get_messages()),
    ]
    mgr = QAArtifactManager(QAArtifactConfig(), None, CatalogBuilder(QAArtifactConfig()))

    assert mgr.has_history_artifacts(store, window_qas) is True
    assert mgr.history_settled(context, store, window_qas) is True
    assert mgr.needs_history_artifact_work(context, store, window_qas) is False


def test_history_settled_true_after_whole_compact_marker():
    from openjiuwen.core.context_engine.qa_artifact.assembly_state import (
        mark_assembly_whole_compact_applied,
    )

    session = FakeSession("session-l2")
    store = QAArtifactStore(session, "/tmp/unused")
    state = store.get_or_init("qa_001")
    state.products_ready = True
    store.save("qa_001", state)

    context = SessionModelContext(
        "default_context_id",
        "session-l2",
        ContextEngineConfig(),
        history_messages=[UserMessage(content="raw history", metadata={"qa_id": "qa_001"})],
    )
    window_qas = [QARef(qa_id="qa_001", tokens=100, is_history=True, get_messages=lambda: [])]
    mgr = QAArtifactManager(QAArtifactConfig(), None, CatalogBuilder(QAArtifactConfig()))

    assert mgr.needs_history_artifact_work(context, store, window_qas) is True
    mark_assembly_whole_compact_applied(context)
    assert mgr.history_settled(context, store, window_qas) is True
    assert mgr.needs_history_artifact_work(context, store, window_qas) is False


@pytest.mark.asyncio
async def test_compact_to_target_zero_progress_reestimates_before_fallback(tmp_path, monkeypatch):
    from openjiuwen.core.context_engine.qa_artifact.schema import QAArtifacts

    session = FakeSession("session-zero")
    context = SessionModelContext(
        "default_context_id",
        "session-zero",
        ContextEngineConfig(),
        history_messages=[UserMessage(content="small", metadata={"qa_id": "qa_001"})],
    )
    qa_ref = QARef(
        qa_id="qa_001",
        tokens=500,
        is_history=True,
        get_messages=lambda: context.get_messages(),
    )
    mgr = QAArtifactManager(
        QAArtifactConfig(full_compact_target_tokens=100000),
        None,
        CatalogBuilder(QAArtifactConfig()),
    )
    ctx = FakeCtx(session, FakeWorkspace(str(tmp_path)))
    ctx.context = context

    monkeypatch.setattr(mgr, "_is_compact_to_target_candidate", lambda *args, **kwargs: True)
    monkeypatch.setattr(mgr, "_refresh_active_qa_products", AsyncMock(return_value=False))
    monkeypatch.setattr(
        mgr,
        "ensure_compacted",
        AsyncMock(return_value=QAArtifacts(overview="overview", entries=[])),
    )
    monkeypatch.setattr(
        "openjiuwen.core.context_engine.qa_artifact.manager.apply_artifact_to_context",
        lambda *args, **kwargs: 0,
    )

    fallback = AsyncMock(return_value=True)
    handled = await mgr.compact_to_target(
        ctx,
        workspace=ctx.workspace,
        window_qas=[qa_ref],
        total_tokens=120000,
        context=context,
        fallback=fallback,
        trigger_total_tokens=100000,
    )

    assert handled is True
    fallback.assert_not_awaited()


@pytest.mark.asyncio
async def test_schedule_freeze_artifact_produce_schedules_background_task(tmp_path, monkeypatch):
    session = FakeSession("session-freeze-produce")
    native = [
        UserMessage(content="question"),
        AssistantMessage(content="answer " * 5000),
    ]
    context = SessionModelContext(
        "default_context_id",
        "session-freeze-produce",
        ContextEngineConfig(),
        history_messages=native,
    )
    ctx = FakeCtx(session, FakeWorkspace(str(tmp_path)))
    ctx.context = context
    mgr = QAArtifactManager(
        QAArtifactConfig(history_block_compact_tokens=8000),
        None,
        CatalogBuilder(QAArtifactConfig()),
    )

    produce_calls: list[str] = []

    async def _fake_produce_impl(_ctx, _workspace, qa, *, mark_ready, messages=None):
        produce_calls.append(qa.qa_id)
        assert mark_ready is True
        assert len(messages or []) == 2

    monkeypatch.setattr(mgr, "_produce_impl", _fake_produce_impl)

    scheduled = mgr.schedule_freeze_artifact_produce(
        ctx,
        workspace=ctx.workspace,
        qa_id="qa_007",
        native_messages=native,
    )
    assert scheduled is True
    for _ in range(50):
        if produce_calls:
            break
        await asyncio.sleep(0)
    assert produce_calls == ["qa_007"]


def test_schedule_freeze_artifact_produce_skips_when_already_complete(tmp_path):
    session = FakeSession("session-freeze-skip")
    native = [UserMessage(content="q", metadata={"context_message_id": "m1"})]
    store = QAArtifactStore(session, str(tmp_path))
    state = store.get_or_init("qa_008")
    state.products_ready = True
    state.covers_upto_message_id = "m1"
    store.save("qa_008", state)

    mgr = QAArtifactManager(QAArtifactConfig(), None, CatalogBuilder(QAArtifactConfig()))
    ctx = FakeCtx(session, FakeWorkspace(str(tmp_path)))

    scheduled = mgr.schedule_freeze_artifact_produce(
        ctx,
        workspace=ctx.workspace,
        qa_id="qa_008",
        native_messages=native,
    )
    assert scheduled is False


def test_schedule_freeze_artifact_produce_skips_below_history_block_threshold(tmp_path):
    session = FakeSession("session-freeze-small")
    native = [UserMessage(content="short q"), AssistantMessage(content="short a")]
    mgr = QAArtifactManager(
        QAArtifactConfig(history_block_compact_tokens=8000),
        None,
        CatalogBuilder(QAArtifactConfig()),
    )
    ctx = FakeCtx(session, FakeWorkspace(str(tmp_path)))

    scheduled = mgr.schedule_freeze_artifact_produce(
        ctx,
        workspace=ctx.workspace,
        qa_id="qa_009",
        native_messages=native,
    )
    assert scheduled is False

