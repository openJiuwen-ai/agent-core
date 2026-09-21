# coding: utf-8

"""Tests for openjiuwen.core.common.utils.windows_junction."""

from __future__ import annotations

import os
import shutil
import sys

import pytest

from openjiuwen.core.common.utils import windows_junction
from openjiuwen.core.common.utils.windows_junction import create_windows_junction


@pytest.mark.level0
def test_mklink_success_skips_reparse_fallback(monkeypatch, tmp_path):
    """mklink 成功时不触发 reparse 兜底。"""
    calls = []
    monkeypatch.setattr(windows_junction, "_create_junction_via_mklink", lambda t, l: None)
    monkeypatch.setattr(
        windows_junction,
        "_create_junction_via_reparse",
        lambda t, l: calls.append((t, l)),
    )

    create_windows_junction(str(tmp_path / "target"), str(tmp_path / "link"))

    assert calls == []


@pytest.mark.level0
def test_mklink_failure_falls_back_to_reparse(monkeypatch, tmp_path):
    """mklink 失败（如 248+ 长路径）时走 reparse 长路径兜底。"""
    monkeypatch.setattr(
        windows_junction,
        "_create_junction_via_mklink",
        lambda t, l: (_ for _ in ()).throw(OSError(206, "文件名或扩展名太长")),
    )
    calls = []
    monkeypatch.setattr(
        windows_junction,
        "_create_junction_via_reparse",
        lambda t, l: calls.append((t, l)),
    )

    target, link = str(tmp_path / "target"), str(tmp_path / "link")
    create_windows_junction(target, link)

    assert calls == [(target, link)]


@pytest.mark.level0
def test_both_paths_failing_raises_with_both_reasons(monkeypatch, tmp_path):
    """mklink 与 reparse 都失败时抛出合并了两者原因的 OSError。"""
    monkeypatch.setattr(
        windows_junction,
        "_create_junction_via_mklink",
        lambda t, l: (_ for _ in ()).throw(OSError("mklink boom")),
    )
    monkeypatch.setattr(
        windows_junction,
        "_create_junction_via_reparse",
        lambda t, l: (_ for _ in ()).throw(OSError("reparse boom")),
    )

    with pytest.raises(OSError, match="mklink boom") as exc_info:
        create_windows_junction(str(tmp_path / "target"), str(tmp_path / "link"))
    assert "reparse boom" in str(exc_info.value)


@pytest.mark.skipif(sys.platform != "win32", reason="junction semantics are Windows-only")
@pytest.mark.level0
def test_reparse_junction_long_path_roundtrip(tmp_path):
    """真机集成：链接路径超过 248（mklink 目录创建上限）时 reparse 兜底建成，
    且能经 \\\\?\\ 前缀跨链接读写、rmdir 只删链接不删目标。

    覆盖的事故现场：`.agent_teams\\\\<50字符团队名>\\\\workspaces\\\\<成员>_workspace
    \\\\.team\\\\<50字符团队名>` 链接路径 248-249 字符，mklink 必败。
    """
    target = tmp_path / "team-workspace"
    target.mkdir()
    (target / "hello.txt").write_text("shared-content", encoding="utf-8")

    # 把链接路径垫到 248+：tmp_path 下叠深目录（脚手架全程 \\\\?\\ 前缀）
    deep = tmp_path
    while len(str(deep)) < 200:
        deep = deep / ("d" * 40)
    os.makedirs("\\\\?\\" + str(deep))
    link = deep / ("link-" + "x" * 40)
    assert len(str(link)) > 248

    # mklink 在这个深度必败，驱动真实兜底链
    create_windows_junction(str(target), str(link))

    through = "\\\\?\\" + str(link / "hello.txt")
    with open(through, encoding="utf-8") as f:
        assert f.read() == "shared-content"

    os.rmdir("\\\\?\\" + str(link))
    assert (target / "hello.txt").exists()

    shutil.rmtree("\\\\?\\" + str(tmp_path), ignore_errors=True)
