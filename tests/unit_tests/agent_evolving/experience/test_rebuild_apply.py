# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from openjiuwen.agent_evolving.experience.rebuild import _to_index_item
from openjiuwen.agent_evolving.checkpointing.types import EvolutionPatch, EvolutionRecord
from openjiuwen.agent_evolving.signal.base import EvolutionTarget
from openjiuwen.harness.rails.evolution.commands import (
    build_rebuild_llm_direct_prompt,
    format_rebuild_context,
)


def test_rebuild_index_item_includes_action() -> None:
    record = EvolutionRecord.make(
        source="skill_sleep",
        context="t",
        change=EvolutionPatch(
            section="Instructions",
            action="delete",
            content="空气质量",
            target=EvolutionTarget.BODY,
        ),
        summary="删除空气质量",
    )
    item = _to_index_item(record)
    assert item["action"] == "delete"


def test_format_rebuild_context_marks_delete_removal() -> None:
    text = format_rebuild_context(
        {
            "records": [
                {
                    "record_id": "ev_1",
                    "summary": "删除空气质量",
                    "target": "body",
                    "section": "Instructions",
                    "action": "delete",
                    "score": 0.6,
                    "content": "- 用户点名空气质量时必须输出 AQI",
                }
            ]
        }
    )
    assert "action=delete" in text
    assert "REMOVE the following from SKILL.md" in text


def test_build_rebuild_llm_direct_prompt_includes_delete_rules() -> None:
    system, user = build_rebuild_llm_direct_prompt(
        subject={"kind": "skill", "name": "tianqi"},
        current_skill_md="---\nname: tianqi\n---\n# tianqi\n",
        rebuild_context={
            "skill_md_path": "/tmp/tianqi/SKILL.md",
            "records": [
                {
                    "record_id": "ev_1",
                    "action": "delete",
                    "summary": "删空气质量",
                    "target": "body",
                    "section": "Instructions",
                    "score": 0.6,
                    "content": "空气质量",
                }
            ],
        },
    )
    assert "action=delete" in system
    assert "MUST delete the corresponding content" in system
    assert "Experience action rules" in user
    assert "action=delete" in user
    assert "delete that content from the rebuilt file" in user
    assert "Current SKILL.md:" in user
