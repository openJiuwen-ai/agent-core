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
