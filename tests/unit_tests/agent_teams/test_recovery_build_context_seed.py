# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Recovery must serialize the same platform context that the leader uses."""

import copy
from typing import Any
from unittest.mock import PropertyMock

import pytest

from openjiuwen.agent_teams.agent.payload import SpawnPayloadBuilder
from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.runtime.metadata import write_team_namespace
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.team import TeamRuntimeContext
from openjiuwen.core.session.agent_team import create_agent_team_session
from openjiuwen.harness.schema import build_context as context_module
from openjiuwen.harness.schema.build_context import BuildContext


@pytest.mark.parametrize("old_seed", [None, {"execution_settings_managed": False}])
@pytest.mark.parametrize("new_seed", [None, {"execution_settings_managed": True, "nested": {"version": 1}}])
def test_recovery_spawn_uses_current_seed(
    monkeypatch: pytest.MonkeyPatch,
    old_seed: dict[str, Any] | None,
    new_seed: dict[str, Any] | None,
) -> None:
    sid = "recovery-seed-test"
    team_name = "recovery-seed-team"
    live = BuildContext(extras={"execution_settings_managed": True})
    runtime = TeamAgentSpec(
        team_name=team_name,
        agents={"leader": {}},
        build_context=live,
        build_context_seed=copy.deepcopy(new_seed),
    )
    saved = runtime.model_dump(mode="json")
    saved["build_context_seed"] = old_seed
    session = create_agent_team_session(session_id=sid, team_id=team_name)
    write_team_namespace(session, team_name, {
        "spec": saved,
        "context": {
            "role": "leader",
            "member_name": "leader",
            "team_spec": {"team_name": team_name, "display_name": team_name, "leader_member_name": "leader"},
        },
    })
    captured = {}

    def configure(agent: TeamAgent, spec: TeamAgentSpec, context: TeamRuntimeContext) -> None:
        captured["spec"] = spec
        captured["context"] = context

    def rebuild(seed: dict[str, Any]) -> BuildContext:
        return BuildContext(extras=dict(seed))

    monkeypatch.setattr(TeamAgent, "configure", configure)
    monkeypatch.setattr(TeamAgent, "team_backend", PropertyMock(return_value=None))
    monkeypatch.setattr(context_module, "_BUILD_CONTEXT_FACTORY", rebuild)
    token = set_session_id(sid)
    try:
        TeamAgent.recover_from_session(session, team_name, runtime_spec=runtime)
        recovered = captured["spec"]
        assert recovered.build_context is live
        assert recovered.build_context_seed == new_seed
        payload = SpawnPayloadBuilder(recovered, captured["context"]).build_spawn_config(captured["context"])
        child = TeamAgentSpec.model_validate(payload.payload["spec"])
        child.materialize_build_context()
        if new_seed is None:
            assert child.build_context is None
        else:
            assert child.build_context.extras["execution_settings_managed"] is True
            runtime.build_context_seed["nested"]["version"] = 2
            assert recovered.build_context_seed["nested"]["version"] == 1
    finally:
        reset_session_id(token)
