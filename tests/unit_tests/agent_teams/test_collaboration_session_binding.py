# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Group history scope comes from the actual runtime, not ambient context."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.session_manager import SessionManager
from openjiuwen.agent_teams.agent.state import TeamAgentState
from openjiuwen.agent_teams.context import get_session_id, reset_session_id, set_session_id
from openjiuwen.agent_teams.paths import reset_task_openjiuwen_home, set_task_openjiuwen_home
from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
from openjiuwen.agent_teams.schema.blueprint import DeepAgentSpec, StorageSpec, TeamAgentSpec
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.team import TeamBackend


@pytest.mark.asyncio
@pytest.mark.parametrize("ambient", ["", "unrelated-parent-session"])
@pytest.mark.parametrize("group_chat", [True, False])
async def test_real_activation_binds_requested_session_without_host_contextvar(tmp_path, ambient, group_chat):
    home = set_task_openjiuwen_home(tmp_path)
    token = set_session_id(ambient)
    manager = TeamRuntimeManager()
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()}, team_name="bound-group", spawn_mode="inprocess",
        enable_group_chat=group_chat, evolution_enabled=False,
        storage=StorageSpec(type="sqlite", params={"connection_string": str(tmp_path / "scope.sqlite")}),
    )
    activation = None
    try:
        activation = await manager.activate(spec, "requested-session", {"query": ""})
        backend = activation.agent.team_backend
        if group_chat:
            assert backend.group_session_id == "requested-session"
            conversation = await backend.group_conversation()
            assert conversation.session_id == "requested-session"
        else:
            assert backend.group_session_id == ""
            with pytest.raises(ValueError, match="disabled"):
                await backend.group_conversation()
        assert get_session_id() == ambient
    finally:
        if activation is not None:
            await manager.stop_team(team_name=spec.team_name, session_id="requested-session")
            await activation.agent.team_backend.db.close()
        reset_session_id(token)
        reset_task_openjiuwen_home(home)


@pytest.mark.asyncio
async def test_group_history_uses_bound_member_session_not_ambient_scope(tmp_path):
    home = set_task_openjiuwen_home(tmp_path)
    token = set_session_id("parent-session")
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()}, team_name="outer", enable_group_chat=True)
    backend = TeamBackend("outer", "representative", False,
                          SimpleNamespace(create_cur_session_tables=AsyncMock()), AsyncMock())
    backend.group_chat_spec = spec
    configurator = SimpleNamespace(team_backend=backend, spec=spec, role=TeamRole.TEAMMATE)
    manager = SessionManager(state=TeamAgentState(), configurator=configurator, recovery_manager=SimpleNamespace())
    try:
        assert backend.group_session_id == ""
        await manager.bind_session(SimpleNamespace(get_session_id=lambda: "actual-session"))
        assert backend.group_session_id == "actual-session"
        conversation = await backend.group_conversation()
        assert (conversation.team_name, conversation.session_id) == ("outer", "actual-session")
        manager.release_session()
        assert get_session_id() == "parent-session"
        assert backend.group_session_id == "actual-session"
        assert (await backend.group_conversation()).path == conversation.path
        with pytest.raises(ValueError, match="stop and rebuild"):
            await manager.bind_session(SimpleNamespace(get_session_id=lambda: "wrong-session"))
        assert get_session_id() == "parent-session"
    finally:
        manager.release_session()
        reset_session_id(token)
        reset_task_openjiuwen_home(home)
