# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from pathlib import Path

import pytest

from openjiuwen.agent_teams.external.manifest import external_cli_agent_spec_from_template
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.schema.extension_spec import AgentRuntimeSpec, AgentTemplateSpec, SkillSpec
from openjiuwen.harness_providers.factory import resolve_provider


@pytest.mark.parametrize(
    ("provider_name", "cli_agent"),
    [("codex", "codex"), ("claudecode", "claude")],
)
def test_external_cli_config_comes_from_parsed_template(
    tmp_path: Path,
    provider_name: str,
    cli_agent: str,
) -> None:
    skill = tmp_path / "review"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Review", encoding="utf-8")
    version = resolve_provider(provider_name).card.implementation_version
    template = AgentTemplateSpec(
        agent_card=AgentCard(id="reviewer", name="Reviewer", description="Reviews code"),
        runtime=AgentRuntimeSpec(
            provider_name=provider_name,
            provider_version=version,
            config={"skill_conflict": "replace"},
        ),
        skills=[SkillSpec(dir=str(skill))],
    )

    config = external_cli_agent_spec_from_template(template)

    assert config is not None
    assert config.cli_agent == cli_agent
    assert config.skill_conflict == "replace"
    assert config.skills == [{"dir": str(skill), "mode": "all", "enabled_skills": None}]


def test_external_cli_config_rejects_sdk_paths_and_unknown_config(tmp_path: Path) -> None:
    version = resolve_provider("codex").card.implementation_version
    base = {
        "provider_name": "codex",
        "provider_version": version,
    }
    template = AgentTemplateSpec(
        agent_card=AgentCard(id="reviewer", name="Reviewer", description="Reviews code"),
        runtime=AgentRuntimeSpec(**base, sdk_paths=[str(tmp_path / "provider.whl")]),
    )
    with pytest.raises(ValueError, match="sdk_paths is not supported"):
        external_cli_agent_spec_from_template(template)

    template.runtime = AgentRuntimeSpec(**base, config={"cwd": "/tmp"})
    with pytest.raises(ValueError, match="runtime.config cannot set: cwd"):
        external_cli_agent_spec_from_template(template)


@pytest.mark.parametrize("provider_name", ["codex", "claudecode"])
def test_external_manifest_appends_skills_by_default(provider_name: str) -> None:
    version = resolve_provider(provider_name).card.implementation_version
    template = AgentTemplateSpec(
        agent_card=AgentCard(id="member", name="Member", description="External member"),
        runtime=AgentRuntimeSpec(provider_name=provider_name, provider_version=version),
    )

    config = external_cli_agent_spec_from_template(template)
    assert config is not None
    assert config.skill_conflict == "append"
