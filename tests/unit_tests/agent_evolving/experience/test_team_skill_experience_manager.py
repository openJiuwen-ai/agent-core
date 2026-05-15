# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionPatch,
    EvolutionRecord,
    EvolutionTarget,
)
from openjiuwen.agent_evolving.experience.skill_experience_manager import ExperienceManager
from openjiuwen.agent_evolving.types import ApplyResult


def _make_record(*, content: str = "team experience") -> EvolutionRecord:
    return EvolutionRecord.make(
        source="team-skill",
        context="ctx",
        change=EvolutionPatch(
            section="Workflow",
            action="append",
            content=content,
            target=EvolutionTarget.BODY,
        ),
    )


def _make_manager(*, language: str = "cn") -> ExperienceManager:
    store = Mock()
    scorer = Mock()
    return ExperienceManager(
        store=store,
        scorer=scorer,
        kind="team-skill",
        language=language,
    )


def test_stage_apply_results_exposes_team_proposal_fields_and_apply_results():
    manager = _make_manager()
    record = _make_record(content="apply-result")

    request = manager.stage_apply_results(
        "team-skill-a",
        [
            ApplyResult(
                operator_id="skill_experience_team-skill-a",
                target="experiences",
                applied=True,
                mode="append",
                effect="pending_change",
                records=[record],
                change_type="skill_experience_entry",
                lifecycle_stage="local_apply_completed",
            )
        ],
        source="team_skill_experience_updater",
        request_id_prefix="team_skill_evolve",
        user_query="",
        signal_type="user_intent",
        signal_source="explicit_request",
    )

    assert request.proposal.user_query == ""
    assert request.proposal.signal_type == "user_intent"
    assert request.proposal.signal_source == "explicit_request"
    assert request.proposal.source == "team_skill_experience_updater"
    assert request.request_id is not None
    assert request.request_id.startswith("team_skill_evolve")
    assert len(request.apply_results) == 1


@pytest.mark.asyncio
async def test_request_simplify_stages_team_governance():
    manager = _make_manager()
    record = _make_record()
    manager._store.skill_exists = Mock(return_value=True)
    manager._store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[record]))
    manager._store.read_skill_content = AsyncMock(return_value="# team skill")
    manager._store.extract_description_from_skill_md = Mock(return_value="summary")
    manager._scorer.simplify = AsyncMock(return_value=[{"action": "KEEP", "record_id": record.id, "reason": "good"}])

    request_id = await manager.request_simplify("team-skill-a")

    assert request_id is not None
    assert request_id in manager.pending_governance
    assert manager.pending_governance[request_id]["kind"] == "simplify"
    assert manager.pending_governance[request_id]["skill_name"] == "team-skill-a"


@pytest.mark.asyncio
async def test_request_rebuild_uses_shared_helper():
    manager = _make_manager(language="en")
    record = _make_record(content="handoff checklist")
    record.score = 0.8
    manager._store.skill_exists = Mock(return_value=True)
    manager._store.archive_skill_body = AsyncMock(return_value="SKILL.v1.md")
    manager._store.archive_evolutions = AsyncMock(return_value="evolutions.v1.json")
    manager._store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[record]))
    manager._store.clear_evolutions = AsyncMock()

    context = await manager.request_rebuild(
        "team-skill-a",
        user_intent="optimize collaboration",
        min_score=0.5,
    )

    assert context is not None
    assert "teamskill-creator" in context.lower()
    manager._store.clear_evolutions.assert_awaited_once_with("team-skill-a")


@pytest.mark.asyncio
async def test_request_rebuild_context_keeps_english_record_labels():
    manager = _make_manager(language="en")
    record = _make_record(content="handoff checklist")
    record.score = 0.8
    manager._store.skill_exists = Mock(return_value=True)
    manager._store.archive_skill_body = AsyncMock(return_value="SKILL.v1.md")
    manager._store.archive_evolutions = AsyncMock(return_value="evolutions.v1.json")
    manager._store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[record]))
    manager._store.clear_evolutions = AsyncMock()

    prompt = await manager.request_rebuild(
        "team-skill-a",
        user_intent="optimize collaboration",
        min_score=0.5,
    )

    assert prompt is not None
    assert "Experience #1" in prompt
    assert "Content: handoff checklist" in prompt
    assert "经验 #" not in prompt
    assert "内容:" not in prompt
