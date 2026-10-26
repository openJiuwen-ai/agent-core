# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.agent_evolving.skill_train.utils import extract_json

_SKILL = """---
name: azure-bgp
---
# BGP
Prefer cycles use "quotes" and
a real newline.
"""


def test_marked_skill_is_installed_outside_json() -> None:
    reply = (
        '{"batch_size": 1, "patch": {"reasoning": "rewrite", '
        '"skill_candidates": [{"title": "bgp", "change_summary": ["paths"], "new_skill": ""}]}}\n'
        "<<<SKILL>>>\n"
        + _SKILL
    )
    parsed = extract_json(reply)
    assert parsed is not None
    skill = parsed["patch"]["skill_candidates"][0]["new_skill"]
    assert skill.startswith("---\nname: azure-bgp")
    assert '"quotes"' in skill


def test_raw_newlines_inside_new_skill_still_parse() -> None:
    reply = (
        '{\n'
        '  "patch": {\n'
        '    "reasoning": "rewrite",\n'
        '    "skill_candidates": [\n'
        '      {"title": "bgp", "change_summary": [], "new_skill": "'
        + _SKILL.replace('"', '\\"')
        + '"}\n'
        "    ]\n"
        "  }\n"
        "}"
    )
    parsed = extract_json(reply)
    assert parsed is not None
    assert "name: azure-bgp" in parsed["patch"]["skill_candidates"][0]["new_skill"]


def test_bare_skill_document_becomes_a_candidate() -> None:
    parsed = extract_json(_SKILL)
    assert parsed is not None
    assert parsed["patch"]["skill_candidates"][0]["new_skill"].startswith("---\nname: azure-bgp")
