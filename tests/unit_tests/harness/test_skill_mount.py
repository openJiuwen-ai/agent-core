# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Skill roots preserve discovery, host ownership, and filesystem-backed validation."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail
from openjiuwen.harness.skills.mount import SkillResourceValidator


def _write_skill(root: Path, name: str, description: str) -> Path:
    """Create a minimal skill directory for discovery tests."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(["---", f"description: {description}", "---", "", f"# {name}", ""]),
        encoding="utf-8",
    )
    return skill_dir


def test_mount_handles_preserve_existing_roots_and_release_only_owned_roots(tmp_path):
    host = tmp_path / "host-skills"
    candidate = tmp_path / "candidate"
    rail = SkillUseRail(str(host), include_tools=False, enabled_skills=["required"])
    host_mount = rail.mount_skill_root(host)
    candidate_mount = rail.mount_skill_root(candidate)
    borrowed_mount = rail.mount_skill_root(candidate)

    host_mount.unmount()
    borrowed_mount.unmount()
    assert {Path(root) for root in rail.skills_dir} == {host, candidate}
    candidate_mount.unmount()
    candidate_mount.unmount()
    assert [Path(root) for root in rail.skills_dir] == [host]
    assert rail.enabled_skills == {"required"}
    assert not rail.include_tools

    candidate_mount.ensure_mounted()
    candidate_mount.unmount()
    assert [Path(root) for root in rail.skills_dir] == [host]


@pytest.mark.asyncio
async def test_required_skill_revalidates_with_changed_backend_and_metadata(tmp_path):
    directory = tmp_path / "required"
    directory.mkdir()
    path = directory / "SKILL.md"
    text = "---\nname: required\ndescription: Required skill.\n---\n# Body\n"
    path.write_text(text, encoding="utf-8")
    reader = AsyncMock(return_value=SimpleNamespace(code=0, data=SimpleNamespace(content=text)))
    operation = SimpleNamespace(fs=lambda: SimpleNamespace(read_file=reader))
    validator = SkillResourceValidator("required", directory)
    await validator.validate(operation)
    await validator.validate(operation)
    assert reader.await_count == 1
    assert reader.await_args.args == (str(path),)
    assert reader.await_args.kwargs == {"mode": "text", "encoding": "utf-8", "head": 64, "only_read": True}

    denied_reader = AsyncMock(return_value=SimpleNamespace(code=1, message="access denied"))
    denied = SimpleNamespace(fs=lambda: SimpleNamespace(read_file=denied_reader))
    with pytest.raises(ValueError, match="access denied"):
        await validator.validate(denied)
    denied_reader.assert_awaited_once()

    changed = text.replace("name: required", "name: another-skill")
    path.write_text(changed, encoding="utf-8")
    reader.return_value.data.content = changed
    with pytest.raises(ValueError, match="invalid SKILL.md frontmatter"):
        await validator.validate(operation)


@pytest.mark.asyncio
async def test_required_skill_validation_propagates_cancellation(tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text("---\nname: required\ndescription: Required skill.\n---\n", encoding="utf-8")
    reader = AsyncMock(side_effect=asyncio.CancelledError)
    operation = SimpleNamespace(fs=lambda: SimpleNamespace(read_file=reader))
    validator = SkillResourceValidator("required", tmp_path)
    with pytest.raises(asyncio.CancelledError):
        await validator.validate(operation)
    reader.side_effect = None
    reader.return_value = SimpleNamespace(code=0, data=SimpleNamespace(content=path.read_text(encoding="utf-8")))
    await validator.validate(operation)
    assert reader.await_count == 2


def test_direct_skill_root_excludes_siblings_and_nested_skills(tmp_path):
    selected = _write_skill(tmp_path, "selected", "Selected skill")
    _write_skill(tmp_path, "sibling", "Sibling skill")
    _write_skill(selected, "private-nested", "Nested skill")
    found = SkillUseRail._discover_skill_dirs([selected])
    assert [directory for directory, _ in found] == [selected]
    assert found[0][1] == (selected / "SKILL.md").stat().st_mtime


def test_library_order_groups_hidden_and_skip_rules_are_preserved(tmp_path):
    first = _write_skill(tmp_path / "a-group", "first", "First skill")
    last = _write_skill(tmp_path, "z-last", "Last skill")
    _write_skill(last, "private-nested", "Nested skill")
    _write_skill(tmp_path / ".hidden", "hidden", "Hidden skill")
    _write_skill(tmp_path / "assets", "asset", "Asset skill")
    found = SkillUseRail._discover_skill_dirs([tmp_path])
    assert [directory for directory, _ in found] == [first, last]


def test_overlapping_direct_roots_and_symlink_cycles_are_deduplicated(tmp_path):
    selected = _write_skill(tmp_path / "group", "selected", "Selected skill")
    try:
        (tmp_path / "group" / "cycle").symlink_to(tmp_path, target_is_directory=True)
        alias = tmp_path / "alias"
        alias.symlink_to(selected, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    found = SkillUseRail._discover_skill_dirs([selected, selected, alias, tmp_path])
    assert [directory for directory, _ in found] == [selected]
