# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os
import tempfile
import shutil
from types import MethodType

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.core.sys_operation.cwd import get_cwd, set_cwd
from openjiuwen.harness.prompts.tools.filesystem import (
    get_glob_input_params,
    get_grep_input_params,
)
from openjiuwen.harness.tools import (
    ReadFileTool, WriteFileTool, EditFileTool,
    GlobTool, ListDirTool, GrepTool,
)
from openjiuwen.harness.tools.filesystem import _FILE_READ_REGISTRY


@pytest.fixture
def temp_dir():
    dir_path = tempfile.mkdtemp()
    yield dir_path
    shutil.rmtree(dir_path)


@pytest_asyncio.fixture(name="sys_op")
async def sys_op_fixture():
    await Runner.start()
    card_id = "test_filesystem_tools_op"
    card = SysOperationCard(id=card_id, mode=OperationMode.LOCAL, work_config=LocalWorkConfig(
        shell_allowlist=["echo", "ls", "dir", "cd", "pwd", "python", "python3", "pip", "pip3",
                         "npm", "node", "git", "cat", "type", "mkdir", "md", "rm", "rd", "cp",
                         "copy", "mv", "move", "grep", "rg", "find", "curl", "wget", "ps", "df", "ping"]
    ))
    Runner.resource_mgr.add_sys_operation(card)
    op = Runner.resource_mgr.get_sys_operation(card_id)
    yield op
    Runner.resource_mgr.remove_sys_operation(sys_operation_id=card_id)
    await Runner.stop()


@pytest.mark.asyncio
async def test_file_read_write(sys_op, temp_dir):
    write_tool = WriteFileTool(sys_op)
    read_tool = ReadFileTool(sys_op)

    file_path = os.path.join(temp_dir, "test.txt")
    content = "第一行\n第二行\n第三行"

    write_res = await write_tool.invoke({"file_path": file_path, "content": content})
    assert write_res.success is True
    assert write_res.data["bytes_written"] > 0
    assert os.path.exists(file_path)

    read_res = await read_tool.invoke({"file_path": file_path})
    assert read_res.success is True
    assert "第一行" in read_res.data["content"]
    assert "第二行" in read_res.data["content"]
    assert "第三行" in read_res.data["content"]
    assert read_res.data["line_count"] == 3

    read_partial = await read_tool.invoke({"file_path": file_path, "offset": 1, "limit": 1})
    assert read_partial.success is True
    assert "第二行" in read_partial.data["content"]


@pytest.mark.asyncio
async def test_write_file_tool_requires_read_before_overwrite(sys_op, temp_dir):
    write_tool = WriteFileTool(sys_op)
    file_path = os.path.join(temp_dir, "existing.txt")
    with open(file_path, "w", encoding="utf-8") as fh:
        fh.write("existing content")

    _FILE_READ_REGISTRY.pop(file_path, None)
    res = await write_tool.invoke({"file_path": file_path, "content": "replacement"})

    assert res.success is False
    assert "read" in res.error.lower()


@pytest.mark.asyncio
async def test_write_file_tool_updates_existing_file_after_read(sys_op, temp_dir):
    write_tool = WriteFileTool(sys_op)
    read_tool = ReadFileTool(sys_op)
    file_path = os.path.join(temp_dir, "rewrite.txt")
    with open(file_path, "w", encoding="utf-8") as fh:
        fh.write("old content")

    await read_tool.invoke({"file_path": file_path})
    res = await write_tool.invoke({"file_path": file_path, "content": "new content\n"})

    assert res.success is True
    assert res.data["type"] == "update"
    assert res.data["created"] is False
    assert res.data["original_file"] == "old content"
    assert _FILE_READ_REGISTRY[file_path].content == "new content\n"


@pytest.mark.asyncio
async def test_write_file_tool_reads_existing_content_via_sys_operation_fs(sys_op, temp_dir):
    write_tool = WriteFileTool(sys_op)
    read_tool = ReadFileTool(sys_op)
    file_path = os.path.join(temp_dir, "rewrite_via_fs.txt")
    with open(file_path, "w", encoding="utf-8") as fh:
        fh.write("old content")

    await read_tool.invoke({"file_path": file_path})

    fs = sys_op.fs()
    original_read_file = fs.read_file
    calls: list[tuple[str, str]] = []

    async def tracked_read_file(self, path: str, *args, **kwargs):
        calls.append((path, kwargs.get("mode", "text")))
        return await original_read_file(path, *args, **kwargs)

    fs.read_file = MethodType(tracked_read_file, fs)
    try:
        res = await write_tool.invoke({"file_path": file_path, "content": "new content"})
    finally:
        fs.read_file = original_read_file

    assert res.success is True
    assert (file_path, "bytes") in calls


@pytest.mark.asyncio
async def test_write_file_tool_rejects_externally_modified_existing_file(sys_op, temp_dir):
    write_tool = WriteFileTool(sys_op)
    read_tool = ReadFileTool(sys_op)
    file_path = os.path.join(temp_dir, "stale.txt")
    with open(file_path, "w", encoding="utf-8") as fh:
        fh.write("before")

    await read_tool.invoke({"file_path": file_path})
    with open(file_path, "w", encoding="utf-8") as fh:
        fh.write("changed externally")

    res = await write_tool.invoke({"file_path": file_path, "content": "replacement"})

    assert res.success is False
    assert "modified since read" in res.error
    assert file_path not in _FILE_READ_REGISTRY


@pytest.mark.asyncio
async def test_edit_file(sys_op, temp_dir):
    write_tool = WriteFileTool(sys_op)
    read_tool = ReadFileTool(sys_op)
    edit_tool = EditFileTool(sys_op)
    file_path = os.path.join(temp_dir, "edit.txt")
    content = "Hello Google DeepMind Antigravity"
    await write_tool.invoke({"file_path": file_path, "content": content})
    await read_tool.invoke({"file_path": file_path})

    edit_res = await edit_tool.invoke({
        "file_path": file_path,
        "old_string": "Hello",
        "new_string": "Hell0",
        "replace_all": False
    })
    assert edit_res.success is True
    assert edit_res.data["replacements"] == 1

    read_res = await sys_op.fs().read_file(file_path)
    assert "Hell0 Google" in read_res.data.content


@pytest.mark.asyncio
async def test_glob_and_ls(sys_op, temp_dir):
    write_tool = WriteFileTool(sys_op)
    glob_tool = GlobTool(sys_op)
    ls_tool = ListDirTool(sys_op)

    os.makedirs(os.path.join(temp_dir, "subdir"), exist_ok=True)
    await write_tool.invoke({"file_path": os.path.join(temp_dir, "a.py"), "content": "1"})
    await write_tool.invoke({"file_path": os.path.join(temp_dir, "subdir", "b.py"), "content": "2"})
    await write_tool.invoke({"file_path": os.path.join(temp_dir, "c.txt"), "content": "3"})

    glob_res = await glob_tool.invoke({"pattern": "**/*.py", "path": temp_dir})
    assert glob_res.success is True
    assert glob_res.data["count"] == 2

    ls_res = await ls_tool.invoke({"path": temp_dir, "show_hidden": False})
    assert ls_res.success is True
    assert "subdir" in ls_res.data["dirs"]
    assert "a.py" in ls_res.data["files"]


@pytest.mark.asyncio
async def test_glob_tool_returns_structured_output(sys_op, temp_dir):
    write_tool = WriteFileTool(sys_op)
    glob_tool = GlobTool(sys_op)

    os.makedirs(os.path.join(temp_dir, "subdir"), exist_ok=True)
    await write_tool.invoke({"file_path": os.path.join(temp_dir, "a.py"), "content": "1"})
    await write_tool.invoke({"file_path": os.path.join(temp_dir, "subdir", "b.py"), "content": "2"})
    await write_tool.invoke({"file_path": os.path.join(temp_dir, "c.txt"), "content": "3"})

    glob_res = await glob_tool.invoke({"pattern": "**/*.py", "path": temp_dir})

    assert glob_res.success is True
    assert sorted(glob_res.data["filenames"]) == ["a.py", os.path.join("subdir", "b.py")]
    assert glob_res.data["numFiles"] == 2
    assert glob_res.data["count"] == 2
    assert glob_res.data["truncated"] is False
    assert isinstance(glob_res.data["durationMs"], int)
    assert len(glob_res.data["matching_files"]) == 2


@pytest.mark.asyncio
async def test_glob_tool_defaults_to_workdir_when_path_omitted(sys_op, temp_dir):
    write_tool = WriteFileTool(sys_op)
    glob_tool = GlobTool(sys_op)

    await write_tool.invoke({"file_path": os.path.join(temp_dir, "a.py"), "content": "1"})

    original_workdir = get_cwd()
    set_cwd(temp_dir)
    try:
        glob_res = await glob_tool.invoke({"pattern": "*.py"})
    finally:
        set_cwd(original_workdir)

    assert glob_res.success is True
    assert glob_res.data["filenames"] == ["a.py"]
    assert glob_res.data["numFiles"] == 1


@pytest.mark.asyncio
async def test_glob_tool_truncates_results(sys_op, temp_dir):
    write_tool = WriteFileTool(sys_op)
    glob_tool = GlobTool(sys_op)

    for idx in range(105):
        await write_tool.invoke({"file_path": os.path.join(temp_dir, f"file_{idx}.py"), "content": str(idx)})

    glob_res = await glob_tool.invoke({"pattern": "*.py", "path": temp_dir})

    assert glob_res.success is True
    assert glob_res.data["truncated"] is True
    assert glob_res.data["numFiles"] == 100
    assert glob_res.data["count"] == 100
    assert len(glob_res.data["filenames"]) == 100
    assert len(glob_res.data["matching_files"]) == 100


@pytest.mark.asyncio
async def test_grep_tool(sys_op, temp_dir):
    if not shutil.which("rg") and not shutil.which("grep"):
        pytest.skip("Neither rg nor grep found in PATH")

    write_tool = WriteFileTool(sys_op)
    grep_tool = GrepTool(sys_op)
    file_path = os.path.join(temp_dir, "grep_test.txt")
    content = "Target Line 1\nOther Line\nTarget Line 2\n"
    await write_tool.invoke({"file_path": file_path, "content": content})

    grep_res = await grep_tool.invoke({
        "pattern": "Target",
        "path": temp_dir,
        "ignore_case": False
    })
    assert grep_res.success is True
    assert grep_res.data["count"] == 2
    assert "Other Line" not in grep_res.data["stdout"]


@pytest.mark.asyncio
async def test_grep_tool_content_mode_supports_pagination_and_glob(sys_op, temp_dir):
    if not shutil.which("rg") and not shutil.which("grep"):
        pytest.skip("Neither rg nor grep found in PATH")

    write_tool = WriteFileTool(sys_op)
    grep_tool = GrepTool(sys_op)

    await write_tool.invoke({"file_path": os.path.join(temp_dir, "a.py"), "content": "Target 1\nskip\nTarget 2\n"})
    await write_tool.invoke({"file_path": os.path.join(temp_dir, "b.txt"), "content": "Target txt\n"})

    grep_res = await grep_tool.invoke({
        "pattern": "Target",
        "path": temp_dir,
        "glob": "*.py",
        "output_mode": "content",
        "head_limit": 1,
        "offset": 1,
    })

    assert grep_res.success is True
    assert grep_res.data["mode"] == "content"
    assert grep_res.data["numLines"] == 1
    assert grep_res.data["appliedOffset"] == 1
    assert "a.py" in grep_res.data["content"]
    assert "Target 2" in grep_res.data["content"]
    assert "Target txt" not in grep_res.data["content"]


@pytest.mark.asyncio
async def test_grep_tool_files_mode_excludes_vcs_directory(sys_op, temp_dir):
    if not shutil.which("rg") and not shutil.which("grep"):
        pytest.skip("Neither rg nor grep found in PATH")

    write_tool = WriteFileTool(sys_op)
    grep_tool = GrepTool(sys_op)

    git_dir = os.path.join(temp_dir, ".git")
    os.makedirs(git_dir, exist_ok=True)

    await write_tool.invoke({"file_path": os.path.join(temp_dir, "main.py"), "content": "needle\n"})
    await write_tool.invoke({"file_path": os.path.join(git_dir, "ignored.txt"), "content": "needle\n"})

    grep_res = await grep_tool.invoke({
        "pattern": "needle",
        "path": temp_dir,
        "output_mode": "files_with_matches",
    })

    assert grep_res.success is True
    assert grep_res.data["mode"] == "files_with_matches"
    assert grep_res.data["filenames"] == ["main.py"]
    assert grep_res.data["numFiles"] == 1


@pytest.mark.asyncio
async def test_grep_tool_defaults_to_content_mode(sys_op, temp_dir):
    if not shutil.which("rg") and not shutil.which("grep"):
        pytest.skip("Neither rg nor grep found in PATH")

    write_tool = WriteFileTool(sys_op)
    grep_tool = GrepTool(sys_op)

    await write_tool.invoke({"file_path": os.path.join(temp_dir, "main.py"), "content": "needle\n"})

    grep_res = await grep_tool.invoke({
        "pattern": "needle",
        "path": temp_dir,
    })

    assert grep_res.success is True
    assert grep_res.data["mode"] == "content"
    assert "main.py" in grep_res.data["content"]
    assert grep_res.data["numLines"] == 1


@pytest.mark.asyncio
async def test_grep_tool_count_mode_returns_structured_counts(sys_op, temp_dir):
    if not shutil.which("rg") and not shutil.which("grep"):
        pytest.skip("Neither rg nor grep found in PATH")

    write_tool = WriteFileTool(sys_op)
    grep_tool = GrepTool(sys_op)

    await write_tool.invoke({"file_path": os.path.join(temp_dir, "one.py"), "content": "hit\nhit\n"})
    await write_tool.invoke({"file_path": os.path.join(temp_dir, "two.py"), "content": "hit\n"})

    grep_res = await grep_tool.invoke({
        "pattern": "hit",
        "path": temp_dir,
        "glob": "*.py",
        "output_mode": "count",
    })

    assert grep_res.success is True
    assert grep_res.data["mode"] == "count"
    assert grep_res.data["numFiles"] == 2
    assert grep_res.data["numMatches"] == 3
    assert "one.py:2" in grep_res.data["content"]
    assert "two.py:1" in grep_res.data["content"]


def test_glob_input_params_keep_pattern_required_and_path_optional():
    schema = get_glob_input_params("en")

    assert schema["required"] == ["pattern"]
    assert "path" in schema["properties"]


def test_grep_input_params_expose_structured_fields():
    schema = get_grep_input_params("en")

    assert schema["required"] == ["pattern"]
    assert "output_mode" in schema["properties"]
    assert "glob" in schema["properties"]
    assert "head_limit" in schema["properties"]
    assert "offset" in schema["properties"]
    assert "-B" in schema["properties"]
    assert "-A" in schema["properties"]
    assert "-C" in schema["properties"]
    assert "context" in schema["properties"]
    assert "-n" in schema["properties"]
    assert "-i" in schema["properties"]
    assert "type" in schema["properties"]
    assert "multiline" in schema["properties"]


@pytest.mark.asyncio
async def test_edit_file_tool_requires_read_first(sys_op, temp_dir):
    write_tool = WriteFileTool(sys_op)
    edit_tool = EditFileTool(sys_op)
    file_path = os.path.join(temp_dir, "max_edit.txt")
    await write_tool.invoke({"file_path": file_path, "content": "hello world"})

    # Must fail without prior read_file call
    _FILE_READ_REGISTRY.pop(file_path, None)
    res = await edit_tool.invoke({
        "file_path": file_path,
        "old_string": "hello",
        "new_string": "hi",
    })
    assert res.success is False
    assert "read" in res.error.lower()


@pytest.mark.asyncio
async def test_edit_file_tool_full_workflow(sys_op, temp_dir):
    write_tool = WriteFileTool(sys_op)
    read_tool = ReadFileTool(sys_op)
    edit_tool = EditFileTool(sys_op)
    file_path = os.path.join(temp_dir, "max_edit2.txt")
    content = "alpha beta gamma\nalpha delta"
    await write_tool.invoke({"file_path": file_path, "content": content})

    # Read populates the registry
    read_res = await read_tool.invoke({"file_path": file_path})
    assert read_res.success is True
    assert file_path in _FILE_READ_REGISTRY

    # Unique match succeeds
    edit_res = await edit_tool.invoke({
        "file_path": file_path,
        "old_string": "beta gamma",
        "new_string": "beta GAMMA",
    })
    assert edit_res.success is True
    assert edit_res.data["replacements"] == 1

    # Registry refreshed after write
    assert file_path in _FILE_READ_REGISTRY

    # Verify content
    read2 = await read_tool.invoke({"file_path": file_path})
    assert "beta GAMMA" in read2.data["content"]


@pytest.mark.asyncio
async def test_edit_file_tool_reads_existing_content_via_sys_operation_fs(sys_op, temp_dir):
    write_tool = WriteFileTool(sys_op)
    read_tool = ReadFileTool(sys_op)
    edit_tool = EditFileTool(sys_op)
    file_path = os.path.join(temp_dir, "edit_via_fs.txt")
    with open(file_path, "w", encoding="utf-8", newline="") as fh:
        fh.write("alpha\r\nbeta\r\n")

    await read_tool.invoke({"file_path": file_path})

    fs = sys_op.fs()
    original_read_file = fs.read_file
    calls: list[tuple[str, str]] = []

    async def tracked_read_file(self, path: str, *args, **kwargs):
        calls.append((path, kwargs.get("mode", "text")))
        return await original_read_file(path, *args, **kwargs)

    fs.read_file = MethodType(tracked_read_file, fs)
    try:
        res = await edit_tool.invoke({
            "file_path": file_path,
            "old_string": "beta",
            "new_string": "gamma",
        })
    finally:
        fs.read_file = original_read_file

    assert res.success is True
    assert (file_path, "bytes") in calls


@pytest.mark.asyncio
async def test_edit_file_tool_replace_all(sys_op, temp_dir):
    write_tool = WriteFileTool(sys_op)
    read_tool = ReadFileTool(sys_op)
    edit_tool = EditFileTool(sys_op)
    file_path = os.path.join(temp_dir, "max_replace_all.txt")
    await write_tool.invoke({"file_path": file_path, "content": "foo foo foo"})
    await read_tool.invoke({"file_path": file_path})

    # Multiple matches without replace_all → error
    res = await edit_tool.invoke({
        "file_path": file_path,
        "old_string": "foo",
        "new_string": "bar",
    })
    assert res.success is False
    assert "3" in res.error

    # replace_all=True → replaces all
    res2 = await edit_tool.invoke({
        "file_path": file_path,
        "old_string": "foo",
        "new_string": "bar",
        "replace_all": True,
    })
    assert res2.success is True
    assert res2.data["replacements"] == 3


@pytest.mark.asyncio
async def test_edit_file_tool_new_file_creation(sys_op, temp_dir):
    edit_tool = EditFileTool(sys_op)
    file_path = os.path.join(temp_dir, "new_from_edit.txt")

    res = await edit_tool.invoke({
        "file_path": file_path,
        "old_string": "",
        "new_string": "created by edit",
    })
    assert res.success is True
    assert res.data.get("created") is True
    assert os.path.exists(file_path)


@pytest.mark.asyncio
async def test_edit_file_tool_rejects_identical_strings(sys_op, temp_dir):
    edit_tool = EditFileTool(sys_op)
    file_path = os.path.join(temp_dir, "noop.txt")
    res = await edit_tool.invoke({
        "file_path": file_path,
        "old_string": "same",
        "new_string": "same",
    })
    assert res.success is False
    assert "identical" in res.error.lower()


@pytest.mark.asyncio
async def test_edit_file_tool_rejects_ipynb(sys_op, temp_dir):
    edit_tool = EditFileTool(sys_op)
    res = await edit_tool.invoke({
        "file_path": os.path.join(temp_dir, "notebook.ipynb"),
        "old_string": "x",
        "new_string": "y",
    })
    assert res.success is False
    assert "NotebookEdit" in res.error


@pytest.mark.asyncio
async def test_edit_file_tool_html_desanitization(sys_op, temp_dir):
    write_tool = WriteFileTool(sys_op)
    read_tool = ReadFileTool(sys_op)
    edit_tool = EditFileTool(sys_op)
    file_path = os.path.join(temp_dir, "entities.txt")
    await write_tool.invoke({"file_path": file_path, "content": "a < b && c > d"})
    await read_tool.invoke({"file_path": file_path})

    # Pass HTML-escaped old_string (as the model might receive from XML tool calling)
    res = await edit_tool.invoke({
        "file_path": file_path,
        "old_string": "a &lt; b &amp;&amp; c &gt; d",
        "new_string": "x",
    })
    assert res.success is True


@pytest.mark.asyncio
async def test_read_file_tool_text(sys_op, temp_dir):
    write_tool = WriteFileTool(sys_op)
    read_tool = ReadFileTool(sys_op)
    file_path = os.path.join(temp_dir, "max.txt")
    content = "alpha\nbeta\ngamma\ndelta\n"
    await write_tool.invoke({"file_path": file_path, "content": content})

    first = await read_tool.invoke({"file_path": file_path, "offset": 1, "limit": 2})
    assert first.success is True
    assert first.data["content"].startswith("     1\tbeta")
    assert "     2\tgamma" in first.data["content"]

    # Second read of the same range always returns full content (no dedup).
    second = await read_tool.invoke({"file_path": file_path, "offset": 1, "limit": 2})
    assert second.success is True
    assert second.data["content"].startswith("     1\tbeta")
    assert "     2\tgamma" in second.data["content"]

    # Relative paths resolve against get_cwd(); the file is not in the default cwd.
    relative_missing = await read_tool.invoke({"file_path": "max.txt"})
    assert relative_missing.success is False

    # With cwd pointing at temp_dir, relative paths resolve correctly.
    set_cwd(temp_dir)
    relative_found = await read_tool.invoke({"file_path": "max.txt"})
    assert relative_found.success is True


@pytest.mark.asyncio
async def test_read_file_tool_image_returns_metadata_not_base64(sys_op, temp_dir):
    read_tool = ReadFileTool(sys_op)
    file_path = os.path.join(temp_dir, "sample.png")
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
        b"\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01"
        b"\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    with open(file_path, "wb") as fh:
        fh.write(png_bytes)

    res = await read_tool.invoke({"file_path": file_path})

    assert res.success is True
    assert "Image file read:" in res.data["content"]
    assert "data:image" not in res.data["content"]
    assert res.data["file_path"] == file_path


def test_read_file_tool_capability_flags_keep_backward_compatibility():
    assert ReadFileTool.is_read_only() is True
    assert ReadFileTool.is_concurrency_safe() is True
    assert ReadFileTool.check_permissions() == "allow"

    assert ReadFileTool.isReadOnly() is True
    assert ReadFileTool.isConcurrencySafe() is True
    assert ReadFileTool.checkPermissions() == "allow"
