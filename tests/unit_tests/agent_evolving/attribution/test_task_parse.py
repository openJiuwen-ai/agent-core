# coding: utf-8
"""Tests for file-artifact extraction and the task-parse pseudo-agent."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openjiuwen.agent_evolving.attribution.model import TaskParseResult
from openjiuwen.agent_evolving.attribution.nodes import TrajectoryNode
from openjiuwen.agent_evolving.attribution.task_parse import (
    TaskParseError,
    build_task_parse_digest,
    extract_file_candidates,
    run_task_parse,
)

pytestmark = pytest.mark.asyncio


def _node(index: int, role: str, content: str = "", calls: list[dict[str, Any]] | None = None) -> TrajectoryNode:
    return TrajectoryNode(
        node_id=f"main:{index}",
        subagent_key="main",
        display_name="M",
        kind="main_agent",
        role=role,
        content=content or None,
        tool_calls=calls or [],
        start_ts_ns=index * 100,
        end_ts_ns=index * 100 + 50,
    )


def _call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "arguments": json.dumps(arguments)}


def _nodes_from_commands(commands: list[tuple[str, str]]) -> dict[str, list[TrajectoryNode]]:
    return {
        "main": [
            _node(index, "assistant", calls=[_call(tool, {"command": command})])
            for index, (tool, command) in enumerate(commands)
        ]
    }


POSIX_POSITIVE = [
    ("cat > /tmp/report.md <<'EOF'\nhi\nEOF", "/tmp/report.md"),
    ("echo hi >> note.md", "note.md"),
    ("echo hi>out.txt", "out.txt"),
    ("ls /tmp | tee out.log", "out.log"),
    ("touch new.py", "new.py"),
    ("cp a.py b.py", "b.py"),
    ("mv old.txt new.txt", "new.txt"),
    ("ln -s /data/x /data/y", "/data/y"),
    ("truncate -s 0 empty.md", "empty.md"),
    ("dd if=/dev/zero of=d.bin bs=1m count=1", "d.bin"),
    ("wget -O app.zip https://example.com/a", "app.zip"),
    ("curl -o data.json https://example.com/a", "data.json"),
    ("sort -o sorted.txt in.txt", "sorted.txt"),
]


@pytest.mark.parametrize("command,path", POSIX_POSITIVE)
def test_extract_posix_create(command: str, path: str) -> None:
    candidates, _ = extract_file_candidates(_nodes_from_commands([("bash", command)]))
    assert any(candidate == path for candidate, _, _ in candidates)


POSIX_EDIT = [
    ("tee -a append.log", "append.log"),
    ("sed -i 's/a/b/' conf.yaml", "conf.yaml"),
    ("perl -pi -e 's/x/y/g' f.txt", "f.txt"),
    ("dos2unix win.txt", "win.txt"),
]


@pytest.mark.parametrize("command,path", POSIX_EDIT)
def test_extract_posix_edit(command: str, path: str) -> None:
    candidates, _ = extract_file_candidates(_nodes_from_commands([("bash", command)]))
    assert any(candidate == path for candidate, _, _ in candidates)


WINDOWS_POSITIVE = [
    ("cmd", "copy /y a.txt b.txt", "b.txt"),
    ("cmd", "xcopy src dst /E", "dst"),
    ("cmd", "robocopy src dst /MIR", "dst"),
    ("cmd", "move a.txt b.txt", "b.txt"),
    ("cmd", "ren old.txt new.txt", "new.txt"),
    ("cmd", "fsutil file createnew f.txt 0", "f.txt"),
    ("cmd", "certutil -decode a.b64 out.bin", "out.bin"),
    ("powershell", "Set-Content -Path out.txt -Value x", "out.txt"),
    ("powershell", "Out-File log.txt", "log.txt"),
    ("powershell", "New-Item report.md", "report.md"),
    ("powershell", "Copy-Item a.txt b.txt", "b.txt"),
    ("powershell", "Export-Csv -Path r.csv", "r.csv"),
    ("powershell", '[System.IO.File]::WriteAllText("C:\\x\\a.txt","hi")', "c:\\x\\a.txt"),
]


@pytest.mark.parametrize("tool,command,path", WINDOWS_POSITIVE)
def test_extract_windows_commands(tool: str, command: str, path: str) -> None:
    candidates, _ = extract_file_candidates(_nodes_from_commands([(tool, command)]))
    assert any(candidate == path for candidate, _, _ in candidates)


def test_extract_ps_append_edit() -> None:
    candidates, _ = extract_file_candidates(_nodes_from_commands([("powershell", "Add-Content out.txt y")]))
    assert any(candidate == "out.txt" and "create" not in tool_type for candidate, _, tool_type in candidates)


NEGATIVE_COMMANDS = [
    "cat a.txt",
    "head -n 5 log.txt",
    "grep -n err log.txt",
    "rg TODO src",
    "sed -n '1,5p' f.txt",
    'echo "a > b"',
    "ls > /dev/null 2>&1",
    "wc -l f.txt",
    "md5sum f",
    "rm -f old.txt",
]


@pytest.mark.parametrize("command", NEGATIVE_COMMANDS)
def test_extract_pure_reads_not_listed(command: str) -> None:
    candidates, _ = extract_file_candidates(_nodes_from_commands([("bash", command)]))
    assert candidates == []


def test_extract_r0_priority_and_grading() -> None:
    nodes = {
        "main": [
            _node(0, "assistant", calls=[_call("write_file", {"file_path": "/w/report.md", "content": "x"})]),
            _node(1, "assistant", calls=[_call("send_file_to_user", {"abs_file_path_list": '["/w/report.md"]'})]),
            _node(2, "assistant", calls=[_call("edit_file", {"file_path": "/w/notes.md"})]),
        ]
    }
    candidates, _ = extract_file_candidates(nodes)
    assert [path for path, _, _ in candidates] == ["/w/report.md", "/w/notes.md"]
    assert candidates[0][2] == "send_file_to_user"
    assert candidates[1][2] == "edit_file"


def test_extract_send_file_to_user_three_shapes() -> None:
    shapes = [
        "/w/single.md",
        '["/w/a.md", "/w/b.md"]',
        ["/w/c.md"],
    ]
    for index, value in enumerate(shapes):
        nodes = {"main": [_node(index, "assistant", calls=[_call("send_file_to_user", {"abs_file_path_list": value})])]}
        candidates, _ = extract_file_candidates(nodes)
        expected = ["/w/single.md"] if index == 0 else (["/w/a.md", "/w/b.md"] if index == 1 else ["/w/c.md"])
        assert sorted(path for path, _, _ in candidates) == sorted(expected)


def test_extract_dedup_keeps_last_touch() -> None:
    nodes = {
        "main": [
            _node(0, "assistant", calls=[_call("write_file", {"file_path": "/w/f.md", "content": "a"})]),
            _node(1, "assistant", calls=[_call("bash", {"command": "sed -i 's/a/b/' /w/f.md"})]),
        ]
    }
    candidates, _ = extract_file_candidates(nodes)
    assert len(candidates) == 1
    assert candidates[0] == ("/w/f.md", "main:1", "bash:edit")


def test_extract_low_confidence_counted_not_listed() -> None:
    command = "python3 -c \"open('/tmp/o.txt','w').write('hi')\""
    candidates, meta = extract_file_candidates(_nodes_from_commands([("bash", command)]))
    assert meta["low_confidence"] >= 1
    assert all(path != "/tmp/o.txt" for path, _, _ in candidates)


def test_build_task_parse_digest_sections_and_dedup() -> None:
    nodes = {
        "main": [
            _node(0, "user", "帮我做任务"),
            _node(1, "assistant", "我来处理"),
            _node(2, "assistant", calls=[_call("write_file", {"file_path": "/w/f.md", "content": "x"})]),
        ]
    }
    candidates = [("/w/f.md", "main:2", "write_file")]
    digest = build_task_parse_digest(nodes, candidates, "zh")
    assert "帮我做任务" in digest
    assert "文件产物候选" in digest
    assert "/w/f.md" in digest


class _FakeLLM:
    def __init__(self, calls: list[list[dict[str, Any]]], failures: int = 0) -> None:
        self._calls = calls
        self._failures = failures
        self._attempts = 0
        self.last_messages: list[dict[str, Any]] = []

    async def invoke(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], **kwargs: Any) -> Any:
        del tools, kwargs
        self.last_messages = messages
        self._attempts += 1
        if self._attempts <= self._failures:
            return _Response(tool_calls=[])
        batch = self._calls[min(self._attempts - 1, len(self._calls) - 1)]
        return _Response(tool_calls=[{"name": item["name"], "arguments": json.dumps(item["args"])} for item in batch])


class _Response:
    def __init__(self, tool_calls: list[dict[str, Any]]) -> None:
        self.tool_calls = tool_calls


async def test_run_task_parse_happy_path() -> None:
    nodes = {
        "main": [
            _node(0, "user", "帮我写报告"),
            _node(1, "assistant", "好的，报告如下"),
            _node(2, "assistant", calls=[_call("write_file", {"file_path": "/w/report.md", "content": "x"})]),
        ]
    }
    llm = _FakeLLM(
        [
            [
                {"name": "report_task_description", "args": {"task": "写报告"}},
                {"name": "report_message_artifacts", "args": {"node_ids": ["main:1"]}},
                {"name": "report_file_artifacts", "args": {"paths": ["/w/report.md"]}},
            ]
        ]
    )
    result = await run_task_parse(nodes, "zh", llm)
    assert isinstance(result, TaskParseResult)
    assert result.task_description == "写报告"
    assert result.message_artifacts == [("main:1", "好的，报告如下")]
    assert result.file_artifacts == ["/w/report.md"]
    assert result.run_meta["attempts"] == 1


async def test_run_task_parse_retry_then_success() -> None:
    nodes = {"main": [_node(0, "user", "帮我写报告")]}
    llm = _FakeLLM(
        [[{"name": "report_task_description", "args": {"task": "写报告"}}]],
        failures=1,
    )
    result = await run_task_parse(nodes, "zh", llm)
    assert result.task_description == "写报告"
    assert result.run_meta["attempts"] == 2


async def test_run_task_parse_retry_exhausted() -> None:
    nodes = {"main": [_node(0, "user", "帮我写报告")]}
    llm = _FakeLLM([[]], failures=10)
    with pytest.raises(TaskParseError) as excinfo:
        await run_task_parse(nodes, "zh", llm)
    assert excinfo.value.error_type == "invalid_response"


async def test_run_task_parse_error_tool_aborts() -> None:
    nodes = {"main": [_node(0, "user", "帮我写报告")]}
    llm = _FakeLLM([[{"name": "report_task_error", "args": {"error_type": "incomplete_task"}}]])
    with pytest.raises(TaskParseError) as excinfo:
        await run_task_parse(nodes, "zh", llm)
    assert excinfo.value.error_type == "incomplete_task"


async def test_run_task_parse_mixed_error_and_report_retries() -> None:
    nodes = {"main": [_node(0, "user", "帮我写报告")]}
    llm = _FakeLLM(
        [
            [
                {"name": "report_task_error", "args": {"error_type": "incomplete_task"}},
                {"name": "report_task_description", "args": {"task": "写报告"}},
            ],
            [{"name": "report_task_description", "args": {"task": "写报告"}}],
        ]
    )
    result = await run_task_parse(nodes, "zh", llm)
    assert result.task_description == "写报告"
    assert result.run_meta["attempts"] == 2
