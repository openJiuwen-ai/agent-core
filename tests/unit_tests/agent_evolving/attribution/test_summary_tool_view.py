"""The actual model input retains tool results and identities while previewing large bodies."""

import json

import pytest

from openjiuwen.agent_evolving.attribution.nodes import TrajectoryNode
from openjiuwen.agent_evolving.attribution.trajectory_summary import summarize_trajectory
from openjiuwen.agent_evolving.attribution.trajectory_summary.prepare import node_text


@pytest.mark.parametrize("encoded", [True, False])
def test_tool_arguments_preserve_structure_metadata_and_original_code(encoded):
    args = {"file_path": "outputs/game.html", "call_goal": "Create the requested game", "content": "CODE" * 5000,
            "command": "node --check game.js", "old_string": "OLD" * 1000, "new_string": "NEW" * 1000}
    value = json.dumps(args) if encoded else args
    node = TrajectoryNode("A:7", "A", "Worker", "team_member", "assistant",
                          tool_calls=[{"id": "call7", "function": {"name": "write_file", "arguments": value}}])
    view = json.loads(node_text(node))
    call = view["tool_calls"][0]
    projected = json.loads(call["function"]["arguments"]) if encoded else call["function"]["arguments"]
    assert call["id"] == "call7" and call["function"]["name"] == "write_file" and view["node_id"] == "A:7"
    for key in ("file_path", "call_goal", "command"):
        assert projected[key] == args[key]
    for key in ("content", "old_string", "new_string"):
        assert len(projected[key]) <= 1000 and "tool body preview" in projected[key]
    assert node.tool_calls[0]["function"]["arguments"] == value


def test_tool_return_keeps_middle_failure_and_tail_without_rewriting_source():
    content = "ordinary output\n" * 100 + "PASS summary: test passed\n" * 100 + "ERROR: required assertion failed\n" + "more output\n" * 300 + "FINAL: exit code 1"
    node = TrajectoryNode("A:8", "A", "Worker", "team_member", "tool", content=content, name="bash",
                          tool_call_id="call7", error=True)
    view = json.loads(node_text(node))
    assert len(view["content"]) <= 1000 and "ERROR: required assertion failed" in view["content"]
    assert view["content"].endswith("FINAL: exit code 1") and view["error"]
    assert view["tool_call_id"] == "call7" and node.content == content


def test_structured_return_preserves_status_exit_code_paths_and_failure_details():
    result = {"status": "failed", "exit_code": 1, "path": "outputs/game.html",
              "stdout": "routine\n" * 1000, "stderr": "SyntaxError: invalid token", "failed_tests": ["wall_collision"]}
    node = TrajectoryNode("A:8", "A", "Worker", "team_member", "tool", content=json.dumps(result))
    projected = json.loads(json.loads(node_text(node))["content"])
    for field in ("status", "exit_code", "path", "stderr", "failed_tests"):
        assert projected[field] == result[field]
    assert len(projected["stdout"]) <= 1000


def test_upstream_cut_invalid_json_remains_visible_as_partial_input():
    value = '{"file_path":"game.html","content":"' + "X" * 2000 + '...<OTel attribute truncated: 10000 chars omitted>'
    node = TrajectoryNode("A:7", "A", "Worker", "team_member", "assistant",
                          tool_calls=[{"name": "write_file", "arguments": value}])
    view = json.loads(node_text(node))
    assert '"file_path":"game.html"' in view["tool_calls"][0]["arguments"]
    assert "OTel attribute truncated" in view["tool_calls"][0]["arguments"]
    assert view["source_truncated_fields"] == ["tool_calls[0].arguments"]
    assert node.tool_calls[0]["arguments"] == value


def test_malformed_function_metadata_does_not_make_projection_fail():
    node = TrajectoryNode("A:7", "A", "Worker", "team_member", "assistant",
                          tool_calls=[{"id": "call7", "function": "incomplete source metadata"}])
    assert json.loads(node_text(node))["tool_calls"] == node.tool_calls


@pytest.mark.asyncio
async def test_public_summary_entry_uses_previews_without_extra_calls_or_total_caps():
    observed = []
    original = "A" * 30000
    class Model:
        async def invoke(self, messages, tools, **kwargs):
            envelope = json.loads(messages[-1]["content"])
            observed.append(envelope)
            assert "max_tokens" not in kwargs
            if "primary" in envelope["input"]:
                projected = envelope["input"]["primary"][0]
                assert len(projected["tool_calls"][0]["arguments"]["content"]) <= 1000
                assert "previewed_fields" in messages[0]["content"]
            return {"tool_calls": [{"name": "report_summary", "arguments": {
                "narrative": "Recorded write operation; omitted code was not verified.", "problems": [], "display_summary": "Operation recorded."}}]}
    node = TrajectoryNode("A:0", "A", "Worker", "team_member", "assistant", turn_id="t1",
                          tool_calls=[{"name": "write_file", "arguments": {"file_path": "game.html", "content": original}}])
    tree = await summarize_trajectory(model=Model(), nodes_by_agent={"A": [node]})
    assert tree.status == "completed" and not tree.warnings and len(observed) == 2
    assert node.tool_calls[0]["arguments"]["content"] == original
