# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Model-facing rendering of harness tool results."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.harness.tools.agent_mode_tools import SwitchModeTool
from openjiuwen.harness.tools.base_tool import ToolOutput, render_fields
from openjiuwen.harness.tools.browser_move.offload_recall import BrowserOffloadRecallTool
from openjiuwen.harness.tools.code import CodeTool
from openjiuwen.harness.tools.coding_memory import CodingMemoryEditTool, CodingMemoryWriteTool
from openjiuwen.harness.tools.filesystem import (
    EditFileTool,
    GlobTool,
    GrepTool,
    ListDirTool,
    WriteFileTool,
)
from openjiuwen.harness.tools.goal import GetCurrentGoalTool, SubmitGoalReportTool
from openjiuwen.harness.tools.lsp_tool import LspTool
from openjiuwen.harness.tools.mcp_tools import ListMcpResourcesTool, ReadMcpResourceTool
from openjiuwen.harness.tools.memory import MemoryGetTool, MemorySearchTool, WriteMemoryTool
from openjiuwen.harness.tools.multimodal.audio import AudioMetadataTool
from openjiuwen.harness.tools.multimodal.vision import VisualQuestionAnsweringTool
from openjiuwen.harness.tools.shell import BashTool
from openjiuwen.harness.tools.skills.list_skill import ListSkillTool
from openjiuwen.harness.tools.subagent.session_tools import SessionsCancelTool
from openjiuwen.harness.tools.subagent.subagent_tools import SubagentListTool, SubagentWaitTool
from openjiuwen.harness.tools.subagent.task_tool import TaskTool
from openjiuwen.harness.tools.todo import TodoCreateTool, TodoGetTool, TodoListTool
from openjiuwen.harness.tools.tool_discovery.tool_search import ToolSearchTool
from openjiuwen.harness.tools.worktree import ExitWorktreeTool

_OP = SimpleNamespace(fs=lambda: None)


def _card(name: str) -> ToolCard:
    return ToolCard(id=name, name=name, description=name)


@pytest.mark.level0
def test_render_fields_skips_empty_values_and_encodes_structures() -> None:
    text = render_fields({"id": "a1", "note": None, "empty": "", "done": False, "tags": ["x"]})

    assert text == 'id: a1\ndone: false\ntags: ["x"]'
    assert render_fields({"a": 1, "b": "two"}, separator=" | ") == "a: 1 | b: two"


@pytest.mark.level0
def test_write_file_does_not_echo_previous_content() -> None:
    output = ToolOutput(
        success=True,
        data={
            "file_path": "/w/a.py",
            "bytes_written": 12,
            "type": "update",
            "created": False,
            "original_file": "SECRET OLD BODY",
        },
    )

    text = WriteFileTool(_OP, "en").render_for_llm(output)

    assert text == "Updated /w/a.py (12 bytes written)."
    assert WriteFileTool(_OP, "en").render_for_llm(ToolOutput(success=False, error="denied")) == "denied"


@pytest.mark.level1
def test_edit_file_reports_creation_and_replacements() -> None:
    tool = EditFileTool(_OP, "en")

    assert tool.render_for_llm(ToolOutput(success=True, data={"file_path": "/w/a", "replacements": 2})) == (
        "Edited /w/a (2 replacement(s))."
    )
    assert tool.render_for_llm(
        ToolOutput(success=True, data={"file_path": "/w/b", "replacements": 0, "created": True})
    ) == "Created /w/b."


@pytest.mark.level1
def test_glob_and_list_dir_render_plain_lines() -> None:
    glob = GlobTool(_OP, "en")
    found = ToolOutput(success=True, data={"matching_files": ["/w/a.py", "/w/b.py"], "truncated": True})

    assert glob.render_for_llm(found).splitlines()[:2] == ["/w/a.py", "/w/b.py"]
    assert "truncated" in glob.render_for_llm(found)
    assert glob.render_for_llm(ToolOutput(success=True, data={"matching_files": [], "truncated": False})) == (
        "No files found."
    )

    listing = ToolOutput(success=True, data={"files": ["a.py"], "dirs": ["pkg"]})
    assert ListDirTool(_OP, "en").render_for_llm(listing) == "pkg/\na.py"


@pytest.mark.level1
def test_grep_renders_matches_and_keeps_partial_matches_on_failure() -> None:
    tool = GrepTool(_OP, "en")
    base = {"stdout": "", "exit_code": 1, "appliedLimit": None}

    assert tool.render_for_llm(ToolOutput(success=True, data=base)) == "No matches found."
    files = {**base, "stdout": "a.py\nb.py", "exit_code": 0, "appliedLimit": 2}
    assert tool.render_for_llm(ToolOutput(success=True, data=files)).startswith("a.py\nb.py\n(Results truncated")
    partial = {**base, "stdout": "a.py:1:x", "exit_code": 2}
    assert tool.render_for_llm(ToolOutput(success=False, data=partial, error="rg: b.py: denied")) == (
        "rg: b.py: denied\na.py:1:x"
    )
    assert tool.render_for_llm(ToolOutput(success=False, data={**base, "exit_code": 2}, error="")) == (
        "Search failed with exit code 2."
    )


@pytest.mark.level1
def test_code_and_bash_render_terminal_text() -> None:
    code = CodeTool(_OP, "en")
    failed = ToolOutput(success=False, data={"stdout": "partial", "stderr": "", "exit_code": 3}, error="")

    assert code.render_for_llm(failed) == "partial\nExit code: 3"
    assert code.render_for_llm(ToolOutput(success=True, data={"stdout": "", "stderr": "", "exit_code": 0})) == (
        "Code executed successfully with no output."
    )

    bash = BashTool(_OP, "en")
    assert bash.render_for_llm(ToolOutput(success=True, data={"pid": 42, "status": "started"})) == (
        "Command started in background (pid 42)."
    )
    assert bash.render_for_llm(ToolOutput(success=True, data={"content": "Exit Code: 0"})) == "Exit Code: 0"


@pytest.mark.level1
def test_memory_tools_render_text() -> None:
    hits = {
        "results": [{"citation": "memory/a.md#L1-L2", "score": 0.5, "snippet": "likes tea"}],
        "disabled": False,
    }
    assert MemorySearchTool(_OP, "en").render_for_llm(ToolOutput(success=True, data=hits)) == (
        "memory/a.md#L1-L2 (score 0.50)\nlikes tea"
    )
    assert MemoryGetTool(_OP, "en").render_for_llm(ToolOutput(success=True, data={"disabled": False})) == (
        "No memory content found."
    )
    written = {"success": True, "path": "/m/a.md", "appended": True}
    assert WriteMemoryTool(_OP, "en").render_for_llm(ToolOutput(success=True, data=written)) == (
        "Appended to memory file /m/a.md."
    )
    skipped = {"success": True, "path": "/m/b.md", "mode": "skip", "note": "Content is redundant"}
    assert CodingMemoryWriteTool(_OP, "en").render_for_llm(ToolOutput(success=True, data=skipped)) == (
        "Skipped writing coding memory file /m/b.md.\nContent is redundant"
    )
    edited = {"success": True, "path": "/m/b.md", "new_content": "WHOLE FILE"}
    assert CodingMemoryEditTool(_OP, "en").render_for_llm(ToolOutput(success=True, data=edited)) == (
        "Edited coding memory file /m/b.md."
    )


@pytest.mark.level1
def test_mcp_resource_and_lsp_tools_render_text() -> None:
    resources = [{"uri": "file:///a", "name": "a", "mimeType": "text/plain", "description": "doc"}]
    assert ListMcpResourcesTool("en").render_for_llm(ToolOutput(success=True, data=resources)) == (
        "file:///a (a, text/plain): doc"
    )
    contents = [
        {"uri": "file:///a", "mimeType": "text/plain", "text": "hello"},
        {"uri": "file:///b", "mimeType": "image/png", "text": None},
    ]
    assert ReadMcpResourceTool("en").render_for_llm(ToolOutput(success=True, data=contents)) == (
        "hello\n\n[binary content: file:///b (image/png)]"
    )
    lsp = LspTool(language="en")
    assert lsp.render_for_llm(ToolOutput(success=True, data={"result": "", "operation": "hover"})) == (
        "No results found."
    )


@pytest.mark.level1
def test_task_tool_renders_answer_and_browser_orchestration() -> None:
    tool = TaskTool(_card("task_tool"), parent_agent=None, language="en")

    plain = ToolOutput(success=True, data={"output": "done", "agent_id": "a1"})
    assert tool.render_for_llm(plain) == "done"

    browser = ToolOutput(
        success=True,
        data={"output": "found 2 items", "agent_id": "a1", "resume_task_id": "s1", "retryable": False},
    )
    answer, block = tool.render_for_llm(browser).split("\n\n", 1)
    assert answer == "found 2 items"
    assert json.loads(block) == {"browser_orchestration": {"resume_task_id": "s1", "retryable": False}}

    refused_payload = json.dumps({"browser_orchestration": {"code": "browser_query_already_running"}})
    refused = ToolOutput(success=True, data={"output": refused_payload, "code": "browser_query_already_running"})
    assert tool.render_for_llm(refused) == refused_payload


@pytest.mark.level1
def test_subagent_and_session_tools_render_text() -> None:
    wait = SubagentWaitTool(_card("subagent_wait"), parent_agent=None, language="en")
    waited = ToolOutput(
        success=True,
        data={"statuses": {"s1": "completed"}, "results": {"s1": "answer"}, "output_files": {}, "timed_out": True},
    )
    assert wait.render_for_llm(waited) == (
        "subagent_id: s1\nstatus: completed\nresult:\nanswer\n\n"
        "Timed out before every subagent reached a final status."
    )

    listed = ToolOutput(
        success=True,
        data={
            "capacity": {"used": 1, "max": 4},
            "live_subagents": [{"subagent_id": "s1", "status": "running", "turn_outcome": None}],
            "closed_subagents": [],
        },
    )
    text = SubagentListTool(_card("subagent_list"), parent_agent=None, language="en").render_for_llm(listed)
    assert text == (
        "Capacity: 1/4 subagents in use.\n\n"
        "Live subagents:\n- subagent_id: s1 | status: running\n\n"
        "Closed subagents (resumable): none"
    )

    cancel = SessionsCancelTool(parent_agent=None, toolkit=None, language="en")
    failed = ToolOutput(success=False, data={"task_id": "t1", "status": "running", "message": "Task t1 cancel failed"})
    assert cancel.render_for_llm(failed) == "Task t1 cancel failed"


@pytest.mark.level1
def test_discovery_tools_render_text() -> None:
    skills = ListSkillTool(lambda: [], language="en")
    listed = ToolOutput(
        success=True,
        data={"skills": [{"name": "pdf", "description": "Read PDFs", "skill_md_path": "/s/pdf/SKILL.md"}]},
    )
    assert skills.render_for_llm(listed) == "- pdf: Read PDFs (/s/pdf/SKILL.md)"

    search = ToolSearchTool(search_tools=None, language="en")
    parameters = {"type": "object", "properties": {"id": {"type": "string"}}}
    found = ToolOutput(
        success=True,
        data={
            "query": "cron",
            "results": [{"name": "cron", "description": "Jobs", "parameters": parameters}],
            "count": 1,
        },
    )
    text = search.render_for_llm(found)
    assert text.startswith('1 tool(s) matched "cron".\n\n## cron\nJobs\nParameters: ')
    assert json.loads(text.split("Parameters: ", 1)[1]) == parameters
    empty = ToolOutput(success=True, data={"query": "x", "results": [], "count": 0})
    assert search.render_for_llm(empty) == 'No tools matched "x".'


@pytest.mark.level1
def test_dict_returning_tools_render_text() -> None:
    assert TodoCreateTool(_OP, language="en").render_for_llm({"message": "Created 1 task"}) == "Created 1 task"
    tasks = {"tasks": [{"id": "t1", "content": "Write", "status": "pending", "depends_on": ["t0"]}]}
    assert TodoListTool(_OP, language="en").render_for_llm(tasks) == "- [pending] t1: Write (depends on: t0)"
    assert TodoListTool(_OP, language="en").render_for_llm({"tasks": []}) == "No active tasks."
    assert TodoGetTool(_OP, language="en").render_for_llm({"todo": {"id": "t1", "description": None}}) == "id: t1"

    assert SubmitGoalReportTool(None, language="en").render_for_llm(
        {"result": "report_accepted", "status": "continue"}
    ) == "Goal report accepted (status: continue)."
    goal = GetCurrentGoalTool(None, language="en")
    assert goal.render_for_llm({"has_goal": False, "message": "No goal."}) == "No goal."
    assert goal.render_for_llm({"has_goal": True, "goal_id": "g1", "last_assessment": None}) == "goal_id: g1"


@pytest.mark.level1
def test_lifecycle_and_model_tools_render_text() -> None:
    switch = SwitchModeTool(agent_ref=None, language="en")
    assert switch.render_for_llm(ToolOutput(success=True, data={"current_mode": "plan", "message": "Plan on"})) == (
        "Plan on"
    )

    exit_tool = ExitWorktreeTool(manager=None, language="en")
    exited = ToolOutput(success=True, data={"message": "Removed worktree", "discarded_files": 2})
    assert exit_tool.render_for_llm(exited) == "Removed worktree\ndiscarded_files: 2"

    vqa = VisualQuestionAnsweringTool(language="en")
    assert vqa.render_for_llm(ToolOutput(success=True, data={"answer": "", "model": "m"})) == (
        "The vision model returned no answer."
    )
    metadata = AudioMetadataTool(language="en")
    assert metadata.render_for_llm(ToolOutput(success=True, data={"duration_seconds": 3.5, "title": None})) == (
        "duration_seconds: 3.5"
    )


@pytest.mark.level1
def test_offload_recall_renders_paging_header(tmp_path) -> None:
    tool = BrowserOffloadRecallTool(tmp_path, language="en")
    found = ToolOutput(
        success=True,
        data={
            "handle": "h" * 32,
            "found": True,
            "tool_name": "browser_probe",
            "offset": 0,
            "returned_chars": 5,
            "original_size": 9,
            "next_offset": 5,
            "content": "hello",
        },
    )
    header, body = tool.render_for_llm(found).split("\n", 1)

    assert "next_offset: 5" in header
    assert body == "hello"
    missing = ToolOutput(
        success=True,
        data={"handle": "h" * 32, "found": False, "query": "cart", "original_size": 9, "content": ""},
    )
    assert tool.render_for_llm(missing).startswith('No match for "cart"')
