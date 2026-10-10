# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Builtin skills in capabilities.json must resolve to selfEvolution=off."""

from __future__ import annotations

import json
from pathlib import Path

from openjiuwen.agent_evolving.skill_self_evolution import (
    get_skill_self_evolution_mode,
    is_capabilities_builtin_skill,
    load_capabilities_builtin_skill_names,
    load_skill_self_evolution_map,
    resolve_skill_evolution_action,
)


def _write_capabilities(path: Path, entries: list[dict]) -> None:
    path.write_text(
        json.dumps({"capabilities": entries}),
        encoding="utf-8",
    )


def test_builtin_source_forced_off_even_when_self_evolution_auto(tmp_path: Path):
    caps = tmp_path / "capabilities.json"
    _write_capabilities(
        caps,
        [
            {
                "type": "skill",
                "id": "pptx-craft",
                "source": "builtin",
                "selfEvolution": "auto",
            },
            {"type": "skill", "id": "custom", "selfEvolution": "suggest"},
        ],
    )
    mapping = load_skill_self_evolution_map(caps)
    assert mapping["pptx-craft"] == "off"
    assert mapping["custom"] == "suggest"
    assert get_skill_self_evolution_mode("pptx-craft", capabilities_path=caps) == "off"
    assert (
        resolve_skill_evolution_action(
            "pptx-craft",
            default_auto_save=True,
            capabilities_path=caps,
        )
        == "off"
    )


def test_unlisted_external_still_uses_default_auto_save(tmp_path: Path):
    caps = tmp_path / "capabilities.json"
    _write_capabilities(caps, [{"type": "skill", "id": "custom", "selfEvolution": "off"}])
    assert (
        resolve_skill_evolution_action(
            "other-skill",
            default_auto_save=True,
            capabilities_path=caps,
        )
        == "auto"
    )
    assert (
        resolve_skill_evolution_action(
            "other-skill",
            default_auto_save=False,
            capabilities_path=caps,
        )
        == "suggest"
    )


def test_is_capabilities_builtin_skill(tmp_path: Path):
    caps = tmp_path / "capabilities.json"
    _write_capabilities(
        caps,
        [
            {"type": "skill", "id": "pptx-craft", "source": "builtin"},
            {"type": "skill", "id": "custom", "selfEvolution": "auto"},
        ],
    )
    assert load_capabilities_builtin_skill_names(caps) == {"pptx-craft"}
    assert is_capabilities_builtin_skill("pptx-craft", capabilities_path=caps)
    assert is_capabilities_builtin_skill("PPTX-CRAFT", capabilities_path=caps)
    assert not is_capabilities_builtin_skill("custom", capabilities_path=caps)
