# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Template → Team member assembly: persona rendering and capability snapshot."""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.experts import (
    apply_template_snapshot,
    compose_template_member,
    render_template_persona,
    template_snapshot,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.schema.deep_agent_spec import DeepAgentSpec, WorkspaceSpec
from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec, PromptSectionSpec, SkillSpec


def _template(*sections: PromptSectionSpec, skills: tuple[str, ...] = ()) -> AgentTemplateSpec:
    return AgentTemplateSpec(
        agent_card=AgentCard(id="clip", name="clip", description="Shot artist"),
        prompt_sections=list(sections),
        skills=[SkillSpec(dir=f"/pkg/skills/{name}") for name in skills],
    )


def _base(**fields: object) -> DeepAgentSpec:
    values: dict[str, object] = {
        "language": "en",
        "skills": ["shared", "clip"],
        "workspace": WorkspaceSpec(root_path="/ws/teammate"),
    }
    values.update(fields)
    return DeepAgentSpec(**values)


@pytest.mark.level0
def test_persona_orders_by_priority_strips_and_drops_empty_sections() -> None:
    template = _template(
        PromptSectionSpec(name="rules", content={"cn": "  规则 {{ workspace }}  \n"}, priority=30),
        PromptSectionSpec(name="blank", content={"cn": "   \n"}, priority=20),
        PromptSectionSpec(name="identity", content={"cn": "\n我是 {{language}}\n"}, priority=10),
    )
    persona = render_template_persona(template, language="cn", workspace="/ws")
    assert persona == "我是 cn\n\n规则 /ws"


@pytest.mark.level0
def test_persona_language_fallback_and_placeholders() -> None:
    template = _template(
        PromptSectionSpec(name="a", content={"en": "EN {{ who }} {{ unknown }}"}, render_params={"who": "me"}, priority=10),
        PromptSectionSpec(name="b", content={"cn": "只有中文"}, priority=20),
        PromptSectionSpec(name="c", content={"fr": "seulement"}, priority=30),
    )
    assert render_template_persona(template, language="cn") == "EN me {{ unknown }}\n\n只有中文\n\nseulement"
    assert render_template_persona(template, language="  ") == render_template_persona(template, language="cn")
    assert render_template_persona(template, language=None, workspace=None) == render_template_persona(
        template, language="cn", workspace=""
    )


@pytest.mark.level0
def test_snapshot_drops_prompt_sections_and_apply_unions_skill_names() -> None:
    template = _template(
        PromptSectionSpec(name="identity", content={"cn": "x"}),
        skills=("clip", "storyboard", "clip"),
    )
    snapshot = template_snapshot(template)
    assert snapshot["prompt_sections"] == []
    assert snapshot == template.model_copy(update={"prompt_sections": []}).model_dump(mode="json")

    spec = apply_template_snapshot(_base(), snapshot)
    assert spec.agent_template_spec == snapshot
    assert spec.skills == ["shared", "clip", "storyboard"]
    assert _base().agent_template_spec is None, "the base spec is not mutated"


@pytest.mark.level0
def test_compose_equals_the_step_by_step_assembly() -> None:
    template = _template(
        PromptSectionSpec(name="identity", content={"en": "Work in {{ workspace }} ({{ language }})"}),
        skills=("render",),
    )
    base = _base()
    member = compose_template_member(base, template)

    stepwise_spec = apply_template_snapshot(base, template_snapshot(template))
    stepwise_persona = render_template_persona(template, language=base.language, workspace="/ws/teammate")
    assert member.spec.model_dump(mode="json") == stepwise_spec.model_dump(mode="json")
    assert member.persona == stepwise_persona == "Work in /ws/teammate (en)"


@pytest.mark.level1
def test_compose_defaults_language_and_workspace() -> None:
    template = _template(PromptSectionSpec(name="identity", content={"cn": "{{language}}|{{workspace}}"}))
    member = compose_template_member(_base(language=None, workspace=None), template)
    assert member.persona == "cn|"
    assert compose_template_member(_base(), template, language="cn").persona == "cn|/ws/teammate"
