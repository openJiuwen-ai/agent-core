#!/usr/bin/env python
# coding: utf-8
"""Tests for file-upload helpers and built-in upload actions."""
# pylint: disable=protected-access
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch

from openjiuwen.deepagents.tools.browser_move.controllers.action import ActionController, register_builtin_actions
from openjiuwen.deepagents.tools.browser_move.controllers.action import _build_set_input_files_script, _list_dir_files


def _run(coro):
    return asyncio.run(coro)


def _make_ctl_with_builtins() -> ActionController:
    ctl = ActionController()
    register_builtin_actions(controller=ctl)
    return ctl


# ---------------------------------------------------------------------------
# _list_dir_files
# ---------------------------------------------------------------------------


def test_list_dir_files_returns_file_entries_with_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "doc.pdf").write_bytes(b"hello")
        (root / "img.png").write_bytes(b"xx")
        result = _list_dir_files(root)

    names = {e["name"] for e in result}
    assert names == {"doc.pdf", "img.png"}
    for entry in result:
        assert "path" in entry
        assert entry["size_bytes"] >= 0


def test_list_dir_files_handles_missing_directory() -> None:
    result = _list_dir_files(Path("/nonexistent/path/that/does/not/exist"))
    assert result == []


# ---------------------------------------------------------------------------
# _build_set_input_files_script
# ---------------------------------------------------------------------------


def test_build_set_input_files_script_embeds_selector_and_paths() -> None:
    script = _build_set_input_files_script("#upload", ["/data/a.pdf", "/data/b.csv"])
    assert "#upload" in script
    assert "/data/a.pdf" in script
    assert "/data/b.csv" in script


# ---------------------------------------------------------------------------
# list_upload_files builtin action
# ---------------------------------------------------------------------------


def test_list_upload_files_returns_error_when_env_not_set() -> None:
    ctl = _make_ctl_with_builtins()
    with patch("controllers.action.resolve_upload_root", return_value=None):
        result = _run(ctl.run_action("list_upload_files"))
    assert result["ok"] is False
    assert "BROWSER_UPLOAD_ROOT" in result["error"]
    assert result["files"] == []


def test_list_upload_files_returns_error_when_dir_missing() -> None:
    ctl = _make_ctl_with_builtins()
    with patch("controllers.action.resolve_upload_root", return_value=Path("/tmp/does_not_exist_xyz_99")):
        result = _run(ctl.run_action("list_upload_files"))
    assert result["ok"] is False
    assert result["files"] == []


def test_list_upload_files_returns_file_list_when_dir_exists() -> None:
    ctl = _make_ctl_with_builtins()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "report.xlsx").write_bytes(b"data")
        with patch("controllers.action.resolve_upload_root", return_value=root):
            result = _run(ctl.run_action("list_upload_files"))

    assert result["ok"] is True
    assert any(f["name"] == "report.xlsx" for f in result["files"])


# ---------------------------------------------------------------------------
# browser_set_input_files builtin action
# ---------------------------------------------------------------------------


def test_set_input_files_returns_error_when_paths_empty() -> None:
    ctl = _make_ctl_with_builtins()
    result = _run(ctl.run_action("browser_set_input_files", paths=[]))
    assert result["ok"] is False
    assert "paths" in result["error"].lower()


def test_set_input_files_uses_code_executor_when_bound() -> None:
    ctl = _make_ctl_with_builtins()

    async def fake_executor(js_code: str):
        return {"ok": True, "selector": 'input[type="file"]', "paths": ["/tmp/x.pdf"]}

    ctl._code_executor = fake_executor
    result = _run(ctl.run_action("browser_set_input_files", paths=["/tmp/x.pdf"]))
    assert result["ok"] is True


def test_set_input_files_defaults_selector_to_file_input() -> None:
    ctl = _make_ctl_with_builtins()
    captured: list[str] = []

    async def capture_executor(js_code: str):
        captured.append(js_code)
        return {"ok": True, "selector": 'input[type="file"]', "paths": ["/tmp/f.txt"]}

    ctl._code_executor = capture_executor
    _run(ctl.run_action("browser_set_input_files", paths=["/tmp/f.txt"]))
    assert captured and 'input[type="file"]' in captured[0]


def test_set_input_files_returns_error_when_no_executor_and_no_runner() -> None:
    ctl = _make_ctl_with_builtins()
    assert ctl._code_executor is None
    assert ctl._runtime_runner is None
    result = _run(ctl.run_action("browser_set_input_files", paths=["/tmp/f.txt"]))
    assert result["ok"] is False
    assert "runtime_not_bound" in result["error"] or "bind_runtime" in result["error"]
