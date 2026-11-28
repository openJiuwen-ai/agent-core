# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Template connectors are resolved by the host into the member's own MCPs and Skills."""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.experts import template_connectors as tc
from openjiuwen.agent_teams.experts.template_connectors import ResolvedConnectors
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec


def _cfg(name: str) -> McpServerConfig:
    return McpServerConfig(server_name=name, server_path="", client_type="stdio")


def _spec(
    connectors: list[str] | None,
    mcps: list[McpServerConfig] | None = None,
    *,
    skills: list[str] | None = None,
    template_skills: list[dict] | None = None,
) -> DeepAgentSpec:
    snapshot = None
    if connectors is not None:
        snapshot = {"connectors": connectors, "skills": list(template_skills or [])}
    return DeepAgentSpec(agent_template_spec=snapshot, mcps=mcps, skills=skills)


@pytest.fixture(autouse=True)
def _reset_resolver():
    yield
    tc.register_template_connector_resolver(None)


@pytest.mark.level0
def test_resolved_connectors_are_appended_for_that_member() -> None:
    calls: list[tuple[list[str], str, str]] = []

    def resolver(names: list[str], team: str, member: str) -> ResolvedConnectors:
        calls.append((names, team, member))
        return ResolvedConnectors(mcps=[_cfg(name) for name in names])

    tc.register_template_connector_resolver(resolver)

    out = tc.attach_template_connectors(_spec(["amap", "amap"]), team_name="t1", member_name="amap-expert")

    assert [cfg.server_name for cfg in out.mcps] == ["amap"]
    assert calls == [(["amap"], "t1", "amap-expert")]


@pytest.mark.level0
def test_existing_server_is_not_duplicated() -> None:
    tc.register_template_connector_resolver(
        lambda names, team, member: ResolvedConnectors(mcps=[_cfg("amap"), _cfg("harmonyos-mcp")])
    )

    out = tc.attach_template_connectors(
        _spec(["amap", "harmonyos-mcp"], mcps=[_cfg("amap")]), team_name="t", member_name="m"
    )

    assert [cfg.server_name for cfg in out.mcps] == ["amap", "harmonyos-mcp"]


@pytest.mark.level0
def test_connector_skills_become_template_skills_and_seed_allow_list(tmp_path) -> None:
    """CLI connector Skills mount per Skill dir and join the member's own Skill names (plan A)."""
    pr_dir = str(tmp_path / "gitcode" / "gitcode-pr")
    issue_dir = str(tmp_path / "gitcode" / "gitcode-issue")
    tc.register_template_connector_resolver(
        lambda names, team, member: ResolvedConnectors(skill_dirs=[pr_dir, issue_dir])
    )

    out = tc.attach_template_connectors(_spec(["gitcode"]), team_name="t", member_name="gitcode-expert")

    assert out.skills == ["gitcode-pr", "gitcode-issue"]
    assert [item["dir"] for item in out.agent_template_spec["skills"]] == [pr_dir, issue_dir]
    assert out.agent_template_spec["connectors"] == ["gitcode"]
    assert out.mcps is None
    snapshot = AgentTemplateSpec.model_validate({"agent_card": {"name": "x"}, **out.agent_template_spec})
    assert [skill.dir for skill in snapshot.skills] == [pr_dir, issue_dir]


@pytest.mark.level1
def test_connector_skills_append_after_template_skills_without_duplicates(tmp_path) -> None:
    own_dir = str(tmp_path / "pkg" / "skills" / "bookmark-curation")
    pr_dir = str(tmp_path / "gitcode" / "gitcode-pr")
    tc.register_template_connector_resolver(
        lambda names, team, member: ResolvedConnectors(skill_dirs=[pr_dir, pr_dir])
    )
    spec = _spec(["gitcode"], skills=["bookmark-curation"], template_skills=[{"dir": own_dir, "mode": "all"}])

    out = tc.attach_template_connectors(spec, team_name="t", member_name="m")

    assert out.skills == ["bookmark-curation", "gitcode-pr"]
    assert [item["dir"] for item in out.agent_template_spec["skills"]] == [own_dir, pr_dir]


@pytest.mark.level1
@pytest.mark.parametrize("connectors", [None, []])
def test_member_without_connectors_is_untouched(connectors: list[str] | None) -> None:
    def resolver(names, team, member):  # pragma: no cover - must not be called
        raise AssertionError("resolver called without connectors")

    tc.register_template_connector_resolver(resolver)
    spec = _spec(connectors)

    assert tc.attach_template_connectors(spec, team_name="t", member_name="m") is spec


@pytest.mark.level1
def test_no_resolver_keeps_spec() -> None:
    spec = _spec(["amap"])

    assert tc.attach_template_connectors(spec, team_name="t", member_name="m") is spec


@pytest.mark.level1
def test_malformed_resolver_results_are_skipped_not_raised(tmp_path) -> None:
    """Non-config MCP entries, duplicates within one result and relative skill dirs never fail the build."""
    good_dir = str(tmp_path / "gitcode" / "gitcode-pr")
    tc.register_template_connector_resolver(
        lambda names, team, member: ResolvedConnectors(
            mcps=[_cfg("amap"), {"server_name": "dict"}, None, _cfg("amap")],
            skill_dirs=["relative/skill", None, good_dir],
        )
    )

    out = tc.attach_template_connectors(_spec(["amap", "gitcode"]), team_name="t", member_name="m")

    assert [cfg.server_name for cfg in out.mcps] == ["amap"]
    assert out.skills == ["gitcode-pr"]
    assert [item["dir"] for item in out.agent_template_spec["skills"]] == [good_dir]


@pytest.mark.level1
def test_non_resolved_result_keeps_spec() -> None:
    """A resolver returning something other than ResolvedConnectors is ignored, not raised."""
    tc.register_template_connector_resolver(lambda names, team, member: [_cfg("amap")])
    spec = _spec(["amap"])

    assert tc.attach_template_connectors(spec, team_name="t", member_name="m") is spec


@pytest.mark.level1
def test_nothing_resolved_keeps_spec() -> None:
    tc.register_template_connector_resolver(lambda names, team, member: ResolvedConnectors())
    spec = _spec(["gitcode"])

    assert tc.attach_template_connectors(spec, team_name="t", member_name="m") is spec


@pytest.mark.level1
def test_resolver_failure_degrades_without_raising() -> None:
    def resolver(names, team, member):
        raise RuntimeError("boom")

    tc.register_template_connector_resolver(resolver)
    spec = _spec(["amap"], mcps=[_cfg("global")])

    out = tc.attach_template_connectors(spec, team_name="t", member_name="m")

    assert [cfg.server_name for cfg in out.mcps] == ["global"]
