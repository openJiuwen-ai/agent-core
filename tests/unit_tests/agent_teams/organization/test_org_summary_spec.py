# coding: utf-8

from openjiuwen.agent_teams.organization.schema import ORG_SUMMARY_CAPABILITY, ORG_SUMMARY_TASK_TYPE
from openjiuwen.agent_teams.organization.summary import (
    LaunchedSummaryTeam,
    SummaryTeamFactory,
    SummaryTeamSpec,
)


def test_summary_team_spec_defaults_to_summary_capability():
    spec = SummaryTeamSpec()
    assert spec.capabilities == (ORG_SUMMARY_CAPABILITY,)
    assert spec.capabilities == ("summary",)
    assert spec.display_name == "Summary Team"
    assert spec.name == "summary-team-preset"
    assert isinstance(spec.prompt, str) and spec.prompt
    assert ORG_SUMMARY_TASK_TYPE in spec.prompt


def test_summary_team_spec_to_dict_lists_sequences():
    spec = SummaryTeamSpec()
    data = spec.to_dict()
    assert data["capabilities"] == ["summary"]
    assert data["tool_set"] == []
    assert data["model_policy"] == {"strategy": "default"}


def test_launched_summary_team_to_dict():
    spec = SummaryTeamSpec()
    launched = LaunchedSummaryTeam(
        team_id="summary-team",
        leader_id="summary-leader",
        root_task_id="root-1",
        summary_task_id="summary-1",
        spec=spec,
    )
    data = launched.to_dict()
    assert data["team_id"] == "summary-team"
    assert data["leader_id"] == "summary-leader"
    assert data["root_task_id"] == "root-1"
    assert data["summary_task_id"] == "summary-1"
    assert data["spec"]["capabilities"] == ["summary"]


def test_summary_team_factory_is_a_protocol():
    # Protocol subclasses carry the private _is_protocol flag (typing sets the
    # public __is_protocol__ attribute only for @runtime_checkable).
    assert getattr(SummaryTeamFactory, "_is_protocol", False) is True
