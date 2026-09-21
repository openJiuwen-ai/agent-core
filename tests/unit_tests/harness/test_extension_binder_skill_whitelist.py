# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""_bind_skill / _unbind visibility contract for SkillUseRail.

Binding a template skill must never derive an ``enabled_skills`` allow-list
from the skill's directory name: doing so flips the rail from "no filter"
to "only the bound skill", which hides every skill the agent already
exposes (e.g. pre-installed workspace skills) for the rest of the session.
Only a manifest that explicitly declares ``enabled_skills`` narrows the
allow-list. Leaf bookkeeping (``bound_leaf_dirs``) lets sibling leaves
under one root share the mount, and ``_unbind`` keeps the root until its
last bound leaf is removed.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from openjiuwen.harness.extension_binder import _bind_skill, _unbind
from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail
from openjiuwen.harness.resources.extension_resolver import ResolvedSkill


def _write_skill(root: Path, name: str, description: str = "desc") -> Path:
    """Create a minimal leaf skill directory with SKILL.md."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return skill_dir


class _FakeAgent:
    """Minimal DeepAgent stand-in for the binder's rail/config access."""

    def __init__(self, rail: SkillUseRail):
        self._rail = rail
        self.deep_config = SimpleNamespace(skills=None)

    def find_rails_by_type(self, types):
        return [self._rail] if isinstance(self._rail, tuple(types)) else []

    def is_registered_rail(self, rail) -> bool:
        return rail is self._rail


def _skill_names(rail: SkillUseRail) -> set[str]:
    return {skill.name for skill in rail.skills}


def _skill_by_name(rail: SkillUseRail, name: str):
    return next(skill for skill in rail.skills if skill.name == name)


@pytest_asyncio.fixture()
async def preset_rail(tmp_path: Path):
    """A rail over a preset workspace root with one pre-installed skill."""
    preset_root = tmp_path / "workspace" / "skills"
    _write_skill(preset_root, "preset-skill", "pre-installed")
    rail = SkillUseRail(skills_dir=str(preset_root), skill_mode="all")
    await rail.reload_skills()
    return rail


@pytest.mark.asyncio
async def test_bind_leaf_keeps_existing_skills_visible(preset_rail, tmp_path: Path):
    """Core regression: binding a leaf must not whitelist away preset skills."""
    leaf = _write_skill(tmp_path / "pkg" / "skills", "expert-skill", "from package")
    agent = _FakeAgent(preset_rail)

    await _bind_skill(agent, ResolvedSkill(directory=str(leaf), mode="all"))

    assert preset_rail.enabled_skills == set()
    assert _skill_names(preset_rail) == {"preset-skill", "expert-skill"}


@pytest.mark.asyncio
async def test_explicit_enabled_skills_still_whitelists(preset_rail, tmp_path: Path):
    """A manifest that explicitly declares enabled_skills keeps the contract."""
    leaf = _write_skill(tmp_path / "pkg" / "skills", "expert-skill", "from package")
    agent = _FakeAgent(preset_rail)

    await _bind_skill(
        agent,
        ResolvedSkill(directory=str(leaf), mode="all", enabled_skills=["expert-skill"]),
    )

    assert preset_rail.enabled_skills == {"expert-skill"}
    assert _skill_names(preset_rail) == {"expert-skill"}


@pytest.mark.asyncio
async def test_sibling_leaf_merge_no_raise(preset_rail, tmp_path: Path):
    """Two leaves under one package root share the mount instead of raising."""
    pkg_root = tmp_path / "pkg" / "skills"
    leaf_a = _write_skill(pkg_root, "skill-a", "a")
    leaf_b = _write_skill(pkg_root, "skill-b", "b")
    agent = _FakeAgent(preset_rail)

    await _bind_skill(agent, ResolvedSkill(directory=str(leaf_a), mode="all"))
    await _bind_skill(agent, ResolvedSkill(directory=str(leaf_b), mode="all"))

    assert _skill_names(preset_rail) == {"preset-skill", "skill-a", "skill-b"}
    assert list(preset_rail.skills_dir).count(str(pkg_root.resolve())) == 1


@pytest.mark.asyncio
async def test_rebind_same_leaf_raises(preset_rail, tmp_path: Path):
    """Binding the exact same leaf twice is still a hard conflict."""
    leaf = _write_skill(tmp_path / "pkg" / "skills", "expert-skill", "from package")
    agent = _FakeAgent(preset_rail)

    await _bind_skill(agent, ResolvedSkill(directory=str(leaf), mode="all"))
    with pytest.raises(ValueError, match="Skill already bound"):
        await _bind_skill(agent, ResolvedSkill(directory=str(leaf), mode="all"))


@pytest.mark.asyncio
async def test_unbind_siblings_then_last_leaf_removes_root(preset_rail, tmp_path: Path):
    """Root survives until its last bound leaf is unbound; preset skills stay."""
    pkg_root = tmp_path / "pkg" / "skills"
    leaf_a = _write_skill(pkg_root, "skill-a", "a")
    leaf_b = _write_skill(pkg_root, "skill-b", "b")
    agent = _FakeAgent(preset_rail)
    ref_a = await _bind_skill(agent, ResolvedSkill(directory=str(leaf_a), mode="all"))
    ref_b = await _bind_skill(agent, ResolvedSkill(directory=str(leaf_b), mode="all"))

    await _unbind(agent, ref_a)
    # Sibling still bound: root stays mounted.
    assert str(pkg_root.resolve()) in list(preset_rail.skills_dir)
    assert "skill-b" in _skill_names(preset_rail)
    assert "preset-skill" in _skill_names(preset_rail)

    await _unbind(agent, ref_b)
    # Last leaf gone: root removed, preset skills untouched.
    assert str(pkg_root.resolve()) not in list(preset_rail.skills_dir)
    assert _skill_names(preset_rail) == {"preset-skill"}
    assert preset_rail.enabled_skills == set()


@pytest.mark.asyncio
async def test_same_name_shadows_preset_then_restores(preset_rail, tmp_path: Path):
    """A package skill with a preset's name shadows it; unbinding restores."""
    leaf = _write_skill(tmp_path / "pkg" / "skills", "preset-skill", "package override")
    agent = _FakeAgent(preset_rail)

    ref = await _bind_skill(agent, ResolvedSkill(directory=str(leaf), mode="all"))

    shadowing = _skill_by_name(preset_rail, "preset-skill")
    assert Path(shadowing.directory).resolve() == leaf.resolve()

    await _unbind(agent, ref)

    restored = _skill_by_name(preset_rail, "preset-skill")
    assert Path(restored.directory).resolve() != leaf.resolve()
    assert _skill_names(preset_rail) == {"preset-skill"}
