# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.checkpointer.inmemory import InMemoryCheckpointer
from openjiuwen.core.session.config.base import Config
from openjiuwen.core.session.internal.agent import AgentSession


async def _add_agent_store(checkpointer: InMemoryCheckpointer, session_id: str) -> None:
    session = AgentSession(
        session_id=session_id,
        config=Config(),
        card=SimpleNamespace(id=f"agent-{session_id}"),
    )
    await checkpointer.pre_agent_execute(session, None)


@pytest.mark.asyncio
async def test_runner_release_removes_exact_session_and_colon_children_only(monkeypatch):
    checkpointer = InMemoryCheckpointer()
    for session_id in ("chat-1", "chat-1:child", "chat-10", "chat-11", "chat-1xyz"):
        await _add_agent_store(checkpointer, session_id)

    monkeypatch.setattr(CheckpointerFactory, "get_checkpointer", lambda: checkpointer)
    monkeypatch.setattr(
        "openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_release_info",
        AsyncMock(return_value=None),
    )

    await Runner.release("chat-1")

    assert set(checkpointer._agent_stores) == {"chat-10", "chat-11", "chat-1xyz"}

    await Runner.release("chat-1")

    assert set(checkpointer._agent_stores) == {"chat-10", "chat-11", "chat-1xyz"}


@pytest.mark.asyncio
async def test_release_for_agent_keeps_session_store():
    checkpointer = InMemoryCheckpointer()
    await _add_agent_store(checkpointer, "chat-1")
    agent_store = checkpointer._agent_stores["chat-1"]

    await checkpointer.release("chat-1", agent_id="agent-chat-1")

    assert checkpointer._agent_stores["chat-1"] is agent_store
