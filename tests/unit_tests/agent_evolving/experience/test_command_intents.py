# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from openjiuwen.harness.rails.evolution.commands import build_rebuild_command_prompt


def test_build_rebuild_command_prompt_returns_prompt():
    prompt = build_rebuild_command_prompt(
        subject={"kind": "skill", "name": "skill-a"},
        user_intent="make it stricter",
        rebuild_context={
            "min_score": 0.5,
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

    assert "evolve_rebuild" in prompt
    assert "skill-creator" in prompt
    assert "Always validate inputs strictly." in prompt
    assert "reset evolutions.json" not in prompt
    assert "{skills_base}/skill-a/SKILL.md" in prompt
    assert "Resolve `{skills_base}`" in prompt
    assert "Required rebuild workflow" in prompt
    assert "1. read_file `skill-creator/SKILL.md`" in prompt
    assert "4. write_file `{skills_base}/skill-a/SKILL.md`" in prompt
    assert "chat-only drafts do NOT count as completion" in prompt


def test_build_rebuild_command_prompt_uses_skills_base_for_target_path():
    prompt = build_rebuild_command_prompt(
        subject={"kind": "skill", "name": "skill-a"},
        skills_base="/data/skills/",
        rebuild_context={"records": [], "overflow_index": {"items": []}},
    )

    assert "/data/skills/skill-a/SKILL.md" in prompt
    assert "2. read_file `/data/skills/skill-a/SKILL.md`" in prompt
    assert "4. write_file `/data/skills/skill-a/SKILL.md`" in prompt
    assert "Resolve `{skills_base}`" not in prompt


def test_build_rebuild_command_prompt_reads_skills_base_from_rebuild_context():
    prompt = build_rebuild_command_prompt(
        subject={"kind": "skill", "name": "skill-a"},
        rebuild_context={
            "skills_base": "/workspace/skills",
            "records": [],
            "overflow_index": {"items": []},
        },
    )

    assert "/workspace/skills/skill-a/SKILL.md" in prompt
    assert "Resolve `{skills_base}`" not in prompt


def test_build_rebuild_command_prompt_uses_swarmskill_creator_for_swarm_skill():
    prompt = build_rebuild_command_prompt(
        subject={"kind": "swarm-skill", "name": "team-skill"},
        skills_base="/data/skills",
        rebuild_context={"records": [], "overflow_index": {"items": []}},
    )

    assert "swarmskill-creator" in prompt
    assert "1. read_file `swarmskill-creator/SKILL.md`" in prompt
    assert "/data/skills/team-skill/SKILL.md" in prompt
