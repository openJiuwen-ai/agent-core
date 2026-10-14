# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import Mock

import pytest

from openjiuwen.core.graph.store import create_state
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session.checkpointer.inmemory import (
    AgentTeamStorage,
    AgentStorage,
    InMemoryCheckpointer,
    WorkflowStorage,
)
from openjiuwen.core.session.config.base import Config
from openjiuwen.core.session.internal.agent import AgentSession
from openjiuwen.core.session.internal.agent_team import AgentTeamSession
from openjiuwen.core.session.internal.workflow import WorkflowSession
from openjiuwen.core.session.state.agent_state import StateCollection


@pytest.mark.asyncio
async def test_inmemory_agent_storage_save_recover_exists_and_clear():
    storage = AgentStorage()
    session = AgentSession(session_id="session-agent", config=Config())
    session.agent_id = Mock(return_value="agent-1")
    session.state().update({"name": "alice"})
    session.state().update_global({"shared": "value"})

    await storage.save(session)

    assert await storage.exists(session) is True

    recovered = AgentSession(session_id="session-agent", config=Config())
    recovered.agent_id = Mock(return_value="agent-1")
    await storage.recover(recovered)

    assert recovered.state().get("name") == "alice"
    assert recovered.state().get_global("shared") == "value"

    await storage.clear("agent-1")
    assert await storage.exists(session) is False


@pytest.mark.asyncio
async def test_inmemory_agent_group_storage_save_recover_exists_and_clear():
    storage = AgentTeamStorage()
    session = AgentTeamSession(session_id="session-team", team_id="team-1", config=Config())
    session.state().update({"agent_local": "should_not_be_restored"})
    session.state().update_global({"team": "alpha"})

    await storage.save(session)

    assert await storage.exists(session) is True

    recovered = AgentTeamSession(session_id="session-team", team_id="team-1", config=Config())
    recovered.state = Mock(return_value=StateCollection())
    await storage.recover(recovered)

    assert recovered.state().get_global("team") == "alpha"
    assert recovered.state().get("agent_local") is None

    await storage.clear("team-1")
    assert await storage.exists(session) is False


@pytest.mark.asyncio
async def test_inmemory_interactive_resume_without_checkpoint_raises():
    """修复6 统一校验：InteractiveInput 恢复但无任何 checkpoint → 显式抛错。

    此前 InMemory（默认后端）的 InteractiveInput 分支直接 recover() 静默
    返回，图从 start 节点全新起跑并在首个节点以难以定位的校验错误崩溃
    （如 Message content=None）——修复 6 校验此前仅覆盖 Persistence 后端。
    """
    checkpointer = InMemoryCheckpointer()
    parent = AgentSession(session_id="session-resume", config=Config())
    session = WorkflowSession(
        workflow_id="wf-1", parent=parent, session_id="session-resume"
    )

    with pytest.raises(Exception, match="no workflow checkpoint"):
        await checkpointer.pre_workflow_execute(session, InteractiveInput())


@pytest.mark.asyncio
async def test_inmemory_interactive_resume_with_unresumable_graph_raises():
    """修复6 统一校验：workflow state 存在但 graph checkpoint 缺失/空 →
    显式抛错（此前 InMemory 后端静默全新起跑）。"""
    checkpointer = InMemoryCheckpointer()
    parent = AgentSession(session_id="session-resume2", config=Config())
    session = WorkflowSession(
        workflow_id="wf-2", parent=parent, session_id="session-resume2"
    )
    session.state().update({"k": "v"})
    session.state().commit()
    await checkpointer._workflow_stores.setdefault("session-resume2", WorkflowStorage()).save(session)

    with pytest.raises(Exception, match="graph checkpoint"):
        await checkpointer.pre_workflow_execute(session, InteractiveInput())


@pytest.mark.asyncio
async def test_inmemory_interactive_resume_with_resumable_graph_recovers():
    """正例：io 侧与图侧快照齐备且图可续跑 → 校验通过、recover 正常执行。"""
    checkpointer = InMemoryCheckpointer()
    parent = AgentSession(session_id="session-resume3", config=Config())
    session = WorkflowSession(
        workflow_id="wf-3", parent=parent, session_id="session-resume3"
    )
    session.state().update({"k": "v"})
    session.state().commit()
    await checkpointer._workflow_stores.setdefault("session-resume3", WorkflowStorage()).save(session)
    await checkpointer._graph_store.save(
        "session-resume3",
        "wf-3",
        create_state("wf-3", 3, {"chan": "value"}),
    )

    await checkpointer.pre_workflow_execute(session, InteractiveInput())

    assert session.state().get("k") == "v"
