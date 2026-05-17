# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from openjiuwen.agent_evolving.checkpointing.types import EvolutionPatch, EvolutionRecord, EvolutionTarget
from openjiuwen.harness.rails.evolution.approval_events import (
    build_evolution_progress_event,
    build_progress_event,
    build_skill_approval_event,
    build_simplify_approval_event,
    build_team_skill_approval_event_from_records,
)


def _make_record(*, content: str = "experience content") -> EvolutionRecord:
    return EvolutionRecord.make(
        source="signal:skill-a",
        context="ctx",
        change=EvolutionPatch(
            section="Troubleshooting",
            action="append",
            content=content,
            target=EvolutionTarget.BODY,
        ),
    )


def test_build_progress_event_matches_reasoning_payload():
    event = build_progress_event("[Team Skill Evolution]", "analysis started")

    assert event.type == "llm_reasoning"
    assert event.payload == {"content": "[Team Skill Evolution] analysis started\n"}


def test_build_evolution_progress_event_includes_normalized_meta():
    event = build_evolution_progress_event(
        rail_kind="regular",
        stage="approval_required",
        message="awaiting approval",
        skill_name="skill-a",
        request_id="req-1",
        prefix="[Skill Evolution]",
    )

    assert event.type == "llm_reasoning"
    assert event.payload["content"] == "[Skill Evolution] awaiting approval\n"
    assert event.payload["_evolution_meta"] == {
        "event_kind": "progress",
        "rail_kind": "regular",
        "stage": "approval_required",
        "skill_name": "skill-a",
        "request_id": "req-1",
    }


def test_build_skill_approval_event_matches_existing_contract():
    pending = [
        _make_record(content="first experience"),
        _make_record(content="second experience"),
    ]

    event = build_skill_approval_event(
        skill_name="skill-a",
        request_id="skill_evolve_1234",
        records=pending,
    )

    assert event.type == "chat.ask_user_question"
    assert event.payload["request_id"] == "skill_evolve_1234"
    assert event.payload["_evolution_meta"] == {
        "event_kind": "approval",
        "skill_name": "skill-a",
        "request_id": "skill_evolve_1234",
    }
    assert event.payload["questions"][0]["header"] == "技能演进审批"
    assert len(event.payload["questions"]) == 2
    assert "Skill 'skill-a'" in event.payload["questions"][0]["question"]


def test_build_simplify_approval_event_matches_existing_contract():
    actions = [
        {"action": "DELETE", "record_id": "ev_1", "reason": "old"},
        {"action": "KEEP", "record_id": "ev_2", "reason": "good"},
    ]

    event = build_simplify_approval_event(
        skill_name="skill-a",
        request_id="evolve_simplify_1234",
        actions=actions,
    )

    assert event.type == "chat.ask_user_question"
    assert event.payload["request_id"] == "evolve_simplify_1234"
    assert event.payload["questions"][0]["header"] == "Skill 精简审批"
    assert "共 2 项操作" in event.payload["questions"][0]["question"]


def test_build_skill_approval_event_supports_english_language():
    event = build_skill_approval_event(
        skill_name="skill-a",
        request_id="skill_evolve_en",
        records=[_make_record(content="english experience")],
        language="en",
    )

    question = event.payload["questions"][0]
    assert question["header"] == "Skill Evolution Approval"
    assert "Skill 'skill-a' generated a new experience" in question["question"]
    assert question["options"][0]["label"] == "Accept"
    assert question["options"][1]["label"] == "Reject"


def test_build_simplify_approval_event_supports_english_language():
    event = build_simplify_approval_event(
        skill_name="skill-a",
        request_id="evolve_simplify_en",
        actions=[{"action": "DELETE", "record_id": "ev_1", "reason": "old"}],
        language="en",
    )

    question = event.payload["questions"][0]
    assert question["header"] == "Skill Simplify Approval"
    assert "Simplify evolution experiences for Skill 'skill-a'" in question["question"]
    assert "1 action(s)" in question["question"]
    assert question["options"][0]["label"] == "Execute"
    assert question["options"][1]["label"] == "Cancel"


def test_build_team_skill_approval_event_from_records_matches_record_payloads():
    event = build_team_skill_approval_event_from_records(
        skill_name="team-skill-a",
        request_id="skill_evolve_team_records",
        records=[
            _make_record(content="## Workflow\n- improve handoff"),
            _make_record(content="## Troubleshooting\n- add retry note"),
        ],
    )

    assert event.type == "chat.ask_user_question"
    assert event.payload["request_id"] == "skill_evolve_team_records"
    assert len(event.payload["questions"]) == 2
    assert "Team Skill 'team-skill-a' evolution" in event.payload["questions"][0]["question"]
    assert "improve handoff" in event.payload["questions"][0]["question"]
    assert "add retry note" in event.payload["questions"][1]["question"]
