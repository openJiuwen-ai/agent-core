# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from openjiuwen.harness.rails.evolution.commands import (
    build_evolve_review_command_prompt,
    build_rebuild_command_prompt,
    build_simplify_command_prompt,
)


def test_build_simplify_command_prompt_accepts_team_skill_alias():
    prompt = build_simplify_command_prompt(
        subject={"kind": "team-skill", "name": "team-skill-a"},
        full_index={"items": [{"record_id": "ev_1", "summary": "Remove duplicate tips."}], "has_more": True},
        index_complete=False,
    )

    assert prompt


def test_build_evolve_review_command_prompt_returns_prompt():
    prompt = build_evolve_review_command_prompt(
        subject={"kind": "skill", "name": "skill-a"},
        user_intent="capture parser lesson",
        review_agent_name="custom_review_agent",
    )

    assert prompt
    assert "evolve_review_task(evolution_review_ref=...)" in prompt
    assert "evolve_review_task.data.output" in prompt
    assert "task_tool" not in prompt
    assert "custom_review_agent" not in prompt


def test_build_rebuild_command_prompt_returns_prompt():
    prompt = build_rebuild_command_prompt(
        subject={"kind": "skill", "name": "skill-a"},
        user_intent="make it stricter",
        rebuild_context={
            "records": [
                {
                    "record_id": "ev_1",
                    "summary": "Prefer strict validation.",
                    "target": "body",
                    "section": "Troubleshooting",
                    "score": 0.9,
                    "updated_at": "2026-01-01T00:00:00Z",
                    "content": "Always validate inputs strictly.",
                }
            ],
            "overflow_index": {"items": []},
        },
    )

    assert prompt
