# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from pathlib import Path

from openjiuwen.agent_evolving.skill_self_evolution import (
    get_skill_self_evolution_mode,
    load_skill_self_evolution_map,
    normalize_skill_self_evolution,
    resolve_capabilities_config_path,
    resolve_skill_evolution_action,
)


def _write_capabilities(root: Path, capabilities: list[dict]) -> Path:
    office = root / ".office-claw"
    office.mkdir(parents=True, exist_ok=True)
    path = office / "capabilities.json"
    path.write_text(
        json.dumps({"version": 1, "capabilities": capabilities}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def test_normalize_unknown_falls_back_to_off():
    assert normalize_skill_self_evolution("auto") == "auto"
    assert normalize_skill_self_evolution("suggest") == "suggest"
    assert normalize_skill_self_evolution("off") == "off"
    assert normalize_skill_self_evolution("invalid") == "off"
    assert normalize_skill_self_evolution(None) == "off"


def test_resolve_capabilities_from_skills_dir(tmp_path: Path):
    path = _write_capabilities(tmp_path, [])
    skills_dir = tmp_path / ".office-claw" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    assert resolve_capabilities_config_path(skills_dir) == path


def test_resolve_capabilities_from_env(tmp_path: Path, monkeypatch):
    path = _write_capabilities(tmp_path, [])
    monkeypatch.setenv("OFFICE_CLAW_ROOT", str(tmp_path))
    assert resolve_capabilities_config_path() == path


def test_load_map_skips_builtin_and_missing_field(tmp_path: Path):
    path = _write_capabilities(
        tmp_path,
        [
            {"id": "weather", "type": "skill", "source": "external", "selfEvolution": "auto"},
            {"id": "travel-planner", "type": "skill", "source": "external", "selfEvolution": "off"},
            {"id": "daily-briefing", "type": "skill", "source": "builtin", "selfEvolution": "auto"},
            {"id": "no-field", "type": "skill", "source": "external"},
            {"id": "mcp-x", "type": "mcp", "source": "external"},
        ],
    )
    loaded = load_skill_self_evolution_map(path)
    assert loaded == {"weather": "auto", "travel-planner": "off"}
    assert get_skill_self_evolution_mode("weather", capabilities_path=path) == "auto"
    assert get_skill_self_evolution_mode("daily-briefing", capabilities_path=path) is None
    assert get_skill_self_evolution_mode("no-field", capabilities_path=path) is None


def test_resolve_action_off_suggest_auto_and_fallback(tmp_path: Path):
    path = _write_capabilities(
        tmp_path,
        [
            {"id": "weather", "type": "skill", "source": "external", "selfEvolution": "auto"},
            {"id": "pdf", "type": "skill", "source": "external", "selfEvolution": "suggest"},
            {"id": "disabled", "type": "skill", "source": "external", "selfEvolution": "off"},
        ],
    )
    assert (
        resolve_skill_evolution_action("weather", capabilities_path=path, default_auto_save=False)
        == "auto"
    )
    assert (
        resolve_skill_evolution_action("pdf", capabilities_path=path, default_auto_save=True)
        == "suggest"
    )
    assert (
        resolve_skill_evolution_action("disabled", capabilities_path=path, default_auto_save=True)
        == "off"
    )
    assert (
        resolve_skill_evolution_action("unlisted", capabilities_path=path, default_auto_save=True)
        == "auto"
    )
    assert (
        resolve_skill_evolution_action("unlisted", capabilities_path=path, default_auto_save=False)
        == "suggest"
    )
