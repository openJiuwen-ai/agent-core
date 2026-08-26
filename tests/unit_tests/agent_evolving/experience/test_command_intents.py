# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from pathlib import Path

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


def test_build_evolve_review_command_prompt_without_user_intent_marks_empty_intent():
    prompt = build_evolve_review_command_prompt(
        subject={"kind": "skill", "name": "skill-a"},
        user_intent="",
        review_agent_name="custom_review_agent",
    )

    assert prompt


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
    assert "MUST call write_file or edit_file" in prompt
    assert "`skill-a/SKILL.md`" in prompt
    assert "Absolute write target (only):" not in prompt
    assert "Do NOT call todo_complete" in prompt
    assert "Do NOT mark Write/Confirm" in prompt
    assert "reset evolutions.json" not in prompt
    assert "Do NOT edit, rewrite, or clear evolutions.json" in prompt


def test_build_rebuild_command_prompt_uses_absolute_skill_md_path(tmp_path: Path):
    skill_md = tmp_path / "Beer" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("# Beer\n", encoding="utf-8")
    abs_path = str(skill_md.resolve())

    prompt = build_rebuild_command_prompt(
        subject={"kind": "skill", "name": "Beer"},
        user_intent="merge experiences",
        rebuild_context={
            "records": [],
            "overflow_index": {"items": []},
            "skill_md_path": abs_path,
        },
    )

    assert f"`{abs_path}`" in prompt
    assert "`Beer/SKILL.md`" not in prompt
    assert "Absolute write target (only):" in prompt
    assert "Do not glob/search by relative skill name or under agent cwd" in prompt
