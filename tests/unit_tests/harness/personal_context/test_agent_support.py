from __future__ import annotations

import asyncio
import json
import os
import re
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import openjiuwen.harness.personal_context.file_tools as file_tools_module
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.context_engine.processor import forked
from openjiuwen.core.context_engine.processor.base import ContextEvent
from openjiuwen.core.context_engine.processor.compressor.round_level_compressor import (
    RoundLevelCompressor,
    RoundLevelCompressorConfig,
)
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    BaseMessage,
    ModelClientConfig,
    ModelRequestConfig,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent, ModelCallInputs, ToolCallInputs
from openjiuwen.core.sys_operation.cwd import get_cwd, set_cwd
from openjiuwen.harness.personal_context import agent_support, context_pipeline
from openjiuwen.harness.personal_context.status_codes import StatusCode
from openjiuwen.harness.rails import SecurityRail
from openjiuwen.harness.rails.context_engineer import ContextProcessorRail
from openjiuwen.harness.rails.tool_call_resilience_rail import ToolCallResilienceRail
from openjiuwen.harness.tools.base_tool import ToolOutput


def test_personal_context_file_tools_are_exactly_the_seven_bounded_tools(
    tmp_path: Path,
) -> None:
    tools = agent_support._make_personal_context_file_tools(
        cast(Any, object()),
        tmp_path,
    )

    assert [tool.card.name for tool in tools] == [
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "list_files",
        "grep",
        "move_path",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["write_file", "edit_file"])
async def test_personal_context_create_tools_reject_overlong_new_semantic_name_before_write(
    tmp_path: Path,
    tool_name: str,
) -> None:
    sandbox = tmp_path / "sandbox"
    topic = sandbox / "context" / "主题"
    topic.mkdir(parents=True)
    operation = agent_support._make_sys_operation(sandbox)
    tool = next(
        tool
        for tool in agent_support._make_personal_context_file_tools(operation, sandbox)
        if tool.card.name == tool_name
    )
    relative = "context/主题/这是一个超过二十个Unicode字符的新文件名称用于回归.md"
    inputs = (
        {"file_path": relative, "content": "# 内容\n"}
        if tool_name == "write_file"
        else {"file_path": relative, "old_string": "", "new_string": "# 内容\n"}
    )

    result = await tool.invoke(inputs)

    assert result.success is False
    assert "20 Unicode" in str(result.error)
    assert not any(topic.glob("*.md"))
    assert str(tmp_path) not in str(result.error)


@pytest.mark.asyncio
async def test_personal_context_create_tool_allows_description_and_existing_long_name_edit(
    tmp_path: Path,
) -> None:
    sandbox = tmp_path / "sandbox"
    topic = sandbox / "context" / "这是一个已经存在且超过二十个字符的历史目录名称"
    topic.mkdir(parents=True)
    existing = topic / "这是一个已经存在且超过二十个字符的历史文件名称.md"
    existing.write_text("# 旧\n", encoding="utf-8")
    operation = agent_support._make_sys_operation(sandbox)
    tools = agent_support._make_personal_context_file_tools(operation, sandbox)
    write = next(tool for tool in tools if tool.card.name == "write_file")
    edit = next(tool for tool in tools if tool.card.name == "edit_file")
    read = next(tool for tool in tools if tool.card.name == "read_file")

    description_result = await write.invoke(
        {"file_path": topic.joinpath("description.md").as_posix(), "content": "# 说明\n"}
    )
    await read.invoke({"file_path": existing.as_posix()})
    edit_result = await edit.invoke({"file_path": existing.as_posix(), "old_string": "# 旧", "new_string": "# 新"})

    assert description_result.success is True
    assert edit_result.success is True
    assert existing.read_text(encoding="utf-8") == "# 新\n"


def test_agent_baseline_snapshot_restores_long_read_only_tree_with_safe_name(
    tmp_path: Path,
) -> None:
    sandbox = tmp_path / "workspace" / "sandboxes" / "service" / "0123456789abcdef0123456789abcdef"
    source = sandbox / "materialized-source"
    for index in range(4):
        source /= f"segment-{index}-" + ("x" * 52)
    source /= "payload.md"
    context_pipeline._atomic_write(source, b"long-path-source")
    assert len(str(source.absolute())) > 260
    extended_source = context_pipeline._extended_path(source)
    extended_source.chmod(extended_source.stat().st_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)

    baseline = agent_support._snapshot_sandbox(sandbox)
    try:
        assert re.fullmatch(r"agent-baseline-[A-Za-z0-9_-]+", baseline.name)
        baseline_source = baseline / source.relative_to(sandbox)
        assert context_pipeline._extended_path(baseline_source).read_bytes() == b"long-path-source"
        (sandbox / "dirty.txt").write_text("dirty", encoding="utf-8")

        agent_support._restore_sandbox(sandbox, baseline)

        assert not (sandbox / "dirty.txt").exists()
        assert context_pipeline._extended_path(source).read_bytes() == b"long-path-source"
    finally:
        agent_support._make_tree_writable(baseline)
        agent_support._remove_tree(baseline)


def test_agent_baseline_snapshot_failure_removes_partial_long_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = tmp_path / "workspace" / "sandboxes" / "service" / "run"
    sandbox.mkdir(parents=True)

    def fail_after_partial_copy(_source: Path, target: Path) -> None:
        partial = target / "materialized-source"
        for index in range(4):
            partial /= f"segment-{index}-" + ("x" * 52)
        partial /= "payload.md"
        context_pipeline._atomic_write(partial, b"partial")
        extended = context_pipeline._extended_path(partial)
        extended.chmod(extended.stat().st_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
        raise OSError("copy failed")

    monkeypatch.setattr(agent_support, "_copy_tree_contents", fail_after_partial_copy)

    with pytest.raises(OSError, match="copy failed"):
        agent_support._snapshot_sandbox(sandbox)

    assert not list(sandbox.parent.glob("agent-baseline-*"))


@pytest.mark.asyncio
async def test_personal_context_grep_accepts_bounded_regex_and_rejects_escape(
    tmp_path: Path,
) -> None:
    original_cwd = get_cwd()
    sandbox = tmp_path / "sandbox (safe) [1]"
    sandbox.mkdir()
    (sandbox / "notes.md").write_text("Alpha(42)[ok]\n", encoding="utf-8")
    tools = agent_support._make_personal_context_file_tools(
        cast(Any, object()),
        sandbox,
    )
    tool = next(tool for tool in tools if tool.card.name == "grep")
    set_cwd(str(sandbox))
    try:
        result = await tool.invoke({"pattern": r"Alpha\([0-9]+\)\[ok\]", "path": str(sandbox)})
        assert result.success is True

        outside = tmp_path / "outside.md"
        outside.write_text("Alpha(42)[ok]\n", encoding="utf-8")
        escaped = await tool.invoke({"pattern": "Alpha", "path": str(outside)})
        assert escaped.success is False
        assert "outside" in str(escaped.error).casefold() or "sandbox" in str(escaped.error).casefold()
    finally:
        set_cwd(original_cwd)


@pytest.mark.asyncio
async def test_personal_context_grep_bounds_results_without_shell(
    tmp_path: Path,
) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "many.md").write_text(
        "\n".join(f"match-{index}" for index in range(20)),
        encoding="utf-8",
    )
    tools = agent_support._make_personal_context_file_tools(
        cast(Any, object()),
        sandbox,
    )
    tool = next(tool for tool in tools if tool.card.name == "grep")

    result = await tool.invoke(
        {
            "pattern": "match-",
            "path": ".",
            "head_limit": 3,
            "offset": 2,
        }
    )

    assert result.success is True
    assert result.data["count"] == 3
    assert result.data["appliedOffset"] == 2
    assert result.data["appliedLimit"] == 3


def _move_tool(
    sandbox: Path,
    *,
    max_pages_per_directory: int = 20,
    max_subdirectories_per_directory: int = 20,
) -> Any:
    tools = agent_support._make_personal_context_file_tools(
        cast(Any, object()),
        sandbox,
        max_pages_per_directory=max_pages_per_directory,
        max_subdirectories_per_directory=max_subdirectories_per_directory,
    )
    return next(tool for tool in tools if tool.card.name == "move_path")


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes]]:
    snapshot: dict[str, tuple[str, bytes]] = {}
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in sorted(directories):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                snapshot[relative] = ("link", os.readlink(path).encode())
            else:
                snapshot[relative] = ("directory", b"")
        for name in sorted(files):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                snapshot[relative] = ("link", os.readlink(path).encode())
            else:
                snapshot[relative] = ("file", path.read_bytes())
    return snapshot


@pytest.mark.asyncio
async def test_move_path_renames_and_moves_markdown_file_without_rewriting_content(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    first = sandbox / "context" / "主题一"
    second = sandbox / "context" / "主题二"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "旧名称.md").write_text("# 页面\n\n[链接](../主题二/目标.md)\n", encoding="utf-8")
    (second / "目标.md").write_text("# 目标\n", encoding="utf-8")
    tool = _move_tool(sandbox)

    renamed = await tool.invoke({"source_path": "主题一/旧名称.md", "destination_path": "主题一/新名称.md"})
    moved = await tool.invoke({"source_path": "主题一/新名称.md", "destination_path": "主题二/新名称.md"})

    assert renamed.success is True
    assert moved.success is True
    assert not (first / "旧名称.md").exists()
    assert not (first / "新名称.md").exists()
    assert (second / "新名称.md").read_text(encoding="utf-8") == "# 页面\n\n[链接](../主题二/目标.md)\n"


@pytest.mark.asyncio
async def test_move_path_rejects_overlong_new_semantic_name_before_move(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    topic = sandbox / "context" / "主题"
    topic.mkdir(parents=True)
    source = topic / "旧名称.md"
    source.write_text("# 页面\n", encoding="utf-8")

    result = await _move_tool(sandbox).invoke(
        {
            "source_path": "主题/旧名称.md",
            "destination_path": "主题/这是一个超过二十个Unicode字符的新文件名称用于回归.md",
        }
    )

    assert result.success is False
    assert "20 Unicode" in str(result.error)
    assert source.is_file()


@pytest.mark.asyncio
async def test_move_path_moves_complete_markdown_directory(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    source = sandbox / "context" / "旧主题"
    nested = source / "子主题"
    nested.mkdir(parents=True)
    (source / "description.md").write_text("# 旧主题\n", encoding="utf-8")
    (nested / "页面.md").write_text("# 页面\n", encoding="utf-8")
    tool = _move_tool(sandbox)

    result = await tool.invoke({"source_path": "旧主题", "destination_path": "新主题"})

    assert result.success is True
    assert not source.exists()
    assert (sandbox / "context" / "新主题" / "description.md").read_text(encoding="utf-8") == "# 旧主题\n"
    assert (sandbox / "context" / "新主题" / "子主题" / "页面.md").is_file()


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path regression")
@pytest.mark.asyncio
async def test_move_path_renames_deep_existing_windows_page(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    context = sandbox / "context"
    context.mkdir(parents=True)
    long_parts = tuple(f"移动层{index}-" + ("x" * 60) for index in range(4))
    source = context.joinpath(*long_parts, "旧页面.md")
    destination = context.joinpath(*long_parts, "新页面.md")
    context_pipeline._atomic_write(source, "# 页面\n\n长路径正文。\n".encode())
    assert len(str(source.parent)) > 260
    tool = _move_tool(sandbox)

    result = await tool.invoke(
        {
            "source_path": source.relative_to(context).as_posix(),
            "destination_path": destination.relative_to(context).as_posix(),
        }
    )

    assert result.success is True
    assert context_pipeline._path_is_file(destination)
    assert not context_pipeline._path_exists(source)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_path", "destination_path"),
    [
        ("主题/页面.md", "已有/页面.md"),
        ("主题/页面.md", "不存在/页面.md"),
        ("description.md", "根说明.md"),
        ("主题/页面.md", "description.md"),
        ("C:/outside.md", "主题/新页面.md"),
        ("../outside.md", "主题/新页面.md"),
        ("主题\\页面.md", "主题/新页面.md"),
        ("主题/页面.md", "主题/页面.txt"),
        ("主题/附件.txt", "主题/附件二.txt"),
    ],
)
async def test_move_path_rejects_unsafe_or_non_markdown_file_moves_without_changes(
    tmp_path: Path,
    source_path: str,
    destination_path: str,
) -> None:
    sandbox = tmp_path / "sandbox"
    context = sandbox / "context"
    topic = context / "主题"
    existing = context / "已有"
    topic.mkdir(parents=True)
    existing.mkdir()
    (context / "description.md").write_text("# Context\n", encoding="utf-8")
    (topic / "页面.md").write_text("# 页面\n", encoding="utf-8")
    (topic / "附件.txt").write_text("不可移动\n", encoding="utf-8")
    (existing / "页面.md").write_text("# 已有页面\n", encoding="utf-8")
    before = _tree_snapshot(sandbox)

    result = await _move_tool(sandbox).invoke({"source_path": source_path, "destination_path": destination_path})

    assert result.success is False
    assert _tree_snapshot(sandbox) == before


@pytest.mark.asyncio
async def test_move_path_rejects_directory_into_own_subtree_and_non_markdown_tree(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    context = sandbox / "context"
    clean = context / "纯文档目录"
    dirty = context / "混合目录"
    clean.mkdir(parents=True)
    dirty.mkdir()
    (clean / "description.md").write_text("# 纯文档目录\n", encoding="utf-8")
    (dirty / "description.md").write_text("# 混合目录\n", encoding="utf-8")
    (dirty / "配置.json").write_text("{}\n", encoding="utf-8")
    tool = _move_tool(sandbox)

    before = _tree_snapshot(sandbox)
    subtree = await tool.invoke({"source_path": "纯文档目录", "destination_path": "纯文档目录/子目录"})
    dirty_tree = await tool.invoke({"source_path": "混合目录", "destination_path": "新混合目录"})

    assert subtree.success is False
    assert dirty_tree.success is False
    assert _tree_snapshot(sandbox) == before


@pytest.mark.asyncio
async def test_move_path_rejects_symlink_before_changes(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    context = sandbox / "context"
    linked_tree = context / "链接目录"
    linked_tree.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")
    link = linked_tree / "链接.md"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc.__class__.__name__}")
    tool = _move_tool(sandbox)

    before = _tree_snapshot(sandbox)
    result = await tool.invoke({"source_path": "链接目录", "destination_path": "新链接目录"})

    assert result.success is False
    assert _tree_snapshot(sandbox) == before


@pytest.mark.asyncio
async def test_move_path_rejects_reported_reparse_before_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = tmp_path / "sandbox"
    reparse_tree = sandbox / "context" / "重解析目录"
    reparse_tree.mkdir(parents=True)
    reported_reparse = reparse_tree / "页面.md"
    reported_reparse.write_text("# 页面\n", encoding="utf-8")
    tool = _move_tool(sandbox)
    monkeypatch.setattr(file_tools_module, "is_reparse_point", lambda path: path == reported_reparse)

    before = _tree_snapshot(sandbox)
    result = await tool.invoke({"source_path": "重解析目录", "destination_path": "新重解析目录"})

    assert result.success is False
    assert _tree_snapshot(sandbox) == before


@pytest.mark.asyncio
async def test_move_path_enforces_configured_page_and_subdirectory_capacity(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    context = sandbox / "context"
    source = context / "来源"
    full_pages = context / "页面已满"
    full_children = context / "子目录已满"
    source.mkdir(parents=True)
    full_pages.mkdir()
    full_children.mkdir()
    (source / "页面.md").write_text("# 来源\n", encoding="utf-8")
    (full_pages / "description.md").write_text("# 页面已满\n", encoding="utf-8")
    (full_pages / "已有.md").write_text("# 已有\n", encoding="utf-8")
    (full_children / "description.md").write_text("# 子目录已满\n", encoding="utf-8")
    (full_children / "已有子目录").mkdir()
    (context / "description.md").write_text("# Context\n", encoding="utf-8")

    tool = _move_tool(sandbox, max_pages_per_directory=1, max_subdirectories_per_directory=1)
    page_result = await tool.invoke({"source_path": "来源/页面.md", "destination_path": "页面已满/新页面.md"})
    directory_result = await tool.invoke({"source_path": "来源", "destination_path": "子目录已满/新来源"})

    assert page_result.success is False
    assert directory_result.success is False
    assert (source / "页面.md").is_file()
    assert not (full_pages / "新页面.md").exists()
    assert not (full_children / "新来源").exists()


@pytest.mark.parametrize(
    ("ordinary_count", "guidance"),
    [
        (0, ""),
        (15, ""),
        (16, "该目录接近建议上限，优先考虑其他目录或拆分子目录。"),
        (19, "该目录接近建议上限，优先考虑其他目录或拆分子目录。"),
        (20, "该目录已达到建议上限，不要继续放入普通页面；请选择其他目录或建立子目录。"),
        (21, "该目录已达到建议上限，不要继续放入普通页面；请选择其他目录或建立子目录。"),
    ],
)
def test_directory_snapshot_reports_context_child_thresholds(
    tmp_path: Path,
    ordinary_count: int,
    guidance: str,
) -> None:
    sandbox = tmp_path / "sandbox"
    directory = sandbox / "context" / "主动上下文"
    directory.mkdir(parents=True)
    (directory / "description.md").write_text("# 主动上下文\n", encoding="utf-8")
    for index in range(ordinary_count):
        (directory / f"页面-{index:02d}.md").write_text("# 页面\n", encoding="utf-8")
    (directory / "子目录一").mkdir()
    (directory / "子目录二").mkdir()

    snapshot = file_tools_module._directory_snapshot(sandbox, directory)

    assert snapshot == {
        "relative_directory": "context/主动上下文",
        "direct_directory_count": 2,
        "direct_file_count": ordinary_count + 1,
        "ordinary_markdown_count": ordinary_count,
        "max_pages_per_directory": 20,
        "remaining_page_capacity": max(0, 20 - ordinary_count),
        "page_capacity_state": "full" if ordinary_count >= 20 else "near_limit" if ordinary_count >= 16 else "normal",
        "max_subdirectories_per_directory": 20,
        "remaining_subdirectory_capacity": 18,
        "subdirectory_capacity_state": "normal",
        "guidance": guidance,
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path regression")
def test_directory_snapshot_reports_deep_existing_windows_directory(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    context = sandbox / "context"
    context.mkdir(parents=True)
    long_parts = tuple(f"快照层{index}-" + ("x" * 60) for index in range(4))
    directory = context.joinpath(*long_parts)
    page = directory / "页面.md"
    child_description = directory / "子目录" / "description.md"
    context_pipeline._atomic_write(page, "# 页面\n".encode())
    context_pipeline._atomic_write(child_description, "# 子目录\n".encode())
    assert len(str(directory)) > 260

    snapshot = file_tools_module._directory_snapshot(
        sandbox,
        directory,
        max_pages_per_directory=3,
        max_subdirectories_per_directory=4,
    )

    assert snapshot["relative_directory"] == directory.relative_to(sandbox).as_posix()
    assert snapshot["ordinary_markdown_count"] == 1
    assert snapshot["direct_directory_count"] == 1
    assert snapshot["remaining_page_capacity"] == 2
    assert snapshot["remaining_subdirectory_capacity"] == 3


def test_directory_snapshot_distinguishes_context_root_and_non_context_directory(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    context = sandbox / "context"
    inputs = sandbox / "inputs"
    context.mkdir(parents=True)
    inputs.mkdir()
    (context / "description.md").write_text("# Context\n", encoding="utf-8")
    (context / "根层页面.md").write_text("# 不应存在\n", encoding="utf-8")
    (context / "主题").mkdir()
    for index in range(21):
        (inputs / f"输入-{index:02d}.md").write_text("# 输入\n", encoding="utf-8")

    root_snapshot = file_tools_module._directory_snapshot(sandbox, context)
    outside_snapshot = file_tools_module._directory_snapshot(sandbox, inputs)

    assert root_snapshot == {
        "relative_directory": "context",
        "direct_directory_count": 1,
        "direct_file_count": 2,
        "ordinary_markdown_count": 1,
        "max_pages_per_directory": 20,
        "remaining_page_capacity": 19,
        "page_capacity_state": "normal",
        "max_subdirectories_per_directory": 20,
        "remaining_subdirectory_capacity": 19,
        "subdirectory_capacity_state": "normal",
        "guidance": "这里只能保留 description.md 和目录。",
    }
    assert outside_snapshot["relative_directory"] == "inputs"
    assert outside_snapshot["ordinary_markdown_count"] == 21
    assert outside_snapshot["guidance"] == ""


def test_directory_snapshot_failure_is_safe_and_does_not_expose_absolute_path(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox-secret"
    sandbox.mkdir()

    snapshot = file_tools_module._directory_snapshot(sandbox, sandbox / "context" / "不存在")

    assert snapshot == {
        "relative_directory": "context/不存在",
        "stats_unavailable": True,
        "guidance": "目录统计暂不可用；请先使用 list_files 确认目录内容。",
    }
    assert str(sandbox) not in json.dumps(snapshot, ensure_ascii=False)


def _directory_callback_inputs(
    *,
    tool_name: str,
    tool_args: dict[str, object] | str,
    call_id: str = "call-directory-snapshot",
    success: bool = True,
) -> tuple[ToolCallInputs, ToolOutput, ToolMessage]:
    encoded_args = tool_args if isinstance(tool_args, str) else json.dumps(tool_args, ensure_ascii=False)
    tool_call = ToolCall(id=call_id, type="function", name=tool_name, arguments=encoded_args)
    result = ToolOutput(success=success, data={"original": True}, error=None if success else "failed")
    message = ToolMessage(content="original tool result", tool_call_id=call_id)
    return (
        ToolCallInputs(
            tool_call=tool_call,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=result,
            tool_msg=message,
        ),
        result,
        message,
    )


def _directory_callback_state() -> dict[str, object]:
    return {"seen_directories": set(), "snapshot_lock": asyncio.Lock()}


@pytest.mark.asyncio
async def test_after_tool_callback_mutates_existing_result_and_message_without_breaking_continuity(
    tmp_path: Path,
) -> None:
    sandbox = tmp_path / "sandbox"
    directory = sandbox / "context" / "主动上下文"
    directory.mkdir(parents=True)
    (directory / "description.md").write_text("# 主动上下文\n", encoding="utf-8")
    inputs, result, message = _directory_callback_inputs(
        tool_name="list_files",
        tool_args={"path": "context/主动上下文"},
    )
    assistant = AssistantMessage(content="", tool_calls=[inputs.tool_call])
    messages = [assistant, message]
    before_ids = [id(item) for item in messages]
    result_id = id(result)
    message_id = id(message)
    tool_call_id = message.tool_call_id

    await agent_support._after_tool_call_directory_snapshot(
        cast(Any, SimpleNamespace(inputs=inputs)),
        state=_directory_callback_state(),
        sandbox=sandbox,
    )

    assert id(inputs.tool_result) == result_id
    assert id(inputs.tool_msg) == message_id
    assert [id(item) for item in messages] == before_ids
    assert len(messages) == 2
    assert message.tool_call_id == tool_call_id
    assert result.data["directory_snapshot"]["relative_directory"] == "context/主动上下文"
    assert "[directory_snapshot]" in cast(str, message.content)
    assert '"relative_directory": "context/主动上下文"' in cast(str, message.content)
    agent_support.validate_personal_context_messages(messages)


@pytest.mark.asyncio
async def test_snapshot_annotation_preserves_multi_tool_group_through_trim_and_repair(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    first = sandbox / "context" / "主题一"
    second = sandbox / "context" / "主题二"
    first.mkdir(parents=True)
    second.mkdir()
    for directory in (first, second):
        (directory / "description.md").write_text("# 说明\n", encoding="utf-8")
    first_inputs, _, first_message = _directory_callback_inputs(
        tool_name="list_files",
        tool_args={"path": "context/主题一"},
        call_id="call-first",
    )
    second_inputs, _, second_message = _directory_callback_inputs(
        tool_name="list_files",
        tool_args={"path": "context/主题二"},
        call_id="call-second",
    )
    assistant = AssistantMessage(content="", tool_calls=[first_inputs.tool_call, second_inputs.tool_call])
    messages: list[object] = [UserMessage(content="organize"), assistant, first_message, second_message]
    original_ids = [id(item) for item in messages]
    state = _directory_callback_state()

    for inputs in (first_inputs, second_inputs):
        await agent_support._after_tool_call_directory_snapshot(
            cast(Any, SimpleNamespace(inputs=inputs)),
            state=state,
            sandbox=sandbox,
        )

    assert [id(item) for item in messages] == original_ids
    assert [first_message.tool_call_id, second_message.tool_call_id] == ["call-first", "call-second"]
    agent_support.validate_personal_context_messages(messages)
    trimmed = agent_support.trim_personal_context_messages(messages, budget=3)
    assert trimmed == [assistant, first_message, second_message]
    repair_history = [*messages, agent_support._repair_message(["待整理/example.md [normal_page_in_fallback]"])]
    agent_support.validate_personal_context_messages(repair_history)
    assert isinstance(repair_history[-1], UserMessage)


@pytest.mark.asyncio
async def test_after_tool_callback_attaches_every_list_snapshot_but_deduplicates_file_parent(
    tmp_path: Path,
) -> None:
    sandbox = tmp_path / "sandbox"
    first = sandbox / "context" / "主题一"
    second = sandbox / "context" / "主题二"
    first.mkdir(parents=True)
    second.mkdir()
    for directory in (first, second):
        (directory / "description.md").write_text("# 说明\n", encoding="utf-8")
        (directory / "页面.md").write_text("# 页面\n", encoding="utf-8")
    state = _directory_callback_state()

    for tool_args in ({"path": "context/主题一"}, json.dumps({"path": "context/主题一"})):
        inputs, result, _ = _directory_callback_inputs(tool_name="list_files", tool_args=tool_args)
        await agent_support._after_tool_call_directory_snapshot(
            cast(Any, SimpleNamespace(inputs=inputs)), state=state, sandbox=sandbox
        )
        assert result.data["directory_snapshot"]["relative_directory"] == "context/主题一"

    observations: list[tuple[str, str, bool]] = []
    for tool_name, file_path in (
        ("read_file", "context/主题一/页面.md"),
        ("write_file", str(first / "页面.md")),
        ("edit_file", "context/主题二/页面.md"),
    ):
        inputs, result, message = _directory_callback_inputs(
            tool_name=tool_name,
            tool_args={"file_path": file_path},
            call_id=f"call-{tool_name}",
        )
        original_content = message.content
        await agent_support._after_tool_call_directory_snapshot(
            cast(Any, SimpleNamespace(inputs=inputs)), state=state, sandbox=sandbox
        )
        observations.append((tool_name, cast(str, message.content), "directory_snapshot" in result.data))
        if "directory_snapshot" not in result.data:
            assert message.content == original_content

    assert observations[0][2] is False
    assert observations[1][2] is False
    assert observations[2][2] is True
    assert "context/主题二" in observations[2][1]


@pytest.mark.asyncio
async def test_after_tool_callback_attaches_source_and_destination_snapshots_for_move(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    source_parent = sandbox / "context" / "旧主题"
    destination_parent = sandbox / "context" / "新主题"
    source_parent.mkdir(parents=True)
    destination_parent.mkdir()
    for directory in (source_parent, destination_parent):
        (directory / "description.md").write_text("# 说明\n", encoding="utf-8")
    source_page = source_parent / "页面.md"
    destination_page = destination_parent / "页面.md"
    source_page.write_text("# 页面\n", encoding="utf-8")
    source_page.replace(destination_page)
    state = _directory_callback_state()
    inputs, result, message = _directory_callback_inputs(
        tool_name="move_path",
        tool_args={"source_path": "旧主题/页面.md", "destination_path": "新主题/页面.md"},
        call_id="call-move",
    )

    await agent_support._after_tool_call_directory_snapshot(
        cast(Any, SimpleNamespace(inputs=inputs)), state=state, sandbox=sandbox
    )

    snapshots = result.data["directory_snapshots"]
    assert [snapshot["relative_directory"] for snapshot in snapshots] == [
        "context/旧主题",
        "context/新主题",
    ]
    assert [snapshot["ordinary_markdown_count"] for snapshot in snapshots] == [0, 1]
    assert "[directory_snapshots]" in cast(str, message.content)

    read_inputs, read_result, _ = _directory_callback_inputs(
        tool_name="read_file",
        tool_args={"file_path": "context/新主题/页面.md"},
        call_id="call-read-after-move",
    )
    await agent_support._after_tool_call_directory_snapshot(
        cast(Any, SimpleNamespace(inputs=read_inputs)), state=state, sandbox=sandbox
    )
    assert "directory_snapshot" not in read_result.data


@pytest.mark.asyncio
async def test_after_tool_callback_uses_shared_capacity_limits_in_same_tool_result(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    directory = sandbox / "context" / "容量主题"
    directory.mkdir(parents=True)
    (directory / "description.md").write_text("# 容量主题\n", encoding="utf-8")
    (directory / "页面一.md").write_text("# 页面一\n", encoding="utf-8")
    (directory / "页面二.md").write_text("# 页面二\n", encoding="utf-8")
    (directory / "子主题").mkdir()
    inputs, result, message = _directory_callback_inputs(
        tool_name="list_files",
        tool_args={"path": "context/容量主题"},
    )

    await agent_support._after_tool_call_directory_snapshot(
        cast(Any, SimpleNamespace(inputs=inputs)),
        state=_directory_callback_state(),
        sandbox=sandbox,
        max_pages_per_directory=2,
        max_subdirectories_per_directory=1,
    )

    snapshot = result.data["directory_snapshot"]
    assert snapshot["max_pages_per_directory"] == 2
    assert snapshot["page_capacity_state"] == "full"
    assert snapshot["max_subdirectories_per_directory"] == 1
    assert snapshot["subdirectory_capacity_state"] == "full"
    assert "达到上限" in snapshot["guidance"]
    assert "directory_snapshot" in message.content


@pytest.mark.asyncio
async def test_after_tool_callback_ignores_failed_or_unsupported_tool_results(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    state = _directory_callback_state()

    for tool_name, success in (("list_files", False), ("grep", True)):
        inputs, result, message = _directory_callback_inputs(
            tool_name=tool_name,
            tool_args={"path": "."},
            call_id=f"call-{tool_name}",
            success=success,
        )
        await agent_support._after_tool_call_directory_snapshot(
            cast(Any, SimpleNamespace(inputs=inputs)), state=state, sandbox=sandbox
        )
        assert result.data == {"original": True}
        assert message.content == "original tool result"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("authorization: Bearer TOP_SECRET", "authorization: [REDACTED]"),
        ('"authorization":"Bearer TOP_SECRET"', '"authorization":"[REDACTED]"'),
        ("Bearer TOP_SECRET", "Bearer [REDACTED]"),
        ("https://example.invalid/?access_token=TOP_SECRET", "https://example.invalid/"),
        ("https://example.invalid/path?foo=bar&x=1#frag", "https://example.invalid/path"),
        ("file://server/share/file.txt?token=TOP_SECRET#frag", "file://server/share/file.txt"),
        ("https://user:TOP_SECRET@example.invalid/path", "https://[REDACTED]@example.invalid/path"),
        (r"C:\Users\alice\foo.txt", "[PATH_REDACTED]"),
        ("/home/alice/foo.txt", "[PATH_REDACTED]"),
        ("/secret", "[PATH_REDACTED]"),
        ("/tmp", "[PATH_REDACTED]"),
        (r"\\server\share\foo.txt", "[PATH_REDACTED]"),
    ],
)
def test_validation_errors_redact_credentials_and_authorization(value: str, expected: str) -> None:
    result = agent_support._validation_errors([value])
    assert result and "TOP_SECRET" not in result[0]
    assert expected in result[0]


def _tool_message_group() -> list[object]:
    return [
        AssistantMessage(
            content="",
            tool_calls=[ToolCall(id="call-1", type="function", name="read", arguments="{}")],
        ),
        ToolMessage(content="ok", tool_call_id="call-1"),
    ]


def test_validate_message_continuity_accepts_complete_tool_group() -> None:
    messages = [UserMessage(content="read this"), *_tool_message_group()]
    agent_support.validate_personal_context_messages(messages)


@pytest.mark.parametrize(
    "messages",
    [
        [AssistantMessage(content="", tool_calls=[ToolCall(id="", type="function", name="read", arguments="{}")])],
        [
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(id="call-1", type="function", name="read", arguments="{}"),
                    ToolCall(id="call-1", type="function", name="read", arguments="{}"),
                ],
            ),
            ToolMessage(content="ok", tool_call_id="call-1"),
        ],
        [ToolMessage(content="orphan", tool_call_id="call-1")],
        [
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="call-1", type="function", name="read", arguments="{}")],
            ),
            UserMessage(content="inserted"),
            ToolMessage(content="ok", tool_call_id="call-1"),
        ],
        [
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="call-1", type="function", name="read", arguments="{}")],
            ),
        ],
    ],
)
def test_validate_personal_context_messages_rejects_broken_tool_group(messages: list[object]) -> None:
    with pytest.raises(Exception):
        agent_support.validate_personal_context_messages(messages)


def test_trim_personal_context_messages_keeps_tool_group_together() -> None:
    group = _tool_message_group()
    messages = [UserMessage(content="old"), *group, UserMessage(content="new")]
    kept = agent_support.trim_personal_context_messages(messages, budget=2)
    assert kept in ([UserMessage(content="new")], group, [*group, UserMessage(content="new")])
    assert not (
        any(getattr(item, "tool_calls", None) for item in kept)
        and not any(getattr(item, "tool_call_id", None) for item in kept)
    )


@pytest.mark.parametrize(
    ("model_name", "expected_budget", "expected_trigger", "expected_target"),
    [
        ("personal-context-unknown-model", 200_000, 90_000, 60_000),
        ("gpt-3.5-turbo", 16_385, 14_746, 9_830),
    ],
)
def test_context_processor_uses_core_round_compressor_and_model_context_window(
    model_name: str,
    expected_budget: int,
    expected_trigger: int,
    expected_target: int,
) -> None:
    model_client = ModelClientConfig(
        client_provider="OpenAI",
        api_key="mock-api-key",
        api_base="https://example.invalid/v1",
    )
    model_request = ModelRequestConfig(model=model_name, max_tokens=3)

    rail = agent_support._make_context_processor_rail(model_client, model_request)

    assert isinstance(rail, ContextProcessorRail)
    assert rail._preset is False
    assert len(rail._user_processors) == 1
    processor_name, config = rail._user_processors[0]
    assert processor_name == agent_support._PERSONAL_CONTEXT_ROUND_LEVEL_PROCESSOR_KEY
    assert type(config) is RoundLevelCompressorConfig
    assert config.trigger_context_ratio == pytest.approx(expected_trigger / expected_budget)
    assert config.target_total_tokens == expected_target
    assert config.keep_recent_messages == 6
    assert config.compression_call_max_tokens == 4_096
    assert config.model is model_request
    assert config.model_client is model_client
    assert config.target_total_tokens != model_request.max_tokens

    class ReactConfig:
        model_config_obj = model_request
        model_client_config = model_client
        context_processors: list[tuple[str, object]] = []
        context_engine_config = None

    class Agent:
        react_agent = type("ReactAgent", (), {"_config": ReactConfig()})()

    rail.init(cast(Any, Agent()))
    assert Agent.react_agent._config.context_processors == [(processor_name, config)]
    assert ContextEngine._PROCESSOR_MAP["RoundLevelCompressor"] is RoundLevelCompressor


@pytest.mark.asyncio
async def test_context_processor_uses_core_compressor_after_forked_registry_pollution() -> None:
    forked.deactivate()
    assert ContextEngine._PROCESSOR_MAP["RoundLevelCompressor"] is RoundLevelCompressor
    model_client = ModelClientConfig(
        client_provider="OpenAI",
        api_key="mock-api-key",
        api_base="https://example.invalid/v1",
    )
    model_request = ModelRequestConfig(model="personal-context-unknown-model", max_tokens=3)

    class ReactConfig:
        model_config_obj = model_request
        model_client_config = model_client
        context_processors: list[tuple[str, object]] = []
        context_engine_config = None

    class PollutingAgent:
        react_agent = type("ReactAgent", (), {"_config": ReactConfig()})()

    ContextProcessorRail(preset=True).init(cast(Any, PollutingAgent()))
    polluted_processor = ContextEngine._PROCESSOR_MAP["RoundLevelCompressor"]
    assert polluted_processor is not RoundLevelCompressor
    try:
        rail = agent_support._make_context_processor_rail(model_client, model_request)
        processor_name, config = rail._user_processors[0]
        context = await ContextEngine().create_context(processors=[(processor_name, config)])
        processor = cast(Any, context)._processors[0]

        assert ContextEngine._PROCESSOR_MAP["RoundLevelCompressor"] is polluted_processor
        assert type(processor) is RoundLevelCompressor
        assert type(processor._config) is RoundLevelCompressorConfig
        assert processor._config.target_total_tokens == 60_000
        assert processor._config.compression_call_max_tokens == 4_096
    finally:
        forked.deactivate()


def test_context_processor_internal_registration_is_idempotent_under_concurrency() -> None:
    model_client = ModelClientConfig(
        client_provider="OpenAI",
        api_key="mock-api-key",
        api_base="https://example.invalid/v1",
    )
    model_request = ModelRequestConfig(model="personal-context-unknown-model")

    with ThreadPoolExecutor(max_workers=8) as executor:
        rails = list(
            executor.map(
                lambda _index: agent_support._make_context_processor_rail(model_client, model_request),
                range(32),
            )
        )

    processor_key = agent_support._PERSONAL_CONTEXT_ROUND_LEVEL_PROCESSOR_KEY
    assert ContextEngine._PROCESSOR_MAP[processor_key] is RoundLevelCompressor
    assert all(rail._user_processors[0][0] == processor_key for rail in rails)


@pytest.mark.asyncio
async def test_real_factory_cleanup_unregisters_every_explicit_and_default_rail(tmp_path: Path) -> None:
    model_client = ModelClientConfig(
        client_provider="OpenAI",
        api_key="mock-api-key",
        api_base="https://example.invalid/v1",
    )
    model_request = ModelRequestConfig(model="personal-context-unknown-model")
    model = Model(model_client_config=model_client, model_config=model_request)
    context_rail = agent_support._make_context_processor_rail(model_client, model_request)
    agent, rails = agent_support._make_agent(model, tmp_path, context_rail)

    assert all(type(rail).__name__ != "SysOperationRail" for rail in rails)
    assert {card.name for card in cast(Any, agent).ability_manager.list()} == {
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "list_files",
        "grep",
        "move_path",
    }
    await cast(Any, agent).ensure_initialized()
    configured = cast(Any, agent).configured_rails()
    assert configured == rails
    assert sum(isinstance(rail, SecurityRail) for rail in rails) == 1
    assert sum(isinstance(rail, ToolCallResilienceRail) for rail in rails) == 1

    await agent_support._cleanup_runtime(agent, rails, None, [], None)
    assert cast(Any, agent).configured_rails() == []
    assert cast(Any, agent).ability_manager.list() == []


@pytest.mark.asyncio
async def test_real_inner_callback_manager_registers_named_callbacks_and_cleans_exactly(tmp_path: Path) -> None:
    model_client = ModelClientConfig(
        client_provider="OpenAI",
        api_key="mock-api-key",
        api_base="https://example.invalid/v1",
    )
    model_request = ModelRequestConfig(model="personal-context-unknown-model")
    model = Model(model_client_config=model_client, model_config=model_request)
    context_rail = agent_support._make_context_processor_rail(model_client, model_request)
    agent, rails = agent_support._make_agent(model, tmp_path, context_rail)
    react_agent = cast(Any, agent).react_agent
    manager = react_agent.agent_callback_manager
    callbacks: list[tuple[AgentCallbackEvent, object]] = []
    state: dict[str, Any] | None = None
    registration_error: BaseException | None = None
    registered_callbacks: list[object] = []
    leaked_callbacks: list[object] = []

    try:
        try:
            callbacks, state = await agent_support._register_agent_callbacks(agent, tmp_path)
        except BaseException as exc:
            registration_error = exc
        if registration_error is None:
            for event, callback in callbacks:
                event_name = manager._get_agent_event(event)
                infos = Runner.callback_framework._callbacks[event_name]
                assert [info.callback for info in infos] == [callback]
                registered_callbacks.append(callback)
        await agent_support._cleanup_runtime(agent, rails, None, callbacks, state)
        for event in (
            AgentCallbackEvent.BEFORE_MODEL_CALL,
            AgentCallbackEvent.AFTER_MODEL_CALL,
            AgentCallbackEvent.AFTER_TOOL_CALL,
            AgentCallbackEvent.AFTER_REACT_ITERATION,
        ):
            event_name = manager._get_agent_event(event)
            leaked_callbacks.extend(info.callback for info in Runner.callback_framework._callbacks[event_name])
    finally:
        # Keep the test process isolated when exercising the known broken
        # registration path that appends before reading callback.__name__.
        for event in AgentCallbackEvent:
            event_name = manager._get_agent_event(event)
            Runner.callback_framework._callbacks.pop(event_name, None)

    assert registration_error is None
    assert [callback.__name__ for callback in registered_callbacks] == [
        "personal_context_before_model_call_callback",
        "personal_context_after_model_call_callback",
        "personal_context_after_tool_call_callback",
        "personal_context_after_react_iteration_callback",
    ]
    assert all(asyncio.iscoroutinefunction(callback) for callback in registered_callbacks)
    assert leaked_callbacks == []


@pytest.mark.asyncio
async def test_real_inner_callback_manager_rolls_back_callback_written_before_registration_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_client = ModelClientConfig(
        client_provider="OpenAI",
        api_key="mock-api-key",
        api_base="https://example.invalid/v1",
    )
    model_request = ModelRequestConfig(model="personal-context-unknown-model")
    model = Model(model_client_config=model_client, model_config=model_request)
    context_rail = agent_support._make_context_processor_rail(model_client, model_request)
    agent, rails = agent_support._make_agent(model, tmp_path, context_rail)
    manager = cast(Any, agent).react_agent.agent_callback_manager
    original_info = Runner.callback_framework.logger.info
    failed_after_write = False

    def fail_after_second_callback_write(message: object, *args: object, **kwargs: object) -> None:
        nonlocal failed_after_write
        if "Registered callback" in str(message) and "personal_context_after_model_call_callback" in str(message):
            if not failed_after_write:
                failed_after_write = True
                raise RuntimeError("registration failed after write")
        original_info(message, *args, **kwargs)

    monkeypatch.setattr(Runner.callback_framework.logger, "info", fail_after_second_callback_write)
    try:
        with pytest.raises(RuntimeError, match="registration failed after write"):
            await agent_support._register_agent_callbacks(agent, tmp_path)
        assert failed_after_write is True
        for event in (
            AgentCallbackEvent.BEFORE_MODEL_CALL,
            AgentCallbackEvent.AFTER_MODEL_CALL,
            AgentCallbackEvent.AFTER_TOOL_CALL,
            AgentCallbackEvent.AFTER_REACT_ITERATION,
        ):
            assert manager.has_hooks(event) is False
    finally:
        await agent_support._cleanup_runtime(agent, rails, None, [], None)


@pytest.mark.parametrize("tool_count", [1, 2])
def test_round_compressor_never_splits_tool_group_at_compression_boundary(tool_count: int) -> None:
    calls = [ToolCall(id=f"call-{index}", type="function", name="read", arguments="{}") for index in range(tool_count)]
    group: list[BaseMessage] = [
        AssistantMessage(content="", tool_calls=calls),
        *[ToolMessage(content=f"result-{index}", tool_call_id=call.id) for index, call in enumerate(calls)],
    ]
    messages = [UserMessage(content="old request"), *group, UserMessage(content="recent")]
    compressor = RoundLevelCompressor(RoundLevelCompressorConfig())

    split_boundary = len(group) - 1
    split_targets = compressor._build_raw_targets(messages, compress_end=split_boundary)
    split_messages = [message for target in split_targets for message in target.messages]
    assert all(all(message is not grouped for message in split_messages) for grouped in group)

    complete_targets = compressor._build_raw_targets(messages, compress_end=len(group))
    complete_messages = [message for target in complete_targets for message in target.messages]
    assert all(any(message is grouped for message in complete_messages) for grouped in group)


@pytest.mark.asyncio
@pytest.mark.parametrize("compression_path", ["initial", "recursive", "aggressive", "hard_truncation"])
async def test_add_compression_is_disabled_until_multi_tool_group_closes(
    monkeypatch: pytest.MonkeyPatch,
    compression_path: str,
) -> None:
    context = await ContextEngine().create_context(
        processors=[("RoundLevelCompressor", RoundLevelCompressorConfig())],
        history_messages=[UserMessage(content="old")],
    )
    processor = cast(Any, context)._processors[0]
    compression_calls: list[str] = []

    async def always_trigger(*_args: object, **_kwargs: object) -> bool:
        return True

    async def destructive_add_compression(
        model_context: object,
        _messages: object,
        **_kwargs: object,
    ) -> tuple[ContextEvent, list[BaseMessage]]:
        compression_calls.append(compression_path)
        cast(Any, model_context).set_messages([UserMessage(content=f"{compression_path} summary")])
        return ContextEvent(event_type="RoundLevelCompressor"), []

    monkeypatch.setattr(processor, "trigger_add_messages", always_trigger)
    monkeypatch.setattr(processor, "on_add_messages", destructive_add_compression)
    state: dict[str, Any] = {}
    callback_context = cast(Any, SimpleNamespace(context=context))
    after_model = getattr(agent_support, "_after_model_call_context_compression", None)
    if after_model is not None:
        await after_model(callback_context, state=state)

    assistant = AssistantMessage(
        content="",
        tool_calls=[
            ToolCall(id="call-1", type="function", name="read", arguments="{}"),
            ToolCall(id="call-2", type="function", name="grep", arguments="{}"),
        ],
    )
    first_result = ToolMessage(content="one", tool_call_id="call-1")
    second_result = ToolMessage(content="two", tool_call_id="call-2")
    await context.add_messages(assistant)
    await context.add_messages(first_result)

    # This is a real partial ReAct history: a later call-2 result must still be
    # able to close the group, so none of the ADD compression paths may run.
    assert compression_calls == []
    assert context.get_messages()[-2:] == [assistant, first_result]

    await context.add_messages(second_result)
    messages = context.get_messages()
    assert compression_calls == []
    agent_support.validate_personal_context_messages(messages)

    before_model = getattr(agent_support, "_before_model_call_context_compression", None)
    assert before_model is not None
    await before_model(callback_context, state=state)
    assert await processor.trigger_add_messages(context, [UserMessage(content="next")]) is True


@pytest.mark.asyncio
async def test_add_compression_stays_disabled_for_final_answer_and_first_in_place_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await ContextEngine().create_context(
        processors=[("RoundLevelCompressor", RoundLevelCompressorConfig())],
        history_messages=[UserMessage(content="request")],
    )
    processor = cast(Any, context)._processors[0]
    compression_calls: list[str] = []

    async def always_trigger(*_args: object, **_kwargs: object) -> bool:
        return True

    async def observe_add(
        _context: object,
        messages: list[BaseMessage],
        **_kwargs: object,
    ) -> tuple[ContextEvent, list[BaseMessage]]:
        compression_calls.append(type(messages[0]).__name__)
        return ContextEvent(event_type="RoundLevelCompressor"), messages

    monkeypatch.setattr(processor, "trigger_add_messages", always_trigger)
    monkeypatch.setattr(processor, "on_add_messages", observe_add)
    state: dict[str, Any] = {}
    callback_context = cast(Any, SimpleNamespace(context=context))
    after_model = getattr(agent_support, "_after_model_call_context_compression", None)
    if after_model is not None:
        await after_model(callback_context, state=state)

    await context.add_messages(AssistantMessage(content="invalid final answer"))
    await context.add_messages(UserMessage(content="repair in place"))
    assert compression_calls == []

    before_model = getattr(agent_support, "_before_model_call_context_compression", None)
    assert before_model is not None
    await before_model(callback_context, state=state)
    assert await processor.trigger_add_messages(context, [UserMessage(content="next")]) is True


@pytest.mark.asyncio
async def test_real_model_usage_triggers_active_compression_only_after_complete_react_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await ContextEngine().create_context(
        processors=[("RoundLevelCompressor", RoundLevelCompressorConfig())],
        history_messages=[UserMessage(content="request")],
    )
    processor = cast(Any, context)._processors[0]
    calls: list[tuple[list[str], str]] = []

    async def compress_context(*, processor_types: list[str], **kwargs: object) -> str:
        agent_support.validate_personal_context_messages(context.get_messages())
        calls.append((processor_types, cast(str, kwargs["compression_trigger"])))
        return "compressed"

    monkeypatch.setattr(context, "compress_context", compress_context)
    state: dict[str, Any] = {}
    response = AssistantMessage(content="", usage_metadata={"input_tokens": 90_001, "output_tokens": 1})
    callback_context = cast(
        Any,
        SimpleNamespace(
            context=context,
            inputs=ModelCallInputs(response=response, tools=[]),
        ),
    )
    await agent_support._after_model_call_context_compression(callback_context, state=state)
    assistant = AssistantMessage(
        content="",
        tool_calls=[ToolCall(id="call-1", type="function", name="read", arguments="{}")],
    )
    await context.add_messages(assistant)
    assert calls == []
    await context.add_messages(ToolMessage(content="done", tool_call_id="call-1"))

    await agent_support._after_react_iteration_context_compression(callback_context, state=state)

    assert calls == [([processor.processor_type()], "personal_context_complete_react_iteration")]
    assert state["last_model_input_tokens"] == 0


@pytest.mark.asyncio
async def test_real_model_usage_below_threshold_does_not_force_compression(monkeypatch: pytest.MonkeyPatch) -> None:
    context = await ContextEngine().create_context(
        processors=[("RoundLevelCompressor", RoundLevelCompressorConfig())],
        history_messages=[UserMessage(content="request")],
    )
    calls = 0

    async def compress_context(**_kwargs: object) -> str:
        nonlocal calls
        calls += 1
        return "compressed"

    monkeypatch.setattr(context, "compress_context", compress_context)
    state: dict[str, Any] = {"last_model_input_tokens": 89_999}
    callback_context = cast(Any, SimpleNamespace(context=context, inputs=ModelCallInputs(tools=[])))

    await agent_support._after_react_iteration_context_compression(callback_context, state=state)

    assert calls == 0


@pytest.mark.asyncio
async def test_iteration_reminder_fires_after_complete_tool_group_at_20_40_60_80_only() -> None:
    pushed: list[str] = []

    class Context:
        def push_steering(self, message: str) -> None:
            pushed.append(message)

    state = {"turn_count": 0}
    for _ in range(100):
        await agent_support._after_react_iteration_reminder(cast(Any, Context()), state=state)

    assert [int(re.search(r"executed (\d+) ReAct", message).group(1)) for message in pushed] == [20, 40, 60, 80]
    assert all(message.startswith("[message from PersonalContext system]\n") for message in pushed)
    assert all("100 ReAct" not in message for message in pushed)

    history: list[object] = [
        AssistantMessage(
            content="",
            tool_calls=[
                ToolCall(id="call-1", type="function", name="read", arguments="{}"),
                ToolCall(id="call-2", type="function", name="grep", arguments="{}"),
            ],
        ),
        ToolMessage(content="one", tool_call_id="call-1"),
        ToolMessage(content="two", tool_call_id="call-2"),
        UserMessage(content=pushed[0]),
    ]
    agent_support.validate_personal_context_messages(history)
    assert isinstance(history[3], UserMessage)


class _FakeContext:
    def __init__(self, messages: list[BaseMessage]) -> None:
        self.messages = messages

    def get_messages(self) -> list[BaseMessage]:
        return list(self.messages)

    def pop_messages(self, size: int = 1) -> list[BaseMessage]:
        popped = self.messages[-size:]
        del self.messages[-size:]
        return popped


def test_discard_length_tail_keeps_completed_tool_pair() -> None:
    tool_call = ToolCall(id="call-1", type="function", name="write_file", arguments="{}")
    truncated = AssistantMessage(content="partial", finish_reason="length")
    history: list[BaseMessage] = [
        UserMessage(content="build"),
        AssistantMessage(content="", tool_calls=[tool_call]),
        ToolMessage(content="written", tool_call_id="call-1"),
        UserMessage(content="continue"),
        truncated,
    ]
    expected = history[:-1]
    context = _FakeContext(history)
    agent = SimpleNamespace(_get_context_or_error=lambda **_kwargs: context)

    assert agent_support._discard_length_limited_tail(agent, "session", truncated) is True
    assert context.get_messages() == expected
    agent_support.validate_personal_context_messages(context.get_messages())


@pytest.mark.parametrize("finish_reason", ["stop", "tool_calls", "null"])
def test_discard_length_tail_rejects_non_length_result(finish_reason: str) -> None:
    result = AssistantMessage(content="done", finish_reason=finish_reason)
    context = _FakeContext([UserMessage(content="build"), result])
    original = context.get_messages()
    agent = SimpleNamespace(_get_context_or_error=lambda **_kwargs: context)

    assert agent_support._discard_length_limited_tail(agent, "session", result) is False
    assert context.get_messages() == original


def test_discard_length_tail_rejects_non_tail_result() -> None:
    result = AssistantMessage(content="partial", finish_reason="max_tokens")
    context = _FakeContext([UserMessage(content="build"), result, AssistantMessage(content="later")])
    original = context.get_messages()
    agent = SimpleNamespace(_get_context_or_error=lambda **_kwargs: context)

    assert agent_support._discard_length_limited_tail(agent, "session", result) is False
    assert context.get_messages() == original


def test_discard_length_tail_rejects_unclosed_tool_history() -> None:
    tool_call = ToolCall(id="call-1", type="function", name="write_file", arguments="{}")
    result = AssistantMessage(content="partial", finish_reason="length")
    context = _FakeContext(
        [
            UserMessage(content="build"),
            AssistantMessage(content="", tool_calls=[tool_call]),
            result,
        ]
    )
    original = context.get_messages()
    agent = SimpleNamespace(_get_context_or_error=lambda **_kwargs: context)

    assert agent_support._discard_length_limited_tail(agent, "session", result) is False
    assert context.get_messages() == original


class _FakeModel:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeReactAgent:
    def __init__(self, events: list[tuple[str, Any]]) -> None:
        self._events = events
        self.agent_callback_manager = self
        self.registered_callbacks: list[tuple[AgentCallbackEvent, object]] = []
        self.unregistered_callbacks: list[tuple[AgentCallbackEvent, object]] = []

    async def register_callback(
        self,
        event: AgentCallbackEvent,
        callback: object,
        priority: int = 100,
    ) -> None:
        self.registered_callbacks.append((event, callback))
        self._events.append(("register_callback", (event, callback, priority)))

    async def unregister(self, event: AgentCallbackEvent, callback: object) -> None:
        self.unregistered_callbacks.append((event, callback))
        self._events.append(("unregister_callback", (event, callback)))

    async def clear_session(self, session_id: str) -> None:
        self._events.append(("clear_session", session_id))


class _FakeAbilityManager:
    def __init__(self, events: list[tuple[str, Any]]) -> None:
        self._events = events

    def teardown_tools(self) -> None:
        self._events.append(("tool_teardown", None))


class _FakeAgent:
    def __init__(self, events: list[tuple[str, Any]], outputs: list[object]) -> None:
        self.card = object()
        self.react_agent = _FakeReactAgent(events)
        self.ability_manager = _FakeAbilityManager(events)
        self._events = events
        self._outputs = outputs
        self.invocations: list[tuple[object, object]] = []
        self.seeded_context: list[BaseMessage] | None = None
        self.context = _FakeContext([])

    @property
    def context_history(self) -> list[BaseMessage]:
        return self.context.messages

    @context_history.setter
    def context_history(self, messages: list[BaseMessage]) -> None:
        self.context.messages = messages

    async def create_new_context_engine(self, *, session_id: str, messages: list[BaseMessage]) -> str:
        self.seeded_context = list(messages)
        self.context_history = list(messages)
        self._events.append(("seed_context", (session_id, list(messages))))
        return session_id

    def get_current_context(self, *, session_id: str) -> list[BaseMessage]:
        self._events.append(("get_context", session_id))
        return list(self.context_history)

    def _get_context_or_error(self, *, session_id: str) -> _FakeContext:
        self._events.append(("get_context_object", session_id))
        return self.context

    async def invoke(self, request: object, *, session: object) -> object:
        self.invocations.append((request, session))
        self._events.append(("invoke", request))
        output = self._outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        query = request.get("query") if isinstance(request, dict) else ""
        if query:
            self.context_history.append(UserMessage(content=str(query)))
        if isinstance(output, AssistantMessage):
            self.context_history.append(output)
        return output


class _FakeSession:
    def __init__(self, session_id: str, events: list[tuple[str, Any]]) -> None:
        self.session_id = session_id
        self._events = events

    def get_session_id(self) -> str:
        return self.session_id

    async def pre_run(self, **_kwargs: object) -> None:
        self._events.append(("pre_run", self.session_id))


def _patch_agent_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    outputs_by_agent: list[list[object]],
    events: list[tuple[str, Any]],
) -> dict[str, Any]:
    created: dict[str, Any] = {
        "agents": [],
        "sessions": [],
        "processor_rails": [],
        "security_rails": [],
        "resilience_rails": [],
        "configs": [],
    }

    def fake_create_deep_agent(model: object, **kwargs: object) -> _FakeAgent:
        events.append(("create_agent", kwargs))
        agent = _FakeAgent(events, outputs_by_agent.pop(0))
        created["agents"].append(agent)
        return agent

    def fake_create_session(*, session_id: str, card: object, close_stream_on_post_run: bool) -> _FakeSession:
        events.append(("create_session", (session_id, card, close_stream_on_post_run)))
        session = _FakeSession(session_id, events)
        created["sessions"].append(session)
        return session

    def fake_context_processor_rail(model_client: object, model_request: object) -> object:
        events.append(("context_processor_rail", (model_client, model_request)))

        class Rail:
            def uninit(self, agent: object) -> None:
                events.append(("context_processor_rail_uninit", agent))

        rail = Rail()
        created["processor_rails"].append(rail)
        return rail

    def fake_security_rail() -> object:
        events.append(("security_rail", None))

        class Rail:
            def uninit(self, agent: object) -> None:
                events.append(("security_rail_uninit", agent))

        rail = Rail()
        created["security_rails"].append(rail)
        return rail

    def fake_resilience_rail() -> object:
        events.append(("resilience_rail", None))

        class Rail:
            def uninit(self, agent: object) -> None:
                events.append(("resilience_rail_uninit", agent))

        rail = Rail()
        created["resilience_rails"].append(rail)
        return rail

    monkeypatch.setattr(agent_support, "Model", _FakeModel)
    monkeypatch.setattr(agent_support, "create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr(agent_support, "create_agent_session", fake_create_session)
    monkeypatch.setattr(agent_support, "SecurityRail", fake_security_rail)
    monkeypatch.setattr(agent_support, "ToolCallResilienceRail", fake_resilience_rail)
    monkeypatch.setattr(agent_support, "_make_context_processor_rail", fake_context_processor_rail)
    return created


@pytest.mark.asyncio
async def test_run_personal_context_agent_creates_unique_session_and_returns_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(
        monkeypatch, outputs_by_agent=[[AssistantMessage(content="  result  ")]], events=events
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    output = await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=[UserMessage(content="summarize")],
        validate_result=lambda _text, _path: [],
    )

    assert output == "result"
    assert len(created["agents"]) == 1
    assert len(created["sessions"]) == 1
    session = created["sessions"][0]
    assert session.session_id.startswith("personal-context-agent-")
    assert len(session.session_id) > len("personal-context-agent-")
    assert [event[0] for event in events].count("clear_session") == 1
    assert [event[0] for event in events].count("tool_teardown") == 1
    assert [event[0] for event in events].count("context_processor_rail_uninit") == 1
    assert [event[0] for event in events].count("security_rail_uninit") == 1
    assert [event[0] for event in events].count("resilience_rail_uninit") == 1
    react_agent = created["agents"][0].react_agent
    assert [event for event, _ in react_agent.registered_callbacks] == [
        AgentCallbackEvent.BEFORE_MODEL_CALL,
        AgentCallbackEvent.AFTER_MODEL_CALL,
        AgentCallbackEvent.AFTER_TOOL_CALL,
        AgentCallbackEvent.AFTER_REACT_ITERATION,
    ]
    assert [callback.__name__ for _, callback in react_agent.registered_callbacks] == [
        "personal_context_before_model_call_callback",
        "personal_context_after_model_call_callback",
        "personal_context_after_tool_call_callback",
        "personal_context_after_react_iteration_callback",
    ]
    assert all(asyncio.iscoroutinefunction(callback) for _, callback in react_agent.registered_callbacks)
    assert react_agent.unregistered_callbacks == react_agent.registered_callbacks


@pytest.mark.asyncio
async def test_length_stop_continues_same_agent_before_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(
        monkeypatch,
        outputs_by_agent=[
            [
                AssistantMessage(content="partial", finish_reason="length"),
                AssistantMessage(content="done", finish_reason="stop"),
            ]
        ],
        events=events,
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    validation_calls: list[str] = []
    messages = [UserMessage(content="build context")]

    output = await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=messages,
        validate_result=lambda text, _path: validation_calls.append(text) or [],
    )

    agent = created["agents"][0]
    assert output == "done"
    assert len(created["agents"]) == 1
    assert len(agent.invocations) == 2
    assert agent.invocations[0][1] is agent.invocations[1][1]
    assert validation_calls == ["done"]
    assert messages == [UserMessage(content="build context")]
    continuation = cast(dict[str, str], agent.invocations[1][0])["query"]
    assert "Continue the unfinished original" in continuation
    assert "no longer than 4000 characters" in continuation
    assert "validate" not in continuation.casefold()
    assert all(
        not (isinstance(message, AssistantMessage) and message.finish_reason in {"length", "max_tokens"})
        for message in agent.context_history
    )
    agent_support.validate_personal_context_messages(agent.context_history)


@pytest.mark.asyncio
async def test_length_continuation_exhausts_after_three_automatic_attempts(
    tmp_path: Path,
) -> None:
    events: list[tuple[str, Any]] = []
    outputs = [AssistantMessage(content=f"partial-{index}", finish_reason="length") for index in range(4)]
    agent = _FakeAgent(events, outputs)
    session = _FakeSession("session", events)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    validation_calls: list[str] = []

    result, errors, invocation_failed = await agent_support._invoke_and_validate(
        agent,
        session,
        [UserMessage(content="build context")],
        sandbox,
        lambda text, _path: validation_calls.append(text) or [],
        query="build context",
    )

    assert result == ""
    assert errors == ["agent length continuation exhausted"]
    assert invocation_failed is True
    assert len(agent.invocations) == 4
    assert validation_calls == []
    assert all(not isinstance(message, AssistantMessage) for message in agent.context_history)
    agent_support.validate_personal_context_messages(agent.context_history)


@pytest.mark.asyncio
async def test_length_continuation_rejects_unsafe_history_without_appending_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, Any]] = []
    truncated = AssistantMessage(content="partial", finish_reason="max_tokens")
    agent = _FakeAgent(events, [truncated])
    session = _FakeSession("session", events)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    validation_calls: list[str] = []
    monkeypatch.setattr(
        agent_support,
        "_discard_length_limited_tail",
        lambda *_args, **_kwargs: False,
    )

    result, errors, invocation_failed = await agent_support._invoke_and_validate(
        agent,
        session,
        [UserMessage(content="build context")],
        sandbox,
        lambda text, _path: validation_calls.append(text) or [],
        query="build context",
    )

    assert result == ""
    assert errors == ["agent length continuation history is unsafe"]
    assert invocation_failed is True
    assert len(agent.invocations) == 1
    assert validation_calls == []
    assert agent.context_history == [UserMessage(content="build context"), truncated]


@pytest.mark.asyncio
async def test_run_personal_context_agent_repairs_in_same_session_after_first_validation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(
        monkeypatch,
        outputs_by_agent=[[AssistantMessage(content="bad"), AssistantMessage(content="fixed")]],
        events=events,
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "tmp").mkdir()
    original_invoke = _FakeAgent.invoke

    async def invoke_with_preserved_sandbox(self: _FakeAgent, request: object, *, session: object) -> object:
        scratch = sandbox / "tmp" / "repair-notes.md"
        if not self.invocations:
            scratch.write_text("keep for first repair", encoding="utf-8")
        else:
            assert scratch.read_text(encoding="utf-8") == "keep for first repair"
        return await original_invoke(self, request, session=session)

    monkeypatch.setattr(_FakeAgent, "invoke", invoke_with_preserved_sandbox)
    validation_errors = iter([["invalid field token=secret"], []])
    messages = [UserMessage(content="summarize logical-1")]

    output = await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=messages,
        validate_result=lambda _text, _path: next(validation_errors),
    )

    assert output == "fixed"
    assert len(created["agents"]) == 1
    agent = created["agents"][0]
    assert len(agent.invocations) == 2
    assert agent.invocations[0][1] is agent.invocations[1][1]
    repair_request = cast(dict[str, str], agent.invocations[1][0])
    assert "secret" not in repair_request["query"]
    assert "修正" in repair_request["query"]
    assert len(messages) == 2
    assert isinstance(messages[-1], UserMessage)
    react_agent = agent.react_agent
    assert len(react_agent.registered_callbacks) == 4
    assert react_agent.unregistered_callbacks == react_agent.registered_callbacks
    assert [event[0] for event in events].count("security_rail_uninit") == 1
    assert [event[0] for event in events].count("resilience_rail_uninit") == 1


@pytest.mark.asyncio
async def test_agent_authored_pending_does_not_trigger_repair_and_keeps_multi_tool_history_contiguous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(
        monkeypatch,
        outputs_by_agent=[[AssistantMessage(content="done")]],
        events=events,
    )
    sandbox = tmp_path / "sandbox"
    context = sandbox / "context"
    formal_context = tmp_path / "workspace" / "context"
    source_root = tmp_path / "workspace" / "source-meta"
    for root in (context, formal_context, source_root):
        root.mkdir(parents=True)
    for root in (context, formal_context):
        (root / "description.md").write_text("# Context\n", encoding="utf-8")
    context_baseline = context_pipeline._snapshot_managed_files(context)
    secret_sentinels = (
        "TOKEN_SENTINEL_1de4",
        "https://user:password@example.test/private?token=hidden",
        "D:\\private\\source\\secret.md",
    )
    tool_group: list[BaseMessage] = []

    async def invoke_with_pending_directory(
        self: _FakeAgent,
        request: object,
        *,
        session: object,
    ) -> object:
        page = context / "待整理" / "飞书" / "2026年08月" / "检索实践.md"
        page.parent.mkdir(parents=True)
        page.write_text(
            "# BM25 检索实践\n\n清晰语义。\n\n" + "\n".join(secret_sentinels) + "\n",
            encoding="utf-8",
        )
        context_pipeline._render_context_navigation(context)
        self.invocations.append((request, session))
        self._events.append(("invoke", request))
        query = request.get("query") if isinstance(request, dict) else ""
        if query:
            self.context_history.append(UserMessage(content=str(query)))
        calls = [
            ToolCall(id="call-first", type="function", name="read_file", arguments="{}"),
            ToolCall(id="call-second", type="function", name="list_files", arguments="{}"),
        ]
        group: list[BaseMessage] = [
            AssistantMessage(content="", tool_calls=calls),
            ToolMessage(content="first result", tool_call_id="call-first"),
            ToolMessage(content="second result", tool_call_id="call-second"),
        ]
        tool_group.extend(group)
        output = self._outputs.pop(0)
        self.context_history.extend([*group, cast(BaseMessage, output)])
        return output

    monkeypatch.setattr(_FakeAgent, "invoke", invoke_with_pending_directory)
    processed = {
        "documents": [
            {
                "logical_id": "notes/search",
                "revision_id": "rev-1",
                "title": "BM25 检索实践",
                "markdown": "清晰语义。\n",
            }
        ],
        "blocks": [],
        "deleted_ids": [],
    }

    output = await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=[UserMessage(content="整理上下文")],
        validate_result=lambda text, path: context_pipeline._validate_filesystem_agent_result(
            text,
            path,
            processed,
            context_baseline=context_baseline,
            materialized_baseline=None,
            inputs_baseline=None,
            baseline_root=formal_context,
            baseline_path_by_candidate=None,
            final_context_root=formal_context,
            source_root=source_root,
            alias_targets=None,
            deleted_source_ids=set(),
            baseline_managed_pages_by_source={},
            baseline_partition_path_by_identity={},
        ),
    )

    assert output == "done"
    agent = cast(_FakeAgent, created["agents"][0])
    assert len(agent.invocations) == 1
    group_start = next(index for index, message in enumerate(agent.context_history) if message is tool_group[0])
    assert agent.context_history[group_start : group_start + 3] == tool_group
    assert [id(message) for message in agent.context_history[group_start : group_start + 3]] == [
        id(message) for message in tool_group
    ]
    assert all(
        "normal_page_in_fallback" not in str(getattr(message, "content", "")) for message in agent.context_history
    )
    agent_support.validate_personal_context_messages(agent.context_history)


@pytest.mark.asyncio
async def test_run_personal_context_agent_repairs_in_same_session_after_invoke_error_with_closed_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(
        monkeypatch,
        outputs_by_agent=[[AssistantMessage(content="fixed")]],
        events=events,
    )
    original_invoke = _FakeAgent.invoke

    async def invoke_with_closed_error(self: _FakeAgent, request: object, *, session: object) -> object:
        if not self.invocations:
            self.invocations.append((request, session))
            query = request.get("query") if isinstance(request, dict) else ""
            self.context_history.extend(
                [UserMessage(content=str(query)), AssistantMessage(content="closed model turn")]
            )
            raise RuntimeError("model failed with secret-token")
        return await original_invoke(self, request, session=session)

    monkeypatch.setattr(_FakeAgent, "invoke", invoke_with_closed_error)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    messages = [UserMessage(content="summarize logical-1")]

    output = await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=messages,
        validate_result=lambda _text, _path: [],
    )

    assert output == "fixed"
    assert len(created["agents"]) == 1
    agent = created["agents"][0]
    assert len(agent.invocations) == 2
    assert agent.invocations[0][1] is agent.invocations[1][1]
    repair_query = cast(dict[str, str], agent.invocations[1][0])["query"]
    assert "修正" in repair_query
    assert "model failed" not in repair_query
    assert "secret-token" not in repair_query
    assert len(messages) == 2
    assert isinstance(messages[-1], UserMessage)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR,
        StatusCode.MODEL_CALL_FAILED,
        StatusCode.COMPONENT_LLM_INVOKE_CALL_FAILED,
        StatusCode.COMPONENT_LLM_EXECUTION_PROCESS_ERROR,
        StatusCode.COMPONENT_TOOL_EXECUTION_ERROR,
        StatusCode.AGENT_TOOL_EXECUTION_ERROR,
        StatusCode.TOOL_EXECUTION_ERROR,
        StatusCode.AGENT_CONTROLLER_INVOKE_CALL_FAILED,
        StatusCode.AGENT_CONTROLLER_EXECUTION_CALL_FAILED,
        StatusCode.AGENT_CONTROLLER_TOOL_EXECUTION_PROCESS_ERROR,
    ],
)
async def test_run_personal_context_agent_repairs_for_allowlisted_invoke_status_with_closed_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: StatusCode
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(
        monkeypatch,
        outputs_by_agent=[[AssistantMessage(content="fixed")]],
        events=events,
    )
    original_invoke = _FakeAgent.invoke

    async def invoke_with_status_error(self: _FakeAgent, request: object, *, session: object) -> object:
        if not self.invocations:
            self.invocations.append((request, session))
            query = request.get("query") if isinstance(request, dict) else ""
            self.context_history.extend([UserMessage(content=str(query)), AssistantMessage(content="closed tool turn")])
            raise BaseError(status, msg="execution failed")
        return await original_invoke(self, request, session=session)

    monkeypatch.setattr(_FakeAgent, "invoke", invoke_with_status_error)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    output = await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=[UserMessage(content="summarize logical-1")],
        validate_result=lambda _text, _path: [],
    )

    assert output == "fixed"
    assert len(created["agents"]) == 1
    assert len(created["agents"][0].invocations) == 2
    assert created["agents"][0].invocations[0][1] is created["agents"][0].invocations[1][1]


@pytest.mark.asyncio
async def test_run_personal_context_agent_clean_redo_after_invoke_error_without_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(
        monkeypatch,
        outputs_by_agent=[[RuntimeError("model failed")], [AssistantMessage(content="clean")]],
        events=events,
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    messages = [UserMessage(content="summarize logical-1")]

    output = await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=messages,
        validate_result=lambda _text, _path: [],
    )

    assert output == "clean"
    assert len(created["agents"]) == 2
    assert len(created["agents"][0].invocations) == 1
    assert len(created["agents"][1].invocations) == 1
    assert created["agents"][0].invocations[0][1] is created["sessions"][0]
    assert created["agents"][1].invocations[0][1] is created["sessions"][1]
    assert messages == [UserMessage(content="summarize logical-1")]


@pytest.mark.asyncio
async def test_run_personal_context_agent_seeds_closed_history_and_uses_only_current_user_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(
        monkeypatch,
        outputs_by_agent=[[AssistantMessage(content="bad"), AssistantMessage(content="fixed")]],
        events=events,
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    messages = [
        UserMessage(content="old request"),
        AssistantMessage(content="old answer"),
        UserMessage(content="current logical-2"),
    ]
    validation_errors = iter([["invalid"], []])

    output = await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=messages,
        validate_result=lambda _text, _path: next(validation_errors),
    )

    assert output == "fixed"
    agent = created["agents"][0]
    assert agent.seeded_context == messages[:2]
    first_query = cast(dict[str, str], agent.invocations[0][0])["query"]
    assert first_query == "current logical-2"
    second_query = cast(dict[str, str], agent.invocations[1][0])["query"]
    assert "old request" not in second_query
    assert "current logical-2" not in second_query
    assert "修正" in second_query
    assert [event[0] for event in events].count("get_context") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_output",
    ["", AssistantMessage(content="x" * (agent_support._MAX_AGENT_OUTPUT_CHARS + 1))],
)
async def test_run_personal_context_agent_accepts_valid_sandbox_when_confirmation_is_empty_or_oversize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_output: object,
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(
        monkeypatch,
        outputs_by_agent=[[first_output]],
        events=events,
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    validation_calls: list[str] = []

    def validate(text: str, _path: Path) -> list[str]:
        validation_calls.append(text)
        return []

    output = await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=[UserMessage(content="summarize")],
        validate_result=validate,
    )

    assert output == ""
    assert len(created["agents"][0].invocations) == 1
    assert validation_calls == [""]


@pytest.mark.asyncio
async def test_run_personal_context_agent_repairs_incomplete_sandbox_after_empty_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(
        monkeypatch,
        outputs_by_agent=[["", AssistantMessage(content="fixed")]],
        events=events,
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    validation_errors = iter([["candidate incomplete"], []])

    output = await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=[
            UserMessage(content="old request"),
            AssistantMessage(content="old answer"),
            UserMessage(content="summarize"),
        ],
        validate_result=lambda _text, _path: next(validation_errors),
    )

    assert output == "fixed"
    assert len(created["agents"][0].invocations) == 2


@pytest.mark.asyncio
async def test_run_personal_context_agent_clean_redo_restores_baseline_after_second_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(
        monkeypatch,
        outputs_by_agent=[
            [AssistantMessage(content="bad"), AssistantMessage(content="still bad")],
            [AssistantMessage(content="clean success")],
        ],
        events=events,
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "baseline.txt").write_text("baseline", encoding="utf-8")
    read_only_tree = sandbox / "materialized-source"
    read_only_tree.mkdir()
    read_only_file = read_only_tree / "README.md"
    read_only_file.write_text("read-only baseline", encoding="utf-8")
    for path in (read_only_file, read_only_tree):
        path.chmod(path.stat().st_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)

    async def fake_invoke(self: _FakeAgent, request: object, *, session: object) -> object:
        self.invocations.append((request, session))
        if len(created["agents"]) == 1:
            marker = "dirty-first.txt" if len(self.invocations) == 1 else "dirty-second.txt"
            (sandbox / marker).write_text("dirty", encoding="utf-8")
        else:
            assert not (sandbox / "dirty-first.txt").exists()
            assert not (sandbox / "dirty-second.txt").exists()
            (sandbox / "redo-dirty.txt").write_text("dirty", encoding="utf-8")
        return self._outputs.pop(0)

    monkeypatch.setattr(_FakeAgent, "invoke", fake_invoke)
    errors = iter([["first"], ["second token=secret"], []])
    messages = [UserMessage(content="summarize")]

    output = await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=messages,
        validate_result=lambda _text, _path: next(errors),
    )

    assert output == "clean success"
    assert len(created["agents"]) == 2
    assert len(created["sessions"]) == 2
    assert created["agents"][0].invocations[1][1] is created["sessions"][0]
    assert created["agents"][1].invocations[0][1] is created["sessions"][1]
    redo_query = cast(dict[str, str], created["agents"][1].invocations[0][0])["query"]
    assert "second" in redo_query
    assert "secret" not in redo_query
    assert (sandbox / "baseline.txt").read_text(encoding="utf-8") == "baseline"
    assert read_only_file.read_text(encoding="utf-8") == "read-only baseline"
    assert not (sandbox / "dirty-first.txt").exists()
    assert not (sandbox / "dirty-second.txt").exists()
    assert list(tmp_path.glob(".personal-context-agent-baseline-*")) == []
    first_react = created["agents"][0].react_agent
    redo_react = created["agents"][1].react_agent
    assert first_react.unregistered_callbacks == first_react.registered_callbacks
    assert redo_react.unregistered_callbacks == redo_react.registered_callbacks
    assert all(
        first_callback is not redo_callback
        for (_, first_callback), (_, redo_callback) in zip(
            first_react.registered_callbacks,
            redo_react.registered_callbacks,
            strict=True,
        )
    )
    assert [callback.__name__ for _, callback in first_react.registered_callbacks] == [
        callback.__name__ for _, callback in redo_react.registered_callbacks
    ]

    snapshot_directory = sandbox / "context" / "重做主题"
    snapshot_directory.mkdir(parents=True)
    snapshot_page = snapshot_directory / "页面.md"
    snapshot_page.write_text("# 页面\n", encoding="utf-8")
    first_inputs, first_result, _ = _directory_callback_inputs(
        tool_name="read_file",
        tool_args={"file_path": "context/重做主题/页面.md"},
        call_id="call-first-attempt",
    )
    redo_inputs, redo_result, _ = _directory_callback_inputs(
        tool_name="read_file",
        tool_args={"file_path": "context/重做主题/页面.md"},
        call_id="call-redo-attempt",
    )
    await first_react.registered_callbacks[2][1](cast(Any, SimpleNamespace(inputs=first_inputs)))
    await redo_react.registered_callbacks[2][1](cast(Any, SimpleNamespace(inputs=redo_inputs)))
    assert first_result.data["directory_snapshot"]["relative_directory"] == "context/重做主题"
    assert redo_result.data["directory_snapshot"]["relative_directory"] == "context/重做主题"

    first_reminders: list[str] = []
    redo_reminders: list[str] = []
    first_context = cast(Any, SimpleNamespace(push_steering=first_reminders.append))
    redo_context = cast(Any, SimpleNamespace(push_steering=redo_reminders.append))
    for _ in range(20):
        await first_react.registered_callbacks[3][1](first_context)
    await redo_react.registered_callbacks[3][1](redo_context)
    assert len(first_reminders) == 1
    assert redo_reminders == []
    assert [event[0] for event in events].count("context_processor_rail_uninit") == 2
    assert [event[0] for event in events].count("security_rail_uninit") == 2
    assert [event[0] for event in events].count("resilience_rail_uninit") == 2


@pytest.mark.asyncio
async def test_clean_redo_restore_failure_is_non_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, Any]] = []
    _patch_agent_runtime(
        monkeypatch,
        outputs_by_agent=[[AssistantMessage(content="bad"), AssistantMessage(content="bad")]],
        events=events,
    )
    monkeypatch.setattr(agent_support, "_restore_sandbox", lambda *_args: (_ for _ in ()).throw(OSError("denied")))
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    errors = iter([["first"], ["second"]])

    with pytest.raises(BaseError) as raised:
        await agent_support.run_personal_context_agent(
            model_client=cast(Any, object()),
            model_request=cast(Any, object()),
            sandbox_path=sandbox,
            messages=[UserMessage(content="summarize")],
            validate_result=lambda _text, _path: next(errors),
        )

    assert raised.value.details == {"fallback_allowed": False}


@pytest.mark.asyncio
async def test_run_personal_context_agent_unclosed_tool_group_skips_in_place_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(
        monkeypatch,
        outputs_by_agent=[[AssistantMessage(content="bad")], [AssistantMessage(content="clean")]],
        events=events,
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    messages = [
        UserMessage(content="summarize"),
        AssistantMessage(
            content="",
            tool_calls=[ToolCall(id="call-1", type="function", name="read", arguments="{}")],
        ),
    ]
    errors = iter([["invalid"], []])

    output = await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=messages,
        validate_result=lambda _text, _path: next(errors),
    )

    assert output == "clean"
    assert len(created["agents"]) == 2
    assert [type(message) for message in messages] == [UserMessage, AssistantMessage]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        PermissionError("denied"),
        BaseError(StatusCode.DEEPAGENT_RUNTIME_ERROR, msg="runtime failed"),
        asyncio.CancelledError(),
    ],
)
async def test_run_personal_context_agent_always_cleans_session_and_rail_on_disk_or_cancelled_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(monkeypatch, outputs_by_agent=[[failure]], events=events)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    expected = asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else BaseError
    with pytest.raises(expected) as caught:
        await agent_support.run_personal_context_agent(
            model_client=cast(Any, object()),
            model_request=cast(Any, object()),
            sandbox_path=sandbox,
            messages=[UserMessage(content="summarize")],
            validate_result=lambda _text, _path: [],
        )

    assert "clear_session" in [event[0] for event in events]
    assert "tool_teardown" in [event[0] for event in events]
    assert [event[0] for event in events].count("security_rail_uninit") == 1
    assert [event[0] for event in events].count("resilience_rail_uninit") == 1
    assert len(created["agents"][0].invocations) == 1
    assert created["agents"][0].react_agent.unregistered_callbacks == (
        created["agents"][0].react_agent.registered_callbacks
    )
    if isinstance(failure, PermissionError):
        assert caught.value.details == {"fallback_allowed": False}
    if isinstance(failure, BaseError):
        assert caught.value.status is StatusCode.DEEPAGENT_RUNTIME_ERROR


@pytest.mark.asyncio
async def test_run_personal_context_agent_configures_explicit_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(monkeypatch, outputs_by_agent=[[AssistantMessage(content="ok")]], events=events)
    monkeypatch.setattr(agent_support, "LocalWorkConfig", lambda **kwargs: kwargs)
    monkeypatch.setattr(agent_support, "SysOperationCard", lambda **kwargs: kwargs)
    monkeypatch.setattr(agent_support, "SysOperation", lambda card: ("sysop", card))
    monkeypatch.setattr(agent_support, "OperationMode", type("Mode", (), {"LOCAL": "local"}))
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=[UserMessage(content="summarize")],
        validate_result=lambda _text, _path: [],
    )

    factory_kwargs = next(event[1] for event in events if event[0] == "create_agent")
    assert factory_kwargs["rails"] == [
        created["processor_rails"][0],
        created["security_rails"][0],
        created["resilience_rails"][0],
    ]
    assert [tool.card.name for tool in factory_kwargs["tools"]] == [
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "list_files",
        "grep",
        "move_path",
    ]
    system_prompt = factory_kwargs["system_prompt"]
    assert "inputs" in system_prompt
    assert "tmp" in system_prompt
    assert "briefing" in system_prompt
    assert "small runs" in system_prompt
    assert "bounded document previews" in system_prompt
    assert "every bounded source preview" not in system_prompt
    assert "large runs" in system_prompt
    assert "read_file, write_file, edit_file, glob, list_files, grep, and move_path" in system_prompt
    assert "Only description.md and directories may exist directly under context/" in system_prompt
    assert "Inspect a directory with list_files before choosing it" in system_prompt
    assert "Organize knowledge by topic across providers" in system_prompt
    assert "待整理" not in system_prompt
    assert "readable but isolated topic in its own semantic directory" in system_prompt
    assert "content-derived navigation directories" in system_prompt
    assert "fallback_route" not in system_prompt
    assert "16 to 19 ordinary Markdown pages" in system_prompt
    assert "20 or more ordinary Markdown pages" in system_prompt
    assert "manually update affected relative links and description.md navigation" in system_prompt
    assert "personal-context-managed-source" in system_prompt
    assert "at most 20 Unicode characters" in system_prompt
    assert "The final .md extension does not count" in system_prompt
    assert "Keep the complete display title in the Markdown H1" in system_prompt
    assert "no more than 4000 characters" in system_prompt
    assert "Write a concise page in one call when it fits this bound" in system_prompt
    assert "Update each affected description.md once" in system_prompt
    assert "repeatedly create temporary repeatedly" not in system_prompt
    assert "bash" not in system_prompt.casefold()
    assert "powershell" not in system_prompt.casefold()
    assert "delete that temporary file" not in system_prompt
    assert "validate.py" in system_prompt
    assert "one lightweight self-check" in system_prompt
    assert "personal_context_provenance_manifest.json" not in system_prompt
    assert "source-proofs" not in system_prompt
    assert "validate_manifest.ps1" not in system_prompt
    workspace = factory_kwargs["workspace"]
    assert workspace.root_path == str(sandbox.resolve())
    assert workspace.directories == []
    assert factory_kwargs["auto_create_workspace"] is False
    assert factory_kwargs["restrict_to_work_dir"] is True
    assert factory_kwargs["enable_task_loop"] is False
    assert factory_kwargs["add_general_purpose_agent"] is False
    assert factory_kwargs["parallel_tool_calls"] is False
    assert factory_kwargs["enable_read_image_multimodal"] is False
    assert factory_kwargs[agent_support._DEFAULT_RETRY_RAIL_FLAG] is False
    assert factory_kwargs["max_iterations"] == 100
    _, sys_card = factory_kwargs["sys_operation"]
    assert sys_card["mode"] == "local"
    work_config = sys_card["work_config"]
    assert work_config["restrict_to_sandbox"] is True
    assert work_config["sandbox_root"] == [str(sandbox.resolve())]
    assert work_config["shell_allowlist"] == []
    assert work_config["dangerous_patterns"] == []


@pytest.mark.asyncio
async def test_agent_capacity_prompt_omits_reversed_near_limit_ranges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, Any]] = []
    _patch_agent_runtime(monkeypatch, outputs_by_agent=[[AssistantMessage(content="ok")]], events=events)
    monkeypatch.setattr(agent_support, "LocalWorkConfig", lambda **kwargs: kwargs)
    monkeypatch.setattr(agent_support, "SysOperationCard", lambda **kwargs: kwargs)
    monkeypatch.setattr(agent_support, "SysOperation", lambda card: ("sysop", card))
    monkeypatch.setattr(agent_support, "OperationMode", type("Mode", (), {"LOCAL": "local"}))
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=[UserMessage(content="summarize")],
        validate_result=lambda _text, _path: [],
        max_pages_per_directory=1,
        max_subdirectories_per_directory=2,
    )

    factory_kwargs = next(event[1] for event in events if event[0] == "create_agent")
    system_prompt = factory_kwargs["system_prompt"]
    assert "1 to 0 ordinary Markdown pages" not in system_prompt
    assert "2 to 1 direct child directories" not in system_prompt
    assert "1 or more ordinary Markdown pages" in system_prompt
    assert "2 or more" in system_prompt
