# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access
"""Safety tests for skill package extraction (``_safe_extract_tar``)."""

from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

import pytest

from openjiuwen.agent_evolving.checkpointing.skill_package import (
    _safe_extract_tar,
    unpack_skill_package,
)


def _make_tar(members: list[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for info, payload in members:
            if payload is None:
                archive.addfile(info)
            else:
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _file(name: str, data: bytes = b"data") -> tuple[tarfile.TarInfo, bytes]:
    return tarfile.TarInfo(name=name), data


def _symlink(name: str, target: str) -> tuple[tarfile.TarInfo, None]:
    info = tarfile.TarInfo(name=name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    return info, None


def _hardlink(name: str, target: str) -> tuple[tarfile.TarInfo, None]:
    info = tarfile.TarInfo(name=name)
    info.type = tarfile.LNKTYPE
    info.linkname = target
    return info, None


def _extract(data: bytes, dest: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        _safe_extract_tar(archive, dest)


@pytest.fixture
def force_fallback(monkeypatch):
    """Force ``unpack_skill_package`` onto the pre-3.12 fallback path."""
    monkeypatch.delattr(tarfile, "data_filter", raising=False)


class TestSafeExtractTarRejects:
    @staticmethod
    def test_rejects_parent_traversal_member(tmp_path: Path):
        dest = tmp_path / "out"
        dest.mkdir()
        data = _make_tar([_file("../evil.txt", b"pwned")])
        with pytest.raises(ValueError, match="Unsafe tar member path"):
            _extract(data, dest)
        assert not (tmp_path / "evil.txt").exists()

    @staticmethod
    def test_rejects_absolute_member(tmp_path: Path):
        dest = tmp_path / "out"
        dest.mkdir()
        data = _make_tar([_file("/tmp/evil.txt", b"pwned")])
        with pytest.raises(ValueError, match="Unsafe tar member path"):
            _extract(data, dest)

    @staticmethod
    def test_rejects_dotdot_in_middle(tmp_path: Path):
        dest = tmp_path / "out"
        dest.mkdir()
        data = _make_tar([_file("a/../../evil.txt", b"pwned")])
        with pytest.raises(ValueError, match="Unsafe tar member path"):
            _extract(data, dest)

    @staticmethod
    def test_rejects_escaping_symlink(tmp_path: Path):
        dest = tmp_path / "out"
        dest.mkdir()
        data = _make_tar([_symlink("link", "../../evil.txt")])
        with pytest.raises(ValueError, match="Unsafe tar link target"):
            _extract(data, dest)

    @staticmethod
    def test_rejects_absolute_symlink_target(tmp_path: Path):
        dest = tmp_path / "out"
        dest.mkdir()
        data = _make_tar([_symlink("link", "/etc/passwd")])
        with pytest.raises(ValueError, match="Unsafe tar link target"):
            _extract(data, dest)

    @staticmethod
    def test_rejects_escaping_hardlink(tmp_path: Path):
        dest = tmp_path / "out"
        dest.mkdir()
        data = _make_tar([_hardlink("link", "../../etc/passwd")])
        with pytest.raises(ValueError, match="Unsafe tar link target"):
            _extract(data, dest)


class TestSafeExtractTarAllows:
    @staticmethod
    def test_extracts_plain_files(tmp_path: Path):
        dest = tmp_path / "out"
        dest.mkdir()
        data = _make_tar([_file("SKILL.md", b"# Skill\n"), _file("sub/a.txt", b"a")])
        _extract(data, dest)
        assert (dest / "SKILL.md").read_bytes() == b"# Skill\n"
        assert (dest / "sub" / "a.txt").read_bytes() == b"a"

    @staticmethod
    def test_symlink_target_is_relative_to_link_directory(tmp_path: Path):
        dest = tmp_path / "out"
        dest.mkdir()
        data = _make_tar([_symlink("sub/link", "../SKILL.md"), _file("SKILL.md", b"# Skill\n")])
        try:
            _extract(data, dest)
        except OSError:
            pytest.skip("symlink creation not permitted on this platform")
        link = dest / "sub" / "link"
        if not link.is_symlink():
            pytest.skip("symlink creation not supported on this platform")
        assert os.readlink(link) == "../SKILL.md"


class TestUnpackSkillPackageFallback:
    @staticmethod
    def test_fallback_rejects_traversal(tmp_path: Path, force_fallback):
        dest = tmp_path / "out"
        data = _make_tar([_file("../evil.txt", b"pwned")])
        with pytest.raises(ValueError, match="Unsafe tar member path"):
            unpack_skill_package(data, dest)
        assert not (tmp_path / "evil.txt").exists()

    @staticmethod
    def test_fallback_extracts_valid_package(tmp_path: Path, force_fallback):
        dest = tmp_path / "out"
        data = _make_tar([_file("SKILL.md", b"# Skill\n")])
        unpack_skill_package(data, dest)
        assert (dest / "SKILL.md").read_bytes() == b"# Skill\n"
