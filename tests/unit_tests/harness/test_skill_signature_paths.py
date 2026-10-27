# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Skill path shortcuts must preserve canonical identity and live refresh."""

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.harness.prompts.builder import SystemPromptBuilder
from openjiuwen.harness.rails.skills import skill_use_rail as skill_module
from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail


@pytest.mark.parametrize(
    "platform,mode,attributes,expected",
    [
        ("nt", stat.S_IFDIR, 0, True),
        ("nt", stat.S_IFDIR, 0x400, False),  # FILE_ATTRIBUTE_REPARSE_POINT
        ("nt", stat.S_IFDIR, None, False),
        ("posix", stat.S_IFDIR, None, True),
        ("posix", stat.S_IFLNK, None, False),
        ("posix", stat.S_IFREG, None, False),
        ("unknown", stat.S_IFDIR, None, False),
    ],
)
def test_plain_directory_requires_reliable_metadata(monkeypatch, platform, mode, attributes, expected):
    info = SimpleNamespace(st_mode=mode)
    if attributes is not None:
        info.st_file_attributes = attributes
    path = SimpleNamespace(lstat=lambda: info)
    monkeypatch.setattr(skill_module, "os", SimpleNamespace(name=platform))
    assert SkillUseRail._is_plain_skill_directory(path) is expected


def test_signature_falls_back_when_directory_attributes_are_unavailable(tmp_path, monkeypatch):
    skill = tmp_path / "example"
    skill.mkdir()
    md = skill / "SKILL.md"
    md.write_text("skill", encoding="utf-8")
    expected = ((str(skill.resolve()), md.stat().st_mtime),)
    original_lstat = Path.lstat
    original_resolve = Path.resolve
    resolved = []

    def unavailable(path):
        if path == skill:
            raise PermissionError("optional attributes probe failed")
        return original_lstat(path)

    def resolve(path, *args, **kwargs):
        resolved.append(path)
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", unavailable)
    monkeypatch.setattr(Path, "resolve", resolve)
    rail = SkillUseRail(skills_dir=str(tmp_path))
    assert rail._build_skills_snapshot_signature() == expected
    assert skill in resolved


def _link_directory(link: Path, target: Path) -> None:
    if os.name == "nt":
        import _winapi

        # A real junction exercises Windows reparse points without admin privileges.
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def _remove_directory_link(link: Path) -> None:
    # Remove only the link created by this test, never its target directory.
    if os.name == "nt":
        assert link.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        link.rmdir()
    else:
        assert link.is_symlink()
        link.unlink()


@pytest.mark.asyncio
@pytest.mark.parametrize("link_location", ["root", "child"])
async def test_link_target_switch_with_identical_mtime_refreshes_before_model(tmp_path, monkeypatch, link_location):
    targets = []
    for name in ("first", "second"):
        target = tmp_path / name
        skill = target / "example"
        skill.mkdir(parents=True)
        md = skill / "SKILL.md"
        md.write_text(
            f"---\nname: example\ndescription: {name} description\n---\n# Example\n",
            encoding="utf-8",
        )
        os.utime(md, (1700000000, 1700000000))
        targets.append(target)

    root = tmp_path / "skills"
    if link_location == "root":
        link = root
        destinations = targets
    else:
        root.mkdir()
        link = root / "example"
        destinations = [target / "example" for target in targets]
    _link_directory(link, destinations[0])

    rail = SkillUseRail(skills_dir=str(root), skill_mode="all", include_tools=False)
    rail.system_prompt_builder = SystemPromptBuilder()
    ctx = AgentCallbackContext(agent=None, inputs=ModelCallInputs(tools=[]), session=None)
    signatures = []
    original_signature = rail._build_skills_snapshot_signature

    def capture_signature():
        result = original_signature()
        signatures.append(result)
        return result

    monkeypatch.setattr(rail, "_build_skills_snapshot_signature", capture_signature)
    try:
        await rail.before_invoke(ctx)
        old_signature = rail._skills_snapshot_signature
        assert rail.skills[0].description == "first description"

        _remove_directory_link(link)
        _link_directory(link, destinations[1])
        await rail.before_model_call(ctx)

        assert rail.skills[0].description == "second description"
        expected_key = str((targets[1] / "example").resolve())
        assert rail._skills_snapshot_signature == ((expected_key, 1700000000.0),)
        assert rail._skills_snapshot_signature != old_signature
        assert "second description" in rail.system_prompt_builder.build()
        # Initial baseline, before-model recheck, then a refreshed baseline.
        assert len(signatures) == 3
        assert signatures[0] == old_signature
        assert signatures[1] == signatures[2] == rail._skills_snapshot_signature
        assert all((target / "example" / "SKILL.md").is_file() for target in targets)
    finally:
        if link.exists():
            _remove_directory_link(link)


def test_plain_directory_signature_matches_resolved_walk_without_child_resolve(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    for name in ("z-example", "a-example"):
        skill = root / name
        skill.mkdir()
        (skill / "SKILL.md").write_text("skill", encoding="utf-8")
    (root / "empty").mkdir()
    (root / "file").write_text("not a directory", encoding="utf-8")
    file_root = tmp_path / "file-root"
    file_root.write_text("not a root directory", encoding="utf-8")
    rail = SkillUseRail(skills_dir=[str(root), str(tmp_path / "missing"), str(file_root)])
    expected = tuple(
        (str(item.resolve()), (item / "SKILL.md").stat().st_mtime)
        for item in sorted(root.iterdir(), key=lambda path: path.name)
        if item.is_dir() and (item / "SKILL.md").exists()
    )
    original_resolve = Path.resolve
    resolved = []

    def resolve(path, *args, **kwargs):
        resolved.append(path)
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert rail._build_skills_snapshot_signature() == expected
    assert root in resolved
    assert root / "a-example" not in resolved
    assert root / "z-example" not in resolved


@pytest.mark.parametrize("operation", ["resolve", "mtime_stat"])
def test_signature_preserves_filesystem_errors(tmp_path, monkeypatch, operation):
    skill = tmp_path / "example"
    skill.mkdir()
    skill_md = skill / "SKILL.md"
    skill_md.write_text("skill", encoding="utf-8")
    rail = SkillUseRail(skills_dir=str(tmp_path))
    failure = OSError("signature probe failure")
    method = "resolve" if operation == "resolve" else "stat"
    original = getattr(Path, method)
    file_stats = 0

    def failing(path, *args, **kwargs):
        nonlocal file_stats
        if operation == "resolve" and path == skill:
            raise failure
        if operation == "mtime_stat" and path == skill_md:
            file_stats += 1
            if file_stats == 2:  # exists() succeeds; the explicit stat() fails.
                raise failure
        return original(path, *args, **kwargs)

    if operation == "resolve":
        original_lstat = Path.lstat

        def unavailable_attributes(path):
            if path == skill:
                raise OSError("directory attributes unavailable")
            return original_lstat(path)

        monkeypatch.setattr(Path, "lstat", unavailable_attributes)
    monkeypatch.setattr(Path, method, failing)
    with pytest.raises(OSError) as caught:
        rail._build_skills_snapshot_signature()
    assert caught.value is failure
