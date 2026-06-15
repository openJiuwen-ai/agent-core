# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import pytest

from openjiuwen.core.context_engine import ContextEngineConfig
from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.qa_artifact.catalog import CatalogBuilder
from openjiuwen.core.context_engine.qa_artifact.manager import QAArtifactManager
from openjiuwen.core.context_engine.qa_artifact.schema import QAArtifactConfig
from openjiuwen.core.context_engine.qa_block.history_buffer import HistoryQABuffer
from openjiuwen.core.context_engine.qa_block.layer import QABlockLayer
from openjiuwen.core.context_engine.qa_block.schema import QABlockEntry, QABlockRegistry
from openjiuwen.core.context_engine.qa_block.store import QABlockStore
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


def _registry(session_id: str = "session-a") -> QABlockRegistry:
    return QABlockRegistry(
        session_id=session_id,
        next_qa_index=3,
        current_qa_id="qa_003",
        blocks={
            "qa_001": QABlockEntry(qa_id="qa_001", qa_index=1, status="completed", is_history=True),
            "qa_002": QABlockEntry(qa_id="qa_002", qa_index=2, status="completed", is_history=True),
        },
    )


@pytest.mark.asyncio
async def test_resolve_messages_for_qa_reads_l0_when_not_preloaded(tmp_path):
    session_id = "session-layer"
    store = QABlockStore(str(tmp_path), session_id)
    l0_messages = [UserMessage(content="old question"), AssistantMessage(content="old answer")]
    await store.write_l0("qa_001", l0_messages)

    history = HistoryQABuffer(max_blocks=3)
    layer = QABlockLayer(_registry(session_id), history, store)
    context = SessionModelContext(
        "default_context_id",
        session_id,
        ContextEngineConfig(),
        history_messages=[],
    )

    sync_ref_before = layer.qa_ref("qa_001", context)
    assert sync_ref_before.get_messages() == []

    resolved = await layer.resolve_messages_for_qa("qa_001", context)
    assert len(resolved) == 2
    assert resolved[0].content == "old question"
    assert "qa_001" in layer._message_cache

    sync_ref_after = layer.qa_ref("qa_001", context)
    assert len(sync_ref_after.get_messages()) == 2


@pytest.mark.asyncio
async def test_qa_ref_for_index_loads_disk_for_render_index(tmp_path):
    session_id = "session-render"
    store = QABlockStore(str(tmp_path), session_id)
    await store.write_l0(
        "qa_002",
        [UserMessage(content="disk only"), AssistantMessage(content="from l0")],
    )

    history = HistoryQABuffer(max_blocks=1)
    history.push("qa_999", [UserMessage(content="hot")])
    layer = QABlockLayer(_registry(session_id), history, store)
    context = SessionModelContext(
        "default_context_id",
        session_id,
        ContextEngineConfig(),
        history_messages=[],
    )

    qa_ref = await layer.qa_ref_for_index("qa_002", context)
    mgr = QAArtifactManager(QAArtifactConfig(), None, CatalogBuilder(QAArtifactConfig()))
    fake_session = FakeSession(session_id)

    class FakeCtx:
        session = fake_session
        workspace = type("W", (), {"root_path": str(tmp_path)})()
        context = None
        sys_operation = None

    text = await mgr.render_index(FakeCtx(), workspace=FakeCtx.workspace, qa=qa_ref)
    assert "disk only" in text
    assert "(empty QA)" not in text


@pytest.mark.asyncio
async def test_resolve_messages_prefers_message_cache_over_disk(tmp_path):
    session_id = "session-cache"
    store = QABlockStore(str(tmp_path), session_id)
    await store.write_l0("qa_001", [UserMessage(content="from disk")])

    history = HistoryQABuffer(max_blocks=3)
    layer = QABlockLayer(_registry(session_id), history, store)
    layer._message_cache["qa_001"] = [UserMessage(content="from cache")]

    resolved = await layer.resolve_messages_for_qa("qa_001", None)
    assert resolved[0].content == "from cache"
