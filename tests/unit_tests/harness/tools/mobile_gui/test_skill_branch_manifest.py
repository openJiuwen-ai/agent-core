# coding: utf-8
"""Tests for mobile_gui skill_branch manifest helpers."""

from __future__ import annotations

from pathlib import Path

from openjiuwen.harness.tools.mobile_gui.skill_branch.manifest import (
    SkillImageEntry,
    build_skill_image_manifest,
    format_manifest_for_prompt,
)


def test_build_skill_image_manifest_resolves_local_images_only(tmp_path: Path):
    """Local ``![](alt)(path)`` entries resolve to files; remote URLs are skipped."""
    skill_dir = tmp_path / "github-com"
    images = skill_dir / "images"
    images.mkdir(parents=True)
    png = images / "github_landing_page.png"
    png.write_bytes(b"fake-png-bytes")

    markdown = (
        "# GitHub\n\n"
        "![Landing page](images/github_landing_page.png)\n\n"
        "![Remote](https://example.com/x.png)\n"
        "![](images/missing.png)\n"
    )
    entries = build_skill_image_manifest(markdown, str(skill_dir))

    assert len(entries) == 1
    entry = entries[0]
    assert isinstance(entry, SkillImageEntry)
    assert entry.image_id == "github_landing_page"
    assert entry.alt == "Landing page"
    assert entry.rel_path == "images/github_landing_page.png"
    assert entry.abs_path == str(png.resolve())
    assert Path(entry.abs_path).is_file()


def test_format_manifest_for_prompt_lists_entries_and_empty_case(tmp_path: Path):
    """Prompt manifest includes image ids; empty manifest explains no local images."""
    empty = format_manifest_for_prompt([])
    assert "no local reference images" in empty

    skill_dir = tmp_path / "skill"
    (skill_dir / "images").mkdir(parents=True)
    img = skill_dir / "images" / "step.png"
    img.write_bytes(b"x")
    entries = build_skill_image_manifest(
        "![Step one](images/step.png)",
        str(skill_dir),
    )
    text = format_manifest_for_prompt(entries)

    assert "step" in text
    assert "Step one" in text
    assert "images/step.png" in text


def test_build_skill_image_manifest_empty_markdown_returns_no_entries(tmp_path: Path):
    assert build_skill_image_manifest("", str(tmp_path)) == []


def test_build_skill_image_manifest_collects_multiple_local_images(tmp_path: Path):
    skill_dir = tmp_path / "skill"
    (skill_dir / "images").mkdir(parents=True)
    for name in ("one.png", "two.png"):
        (skill_dir / "images" / name).write_bytes(b"x")

    markdown = (
        "![One](images/one.png)\n"
        "![Two](images/two.png)\n"
    )
    entries = build_skill_image_manifest(markdown, str(skill_dir))
    assert len(entries) == 2
    ids = {e.image_id for e in entries}
    assert ids == {"one", "two"}


def test_build_skill_image_manifest_disambiguates_duplicate_stems(tmp_path: Path):
    skill_dir = tmp_path / "skill"
    d1 = skill_dir / "a"
    d2 = skill_dir / "b"
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)
    (d1 / "icon.png").write_bytes(b"1")
    (d2 / "icon.png").write_bytes(b"2")

    markdown = "![A](a/icon.png)\n![B](b/icon.png)\n"
    entries = build_skill_image_manifest(markdown, str(skill_dir))
    assert len(entries) == 2
    assert entries[0].image_id == "icon"
    assert entries[1].image_id.startswith("icon_")
