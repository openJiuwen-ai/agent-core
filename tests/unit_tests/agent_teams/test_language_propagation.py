# coding: utf-8
"""Unit tests for language propagation through TeamAgentSpec.build()."""
import pytest

from openjiuwen.agent_teams.schema.blueprint import DeepAgentSpec, TeamAgentSpec


def _make_agent(**kw) -> DeepAgentSpec:
    return DeepAgentSpec(**kw)


def test_language_en_propagates_to_team_spec():
    spec = TeamAgentSpec(agents={"leader": _make_agent()}, language="en")
    agent = spec.build()
    assert agent._ctx.team_spec.language == "en"


def test_language_none_falls_back_to_cn():
    spec = TeamAgentSpec(agents={"leader": _make_agent()})
    agent = spec.build()
    assert agent._ctx.team_spec.language == "cn"


def test_language_zh_normalizes_to_cn():
    spec = TeamAgentSpec(agents={"leader": _make_agent()}, language="zh")
    agent = spec.build()
    assert agent._ctx.team_spec.language == "cn"


def test_language_propagates_to_deep_agent_spec():
    spec = TeamAgentSpec(agents={"leader": _make_agent()}, language="en")
    spec.build()
    assert spec.agents["leader"].language == "en"


def test_per_role_language_override_preserved():
    spec = TeamAgentSpec(agents={"leader": _make_agent(language="cn")}, language="en")
    spec.build()
    assert spec.agents["leader"].language == "cn"
    assert spec.agents["leader"].language == "cn"
