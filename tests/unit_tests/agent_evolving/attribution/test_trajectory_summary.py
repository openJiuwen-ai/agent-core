"""Runtime boundaries, problem recovery, readable time and model-provider failure contracts."""

import asyncio
import json
import logging
from dataclasses import replace

import pytest

from openjiuwen.agent_evolving.attribution.nodes import TrajectoryNode
from openjiuwen.agent_evolving.attribution.trajectory_summary import SummaryLimits, summarize_trajectory
from openjiuwen.agent_evolving.attribution.trajectory_summary.prepare import node_text, prepare
from openjiuwen.agent_evolving.attribution.trajectory_summary.models import format_time_ns


def node(index, agent="A", turn="t1", **kwargs):
    return TrajectoryNode(f"{agent}:{index}", agent, agent, "team_member", "assistant", turn_id=turn,
                          turn_number=1 if turn == "t1" else 2, content="work", **kwargs)


def reply(report, **extra):
    return {"tool_calls": [{"function": {"name": "report_summary", "arguments": json.dumps(report)}}], **extra}


class Model:
    def __init__(self, issues=False):
        self.calls = []
        self.issues = issues

    async def invoke(self, messages, tools, **kwargs):
        envelope = json.loads(messages[-1]["content"])
        self.calls.append((envelope, messages, tools, kwargs))
        payload = envelope["input"]
        if "primary" in payload:
            problems = [{"kind": "tool_error", "summary": "Validation failed and requires attention",
                         "node_ids": [n["node_id"]], "state": "unresolved"}
                        for n in payload["primary"] if n["error"] and self.issues]
            text = payload["agent_id"] + " performed the observed work"
        else:
            problems = [{k: p[k] for k in ("kind", "summary", "state", "source_problem_ids", "resolution_node_ids")}
                        for p in payload["problems"]]
            text = "Combined actions, verification and ending state"
        return reply({"narrative": text, "problems": problems, "display_summary": "Readable user outcome"})


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["zh", "cn", "zh-CN", "en", "en-US"])
async def test_language_selection_reaches_all_levels_and_structured_fields(language):
    from openjiuwen.agent_evolving.attribution.trajectory_summary.prompts import _PROMPT_DIR
    chinese = language in {"zh", "cn", "zh-CN"}
    expected = "已记录关键操作、结果和验证缺口。" if chinese else "Recorded actions, results and verification gaps."
    scopes = set()
    class LanguageModel(Model):
        async def invoke(self, messages, tools, **kwargs):
            envelope = json.loads(messages[-1]["content"])
            scopes.add(envelope["purpose"].split(":", 1)[0])
            stage = envelope["purpose"].split(":", 1)[0]
            selected = {"common", "segment" if stage == "segment" else "merge"}
            if stage == "root":
                selected.add("root")
            for name in ("common", "segment", "merge", "root"):
                section = (_PROMPT_DIR / ("zh" if chinese else "en") / f"{name}.md").read_text(encoding="utf-8").strip()
                assert (section in messages[0]["content"]) == (name in selected)
            assert ("输出语言：中文" if chinese else "Output language: English") in messages[0]["content"]
            assert "report_summary" in messages[0]["content"] and "narrative" in messages[0]["content"]
            guidance = envelope["output_guidance"]
            assert any("\u4e00" <= c <= "\u9fff" for c in guidance) == chinese
            assert ("提交" if chinese else "Submit") in tools[0]["function"]["description"]
            result = await super().invoke(messages, tools, **kwargs)
            report = json.loads(result["tool_calls"][0]["function"]["arguments"])
            report.update(narrative=expected, display_summary=expected)
            for problem in report["problems"]:
                problem["summary"] = expected
            return reply(report)
    opposite_input = "Original English trajectory" if chinese else "原始中文轨迹，需按界面语言输出"
    groups = {"A": [replace(node(0), error=True, content=opposite_input), node(1), node(2, turn="t2")],
              "B": [node(0, "B")]}
    tree = await summarize_trajectory(model=LanguageModel(issues=True), nodes_by_agent=groups,
                                      language=language, limits=SummaryLimits(target_nodes=1))
    assert scopes == {"segment", "turn", "agent", "root"} and tree.status == "completed"
    assert tree.trajectory_summary == expected and tree.display_summary == expected
    assert all(a.summary == expected for a in tree.agent_summaries.values())
    assert all(t.narrative == expected for ts in tree.turns.values() for t in ts)
    assert all(s.narrative == expected for ts in tree.turns.values() for t in ts for s in t.segments)
    assert tree.problems[0].summary == expected and tree.problems[0].locations[0].node_ids == ["A:0"]


@pytest.mark.parametrize("language", ["zh", "en"])
def test_prompt_files_are_cached_across_stages_and_requests(language, monkeypatch):
    from pathlib import Path
    from openjiuwen.agent_evolving.attribution.trajectory_summary.prompts import _load_prompt, summary_prompts
    read_text = Path.read_text
    reads = []
    def track(path, *args, **kwargs):
        reads.append((path.parent.name, path.name))
        return read_text(path, *args, **kwargs)
    _load_prompt.cache_clear()
    monkeypatch.setattr(Path, "read_text", track)
    try:
        for _ in range(2):
            for stage in ("segment", "turn", "agent", "root"):
                result = summary_prompts(language, stage)
                assert ("display_summary" in result["report"]) == (stage == "root")
                assert ("display_summary" in result["retry"]) == (stage == "root")
        assert sorted(reads) == [(language, name + ".md") for name in ("common", "merge", "root", "segment")]
    finally:
        _load_prompt.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["zh", "en"])
async def test_retry_feedback_uses_selected_language_without_changing_schema(language):
    class Retry(Model):
        async def invoke(self, messages, tools, **kwargs):
            first = not self.calls
            result = await super().invoke(messages, tools, **kwargs)
            return reply({"problems": []}) if first else result
    model = Retry()
    tree = await summarize_trajectory(model=model, nodes_by_agent={"A": [node(0)]}, language=language)
    feedback = model.calls[1][0]["retry_feedback"]
    assert ("上次返回未通过校验" if language == "zh" else "Previous response failed") in feedback
    assert "missing narrative" in feedback and "report_summary" in feedback
    assert tree.status == "completed" and len(model.calls) == 3


def test_grouping_follows_runtime_turns_and_primary_nodes_are_unique():
    nodes = [node(0), replace(node(1), role="user"), node(2, turn="t2"), node(3, turn="t1")]
    tree, primary = prepare({"A": nodes}, SummaryLimits(target_nodes=1))
    assert [t.turn_id for t in tree.turns["A"]] == ["t1", "t2"]
    assert [s.start_node_id for s in tree.turns["A"][0].segments] == ["A:0", "A:1", "A:3"]
    assert sorted(n.node_id for group in primary.values() for n in group) == ["A:0", "A:1", "A:2", "A:3"]
    assert all(len({n.turn_id for n in group}) == 1 for group in primary.values())


def test_48_nodes_are_not_split_by_character_count():
    nodes = [replace(node(i), content="large text " * 3000) for i in range(49)]
    _, primary = prepare({"A": nodes}, SummaryLimits(target_nodes=48))
    assert [len(group) for group in primary.values()] == [48, 1]


def test_tool_return_boundary_adjustment_never_crosses_turn():
    call = replace(node(1), tool_calls=[{"id": "c1"}])
    result = replace(node(2), role="tool", tool_call_id="c1")
    _, primary = prepare({"A": [node(0), call, result, node(3)]}, SummaryLimits(target_nodes=2))
    assert [n.node_id for n in primary["A:segment:1"]] == ["A:0", "A:1", "A:2"]
    tree, primary = prepare({"A": [node(0), call, replace(result, turn_id="t2")]}, SummaryLimits(target_nodes=2))
    assert len(tree.turns["A"]) == 2 and primary["A:segment:1"][-1].node_id == "A:1"


def test_time_projection_keeps_original_nanoseconds_and_only_cleans_ansi():
    stamp = 1793763227628526200
    original = replace(node(0), content="\x1b[31mFAIL\x1b[0m\n  code unchanged\n", start_ts_ns=stamp,
                       tool_calls=[{"name": "write_file", "arguments": {"content": "full code " * 10000}}])
    projected = json.loads(node_text(original))
    assert projected["start_time"] == format_time_ns(stamp)
    assert ".628 " in projected["start_time"]
    assert "start_ts_ns" not in projected
    assert projected["content"] == "FAIL\n  code unchanged\n"
    assert len(projected["tool_calls"][0]["arguments"]["content"]) < 1100
    assert projected["previewed_fields"]["tool_calls[0].arguments.content"] == 100000
    assert original.tool_calls[0]["arguments"]["content"] == "full code " * 10000
    assert original.start_ts_ns == stamp and "\x1b" in original.content


def test_turn_inputs_distinguish_internal_sender_from_external_user():
    message = '<team-clock now="2026-11-04 11:26:00 +08:00"/><team-inbound from="worker" time="2026-11-04 11:25:48 +08:00">Teammate tool approval request</team-inbound>'
    internal = replace(node(0), role="user", content=message, start_ts_ns=1793762760000000000)
    external = replace(node(1, "main"), role="user", kind="main_agent", content="Please fix this result")
    tree, _ = prepare({"A": [internal], "main": [external]}, SummaryLimits())
    one, two = tree.turns["A"][0].inputs[0], tree.turns["main"][0].inputs[0]
    assert one.kind == "approval_request" and one.source == "agent" and one.sender_name == "worker"
    assert one.node_id == "A:0" and one.sent_at and one.observed_at
    assert two.source == "user" and two.text == external.content
    exported = tree.to_dict()["turns"]["A"][0]
    assert "inputs" in exported and "triggers" not in exported and "start_time" in exported
    quoted = replace(external, content='Explain <team-inbound from="example" time="2026-11-04 11:25:48 +08:00"> syntax')
    quoted_tree, _ = prepare({"main": [quoted]}, SummaryLimits())
    received = quoted_tree.turns["main"][0].inputs[0]
    assert received.source == "user" and received.sender_name is None and received.sent_at is None


@pytest.mark.asyncio
async def test_raw_inputs_and_roles_survive_classification_without_changing_turns():
    wrapped = '你收到一条消息：\n' + json.dumps({"source": "web", "origin_kind": "external_user_authored",
        "type": "user input", "content": "Make a game", "timestamp": "2026-11-05 15:36:50"})
    reminder = "<system-reminder>Runtime status</system-reminder>"
    approval = "user_inputs={} raw_inputs=None interrupt_answers={'approved': False}"
    nodes = [replace(node(0), kind="main_agent", role="user", content="Make a game"),
             replace(node(1), kind="main_agent", role="user", content=wrapped),
             replace(node(2), kind="main_agent", role="user", content=reminder),
             replace(node(3), kind="main_agent", role="user", content=approval)]
    model = Model()
    tree = await summarize_trajectory(model=model, nodes_by_agent={"A": nodes})
    turn = tree.turns["A"][0]
    assert len(tree.turns["A"]) == 1 and len(turn.segments) == 1
    assert [x.text for x in turn.inputs] == [n.content for n in nodes]
    assert [(x.source, x.kind) for x in turn.inputs] == [
        ("user", "user_input"), ("user", "user_input"), ("system", "system_context"), ("runtime", "approval_response")]
    primary = model.calls[0][0]["input"]["primary"]
    assert [x["role"] for x in primary] == ["user"] * 4
    assert [x["content"] for x in primary] == [n.content for n in nodes]
    assert tree.to_dict()["turns"]["A"][0]["inputs"][1]["text"] == wrapped


@pytest.mark.asyncio
async def test_single_agent_and_multi_agent_reuse_same_hierarchy():
    one = await summarize_trajectory(model=Model(), nodes_by_agent={"A": [node(0)]})
    assert one.agent_summaries == {} and one.status == "completed"
    assert one.display_summary == "Readable user outcome"
    many = await summarize_trajectory(model=Model(), nodes_by_agent={"A": [node(0), node(1, turn="t2")], "B": [node(0, "B")]})
    assert list(many.agent_summaries) == ["A", "B"] and many.status == "completed"
    assert many.view("A", "t1", "A:segment:1")["start_node_id"] == "A:0"


@pytest.mark.asyncio
async def test_no_local_input_or_output_caps_or_extra_paging():
    class Long(Model):
        async def invoke(self, messages, tools, **kwargs):
            result = await super().invoke(messages, tools, **kwargs)
            assert "max_tokens" not in kwargs
            assert "maxLength" not in json.dumps(tools)
            result["tool_calls"][0]["function"]["arguments"] = json.dumps({
                "narrative": "important " * 3000, "problems": [], "display_summary": "Readable result"})
            return result
    model = Long()
    tree = await summarize_trajectory(model=model, nodes_by_agent={"A": [replace(node(0), content="full source " * 20000)]})
    assert tree.status == "completed" and len(tree.trajectory_summary) > 20000
    assert len(model.calls) == 2
    assert model.calls[0][0]["input"]["primary"][0]["content"] == "full source " * 20000


@pytest.mark.asyncio
async def test_no_material_issue_is_valid_even_when_upstream_has_error_flag():
    tree = await summarize_trajectory(model=Model(), nodes_by_agent={"A": [replace(node(0), error=True)]})
    assert tree.status == "completed" and tree.problems == []


@pytest.mark.asyncio
async def test_invalid_auxiliary_evidence_does_not_discard_valid_text():
    class Invalid(Model):
        async def invoke(self, messages, tools, **kwargs):
            if "primary" in json.loads(messages[-1]["content"])["input"]:
                return reply({"narrative": "Useful account", "problems": [{"kind": "tool_error", "summary": "bad",
                              "node_ids": ["outside:1"]}]})
            return await super().invoke(messages, tools, **kwargs)
    tree = await summarize_trajectory(model=Invalid(), nodes_by_agent={"A": [node(0)]})
    assert tree.turns["A"][0].segments[0].narrative == "Useful account"
    assert tree.problems == [] and any(w["code"] == "problem_rejected" for w in tree.warnings)


@pytest.mark.asyncio
@pytest.mark.parametrize("refs", [[" A:0 ", "A:0"], ["0"], [0]])
async def test_leaf_reference_format_is_normalized_without_retry_or_lost_provenance(refs):
    class LocalReferences(Model):
        async def invoke(self, messages, tools, **kwargs):
            response = await super().invoke(messages, tools, **kwargs)
            report = json.loads(response["tool_calls"][0]["function"]["arguments"])
            if "primary" in self.calls[-1][0]["input"]:
                report["problems"] = [{"kind": "tool_error", "summary": "A material failure recovered",
                    "node_ids": refs, "state": "resolved", "resolution_node_ids": [1]}]
            return reply(report)
    model = LocalReferences()
    tree = await summarize_trajectory(model=model, nodes_by_agent={"A": [node(0), node(1)]})
    assert tree.status == "completed" and tree.warnings == [] and len(model.calls) == 2
    assert tree.problems[0].locations[0].node_ids == ["A:0"]
    assert tree.problems[0].resolution_node_ids == ["A:1"]
    assert tree.problems[0].source_problem_ids == ["A:segment:1:p0"]


@pytest.mark.asyncio
@pytest.mark.parametrize("patch, reason", [
    ({"node_ids": ["B:0"]}, "node_ids invalid or outside Segment"),
    ({"node_ids": ["A:99"]}, "node_ids invalid or outside Segment"),
    ({"node_ids": [True]}, "node_ids invalid or outside Segment"),
    ({"node_ids": []}, "node_ids must be a nonempty array"),
    ({"kind": "unsupported"}, "invalid problem kind"),
    ({"summary": " "}, "missing or empty problem summary"),
    ({"state": "resolved", "resolution_node_ids": ["B:0"]}, "resolution_node_ids invalid or outside Segment"),
])
async def test_invalid_leaf_reports_keep_summary_and_identify_candidate_without_retry(patch, reason):
    class InvalidLeaf(Model):
        async def invoke(self, messages, tools, **kwargs):
            response = await super().invoke(messages, tools, **kwargs)
            report = json.loads(response["tool_calls"][0]["function"]["arguments"])
            if "primary" in self.calls[-1][0]["input"]:
                report["problems"] = [{"kind": "tool_error", "summary": "Material issue", "node_ids": ["A:0"], **patch}]
            return reply(report)
    model = InvalidLeaf()
    tree = await summarize_trajectory(model=model, nodes_by_agent={"A": [node(0)]})
    assert len(model.calls) == 2 and tree.status == "degraded"
    assert tree.turns["A"][0].segments[0].narrative and tree.problems == []
    assert len(tree.warnings) == 1
    assert tree.warnings[0]["message"].startswith("candidate 0: " + reason)
    assert tree.warnings[0]["segment_id"] == "A:segment:1"


def test_numeric_reference_does_not_choose_between_ambiguous_local_ids():
    from openjiuwen.agent_evolving.attribution.trajectory_summary.summarize import _Runner
    with pytest.raises(ValueError, match="invalid or outside Segment"):
        _Runner._leaf_ids([0], {"A:0", "A:branch:0"}, {"0": ["A:0", "A:branch:0"]}, "node_ids")


@pytest.mark.asyncio
async def test_internal_user_correction_and_idempotent_send_are_not_tool_failures():
    class Reports(Model):
        async def invoke(self, messages, tools, **kwargs):
            payload = json.loads(messages[-1]["content"])["input"]
            if "primary" in payload:
                n = payload["primary"][0]
                return reply({"narrative": "Recorded input", "problems": [{"kind": "user_correction" if n["role"] == "user" else "tool_error",
                    "summary": "Observed event", "node_ids": [n["node_id"]]}]})
            return await super().invoke(messages, tools, **kwargs)
    internal = replace(node(0), role="user", content='<team-inbound from="leader">Stop and report</team-inbound>')
    skip = replace(node(1), role="tool", name="send_file_to_user", content="文件已发送过，跳过重复投递")
    tree = await summarize_trajectory(model=Reports(), nodes_by_agent={"A": [internal, skip]}, limits=SummaryLimits(target_nodes=1))
    assert tree.turns["A"][0].segments[0].problems[0].kind == "other"
    assert tree.turns["A"][0].segments[1].problems == []


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery_level", ["turn", "agent", "root"])
async def test_recovery_is_updated_at_the_available_scope_with_original_evidence(recovery_level):
    class Recover(Model):
        async def invoke(self, messages, tools, **kwargs):
            envelope = json.loads(messages[-1]["content"])
            result = await super().invoke(messages, tools, **kwargs)
            if envelope["purpose"].startswith(recovery_level + ":"):
                report = json.loads(result["tool_calls"][0]["function"]["arguments"])
                for p in report["problems"]:
                    p.update(state="resolved", summary="The original failure was fixed and verified", resolution_node_ids=["A:1"])
                return reply(report)
            return result
    nodes = [replace(node(0), role="tool", error=True, trace_id="trace", span_id="span"),
             node(1, turn="t1" if recovery_level == "turn" else "t2")]
    groups = {"A": nodes}
    if recovery_level == "agent":
        groups["B"] = [node(0, "B")]
    tree = await summarize_trajectory(model=Recover(issues=True), nodes_by_agent=groups, limits=SummaryLimits(target_nodes=1))
    first = tree.turns["A"][0].segments[0].problems[0]
    assert first.state == "unresolved"  # Historical observation is not overwritten.
    final = tree.problems[0]
    assert final.state == "resolved" and final.resolution_node_ids == ["A:1"]
    assert final.source_problem_ids == [first.problem_id]
    assert final.locations[0].node_refs == [{"node_id": "A:0", "trace_id": "trace", "span_id": "span"}]
    assert final.problem_id == "problem:0" and first.problem_id != final.problem_id


@pytest.mark.asyncio
async def test_bad_recovery_reference_retains_known_issue_and_technical_summary(caplog):
    caplog.set_level(logging.INFO)
    class BadRecovery(Model):
        async def invoke(self, messages, tools, **kwargs):
            result = await super().invoke(messages, tools, **kwargs)
            if json.loads(messages[-1]["content"])["purpose"].startswith("root:"):
                report = json.loads(result["tool_calls"][0]["function"]["arguments"])
                report["problems"][0].update(state="resolved", resolution_node_ids=["missing"])
                return reply(report)
            return result
    tree = await summarize_trajectory(model=BadRecovery(issues=True), nodes_by_agent={"A": [replace(node(0), error=True)]})
    assert tree.problems[0].state == "unresolved" and tree.trajectory_summary
    assert tree.status == "completed" and not tree.warnings
    assert "scope=root:root" in caplog.text and "recovery evidence" in caplog.text


@pytest.mark.asyncio
async def test_turn_merge_receives_input_source_and_mixed_kind_merge_preserves_originals(caplog):
    caplog.set_level(logging.INFO)
    class Mixed(Model):
        async def invoke(self, messages, tools, **kwargs):
            result = await super().invoke(messages, tools, **kwargs)
            envelope = json.loads(messages[-1]["content"])
            report = json.loads(result["tool_calls"][0]["function"]["arguments"])
            payload = envelope["input"]
            if "primary" in payload:
                report["problems"] = [{"kind": "conflict" if payload["primary"][0]["role"] == "user" else "tool_error",
                    "summary": "Material issue", "node_ids": [payload["primary"][0]["node_id"]], "state": "unresolved"}]
            elif envelope["purpose"].startswith("turn:"):
                assert payload["inputs"][0]["source"] == "agent"
                assert payload["inputs"][0]["sender_name"] == "leader"
                report["problems"] = [{"summary": "Invalid merge without explicit kind", "state": "unresolved",
                    "source_problem_ids": [x for p in payload["problems"] for x in p["source_problem_ids"]]}]
            return reply(report)
    nodes = [replace(node(0), role="user", content='<team-inbound from="leader">Correct the required output</team-inbound>'), node(1)]
    tree = await summarize_trajectory(model=Mixed(), nodes_by_agent={"A": nodes}, limits=SummaryLimits(target_nodes=1))
    assert len(tree.problems) == 2 and len(tree.turns["A"][0].problems) == 2
    assert {p.kind for p in tree.problems} == {"conflict", "tool_error"}
    assert tree.status == "completed" and not tree.warnings
    assert "mixed problem kinds" in caplog.text and "scope=turn:A/t1" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing_sources", "unknown_source", "blank_summary", "no_updates", "duplicates"])
async def test_problem_update_fallback_preserves_provenance_without_more_model_calls(mode, caplog):
    caplog.set_level(logging.INFO)
    class Update(Model):
        async def invoke(self, messages, tools, **kwargs):
            result = await super().invoke(messages, tools, **kwargs)
            if json.loads(messages[-1]["content"])["purpose"].startswith("root:"):
                report = json.loads(result["tool_calls"][0]["function"]["arguments"])
                assert tools[0]["function"]["parameters"]["properties"]["problems"]["items"]["properties"]["source_problem_ids"]["minItems"] == 1
                if mode == "missing_sources":
                    report["problems"][0]["source_problem_ids"] = []
                elif mode == "unknown_source":
                    report["problems"][0]["source_problem_ids"] = ["not-a-source"]
                elif mode == "blank_summary":
                    report["problems"][0]["summary"] = " "
                elif mode == "no_updates":
                    report["problems"] = None
                else:
                    source = report["problems"][0]["source_problem_ids"][0]
                    report["problems"][0]["source_problem_ids"] = [source, f" {source} "]
                return reply(report)
            return result
    model = Update(issues=True)
    tree = await summarize_trajectory(model=model, nodes_by_agent={"A": [replace(node(0), error=True)]})
    assert tree.status == "completed" and not tree.warnings and len(model.calls) == 2
    assert len(tree.problems) == 1 and tree.problems[0].state == "unresolved"
    assert tree.problems[0].locations[0].node_ids == ["A:0"]
    assert tree.problems[0].source_problem_ids == ["A:segment:1:p0"]
    if mode != "duplicates":
        assert "problem fallback scope=root:root" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_update", [False, True])
async def test_all_merge_levels_use_compact_problems_but_preserve_full_output_evidence(invalid_update):
    stages = set()
    class Compact(Model):
        async def invoke(self, messages, tools, **kwargs):
            response = await super().invoke(messages, tools, **kwargs)
            envelope = self.calls[-1][0]
            if "primary" not in envelope["input"]:
                stages.add(envelope["purpose"].split(":", 1)[0])
                for problem in envelope["input"]["problems"]:
                    assert set(problem) == {"kind", "summary", "state", "source_problem_ids",
                                            "resolution_node_ids", "locations"}
                    assert problem["source_problem_ids"] and problem["locations"]
                    for location in problem["locations"]:
                        assert set(location) == {"agent_id", "turn_id", "segment_id", "node_ids"}
                if invalid_update:
                    report = json.loads(response["tool_calls"][0]["function"]["arguments"])
                    report["problems"][0]["source_problem_ids"] = ["unknown-source"]
                    return reply(report)
            return response
    groups = {"A": [node(0), node(1), node(2, turn="t2")], "B": [node(0, "B")]}
    groups = {agent: [replace(n, error=True, trace_id="trace-" + n.node_id, span_id="span-" + n.node_id)
                      for n in nodes] for agent, nodes in groups.items()}
    model = Compact(issues=True)
    tree = await summarize_trajectory(model=model, nodes_by_agent=groups, limits=SummaryLimits(target_nodes=1))
    assert stages == {"turn", "agent", "root"} and len(model.calls) == 7
    assert tree.status == "completed" and not tree.warnings and len(tree.problems) == 4
    output = tree.to_dict()
    for problem in output["problems"]:
        assert problem["problem_id"] and problem["source"] == "model" and problem["source_problem_ids"]
        for location in problem["locations"]:
            node_id = location["node_ids"][0]
            assert location["node_refs"] == [{"node_id": node_id, "trace_id": "trace-" + node_id,
                                               "span_id": "span-" + node_id}]
    assert all(p.locations[0].node_refs for ts in tree.turns.values() for t in ts for p in t.problems)
    assert all(p.locations[0].node_refs for agent in tree.agent_summaries.values() for p in agent.problems)


@pytest.mark.asyncio
@pytest.mark.parametrize("dismiss", [False, True])
async def test_merged_issue_keeps_complete_sources_when_parent_returns_partial_reference(dismiss):
    class Partial(Model):
        async def invoke(self, messages, tools, **kwargs):
            result = await super().invoke(messages, tools, **kwargs)
            envelope = json.loads(messages[-1]["content"])
            report = json.loads(result["tool_calls"][0]["function"]["arguments"])
            if envelope["purpose"].startswith("agent:") and len(report["problems"]) == 2:
                report["problems"] = [{"kind": "tool_error", "summary": "One incident observed twice",
                    "state": "unresolved", "source_problem_ids": [p["source_problem_ids"][0] for p in report["problems"]]}]
            elif envelope["purpose"].startswith("root:"):
                assert len(report["problems"][0]["source_problem_ids"]) == 2
                report["problems"][0]["source_problem_ids"] = [report["problems"][0]["source_problem_ids"][0]]
                if dismiss:
                    report["dismissed_problem_ids"] = report["problems"][0]["source_problem_ids"]
                    report["problems"] = []
            return reply(report)
    tree = await summarize_trajectory(model=Partial(issues=True), nodes_by_agent={
        "A": [replace(node(0), error=True), replace(node(1, turn="t2"), error=True)], "B": [node(0, "B")]})
    assert tree.status == "completed" and len(tree.problems) == 1
    assert set(tree.problems[0].source_problem_ids) == {"A:segment:1:p0", "A:segment:2:p0"}
    assert [loc.node_ids for loc in tree.problems[0].locations] == [["A:0"], ["A:1"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("dismiss", [False, True])
async def test_missing_problem_is_retained_unless_explicitly_dismissed(dismiss):
    class Omit(Model):
        async def invoke(self, messages, tools, **kwargs):
            envelope = json.loads(messages[-1]["content"])
            if envelope["purpose"].startswith("root:"):
                ids = [x for p in envelope["input"]["problems"] for x in p["source_problem_ids"]]
                return reply({"narrative": "Ending state", "problems": [], "display_summary": "Outcome",
                              "dismissed_problem_ids": ids if dismiss else []})
            return await super().invoke(messages, tools, **kwargs)
    tree = await summarize_trajectory(model=Omit(issues=True), nodes_by_agent={"A": [replace(node(0), error=True)]})
    assert len(tree.problems) == (0 if dismiss else 1)
    assert len(tree.turns["A"][0].segments[0].problems) == 1


@pytest.mark.asyncio
async def test_truncated_model_output_is_retried_not_marked_success():
    events = []
    class Retry(Model):
        async def invoke(self, messages, tools, **kwargs):
            first = not self.calls
            result = await super().invoke(messages, tools, **kwargs)
            if first:
                result["finish_reason"] = "length"
            return result
    class Sink:
        def report(self, event):
            events.append(event)
    model = Retry()
    tree = await summarize_trajectory(model=model, nodes_by_agent={"A": [node(0)]}, status_sink=Sink())
    assert tree.status == "completed" and len(model.calls) == 3
    assert any(e.status == "failed" and "finish_reason=length" in e.error for e in events)
    assert "model output truncated" in model.calls[1][0]["retry_feedback"]


@pytest.mark.asyncio
async def test_provider_context_overflow_shortens_only_long_fields_and_preserves_source():
    class Overflow(Model):
        async def invoke(self, messages, tools, **kwargs):
            if not self.calls:
                self.calls.append((json.loads(messages[-1]["content"]), messages, tools, kwargs))
                raise ValueError("context_length_exceeded")
            return await super().invoke(messages, tools, **kwargs)
    nodes = [replace(node(i), content="long source " * 3000) for i in range(48)]
    model = Overflow()
    tree = await summarize_trajectory(model=model, nodes_by_agent={"A": nodes})
    second = model.calls[1][0]["input"]["primary"]
    assert len(second) == 48 and [x["node_id"] for x in second] == [n.node_id for n in nodes]
    assert all("truncated_fields" in x for x in second)
    assert all(n.content == "long source " * 3000 for n in nodes)
    assert tree.turns["A"][0].segments[0].status == "degraded"
    assert any(w["code"] == "input_truncated" for w in tree.warnings)


@pytest.mark.asyncio
async def test_root_failure_preserves_all_agents_and_problem_locations():
    class RootFailure(Model):
        async def invoke(self, messages, tools, **kwargs):
            if json.loads(messages[-1]["content"])["purpose"].startswith("root:"):
                raise RuntimeError("offline")
            return await super().invoke(messages, tools, **kwargs)
    tree = await summarize_trajectory(model=RootFailure(issues=True), nodes_by_agent={
        "A": [replace(node(0), error=True)], "B": [node(0, "B")]})
    assert tree.status == "degraded"
    assert "A performed" in tree.trajectory_summary and "B performed" in tree.trajectory_summary
    assert tree.problems[0].locations[0].node_ids == ["A:0"]


@pytest.mark.asyncio
async def test_readable_summary_failure_does_not_discard_technical_result():
    class NoDisplay(Model):
        async def invoke(self, messages, tools, **kwargs):
            result = await super().invoke(messages, tools, **kwargs)
            report = json.loads(result["tool_calls"][0]["function"]["arguments"])
            report.pop("display_summary")
            return reply(report)
    tree = await summarize_trajectory(model=NoDisplay(), nodes_by_agent={"A": [node(0)]})
    assert tree.trajectory_summary and not tree.display_summary
    assert any(w["code"] == "display_summary_missing" for w in tree.warnings)


@pytest.mark.asyncio
async def test_partial_selection_and_summary_read_tools_keep_scope():
    from openjiuwen.agent_evolving.attribution import NodeBackedRuntime
    from openjiuwen.agent_evolving.attribution.snapshot import SlicedNodes
    from openjiuwen.agent_evolving.attribution.tools import build_attribution_tools
    selected = SlicedNodes({"A": [node(1)]})
    selected.whole = {"A": [node(0), node(1), node(2)]}
    tree = await summarize_trajectory(model=Model(), nodes_by_agent=selected)
    assert tree.status == "completed"
    assert not any(w["code"] == "partial_turn" for w in tree.warnings)
    runtime = NodeBackedRuntime(selected)
    runtime.summary_tree = tree
    runtime.trajectory_summary = tree.trajectory_summary
    tools = {tool.card.name: tool for tool in build_attribution_tools(runtime, [])}
    assert "submit_trajectory_summary" not in tools
    assert "view_summary" in tools
    result = await tools["view_summary"].invoke({"agent_id": "A", "turn_id": "t1", "segment_id": "A:segment:1"})
    payload = json.loads(result.data["content"])
    assert payload["start_node_id"] == "A:1"
    assert "problems" not in payload


@pytest.mark.asyncio
async def test_nested_upper_concurrency_and_cancellation_cleanup():
    active, peak = 0, 0
    started = asyncio.Event()
    class Wait(Model):
        async def invoke(self, messages, tools, **kwargs):
            nonlocal active, peak
            if json.loads(messages[-1]["content"])["purpose"].startswith("turn:"):
                active += 1
                peak = max(peak, active)
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    active -= 1
            return await super().invoke(messages, tools, **kwargs)
    task = asyncio.create_task(summarize_trajectory(model=Wait(), nodes_by_agent={
        a: [node(i, a, f"t{i // 2}") for i in range(6)] for a in ["A", "B"]},
        limits=SummaryLimits(target_nodes=1, concurrency=2)))
    await asyncio.wait_for(started.wait(), 2)
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert active == 0 and peak == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("merge_level", ["turn", "agent"])
async def test_local_merge_starts_before_unrelated_segment_finishes(merge_level):
    slow_started, merge_started = asyncio.Event(), asyncio.Event()
    slow_finished = False
    active, peak = 0, 0
    class Pipeline(Model):
        async def invoke(self, messages, tools, **kwargs):
            nonlocal slow_finished, active, peak
            envelope = json.loads(messages[-1]["content"])
            purpose = envelope["purpose"]
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0)
                if purpose.startswith("segment:") and envelope["input"]["agent_id"] == "B":
                    slow_started.set()
                    await merge_started.wait()
                    slow_finished = True
                elif purpose.startswith(merge_level + ":"):
                    await slow_started.wait()
                    assert not slow_finished
                    merge_started.set()
                elif purpose.startswith("root:"):
                    assert slow_finished and merge_started.is_set()
                return await super().invoke(messages, tools, **kwargs)
            finally:
                active -= 1
    # The slow subject is listed first, but the fast subject must merge first.
    groups = {"B": [node(0, "B")], "A": [node(0), node(1, turn="t1" if merge_level == "turn" else "t2")]}
    model = Pipeline()
    tree = await asyncio.wait_for(summarize_trajectory(model=model, nodes_by_agent=groups,
        limits=SummaryLimits(target_nodes=1, concurrency=2)), 3)
    assert tree.status == "completed" and len(model.calls) == 5
    assert active == 0 and peak == 2
    assert list(tree.agent_summaries) == ["B", "A"]
    assert [s.start_node_id for t in tree.turns["A"] for s in t.segments] == ["A:0", "A:1"]


@pytest.mark.asyncio
async def test_single_segment_turns_keep_37_call_path_and_all_output_scopes():
    model = Model()
    groups = {a: [node(i, a, f"turn{i}") for i in range(count)] for a, count in [("A", 29), ("B", 2), ("C", 2)]}
    tree = await summarize_trajectory(model=model, nodes_by_agent=groups)
    purposes = [call[0]["purpose"].split(":", 1)[0] for call in model.calls]
    assert purposes.count("segment") == 33 and purposes.count("agent") == 3
    assert purposes.count("turn") == 0 and purposes.count("root") == 1
    assert len(model.calls) == 37 and tree.status == "completed"
    assert sum(len(ts) for ts in tree.turns.values()) == 33
    assert all(t.narrative == t.segments[0].narrative for ts in tree.turns.values() for t in ts)


@pytest.mark.asyncio
async def test_cancellation_cleans_simultaneous_segment_and_turn_requests():
    merging = asyncio.Event()
    active = 0
    class Block(Model):
        async def invoke(self, messages, tools, **kwargs):
            nonlocal active
            envelope = json.loads(messages[-1]["content"])
            block = (envelope["purpose"].startswith("segment:") and envelope["input"]["agent_id"] == "B")
            block = block or envelope["purpose"].startswith("turn:")
            if block:
                active += 1
                try:
                    if envelope["purpose"].startswith("turn:"):
                        merging.set()
                    await asyncio.Event().wait()
                finally:
                    active -= 1
            return await super().invoke(messages, tools, **kwargs)
    task = asyncio.create_task(summarize_trajectory(model=Block(), nodes_by_agent={
        "B": [node(0, "B")], "A": [node(0), node(1)]}, limits=SummaryLimits(target_nodes=1, concurrency=2)))
    try:
        await asyncio.wait_for(merging.wait(), 3)
        assert active == 2
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert active == 0


@pytest.mark.asyncio
async def test_failed_segment_still_reaches_parent_and_preserves_other_branches():
    class Failure(Model):
        async def invoke(self, messages, tools, **kwargs):
            envelope = json.loads(messages[-1]["content"])
            if "primary" in envelope["input"] and envelope["input"]["primary"][0]["node_id"] == "A:0":
                raise RuntimeError("offline")
            return await super().invoke(messages, tools, **kwargs)
    tree = await asyncio.wait_for(summarize_trajectory(model=Failure(), nodes_by_agent={
        "A": [node(0), node(1)], "B": [node(0, "B")]}, limits=SummaryLimits(target_nodes=1, retries=0)), 3)
    assert tree.status == "degraded" and tree.trajectory_summary
    assert tree.agent_summaries["B"].status == "completed"
    turn = tree.turns["A"][0]
    assert turn.status == "degraded" and turn.segments[0].status == "failed"
    assert turn.segments[0].start_node_id == "A:0" and turn.segments[1].status == "completed"


@pytest.mark.asyncio
async def test_timeout_retains_segment_identity_and_failed_status():
    class Timeout:
        async def invoke(self, *args, **kwargs):
            await asyncio.Event().wait()
    tree = await summarize_trajectory(model=Timeout(), nodes_by_agent={"A": [node(0)]},
                                      limits=SummaryLimits(timeout_s=0.01, retries=0))
    assert tree.status == "failed" and tree.turns["A"][0].segments[0].start_node_id == "A:0"


def test_ambiguous_trace_is_not_assigned_to_last_turn():
    from openjiuwen.agent_evolving.attribution.turns import TurnResolver
    records = [{"traceId": "shared", "spanId": sid, "attributes": [
        {"key": "openjiuwen.turn.id", "value": {"stringValue": tid}},
        {"key": "openjiuwen.turn.number", "value": {"intValue": str(i)}}]}
        for i, (sid, tid) in enumerate([("one", "t1"), ("two", "t2")], 1)]
    records.append({"traceId": "shared", "spanId": "child", "parentSpanId": "one"})
    resolver = TurnResolver(records)
    assert resolver.resolve("shared", "child") == ("t1", 1)
    assert resolver.resolve("shared", "unknown") == (None, None)
