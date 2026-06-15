# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json

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
