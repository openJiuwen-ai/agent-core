# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Portable skill bundle copying and collision behavior."""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from openjiuwen.harness_providers.skills import SkillSource, install_skills, normalize_skills


def bundle(root: Path, name: str = "example", text: str = "instructions") -> Path:
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Test skill\n---\n{text}\n")
    (root / "scripts").mkdir()
    (root / "scripts" / "run.sh").write_text("echo resource")
    (root / "scripts" / "run.sh").chmod(0o755)
    (root / ".hidden").write_bytes(b"\x00\x01resource")
    return root


@pytest.mark.parametrize("provider,relative", [("claudecode", ".claude/skills"), ("codex", ".agents/skills"), ("dsh", ".dsh/skills")])
def test_complete_bundle_and_conflict_policies(tmp_path, provider, relative):
    source = bundle(tmp_path / "source")
    project = tmp_path / "project"
    project.mkdir()
    target = project / relative / "example"
    args = {"provider": provider, "cwd": str(project)}
    assert install_skills((SkillSource(str(source)),), **args) == (target,)
    assert (target / ".hidden").read_bytes() == b"\x00\x01resource"
    assert (target / "scripts/run.sh").stat().st_mode & 0o111
    (source / "SKILL.md").write_text((source / "SKILL.md").read_text() + "updated")
    (target / "stale").write_text("old")
    assert install_skills((SkillSource(str(source)),), **args) == ()
    assert "updated" not in (target / "SKILL.md").read_text()
    install_skills((SkillSource(str(source)),), conflict="replace", **args)
    assert "updated" in (target / "SKILL.md").read_text()
    assert not (target / "stale").exists()
    assert not list(project.glob(".openjiuwen-skill-*"))


def test_library_selection_and_frontmatter_name_collision(tmp_path):
    library = tmp_path / "library"
    bundle(library / "different-folder", "selected")
    bundle(library / "other", "other")
    project = tmp_path / "project"
    project.mkdir()
    existing = bundle(project / ".agents/skills/legacy-folder", "selected", "keep")
    sources = normalize_skills([{"dir": str(library), "enabled_skills": ["selected"], "mode": "auto_list"}], "skip")
    assert install_skills(sources, provider="codex", cwd=str(project)) == ()
    install_skills(sources, provider="codex", cwd=str(project), conflict="replace")
    assert "keep" not in (existing / "SKILL.md").read_text()
    assert not (project / ".agents/skills/other").exists()
    assert not (project / ".agents/skills/selected").exists()


def test_copy_failure_keeps_existing_skill(tmp_path, monkeypatch):
    import shutil
    source = bundle(tmp_path / "source")
    existing = bundle(tmp_path / "project/.dsh/skills/example", text="original")
    def fail_copy(source, destination):
        raise OSError("disk full")
    monkeypatch.setattr(shutil, "copytree", fail_copy)
    with pytest.raises(OSError, match="disk full"):
        install_skills((SkillSource(str(source)),), provider="dsh", cwd=str(tmp_path / "project"), conflict="replace")
    assert "original" in (existing / "SKILL.md").read_text()


def test_symlink_escape_is_rejected_and_self_copy_is_noop(tmp_path):
    source = bundle(tmp_path / "source")
    (tmp_path / "outside").write_text("outside")
    (source / "escape").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="escapes"):
        install_skills((SkillSource(str(source)),), provider="dsh", cwd=str(tmp_path))
    (source / "escape").unlink()
    install_skills((SkillSource(str(source)),), provider="dsh", cwd=str(tmp_path))
    target = tmp_path / ".dsh/skills/example"
    assert install_skills((SkillSource(str(target)),), provider="dsh", cwd=str(tmp_path), conflict="replace") == ()
    assert (target / "SKILL.md").is_file()


def test_concurrent_skip_copies_one_complete_skill(tmp_path):
    source = bundle(tmp_path / "source")
    def copy():
        return install_skills((SkillSource(str(source)),), provider="codex", cwd=str(tmp_path))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: copy(), range(2)))
    assert sum(len(result) for result in results) == 1
    assert (tmp_path / ".agents/skills/example/scripts/run.sh").is_file()


def test_invalid_configuration_and_skill_name(tmp_path):
    with pytest.raises(ValueError, match="skill_conflict"):
        normalize_skills([], "merge")
    source = bundle(tmp_path / "source", "../escape")
    with pytest.raises(ValueError, match="name"):
        install_skills((SkillSource(str(source)),), provider="claudecode", cwd=str(tmp_path))


def test_failed_install_rename_restores_previous_directory(tmp_path, monkeypatch):
    source = bundle(tmp_path / "source")
    existing = bundle(tmp_path / "project/.agents/skills/example", text="original")
    rename = Path.rename
    def fail_new(path, target):
        if path.name == "new":
            raise OSError("rename failed")
        return rename(path, target)
    monkeypatch.setattr(Path, "rename", fail_new)
    with pytest.raises(OSError, match="rename failed"):
        install_skills((SkillSource(str(source)),), provider="codex", cwd=str(tmp_path / "project"), conflict="replace")
    assert "original" in (existing / "SKILL.md").read_text()
    assert not list((tmp_path / "project").glob(".openjiuwen-skill-*"))


def test_internal_symlinks_are_materialized(tmp_path):
    source = bundle(tmp_path / "source")
    (source / "reference").symlink_to(source / ".hidden")
    install_skills((SkillSource(str(source)),), provider="codex", cwd=str(tmp_path))
    target = tmp_path / ".agents/skills/example/reference"
    assert not target.is_symlink()
    assert target.read_bytes() == (source / ".hidden").read_bytes()


def test_replace_destination_symlink_copies_files_without_mutating_source(tmp_path):
    source = bundle(tmp_path / "source")
    scan = tmp_path / ".agents/skills"
    scan.mkdir(parents=True)
    destination = scan / "example"
    destination.symlink_to(source, target_is_directory=True)
    install_skills((SkillSource(str(source)),), provider="codex", cwd=str(tmp_path), conflict="replace")
    assert destination.is_dir() and not destination.is_symlink()
    assert (destination / "scripts/run.sh").is_file()
    assert (source / "SKILL.md").is_file()
