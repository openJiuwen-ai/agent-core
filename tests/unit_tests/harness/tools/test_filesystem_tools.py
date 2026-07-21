# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import os
import base64
import json
import shutil
import tempfile
import unittest
from types import MethodType
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.core.sys_operation.cwd import get_cwd, set_cwd, set_workspace
from openjiuwen.harness.prompts.tools.filesystem import (
    get_glob_input_params,
    get_grep_input_params,
)
from openjiuwen.harness.tools import (
    ReadFileTool, WriteFileTool, EditFileTool,
    GlobTool, ListDirTool, GrepTool,
)
from openjiuwen.harness.tools import filesystem as filesystem_module
from openjiuwen.harness.tools.filesystem import (
    _FILE_READ_REGISTRY,
    _TokenBudget,
    _merge_range,
    _ranges_cover,
    _first_unread_offset,
)


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
async def test_read_file_image_returns_multimodal_payload(sys_op, temp_dir):
    read_tool = ReadFileTool(sys_op)
    file_path = os.path.join(temp_dir, "one_pixel.png")
    raw = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )
    with open(file_path, "wb") as fh:
        fh.write(raw)

    read_res = await read_tool.invoke({"file_path": file_path})

    assert read_res.success is True
    assert "Image file read:" in read_res.data["content"]
    assert "base64," not in read_res.data["content"]
    assert read_res.data["multimodal"][0]["type"] == "image"
    assert read_res.data["multimodal"][0]["data_url"].startswith("data:image/")


@pytest.mark.asyncio
async def test_read_file_image_can_disable_multimodal_payload(sys_op, temp_dir):
    read_tool = ReadFileTool(sys_op, enable_image_multimodal=False)
    file_path = os.path.join(temp_dir, "one_pixel.png")
    raw = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )
    with open(file_path, "wb") as fh:
        fh.write(raw)

    read_res = await read_tool.invoke({"file_path": file_path})

    assert read_res.success is True
    assert "Image file read:" in read_res.data["content"]
    assert "base64," not in read_res.data["content"]
    assert read_res.data["multimodal"] == []
    assert "native image multimodal input is disabled" in read_res.data["content"]


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
async def test_edit_file_tool_accepts_partial_read(sys_op, temp_dir):
    # A large file forces read_file into offset/limit (partial) reads; edit_file
    # must still allow editing once any read_file call has populated the registry —
    # otherwise files too big to read in one shot would be permanently uneditable.
    write_tool = WriteFileTool(sys_op)
    read_tool = ReadFileTool(sys_op)
    edit_tool = EditFileTool(sys_op)
    file_path = os.path.join(temp_dir, "partial_edit.txt")
    await write_tool.invoke({"file_path": file_path, "content": "line1\nline2\nline3"})

    read_res = await read_tool.invoke({"file_path": file_path, "offset": 1, "limit": 1})
    assert read_res.success is True
    assert _FILE_READ_REGISTRY[file_path].is_partial is True

    edit_res = await edit_tool.invoke({
        "file_path": file_path,
        "old_string": "line2",
        "new_string": "line2-edited",
    })
    assert edit_res.success is True
    assert edit_res.data["replacements"] == 1


@pytest.mark.asyncio
async def test_edit_file_tool_editable_after_offset_paginated_reads_only(sys_op, temp_dir):
    # Reproduces the original deadlock: a file larger than MAX_LINES_TO_READ can
    # never produce a non-partial registry entry (default reads truncate and get
    # flagged partial; explicit offset/limit reads are always flagged partial too).
    # edit_file must still succeed once the file has been paginated through via
    # offset/limit alone, with no full read ever taking place.
    write_tool = WriteFileTool(sys_op)
    read_tool = ReadFileTool(sys_op)
    edit_tool = EditFileTool(sys_op)
    file_path = os.path.join(temp_dir, "big_file.txt")

    total_lines = ReadFileTool.MAX_LINES_TO_READ + 500
    lines = [f"line{i}" for i in range(total_lines)]
    lines[2400] = "TARGET_LINE"
    await write_tool.invoke({"file_path": file_path, "content": "\n".join(lines)})

    # Paginate through the file in chunks, mirroring how an agent would read a
    # file too large to fit in one call. Never issue a full (no offset/limit) read.
    page_size = 1000
    for offset in range(0, total_lines, page_size):
        page_res = await read_tool.invoke({"file_path": file_path, "offset": offset, "limit": page_size})
        assert page_res.success is True
    assert _FILE_READ_REGISTRY[file_path].is_partial is True

    edit_res = await edit_tool.invoke({
        "file_path": file_path,
        "old_string": "TARGET_LINE",
        "new_string": "EDITED_LINE",
    })
    assert edit_res.success is True
    assert edit_res.data["replacements"] == 1


@pytest.mark.asyncio
async def test_edit_file_tool_partial_read_still_rejects_external_modification(sys_op, temp_dir):
    write_tool = WriteFileTool(sys_op)
    read_tool = ReadFileTool(sys_op)
    edit_tool = EditFileTool(sys_op)
    file_path = os.path.join(temp_dir, "partial_edit_stale.txt")
    await write_tool.invoke({"file_path": file_path, "content": "line1\nline2\nline3"})
    await read_tool.invoke({"file_path": file_path, "offset": 1, "limit": 1})

    # External modification after the partial read must still be detected.
    with open(file_path, "w", encoding="utf-8") as fh:
        fh.write("line1\nCHANGED\nline3")

    res = await edit_tool.invoke({
        "file_path": file_path,
        "old_string": "line2",
        "new_string": "line2-edited",
    })
    assert res.success is False
    assert "modified externally" in res.error


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
async def test_edit_file_tool_rejects_change_between_edit_read_and_write(sys_op, temp_dir):
    """The write-time compare-and-swap must prevent a lost concurrent update."""
    write_tool = WriteFileTool(sys_op)
    read_tool = ReadFileTool(sys_op)
    edit_tool = EditFileTool(sys_op)
    file_path = os.path.join(temp_dir, "concurrent_edit.txt")
    await write_tool.invoke({"file_path": file_path, "content": "alpha beta"})
    await read_tool.invoke({"file_path": file_path})

    fs = sys_op.fs()
    original_write_file = fs.write_file
    injected = False

    async def concurrent_write(path: str, content: str, **kwargs):
        nonlocal injected
        if not injected and kwargs.get("options", {}).get("expected_content_sha256"):
            injected = True
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("alpha changed-by-another-agent")
        return await original_write_file(path, content, **kwargs)

    fs.write_file = concurrent_write
    try:
        result = await edit_tool.invoke({
            "file_path": file_path,
            "old_string": "beta",
            "new_string": "gamma",
        })
    finally:
        fs.write_file = original_write_file

    assert result.success is False
    assert "changed while preparing" in result.error
    with open(file_path, encoding="utf-8") as fh:
        assert fh.read() == "alpha changed-by-another-agent"


@pytest.mark.asyncio
async def test_edit_file_tool_waits_for_other_instance_and_edits_latest_content(sys_op, temp_dir):
    """Different agent tool instances share one per-file edit transaction."""
    write_tool = WriteFileTool(sys_op)
    read_tool = ReadFileTool(sys_op)
    first_tool = EditFileTool(sys_op)
    second_tool = EditFileTool(sys_op)
    file_path = os.path.join(temp_dir, "shared_agent_edit.txt")
    await write_tool.invoke({"file_path": file_path, "content": "host=local\nlevel=debug"})
    await read_tool.invoke({"file_path": file_path})

    first_has_lock = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()
    first_unlocked = first_tool._invoke_unlocked
    second_unlocked = second_tool._invoke_unlocked

    async def delayed_first(self, inputs, **kwargs):
        first_has_lock.set()
        await release_first.wait()
        return await first_unlocked(inputs, **kwargs)

    async def tracked_second(self, inputs, **kwargs):
        second_entered.set()
        return await second_unlocked(inputs, **kwargs)

    first_tool._invoke_unlocked = MethodType(delayed_first, first_tool)
    second_tool._invoke_unlocked = MethodType(tracked_second, second_tool)

    first_task = asyncio.create_task(first_tool.invoke({
        "file_path": file_path,
        "old_string": "host=local",
        "new_string": "host=production",
    }))
    await first_has_lock.wait()
    second_task = asyncio.create_task(second_tool.invoke({
        "file_path": file_path,
        "old_string": "level=debug",
        "new_string": "level=warn",
    }))
    await asyncio.sleep(0)
    assert not second_entered.is_set()

    release_first.set()
    first_result, second_result = await asyncio.gather(first_task, second_task)
    assert first_result.success is True
    assert second_result.success is True
    with open(file_path, encoding="utf-8") as fh:
        assert fh.read() == "host=production\nlevel=warn"


@pytest.mark.asyncio
async def test_edit_file_tool_reads_existing_content_via_sys_operation_fs(sys_op, temp_dir):
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
async def test_read_file_tool_rejects_large_text_without_explicit_limit(sys_op, temp_dir):
    read_tool = ReadFileTool(sys_op)
    file_path = os.path.join(temp_dir, "large.txt")
    with open(file_path, "w", encoding="utf-8") as fh:
        fh.write("A" * (ReadFileTool.MAX_SIZE_BYTES + 1))

    result = await read_tool.invoke({"file_path": file_path})

    assert result.success is False
    assert "exceeds maximum allowed size" in result.error
    assert "offset and limit" in result.error


@pytest.mark.asyncio
async def test_read_file_tool_allows_large_text_with_explicit_limit(sys_op, temp_dir):
    read_tool = ReadFileTool(sys_op)
    file_path = os.path.join(temp_dir, "large_lines.txt")
    with open(file_path, "w", encoding="utf-8") as fh:
        for idx in range(30000):
            fh.write(f"line-{idx}\n")

    result = await read_tool.invoke({"file_path": file_path, "offset": 0, "limit": 2})

    assert result.success is True
    assert "line-0" in result.data["content"]
    assert "line-1" in result.data["content"]


def test_read_file_tool_capability_flags_keep_backward_compatibility():
    assert ReadFileTool.is_read_only() is True
    assert ReadFileTool.is_concurrency_safe() is True
    assert ReadFileTool.check_permissions() == "allow"

    assert ReadFileTool.isReadOnly() is True
    assert ReadFileTool.isConcurrencySafe() is True
    assert ReadFileTool.checkPermissions() == "allow"


# ── _TokenBudget boundary semantics ─────────────────────────────────

def test_token_budget_initial_state_not_exhausted():
    budget = _TokenBudget(100)
    assert budget.spent == 0
    assert budget.exhausted is False


def test_token_budget_can_fit_within_budget():
    budget = _TokenBudget(100)
    assert budget.can_fit(100) is True


def test_token_budget_can_fit_exact_remaining():
    budget = _TokenBudget(100)
    budget.spend(60)
    assert budget.can_fit(40) is True


def test_token_budget_cannot_fit_over_budget():
    budget = _TokenBudget(100)
    assert budget.can_fit(101) is False


def test_token_budget_spend_accumulates():
    budget = _TokenBudget(100)
    budget.spend(30)
    budget.spend(20)
    assert budget.spent == 50


def test_token_budget_not_exhausted_below_max():
    budget = _TokenBudget(100)
    budget.spend(99)
    assert budget.exhausted is False


def test_token_budget_exhausted_at_exact_max():
    budget = _TokenBudget(100)
    budget.spend(100)
    assert budget.exhausted is True


def test_token_budget_exhausted_over_max():
    budget = _TokenBudget(100)
    budget.spend(150)
    assert budget.exhausted is True


def test_token_budget_cannot_fit_after_exhausted():
    budget = _TokenBudget(100)
    budget.spend(100)
    assert budget.can_fit(1) is False
    assert budget.can_fit(0) is True


def test_token_budget_zero_max_tokens_starts_exhausted():
    budget = _TokenBudget(0)
    assert budget.exhausted is True
    assert budget.can_fit(1) is False
    assert budget.can_fit(0) is True


# ── notebook / PDF progressive truncation ─────────────────────────────────

@pytest.mark.asyncio
async def test_read_file_tool_reads_ipynb_notebook(sys_op, temp_dir):
    """.ipynb files must not be rejected by the binary-file guard in invoke()."""
    read_tool = ReadFileTool(sys_op)
    file_path = os.path.join(temp_dir, "small.ipynb")
    notebook = {
        "cells": [
            {
                "cell_type": "code",
                "source": ["print('hello')\n"],
                "outputs": [{"output_type": "stream", "text": ["hello\n"]}],
            },
            {
                "cell_type": "markdown",
                "source": ["# Title\n"],
                "outputs": [],
            },
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    with open(file_path, "w", encoding="utf-8") as fh:
        json.dump(notebook, fh)

    result = await read_tool.invoke({"file_path": file_path})

    assert result.success is True
    assert "## Cell 1 [code]" in result.data["content"]
    assert "print('hello')" in result.data["content"]
    assert "## Cell 2 [markdown]" in result.data["content"]


@pytest.mark.asyncio
async def test_read_notebook_truncation_on_token_budget(sys_op, temp_dir):
    read_tool = ReadFileTool(sys_op)
    file_path = os.path.join(temp_dir, "big.ipynb")
    cell_source = "x = 1  # " + ("filler " * 80) + "\n"
    cells = [
        {"cell_type": "code", "source": [cell_source], "outputs": []}
        for _ in range(200)
    ]
    notebook = {"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    with open(file_path, "w", encoding="utf-8") as fh:
        json.dump(notebook, fh)

    result = await read_tool.invoke({"file_path": file_path})

    assert result.success is True
    content = result.data["content"]
    assert "## Cell 1 [code]" in content
    assert "## Cell 200 [code]" not in content
    assert "truncated" in content
    assert "/200 cells extracted" in content
    assert f"{ReadFileTool.MAX_TOKENS}-token budget reached" in content


@pytest.mark.asyncio
async def test_read_pdf_truncation_on_token_budget(sys_op, temp_dir, monkeypatch):
    read_tool = ReadFileTool(sys_op)
    file_path = os.path.join(temp_dir, "big.pdf")
    with open(file_path, "wb") as fh:
        fh.write(b"%PDF-1.4 fake content for mocked pdfplumber\n")

    class _FakePage:
        def __init__(self, text: str):
            self._text = text

        def extract_text(self):
            return self._text

    class _FakePdf:
        def __init__(self, pages):
            self.pages = pages

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

    page_text = "word " * 3000
    fake_pdf = _FakePdf([_FakePage(page_text) for _ in range(15)])
    monkeypatch.setattr(filesystem_module.pdfplumber, "open", lambda *_a, **_kw: fake_pdf)

    result = await read_tool.invoke({"file_path": file_path})

    assert result.success is True
    content = result.data["content"]
    assert "## Page 1" in content
    assert "## Page 15" not in content
    assert "truncated" in content
    assert "/15 pages extracted" in content
    assert f"{ReadFileTool.MAX_TOKENS}-token budget reached" in content


# ── history path construction ─────────────────────────────────


class TestWriteFileToolHistoryPath(unittest.TestCase):
    """Unit tests for WriteFileTool._build_history_path — no Runner required."""

    def _make_session(self, session_id: str, agent_id: str | None = None) -> MagicMock:
        mock = MagicMock()
        mock.get_session_id.return_value = session_id
        mock.agent_id.return_value = agent_id
        return mock

    def test_path_contains_agent_id_and_session_id(self):
        """History path embeds both agent_id and session_id."""
        session = self._make_session("sess_abc", agent_id="agent_xyz")
        tool = WriteFileTool(MagicMock())
        path = tool._build_history_path(session)
        assert "agent_xyz" in path
        assert "sess_abc" in path
        session.get_session_id.assert_called_once()

    def test_default_agent_id_used_when_none(self):
        """session.agent_id() returning None falls back to 'default'."""
        session = self._make_session("s1", agent_id=None)
        tool = WriteFileTool(MagicMock())
        path = tool._build_history_path(session)
        assert "default" in path

    def test_workspace_path_is_base_dir(self):
        """Workspace ContextVar is used as the base directory."""
        session = self._make_session("s1", agent_id="a")
        workspace = tempfile.mkdtemp()
        try:
            set_workspace(workspace)
            tool = WriteFileTool(MagicMock())
            path = tool._build_history_path(session)
            assert path.startswith(os.path.realpath(workspace))
            assert ".agent_history" in path
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def test_filename_pattern(self):
        """Filename follows file_ops_{agent_id}_{session_id}.json pattern."""
        session = self._make_session("sess123", agent_id="myagent")
        tool = WriteFileTool(MagicMock())
        path = tool._build_history_path(session)
        filename = os.path.basename(path)
        assert filename == "file_ops_myagent_sess123.json"


class TestEditFileToolHistoryPath(unittest.TestCase):
    """Unit tests for EditFileTool._build_history_path — no Runner required."""

    def _make_session(self, session_id: str, agent_id: str | None = None) -> MagicMock:
        mock = MagicMock()
        mock.get_session_id.return_value = session_id
        mock.agent_id.return_value = agent_id
        return mock

    def test_path_contains_agent_id_and_session_id(self):
        """History path embeds both agent_id and session_id."""
        session = self._make_session("sess_abc", agent_id="agent_xyz")
        tool = EditFileTool(MagicMock())
        path = tool._build_history_path(session)
        assert "agent_xyz" in path
        assert "sess_abc" in path
        session.get_session_id.assert_called_once()

    def test_default_agent_id_used_when_none(self):
        """session.agent_id() returning None falls back to 'default'."""
        session = self._make_session("s1", agent_id=None)
        tool = EditFileTool(MagicMock())
        path = tool._build_history_path(session)
        assert "default" in path

    def test_workspace_path_is_base_dir(self):
        """Workspace ContextVar is used as the base directory."""
        session = self._make_session("s1", agent_id="a")
        workspace = tempfile.mkdtemp()
        try:
            set_workspace(workspace)
            tool = EditFileTool(MagicMock())
            path = tool._build_history_path(session)
            assert path.startswith(os.path.realpath(workspace))
            assert ".agent_history" in path
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def test_filename_pattern(self):
        """Filename follows file_ops_{agent_id}_{session_id}.json pattern."""
        session = self._make_session("sess123", agent_id="myagent")
        tool = EditFileTool(MagicMock())
        path = tool._build_history_path(session)
        filename = os.path.basename(path)
        assert filename == "file_ops_myagent_sess123.json"


# ---------------------------------------------------------------------------
# WriteFileTool cumulative-coverage tests
#
# Mirror the EditFileTool partial-read tests above, but for the full-file
# overwrite tool. WriteFileTool overwrites the whole file, so a partial read
# (offset/limit) only seeing a window is NOT enough — unread content would be
# lost on overwrite. The guard therefore tracks the union of every read
# interval; when the union covers [1, total_lines] the file is "logically
# fully read" and write is allowed. This unblocks large files (which can only
# be read in partial windows) while still refusing to overwrite a file that
# was only partially read.
# ---------------------------------------------------------------------------


def test_merge_range_merges_overlapping_and_adjacent():
    assert _merge_range([(1, 5)], (3, 7)) == [(1, 7)]
    assert _merge_range([(1, 5)], (6, 9)) == [(1, 9)]  # adjacent
    assert _merge_range([(1, 5)], (7, 9)) == [(1, 5), (7, 9)]  # gap kept
    # Head-tail-middle insertion order still collapses to a single span.
    assert _merge_range([(1, 100), (2001, 3824)], (101, 2000)) == [(1, 3824)]


def test_ranges_cover_detects_full_and_partial_coverage():
    assert _ranges_cover([(1, 3824)], 1, 3824) is True
    assert _ranges_cover([(1, 2000), (2001, 3824)], 1, 3824) is True
    assert _ranges_cover([(1, 100), (2001, 3824)], 1, 3824) is False  # gap
    assert _ranges_cover([(1, 2000)], 1, 3824) is False  # tail unread
    assert _ranges_cover([], 1, 3824) is False


def test_first_unread_offset_finds_gap_then_tail():
    assert _first_unread_offset([]) == 0
    assert _first_unread_offset([(1, 2000)]) == 2000
    assert _first_unread_offset([(1, 100), (2001, 3824)]) == 100  # gap start


@pytest.mark.asyncio
async def test_write_file_rejects_partial_read_with_resumable_hint(sys_op, temp_dir):
    # A single partial read covers only a window — overwrite must be refused,
    # and the error must tell the agent where to resume so it doesn't loop blindly.
    write_tool = WriteFileTool(sys_op)
    read_tool = ReadFileTool(sys_op)
    file_path = os.path.join(temp_dir, "partial_write.txt")
    total_lines = ReadFileTool.MAX_LINES_TO_READ + 500
    lines = [f"line{i}" for i in range(total_lines)]
    await write_tool.invoke({"file_path": file_path, "content": "\n".join(lines)})

    _FILE_READ_REGISTRY.pop(file_path, None)
    await read_tool.invoke({"file_path": file_path, "offset": 0, "limit": 1000})

    res = await write_tool.invoke({"file_path": file_path, "content": "overwrite"})
    assert res.success is False
    assert "fully read" in res.error
    assert "offset=" in res.error
    state = _FILE_READ_REGISTRY[file_path]
    assert state.total_lines == total_lines
    assert _first_unread_offset(state.read_ranges) == 1000


@pytest.mark.asyncio
async def test_write_file_allows_after_paginated_read_covers_whole_file(sys_op, temp_dir):
    # Core deadlock fix: a file larger than MAX_LINES_TO_READ can be read end-to-end
    # via offset/limit pages alone; once the union covers the whole file, write is
    # allowed even though every individual read was partial.
    write_tool = WriteFileTool(sys_op)
    read_tool = ReadFileTool(sys_op)
    file_path = os.path.join(temp_dir, "big_overwrite.txt")
    total_lines = ReadFileTool.MAX_LINES_TO_READ + 500
    lines = [f"line{i}" for i in range(total_lines)]
    await write_tool.invoke({"file_path": file_path, "content": "\n".join(lines)})

    _FILE_READ_REGISTRY.pop(file_path, None)
    page_size = 1000
    for offset in range(0, total_lines, page_size):
        await read_tool.invoke({"file_path": file_path, "offset": offset, "limit": page_size})

    state = _FILE_READ_REGISTRY[file_path]
    assert _ranges_cover(state.read_ranges, 1, total_lines) is True

    res = await write_tool.invoke({"file_path": file_path, "content": "fully overwritten"})
    assert res.success is True
    assert res.data["type"] == "update"


@pytest.mark.asyncio
async def test_write_file_allows_after_head_tail_then_middle_fill(sys_op, temp_dir):
    # An agent may read head then tail, leaving a middle gap; write must stay
    # blocked until the gap is filled, then succeed. Verifies range merging.
    write_tool = WriteFileTool(sys_op)
    read_tool = ReadFileTool(sys_op)
    file_path = os.path.join(temp_dir, "gap_write.txt")
    total_lines = 3824
    lines = [f"line{i}" for i in range(total_lines)]
    await write_tool.invoke({"file_path": file_path, "content": "\n".join(lines)})

    _FILE_READ_REGISTRY.pop(file_path, None)
    await read_tool.invoke({"file_path": file_path, "offset": 0, "limit": 100})    # 1-100
    await read_tool.invoke({"file_path": file_path, "offset": 2000})              # 2001-3824, gap 101-2000

    res_gap = await write_tool.invoke({"file_path": file_path, "content": "blocked"})
    assert res_gap.success is False
    assert "offset=100" in res_gap.error

    await read_tool.invoke({"file_path": file_path, "offset": 100, "limit": 1900})  # fill 101-2000
    res_ok = await write_tool.invoke({"file_path": file_path, "content": "merged"})
    assert res_ok.success is True


@pytest.mark.asyncio
async def test_write_file_partial_read_still_blocks_after_external_modification(sys_op, temp_dir):
    # Coverage tracking must not weaken safety: after a partial read, an external
    # modification leaves the cached total/ranges stale. The write must still be
    # refused — the agent has to re-read before overwriting. (After the external
    # change, no new read happens, so the cached coverage is partial against the
    # old total; WriteFileTool blocks it as "not fully read". The next read_file
    # call detects the mtime/size change and recomputes coverage from scratch.)
    write_tool = WriteFileTool(sys_op)
    read_tool = ReadFileTool(sys_op)
    file_path = os.path.join(temp_dir, "stale_partial.txt")
    await write_tool.invoke({"file_path": file_path, "content": "\n".join(f"line{i}" for i in range(20))})

    await read_tool.invoke({"file_path": file_path, "offset": 0, "limit": 10})
    state_before = _FILE_READ_REGISTRY[file_path]
    assert state_before.total_lines == 20

    with open(file_path, "w", encoding="utf-8") as fh:
        fh.write("externally changed\n")

    res = await write_tool.invoke({"file_path": file_path, "content": "overwrite"})
    assert res.success is False  # still refused — safety preserved
    # The next read must recompute total/ranges because the file changed.
    await read_tool.invoke({"file_path": file_path, "offset": 0})
    state_after = _FILE_READ_REGISTRY[file_path]
    assert state_after.total_lines == 1  # file now has 1 line


@pytest.mark.asyncio
async def test_write_file_small_file_single_full_read_allows_overwrite(sys_op, temp_dir):
    # Backward compatibility: a small file read in one shot (no offset/limit)
    # is the old "non-partial" path — it must still be writable immediately,
    # now via the coverage path (union == [1, total]).
    write_tool = WriteFileTool(sys_op)
    read_tool = ReadFileTool(sys_op)
    file_path = os.path.join(temp_dir, "small.txt")
    await write_tool.invoke({"file_path": file_path, "content": "a\nb\nc"})

    _FILE_READ_REGISTRY.pop(file_path, None)
    await read_tool.invoke({"file_path": file_path})
    state = _FILE_READ_REGISTRY[file_path]
    assert _ranges_cover(state.read_ranges, 1, state.total_lines) is True

    res = await write_tool.invoke({"file_path": file_path, "content": "x\ny\nz"})
    assert res.success is True
