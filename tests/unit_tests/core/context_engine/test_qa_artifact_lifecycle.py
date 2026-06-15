# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.context_engine.qa_artifact import QAArtifactConfig, cancel_qa_artifact_tasks_for_session
from openjiuwen.core.context_engine.qa_artifact.lifecycle import register_qa_artifact_manager
from openjiuwen.core.context_engine.qa_artifact.catalog import CatalogBuilder
from openjiuwen.core.context_engine.qa_artifact.manager import QAArtifactManager
from openjiuwen.core.context_engine.qa_block.freezer import QABlockFreezer
from openjiuwen.core.context_engine.qa_block.freezer_lifecycle import (
    cancel_qa_block_freeze_tasks_for_session,
)
from openjiuwen.core.context_engine.qa_ref import QARef


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


class _BlockingManager(QAArtifactManager):
    def __init__(self, config: QAArtifactConfig, release: asyncio.Event):
        super().__init__(config, None, CatalogBuilder(config))
        self._release = release

    async def _produce(self, ctx, workspace, qa, *, mark_ready):
        await self._release.wait()


@pytest.mark.asyncio
async def test_dual_session_same_qa_id_both_schedule(tmp_path):
    release = asyncio.Event()
    config = QAArtifactConfig(history_block_compact_tokens=10)
    mgr = _BlockingManager(config, release)

    window = [QARef(qa_id="qa_001", tokens=100, is_history=True, get_messages=lambda: [])]
    workspace = FakeWorkspace(str(tmp_path))

    ctx_a = FakeCtx(FakeSession("session-a"), workspace)
    ctx_b = FakeCtx(FakeSession("session-b"), workspace)

    started_a = await mgr.maybe_compact_history_block(
        ctx_a,
        workspace=workspace,
        window_qas=window,
        window_tokens=200,
    )
    started_b = await mgr.maybe_compact_history_block(
        ctx_b,
        workspace=workspace,
        window_qas=window,
        window_tokens=200,
    )

    assert started_a is True
    assert started_b is True
    assert ("session-a", "qa_001") in mgr._bg
    assert ("session-b", "qa_001") in mgr._bg

    release.set()
    await asyncio.gather(
        mgr._bg[("session-a", "qa_001")],
        mgr._bg[("session-b", "qa_001")],
    )


@pytest.mark.asyncio
async def test_cancel_session_tasks_clears_bg_and_overview(tmp_path):
    release = asyncio.Event()
    config = QAArtifactConfig(history_block_compact_tokens=10)
    mgr = _BlockingManager(config, release)
    session = FakeSession("session-cancel")
    workspace = FakeWorkspace(str(tmp_path))
    ctx = FakeCtx(session, workspace)
    window = [QARef(qa_id="qa_001", tokens=100, is_history=True, get_messages=lambda: [])]

    await mgr.maybe_compact_history_block(
        ctx,
        workspace=workspace,
        window_qas=window,
        window_tokens=200,
    )
    key = ("session-cancel", "qa_001")
    assert key in mgr._bg

    await mgr.cancel_session_tasks("session-cancel")

    assert key not in mgr._bg
    assert "session-cancel" not in mgr._session_task_keys


@pytest.mark.asyncio
async def test_clear_context_cancels_registered_qa_artifact_tasks(tmp_path):
    release = asyncio.Event()
    config = QAArtifactConfig(history_block_compact_tokens=10)
    mgr = _BlockingManager(config, release)
    register_qa_artifact_manager(mgr)

    engine = ContextEngine()
    session = FakeSession("session-clear")
    workspace = FakeWorkspace(str(tmp_path))
    ctx = FakeCtx(session, workspace)
    window = [QARef(qa_id="qa_001", tokens=100, is_history=True, get_messages=lambda: [])]

    await mgr.maybe_compact_history_block(
        ctx,
        workspace=workspace,
        window_qas=window,
        window_tokens=200,
    )
    assert ("session-clear", "qa_001") in mgr._bg

    await engine.clear_context(session_id="session-clear")

    assert ("session-clear", "qa_001") not in mgr._bg
    release.set()


@pytest.mark.asyncio
async def test_cancel_qa_artifact_tasks_for_session_all_managers():
    release = asyncio.Event()
    config = QAArtifactConfig(history_block_compact_tokens=10)
    mgr_a = _BlockingManager(config, release)
    mgr_b = _BlockingManager(config, release)
    register_qa_artifact_manager(mgr_a)
    register_qa_artifact_manager(mgr_b)
    workspace = FakeWorkspace("/tmp")
    window = [QARef(qa_id="qa_001", tokens=100, is_history=True, get_messages=lambda: [])]

    ctx = FakeCtx(FakeSession("shared-session"), workspace)
    await mgr_a.maybe_compact_history_block(ctx, workspace=workspace, window_qas=window, window_tokens=200)
    await mgr_b.maybe_compact_history_block(ctx, workspace=workspace, window_qas=window, window_tokens=200)

    await cancel_qa_artifact_tasks_for_session("shared-session")

    assert ("shared-session", "qa_001") not in mgr_a._bg
    assert ("shared-session", "qa_001") not in mgr_b._bg
    release.set()


@pytest.mark.asyncio
async def test_freezer_cancel_session_tasks():
    freezer = QABlockFreezer()
    gate = asyncio.Event()

    async def _hang():
        await gate.wait()

    task = asyncio.create_task(_hang())
    freezer._track_freeze_task("session-freeze", task)
    assert "session-freeze" in freezer._freeze_tasks_by_session

    await freezer.cancel_session_tasks("session-freeze")

    assert task.cancelled() or task.done()
    assert "session-freeze" not in freezer._freeze_tasks_by_session
    gate.set()


@pytest.mark.asyncio
async def test_cancel_qa_block_freeze_tasks_for_session():
    gate = asyncio.Event()
    freezer_a = QABlockFreezer()
    freezer_b = QABlockFreezer()

    async def _hang():
        await gate.wait()

    task_a = asyncio.create_task(_hang())
    task_b = asyncio.create_task(_hang())
    freezer_a._track_freeze_task("session-x", task_a)
    freezer_b._track_freeze_task("session-x", task_b)

    await cancel_qa_block_freeze_tasks_for_session("session-x")

    assert task_a.cancelled() or task_a.done()
    assert task_b.cancelled() or task_b.done()
    gate.set()
