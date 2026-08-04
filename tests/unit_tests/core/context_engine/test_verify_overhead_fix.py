# coding: utf-8
"""
快速验证 compact_to_target overhead 补偿修复。

模拟定位结论中的 bug 场景：
  - total_tokens (完整窗口) = 286K（system + context + tools）
  - estimate_context_messages_tokens (仅消息) ≈ 10
  - overhead ≈ 286K
  - full_compact_target_tokens = 92000

修复前：msg_est=10 ≤ 92000 → 误判"已达标" → 返回 True → 286K 原样发模型 → 81027
修复后：msg_est=10+286K=286K > 92000 → 正确走 fallback
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.context_engine import ContextEngineConfig
from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.qa_artifact.catalog import CatalogBuilder
from openjiuwen.core.context_engine.qa_artifact.manager import QAArtifactManager
from openjiuwen.core.context_engine.qa_artifact.schema import QAArtifactConfig, QAArtifacts
from openjiuwen.core.context_engine.qa_ref import QARef
from openjiuwen.core.foundation.llm import UserMessage


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
    def __init__(self, session, workspace):
        self.session = session
        self.workspace = workspace
        self.context = None
        self.sys_operation = None
        self.inputs = type("Inputs", (), {"messages": []})()


@pytest.mark.asyncio
async def test_overhead_compaction_prevents_false_target_hit(tmp_path, monkeypatch):
    """
    Bug 场景复现：
    total_tokens=286000 (完整窗口含 system+tools)，
    但 context messages 只有 10 tokens。
    所有 QA 已压缩过 (reduced=0)，进入 zero-progress 分支。

    修复前：msg_est=10 ≤ 92000 → 误判返回 True
    修复后：通过 count_full_window_tokens 回调传入完整窗口计数
           msg_est=286000 > 92000 → 正确走 fallback
    """
    session = FakeSession("session-overhead-bug")
    context = SessionModelContext(
        "default_context_id",
        "session-overhead-bug",
        ContextEngineConfig(),
        history_messages=[UserMessage(content="hi", metadata={"qa_id": "qa_001"})],
    )
    qa_ref = QARef(
        qa_id="qa_001",
        tokens=500,
        is_history=True,
        get_messages=lambda: context.get_messages(),
    )
    mgr = QAArtifactManager(
        QAArtifactConfig(full_compact_target_tokens=92000),
        None,
        CatalogBuilder(QAArtifactConfig()),
    )
    ctx = FakeCtx(session, FakeWorkspace(str(tmp_path)))
    ctx.context = context

    monkeypatch.setattr(mgr, "_is_compact_to_target_candidate", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        mgr,
        "ensure_compacted",
        AsyncMock(return_value=QAArtifacts(overview="overview", entries=[])),
    )
    monkeypatch.setattr(
        "openjiuwen.core.context_engine.qa_artifact.manager.apply_artifact_to_context",
        lambda *args, **kwargs: 0,
    )

    # 模拟完整窗口计数回调（包含 system + tools + context messages）
    full_window_tokens = 286000

    fallback = AsyncMock(return_value=True)
    handled = await mgr.compact_to_target(
        ctx,
        workspace=ctx.workspace,
        window_qas=[qa_ref],
        total_tokens=286000,
        context=context,
        fallback=fallback,
        trigger_total_tokens=92000,
        count_full_window_tokens=lambda: full_window_tokens,
    )

    assert handled is True
    fallback.assert_awaited_once()
    print("PASS: overhead 补偿生效，286K 完整窗口不再误判为已达标，正确走了 fallback")


@pytest.mark.asyncio
async def test_overhead_zero_when_total_tokens_matches_messages(tmp_path, monkeypatch):
    """
    正常场景：total_tokens 和消息 token 基本一致（无 system/tools 开销），
    overhead=0，行为与修复前一致，不受影响。
    """
    session = FakeSession("session-no-overhead")
    context = SessionModelContext(
        "default_context_id",
        "session-no-overhead",
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
        total_tokens=50000,
        context=context,
        fallback=fallback,
        trigger_total_tokens=50000,
        count_full_window_tokens=lambda: 50000,
    )

    assert handled is True
    assert fallback.await_count == 0
    print("PASS: 无 overhead 场景不受影响，消息 token < target 时正确返回 True")


@pytest.mark.asyncio
async def test_backward_compat_no_callback(tmp_path, monkeypatch):
    """不传 count_full_window_tokens 时走旧逻辑（仅计 context messages）。"""
    session = FakeSession("session-compat")
    context = SessionModelContext(
        "default_context_id",
        "session-compat",
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
        total_tokens=50000,
        context=context,
        fallback=fallback,
        trigger_total_tokens=50000,
        # 不传 count_full_window_tokens，走旧逻辑
    )

    assert handled is True
    assert fallback.await_count == 0
    print("PASS: 向后兼容路径正常，不传回调时行为与修复前一致")
