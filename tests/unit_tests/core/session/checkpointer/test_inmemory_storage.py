# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import Mock

import pytest

from openjiuwen.core.session.checkpointer.inmemory import AgentTeamStorage, AgentStorage
from openjiuwen.core.session.config.base import Config
from openjiuwen.core.session.internal.agent import AgentSession
from openjiuwen.core.session.internal.agent_team import AgentTeamSession
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
