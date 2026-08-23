from openjiuwen.agent_teams.schema.blueprint import DeepAgentSpec, TeamAgentSpec


def test_team_approval_mode_default_user_mediated() -> None:
    """team_approval_mode defaults to user-mediated (feature on by default), leader-mediated opt-out."""
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()})
    assert spec.team_approval_mode == "user-mediated"


def test_team_approval_mode_leader_mediated_opt_out() -> None:
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()}, team_approval_mode="leader-mediated")
    assert spec.team_approval_mode == "leader-mediated"
