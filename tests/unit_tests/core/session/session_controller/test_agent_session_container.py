# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.session_controller import global_controller
from openjiuwen.core.session.session_controller.data_container import AgentSessionContainer
from openjiuwen.core.single_agent import AgentCard


def _agent_session(session_id: str = "session-1"):
    return create_agent_session(session_id=session_id, card=AgentCard(id="agent1"))


@pytest.mark.asyncio
async def test_dump_returns_the_session_state():
    container = AgentSessionContainer(_agent_session())
    container.update({"topic": "restore", "nested": {"count": 2}})

    assert await container.dump() == {"topic": "restore", "nested": {"count": 2}}


@pytest.mark.asyncio
async def test_dump_leaves_out_state_not_written_through_the_container():
    session = _agent_session()
    container = AgentSessionContainer(session)
    container.update({"topic": "restore"})
    session.update_state({"context": object()})

    assert await container.dump() == {"topic": "restore"}


@pytest.mark.asyncio
async def test_dump_without_session_is_empty():
    assert await AgentSessionContainer().dump() == {}


@pytest.mark.asyncio
async def test_load_restores_dumped_state():
    source = AgentSessionContainer(_agent_session())
    source.update({"topic": "restore"})

    restored = await AgentSessionContainer.load("agent1", "session-1", await source.dump())

    assert restored.get("topic") == "restore"


@pytest.mark.asyncio
async def test_live_session_receives_restored_state():
    # When the agent creates its live session, the restored chain data moves into it
    restored = await AgentSessionContainer.load("agent1", "session-1", {"topic": "restore"})
    restored.session.update_state({"context": "placeholder only"})
    chain_session = MagicMock(data_container=restored)
    controller = MagicMock(session_cache={"session-1": chain_session})
    live_session = _agent_session()

    with patch("openjiuwen.core.runner.runner_config.get_runner_config",
               return_value=MagicMock(enable_session_controller=True)), \
            patch.object(global_controller, "get_global_session_controller",
                         return_value=MagicMock(get_agent=MagicMock(return_value=controller))):
        await global_controller._on_agent_session_created(
            session_id="session-1",
            card=AgentCard(id="agent1"),
            session=live_session,
        )

    assert restored.session is live_session
    assert live_session.get_state("topic") == "restore"
    assert live_session.get_state("context") is None
