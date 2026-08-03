# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for initial member harness bootstrap from Team Skill roles."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from openjiuwen.rsi.orchestrator.initial_harness_builder import (
    build_initial_harness_refs_from_team_skill,
)

pytestmark = pytest.mark.level0


def test_bootstrap_skips_team_leader_role(tmp_path: Path) -> None:
    """The Team coordinator is not a member harness optimization target."""
    team_skill_dir = tmp_path / "team_skill"
    roles_dir = team_skill_dir / "roles"
    roles_dir.mkdir(parents=True)
    (team_skill_dir / "SKILL.md").write_text(
        """---
kind: team-skill
name: webpage_team
roles:
  - id: team_leader
    kind: ai_agent
    purpose: Coordinate the task board and review final work.
    skills: []
    tools: []
  - id: visual-designer
    kind: ai_agent
    purpose: Design visual hierarchy and layout.
    skills: []
    tools: []
---

# Webpage Team
""",
        encoding="utf-8",
    )
    (roles_dir / "team_leader.md").write_text("# Leader role\n", encoding="utf-8")
    (roles_dir / "visual-designer.md").write_text("# Designer role\n", encoding="utf-8")

    generated = build_initial_harness_refs_from_team_skill(
        team_skill_dir,
        tmp_path / "out",
    )

    assert generated is not None
    refs = yaml.safe_load(Path(generated.refs_path).read_text(encoding="utf-8"))
    assert refs["harness_refs"].keys() == {"visual-designer"}
    assert [role["member_name"] for role in refs["roles"]] == ["visual-designer"]
    assert not (tmp_path / "out" / "harnesses" / "team_leader").exists()


def test_bootstrap_identity_does_not_deny_later_harness_skills(tmp_path: Path) -> None:
    """Initial identity should not conflict with optimizer-mounted skills."""
    team_skill_dir = tmp_path / "team_skill"
    roles_dir = team_skill_dir / "roles"
    roles_dir.mkdir(parents=True)
    (team_skill_dir / "SKILL.md").write_text(
        """---
kind: team-skill
name: webpage_team
roles:
  - id: visual-designer
    kind: ai_agent
    purpose: Design visual hierarchy and layout.
    skills: []
    tools: []
---

# Webpage Team
""",
        encoding="utf-8",
    )
    (roles_dir / "visual-designer.md").write_text("# Designer role\n", encoding="utf-8")

    generated = build_initial_harness_refs_from_team_skill(
        team_skill_dir,
        tmp_path / "out",
    )

    assert generated is not None
    identity_path = Path(generated.harness_refs["visual-designer"]) / "identity.md"
    identity = identity_path.read_text(encoding="utf-8")
    assert str(team_skill_dir.resolve()) not in identity
    assert "Source Team Skill: mounted team skill" in identity
    assert "Initial Team Skill skills: none declared" in identity
    assert "Additional harness skills may be mounted by the skill section." in identity
    assert "Declared skills: none declared" not in identity
    soul_path = Path(generated.harness_refs["visual-designer"]) / "soul.md"
    soul = soul_path.read_text(encoding="utf-8")
    assert "artifacts/<output_section>.md" in soul
    assert "longer than 4000 characters" in soul
    assert "append=true" in soul
