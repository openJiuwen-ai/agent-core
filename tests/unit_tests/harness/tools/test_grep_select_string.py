# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# Tests for GrepTool's PowerShell Select-String fallback (Windows, no rg).

import os
import shutil
import tempfile
from unittest import mock

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.harness.tools import GrepTool, WriteFileTool


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture():
    await Runner.start()
    card = SysOperationCard(
        id="test_grep_ss_op",
        mode=OperationMode.LOCAL,
        # null allowlist -> bypass allowlist check so PS pipeline commands are allowed
        work_config=LocalWorkConfig(shell_allowlist=None),
    )
    Runner.resource_mgr.add_sys_operation(card)
    op = Runner.resource_mgr.get_sys_operation("test_grep_ss_op")
    yield op
    Runner.resource_mgr.remove_sys_operation(sys_operation_id="test_grep_ss_op")
    await Runner.stop()


# ── command builder unit tests ───────────────────────────────────────────────
# These test the generated PS command string without executing anything.

def _build(tool: GrepTool, path: str, **kw) -> str:
    defaults = dict(
        pattern="needle",
        path=path,
        glob=None,
        output_mode="content",
        context_before=None,
        context_after=None,
        context_c=None,
        context=None,
        case_insensitive=False,
    )
    defaults.update(kw)
    return tool._build_select_string_command(**defaults)


@pytest.mark.asyncio
async def test_cmd_content_mode_formats_filepath_linenum(sys_op, tmp_path):
    tool = GrepTool(sys_op)
    cmd = _build(tool, str(tmp_path))
    assert "ForEach-Object" in cmd
    assert "$_.LineNumber" in cmd
    assert "$_.Path" in cmd


@pytest.mark.asyncio
async def test_cmd_files_with_matches_mode(sys_op, tmp_path):
    tool = GrepTool(sys_op)
    cmd = _build(tool, str(tmp_path), output_mode="files_with_matches")
    assert "Select-Object -ExpandProperty Path -Unique" in cmd
    assert "Group-Object" not in cmd


@pytest.mark.asyncio
async def test_cmd_count_mode(sys_op, tmp_path):
    tool = GrepTool(sys_op)
    cmd = _build(tool, str(tmp_path), output_mode="count")
    assert "Group-Object Path" in cmd
    assert "ForEach-Object" in cmd


@pytest.mark.asyncio
async def test_cmd_case_sensitive_flag_when_not_ignore_case(sys_op, tmp_path):
    tool = GrepTool(sys_op)
    cmd = _build(tool, str(tmp_path), case_insensitive=False)
    assert "-CaseSensitive" in cmd


@pytest.mark.asyncio
async def test_cmd_no_case_sensitive_flag_when_ignore_case(sys_op, tmp_path):
    tool = GrepTool(sys_op)
    cmd = _build(tool, str(tmp_path), case_insensitive=True)
    assert "-CaseSensitive" not in cmd


@pytest.mark.asyncio
async def test_cmd_glob_filter_uses_like(sys_op, tmp_path):
    tool = GrepTool(sys_op)
    cmd = _build(tool, str(tmp_path), glob="*.py")
    assert "-like" in cmd
    assert "*.py" in cmd


@pytest.mark.asyncio
async def test_cmd_brace_glob_expanded(sys_op, tmp_path):
    tool = GrepTool(sys_op)
    cmd = _build(tool, str(tmp_path), glob="*.{ts,tsx}")
    assert "*.ts" in cmd
    assert "*.tsx" in cmd


@pytest.mark.asyncio
async def test_cmd_vcs_directories_excluded(sys_op, tmp_path):
    tool = GrepTool(sys_op)
    cmd = _build(tool, str(tmp_path))
    assert "-notmatch" in cmd
    assert r"\.git" in cmd
    assert r"\.svn" in cmd


@pytest.mark.asyncio
async def test_cmd_context_lines_in_content_mode(sys_op, tmp_path):
    tool = GrepTool(sys_op)
    cmd = _build(tool, str(tmp_path), output_mode="content", context_before=2, context_after=3)
    assert "-Context 2,3" in cmd


@pytest.mark.asyncio
async def test_cmd_context_c_applies_symmetrically(sys_op, tmp_path):
    tool = GrepTool(sys_op)
    cmd = _build(tool, str(tmp_path), output_mode="content", context_c=2)
    assert "-Context 2,2" in cmd


@pytest.mark.asyncio
async def test_cmd_context_lines_not_in_non_content_mode(sys_op, tmp_path):
    tool = GrepTool(sys_op)
    # context args are stripped by invoke() before reaching here for non-content modes,
    # but the builder itself also guards: only adds -Context for output_mode == "content"
    cmd = _build(tool, str(tmp_path), output_mode="files_with_matches", context_before=2)
    assert "-Context" not in cmd


@pytest.mark.asyncio
async def test_cmd_single_file_uses_get_item_not_recurse(sys_op, tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    tool = GrepTool(sys_op)
    cmd = _build(tool, str(f))
    assert "Get-Item -LiteralPath" in cmd
    assert "Get-ChildItem" not in cmd


@pytest.mark.asyncio
async def test_cmd_directory_uses_get_childitem_recurse(sys_op, tmp_path):
    tool = GrepTool(sys_op)
    cmd = _build(tool, str(tmp_path))
    assert "Get-ChildItem" in cmd
    assert "-Recurse" in cmd


@pytest.mark.asyncio
async def test_cmd_error_action_silently_continue(sys_op, tmp_path):
    tool = GrepTool(sys_op)
    cmd = _build(tool, str(tmp_path))
    assert cmd.startswith("$ErrorActionPreference='SilentlyContinue'")


# ── routing / error tests (mock shutil.which → no rg on NT) ─────────────────

@pytest.mark.asyncio
async def test_type_filter_returns_error_without_rg(sys_op, tmp_path):
    tool = GrepTool(sys_op)
    with mock.patch("shutil.which", return_value=None), \
         mock.patch.object(os, "name", "nt"):
        result = await tool.invoke({"pattern": "x", "path": str(tmp_path), "type": "py"})
    assert result.success is False
    assert "rg" in result.error.lower() or "type" in result.error.lower()


@pytest.mark.asyncio
async def test_multiline_returns_error_without_rg(sys_op, tmp_path):
    tool = GrepTool(sys_op)
    with mock.patch("shutil.which", return_value=None), \
         mock.patch.object(os, "name", "nt"):
        result = await tool.invoke({"pattern": "x", "path": str(tmp_path), "multiline": True})
    assert result.success is False
    assert "rg" in result.error.lower() or "multiline" in result.error.lower()


# ── Windows integration tests (skip unless Windows + no rg) ─────────────────

_skip_unless_windows_no_rg = pytest.mark.skipif(
    not (os.name == "nt" and not shutil.which("rg")),
    reason="requires Windows with no rg in PATH",
)


@pytest.mark.asyncio
@_skip_unless_windows_no_rg
async def test_ss_content_mode_basic(sys_op, temp_dir):
    write = WriteFileTool(sys_op)
    grep = GrepTool(sys_op)
    await write.invoke({"file_path": os.path.join(temp_dir, "a.txt"), "content": "Target\nOther\n"})
    result = await grep.invoke({"pattern": "Target", "path": temp_dir})
    assert result.success is True
    assert result.data["count"] == 1
    assert "Other" not in result.data.get("stdout", "")


@pytest.mark.asyncio
@_skip_unless_windows_no_rg
async def test_ss_files_with_matches_mode(sys_op, temp_dir):
    write = WriteFileTool(sys_op)
    grep = GrepTool(sys_op)
    await write.invoke({"file_path": os.path.join(temp_dir, "match.txt"), "content": "needle\n"})
    await write.invoke({"file_path": os.path.join(temp_dir, "nomatch.txt"), "content": "other\n"})
    result = await grep.invoke({"pattern": "needle", "path": temp_dir, "output_mode": "files_with_matches"})
    assert result.success is True
    assert result.data["numFiles"] == 1
    assert "match.txt" in result.data["filenames"][0]
    assert "nomatch.txt" not in result.data["filenames"][0]


@pytest.mark.asyncio
@_skip_unless_windows_no_rg
async def test_ss_count_mode(sys_op, temp_dir):
    write = WriteFileTool(sys_op)
    grep = GrepTool(sys_op)
    await write.invoke({"file_path": os.path.join(temp_dir, "f.txt"), "content": "hit\nhit\nmiss\n"})
    result = await grep.invoke({"pattern": "hit", "path": temp_dir, "output_mode": "count"})
    assert result.success is True
    assert result.data["numMatches"] == 2


@pytest.mark.asyncio
@_skip_unless_windows_no_rg
async def test_ss_glob_filter(sys_op, temp_dir):
    write = WriteFileTool(sys_op)
    grep = GrepTool(sys_op)
    await write.invoke({"file_path": os.path.join(temp_dir, "keep.py"), "content": "needle\n"})
    await write.invoke({"file_path": os.path.join(temp_dir, "skip.txt"), "content": "needle\n"})
    result = await grep.invoke(
        {"pattern": "needle", "path": temp_dir, "glob": "*.py", "output_mode": "files_with_matches"}
    )
    assert result.success is True
    assert result.data["numFiles"] == 1
    assert "keep.py" in result.data["filenames"][0]


@pytest.mark.asyncio
@_skip_unless_windows_no_rg
async def test_ss_excludes_vcs_directory(sys_op, temp_dir):
    write = WriteFileTool(sys_op)
    grep = GrepTool(sys_op)
    git_dir = os.path.join(temp_dir, ".git")
    os.makedirs(git_dir, exist_ok=True)
    await write.invoke({"file_path": os.path.join(temp_dir, "main.py"), "content": "needle\n"})
    await write.invoke({"file_path": os.path.join(git_dir, "ignored.txt"), "content": "needle\n"})
    result = await grep.invoke({"pattern": "needle", "path": temp_dir, "output_mode": "files_with_matches"})
    assert result.success is True
    assert result.data["numFiles"] == 1
    assert ".git" not in result.data["filenames"][0]


@pytest.mark.asyncio
@_skip_unless_windows_no_rg
async def test_ss_case_insensitive(sys_op, temp_dir):
    write = WriteFileTool(sys_op)
    grep = GrepTool(sys_op)
    await write.invoke({"file_path": os.path.join(temp_dir, "f.txt"), "content": "HELLO world\n"})
    result = await grep.invoke({"pattern": "hello", "path": temp_dir, "ignore_case": True})
    assert result.success is True
    assert result.data["count"] == 1
