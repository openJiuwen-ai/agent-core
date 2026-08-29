# coding: utf-8

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from openjiuwen.agent_teams.team_workspace.dir_links import (
    create_dir_link,
    is_dir_link,
    remove_dir_link,
)


@pytest.fixture
def make_pair(tmp_path: Path):
    """Create a real target dir + a link location."""

    def _make(name: str = "target") -> tuple[Path, Path]:
        target = tmp_path / name
        target.mkdir()
        (target / "keep.txt").write_text("keep", encoding="utf-8")
        link = tmp_path / f"{name}-link"
        return target, link

    return _make


@pytest.mark.level0
def test_create_dir_link_makes_symlink_or_junction(make_pair) -> None:
    """Real create_dir_link produces an is_dir_link (symlink or junction)."""
    target, link = make_pair()
    create_dir_link(target, link)
    assert is_dir_link(link)
    assert link.exists()


@pytest.mark.level0
def test_remove_dir_link_never_touches_target(make_pair) -> None:
    target, link = make_pair()
    create_dir_link(target, link)
    assert is_dir_link(link)
    assert remove_dir_link(link) is True
    assert not link.exists()
    assert (target / "keep.txt").exists(), "target contents must be preserved"


@pytest.mark.level0
def test_remove_dir_link_returns_false_for_plain_dir(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert remove_dir_link(plain) is False
    assert plain.is_dir()


@pytest.mark.level0
def test_remove_dir_link_returns_false_for_missing_path(tmp_path: Path) -> None:
    assert remove_dir_link(tmp_path / "missing") is False


@pytest.mark.level0
def test_create_dir_link_reraises_eacces(make_pair, monkeypatch) -> None:
    target, link = make_pair()

    def fake_symlink(*args, **kwargs):
        error = OSError("operation not permitted")
        error.errno = errno.EACCES
        raise error

    monkeypatch.setattr(os, "symlink", fake_symlink)
    with pytest.raises(OSError):
        create_dir_link(target, link)
    assert not link.exists()


@pytest.mark.level0
def test_windows_junction_fallback(make_pair, monkeypatch) -> None:
    """winerror 1314 → mklink /J fallback (simulated junction creation)."""
    target, link = make_pair()

    def fake_symlink(*args, **kwargs):
        error = OSError("privilege not held")
        error.winerror = 1314
        raise error

    monkeypatch.setattr(os, "symlink", fake_symlink)
    monkeypatch.setattr(os, "name", "nt")

    def fake_junction(tgt: Path, lnk: Path) -> None:
        lnk.mkdir()  # simulate mklink /J creating a reparse-point directory

    monkeypatch.setattr(
        "openjiuwen.agent_teams.team_workspace.dir_links._create_windows_junction",
        fake_junction,
    )
    create_dir_link(target, link)
    assert link.is_dir()
