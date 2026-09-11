"""Covers _code_retry_block's use of a prior code_implementation attempt's
agent_trace.jsonl to warn the next attempt away from repeating bash commands
that already failed or timed out -- see _extract_bash_command and
_parse_failed_bash_commands in pipeline/subagents.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    agent_trace_path,
    set_project_root,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.schemas import (
    CodeHandoff,
    OriginalTask,
    PersistedManagerState,
    SubagentReport,
    SubtaskContract,
    TaskState,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.pipeline.subagents import (
    _code_retry_block,
    _extract_bash_command,
    _parse_failed_bash_commands,
)


@pytest.fixture(autouse=True)
def _project_root(tmp_path: Path):
    set_project_root(tmp_path)
    yield


def _write_trace(run_id: str, module: str, round_index: int, attempt: int, lines: list[dict]) -> Path:
    path = agent_trace_path(run_id, module, round_index, attempt)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def _state_with_prior_report(run_id: str, *, round_index: int = 1, attempt: int = 1) -> PersistedManagerState:
    state = PersistedManagerState(
        original_task=OriginalTask(topic="improve the paper"),
        task_state=TaskState(run_id=run_id),
    )
    state.reports.append(
        SubagentReport(
            report_id=f"code_implementation:{round_index}:{attempt}",
            module="code_implementation",
            mode="run",
            round_index=round_index,
            attempt=attempt,
            outcome="failed",
            retryable=True,
            summary="the bash tool timed out while exploring the workspace",
            runtime_failure="timeout",
            handoff=CodeHandoff(log_paths=[]),
        )
    )
    return state


def _contract() -> SubtaskContract:
    return SubtaskContract(
        module="code_implementation",
        mode="run",
        goal="implement the experiment",
        acceptance_criteria=["produces a metrics.json"],
        repair_instruction="fix the timeout",
    )


# -- _extract_bash_command ---------------------------------------------------


def test_extract_bash_command_from_plain_dict():
    assert _extract_bash_command({"command": "find /e/workspace -maxdepth 8"}) == "find /e/workspace -maxdepth 8"


def test_extract_bash_command_from_json_encoded_string():
    raw = json.dumps({"command": "ls -la"})
    assert _extract_bash_command(raw) == "ls -la"


def test_extract_bash_command_from_truncated_wrapper():
    wrapper = {"text": json.dumps({"command": "grep -rl foo /e/workspace"}), "truncated": True}
    assert _extract_bash_command(wrapper) == "grep -rl foo /e/workspace"


def test_extract_bash_command_collapses_whitespace_and_clips():
    long_command = "echo " + "x" * 500
    result = _extract_bash_command({"command": long_command})
    assert result is not None
    assert len(result) <= 300
    assert result.endswith("…")


@pytest.mark.parametrize("bad_input", [None, 42, "not json", {"no_command_here": 1}, {"text": 123}])
def test_extract_bash_command_returns_none_on_garbage(bad_input):
    assert _extract_bash_command(bad_input) is None


# -- _parse_failed_bash_commands ---------------------------------------------


def test_parse_failed_bash_commands_pairs_start_and_error_by_call_id(tmp_path):
    trace_path = tmp_path / "agent_trace.jsonl"
    trace_path.write_text(
        "\n".join(
            json.dumps(line)
            for line in [
                {
                    "event": "tool_call_start",
                    "call_id": "t1",
                    "tool_name": "bash",
                    "arguments": json.dumps({"command": "find /e/workspace -iname '*icl*'"}),
                },
                {
                    "event": "tool_call_error",
                    "call_id": "t1",
                    "tool_name": "bash",
                    "error": {"message": "[120001] Tool 'bash' timed out after 300.0s"},
                },
                {
                    "event": "tool_call_start",
                    "call_id": "t2",
                    "tool_name": "bash",
                    "arguments": json.dumps({"command": "ls output/"}),
                },
                {"event": "tool_call_end", "call_id": "t2", "tool_name": "bash"},
            ]
        ),
        encoding="utf-8",
    )
    failed = _parse_failed_bash_commands(trace_path)
    assert failed == ["find /e/workspace -iname '*icl*'"]


def test_parse_failed_bash_commands_ignores_non_bash_tools(tmp_path):
    trace_path = tmp_path / "agent_trace.jsonl"
    trace_path.write_text(
        "\n".join(
            json.dumps(line)
            for line in [
                {
                    "event": "tool_call_start",
                    "call_id": "t1",
                    "tool_name": "openjiuwen_ref_read_file",
                    "arguments": json.dumps({"path": "docs/x.md"}),
                },
                {"event": "tool_call_error", "call_id": "t1", "tool_name": "openjiuwen_ref_read_file", "error": {}},
            ]
        ),
        encoding="utf-8",
    )
    assert _parse_failed_bash_commands(trace_path) == []


def test_parse_failed_bash_commands_caps_at_limit(tmp_path):
    trace_path = tmp_path / "agent_trace.jsonl"
    lines: list[dict] = []
    for i in range(8):
        lines.append(
            {
                "event": "tool_call_start",
                "call_id": f"t{i}",
                "tool_name": "bash",
                "arguments": json.dumps({"command": f"find /e/workspace -name 'x{i}'"}),
            }
        )
        lines.append(
            {"event": "tool_call_error", "call_id": f"t{i}", "tool_name": "bash", "error": {"message": "timed out"}}
        )
    trace_path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    failed = _parse_failed_bash_commands(trace_path, limit=5)
    assert len(failed) == 5
    # Most-recent commands are kept, not the earliest ones.
    assert failed[-1] == "find /e/workspace -name 'x7'"


def test_parse_failed_bash_commands_tolerates_missing_file(tmp_path):
    assert _parse_failed_bash_commands(tmp_path / "does-not-exist.jsonl") == []


def test_parse_failed_bash_commands_tolerates_truncated_last_line(tmp_path):
    trace_path = tmp_path / "agent_trace.jsonl"
    good_start = json.dumps(
        {
            "event": "tool_call_start",
            "call_id": "t1",
            "tool_name": "bash",
            "arguments": json.dumps({"command": "find /e/workspace -maxdepth 8"}),
        }
    )
    good_error = json.dumps(
        {"event": "tool_call_error", "call_id": "t1", "tool_name": "bash", "error": {"message": "timed out"}}
    )
    truncated = '{"event": "tool_call_start", "call_id": "t2", "tool_name": "bash", "argume'
    trace_path.write_text("\n".join([good_start, good_error, truncated]), encoding="utf-8")
    assert _parse_failed_bash_commands(trace_path) == ["find /e/workspace -maxdepth 8"]


# -- _code_retry_block integration --------------------------------------------


def test_code_retry_block_includes_failed_commands_from_prior_trace():
    run_id = "run-1"
    state = _state_with_prior_report(run_id)
    _write_trace(
        run_id,
        "code_implementation",
        1,
        1,
        [
            {
                "event": "tool_call_start",
                "call_id": "t1",
                "tool_name": "bash",
                "arguments": json.dumps({"command": "find /e/workspace -iname '*paper_improvement*'"}),
            },
            {
                "event": "tool_call_error",
                "call_id": "t1",
                "tool_name": "bash",
                "error": {"message": "[120001] Tool 'bash' timed out after 300.0s"},
            },
        ],
    )
    block = _code_retry_block(_contract(), state)
    assert "Commands already tried and failed" in block
    assert "find /e/workspace -iname '*paper_improvement*'" in block
    # Existing repair-context content must still be present.
    assert "Repair instruction:" in block
    assert "Logs from the previous attempt:" in block


def test_code_retry_block_omits_section_when_no_bash_failures():
    run_id = "run-2"
    state = _state_with_prior_report(run_id)
    _write_trace(
        run_id,
        "code_implementation",
        1,
        1,
        [
            {
                "event": "tool_call_start",
                "call_id": "t1",
                "tool_name": "bash",
                "arguments": json.dumps({"command": "ls"}),
            }
        ],
    )
    block = _code_retry_block(_contract(), state)
    assert "Commands already tried and failed" not in block


def test_code_retry_block_handles_missing_trace_file_gracefully():
    run_id = "run-3"
    state = _state_with_prior_report(run_id)
    # No trace file written for this run/module/round/attempt at all.
    block = _code_retry_block(_contract(), state)
    assert "Commands already tried and failed" not in block
    assert "Repair instruction:" in block


def test_code_retry_block_returns_empty_when_prior_succeeded():
    run_id = "run-4"
    state = _state_with_prior_report(run_id)
    state.reports[-1] = state.reports[-1].model_copy(update={"outcome": "succeeded"})
    assert _code_retry_block(_contract(), state) == ""
