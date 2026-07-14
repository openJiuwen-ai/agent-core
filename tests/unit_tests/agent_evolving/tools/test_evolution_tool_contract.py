# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.agent_evolving.prompts.tools import (
    EvolveSkillExperiencesMetadataProvider,
    ListSkillExperiencesMetadataProvider,
    PrepareSkillEvolutionReviewMetadataProvider,
    SimplifySkillExperiencesMetadataProvider,
)
from openjiuwen.harness.rails.evolution.review.runtime import EvolutionReviewRuntime
from openjiuwen.agent_evolving.tools import create_main_evolution_tools


@pytest.mark.parametrize(
    ("provider", "required", "must_have", "must_not_have"),
    [
        (
            PrepareSkillEvolutionReviewMetadataProvider(),
            ["subject", "user_confirmed"],
            {"subject", "user_confirmed", "user_intent"},
            {"evolution_review_ref", "experiences"},
        ),
        (
            EvolveSkillExperiencesMetadataProvider(),
            ["subject", "evolution_review_ref", "selected_proposal_ids"],
            {"subject", "evolution_review_ref", "selected_proposal_ids"},
            {"experiences"},
        ),
        (
            ListSkillExperiencesMetadataProvider(),
            ["subject"],
            {"subject", "target", "section", "query", "sort"},
            set(),
        ),
    ],
)
def test_main_evolution_metadata_contracts(provider, required, must_have, must_not_have):
    schema = provider.get_input_params()

    assert schema["required"] == required
    assert must_have <= set(schema["properties"])
    assert must_not_have.isdisjoint(schema["properties"])
    assert schema["properties"]["subject"]["properties"]["kind"]["enum"] == ["skill", "swarm-skill"]


def test_evolution_metadata_declares_review_and_simplify_constraints_in_english():
    evolve_schema = EvolveSkillExperiencesMetadataProvider().get_input_params(language="en")
    simplify_schema = SimplifySkillExperiencesMetadataProvider().get_input_params(language="en")

    assert "proposal" in evolve_schema["properties"]["selected_proposal_ids"]["description"].lower()
    action = simplify_schema["properties"]["actions"]["items"]["properties"]["action"]
    assert action["enum"] == ["DELETE", "MERGE", "REFINE", "KEEP"]
    assert "Allowed values" in action["description"]


def test_main_tools_declare_generic_subject_schema():
    tools = create_main_evolution_tools(
        query_service=Mock(),
        submission_service=Mock(),
        prepare_scope=EvolutionReviewRuntime().create_scope,
    )

    subject_schema = tools[0].card.input_params["properties"]["subject"]

    assert subject_schema["properties"]["kind"]["enum"] == ["skill", "swarm-skill"]


@pytest.mark.asyncio
async def test_main_tools_accept_team_skill_alias_as_swarm_skill():
    query_service = Mock()
    query_service.list_experiences = AsyncMock(return_value={"items": [], "total_count": 0})
    tools = {
        tool.card.name: tool
        for tool in create_main_evolution_tools(
            query_service=query_service,
            submission_service=Mock(),
            prepare_scope=EvolutionReviewRuntime().create_scope,
        )
    }

    result = await tools["list_skill_experiences"].invoke(
        {
            "subject": {"kind": "team-skill", "name": "team-a"},
        }
    )

    assert result.success is True
    query_service.list_experiences.assert_awaited_once_with(
        {"kind": "swarm-skill", "name": "team-a"},
        min_score=None,
        limit=20,
        cursor=None,
        target=None,
        section=None,
        query=None,
        sort="score_desc",
    )


@pytest.mark.asyncio
async def test_regular_skill_tool_rejects_unsupported_subject_kind_at_runtime():
    submission_service = Mock()
    submission_service.apply_experience_drafts = AsyncMock()
    tools = {
        tool.card.name: tool
        for tool in create_main_evolution_tools(
            query_service=Mock(),
            submission_service=submission_service,
            prepare_scope=EvolutionReviewRuntime().create_scope,
        )
    }

    result = await tools["evolve_skill_experiences"].invoke(
        {
            "subject": {"kind": "widget", "name": "research-team"},
            "experiences": [{"summary": "x", "content": "y"}],
        }
    )

    assert result.success is False
    assert "subject.kind must be one of" in result.error
    submission_service.apply_experience_drafts.assert_not_awaited()
