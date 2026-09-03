# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import re

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UsageMetadata
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.session.tracer.span import TraceAgentSpan
from openjiuwen.core.session.trajectory.collector import TrajectoryCollector
from openjiuwen.core.session.trajectory.config import TrajectoryConfig
from openjiuwen.core.session.trajectory.handler import (
    _MAX_PENDING_SPANS,
    TrajectoryAgentHandler,
    TrajectoryWorkflowHandler,
)
from openjiuwen.core.session.trajectory.writer import read_trajectory

pytestmark = pytest.mark.asyncio

TRACE_ID = "trace-1"
SESSION_ID = "s1"


@pytest.fixture
def collector(tmp_path):
    return TrajectoryCollector(SESSION_ID, TrajectoryConfig(root=str(tmp_path)))


@pytest.fixture
def handler(collector):
    return TrajectoryAgentHandler(collector)


def _span(invoke_id: str, trace_id: str = TRACE_ID) -> TraceAgentSpan:
    return TraceAgentSpan(invoke_id=invoke_id, trace_id=trace_id)


def _assistant_message(**overrides) -> AssistantMessage:
    fields = {
        "content": "the answer",
        "reasoning_content": "thinking...",
        "tool_calls": [ToolCall(id="call-1", type="function", name="search", arguments='{"q": "x"}')],
        "usage_metadata": UsageMetadata(
            model_name="test-model",
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            cache_tokens=80,
            total_cost=0.5,
            total_latency=1.2,
        ),
    }
    fields.update(overrides)
    return AssistantMessage(**fields)


def _trace_file(tmp_path, trace_id: str = TRACE_ID):
    # Top-level runs live at <YYYY-MM-DD_HH-MM-SS>_<trace_id>/trajectory.json.
    files = list((tmp_path / SESSION_ID).glob(f"*_{trace_id}/trajectory.json"))
    assert len(files) == 1
    return files[0]


async def _read(collector, tmp_path):
    await collector.finalize_all()
    return read_trajectory(_trace_file(tmp_path))


async def test_snapshot_is_readable_mid_run_before_finalize(handler, collector, tmp_path):
    # The file must reflect completed steps while the run is still in flight,
    # carrying the running totals so far; an empty ended_at marks it as
    # unfinished (long-lived hosts may never finalize mid-flight).
    await handler.on_llm_start(_span("i1"), inputs={"inputs": []}, instance_info={"class_name": "m", "type": "llm"})
    await handler.on_llm_end(_span("i1"), outputs={"outputs": _assistant_message()})

    trajectory = read_trajectory(_trace_file(tmp_path))
    assert len(trajectory.steps) == 1
    assert trajectory.steps[0].message == "the answer"
    assert trajectory.final_metrics is not None
    assert trajectory.final_metrics.total_tokens == 120
    assert trajectory.final_metrics.llm_call_count == 1
    assert trajectory.final_metrics.ended_at == ""


async def test_llm_end_produces_agent_step_with_metrics(handler, collector, tmp_path):
    await handler.on_llm_start(_span("i1"), inputs={"inputs": []}, instance_info={"class_name": "m", "type": "llm"})
    await handler.on_llm_end(_span("i1"), outputs={"outputs": _assistant_message()})

    trajectory = await _read(collector, tmp_path)
    step = trajectory.steps[0]
    assert step.role == "agent"
    assert step.message == "the answer"
    assert step.reasoning_content == "thinking..."
    assert step.model_name == "test-model"
    assert step.tool_calls[0].tool_call_id == "call-1"
    assert step.tool_calls[0].function_name == "search"
    assert step.metrics.input_tokens == 100
    assert step.metrics.cache_tokens == 80
    assert step.metrics.cost == 0.5
    assert trajectory.final_metrics.llm_call_count == 1
    assert trajectory.final_metrics.total_tokens == 120
    assert trajectory.final_metrics.success is True


async def test_tool_result_links_to_declared_tool_call(handler, collector, tmp_path):
    await handler.on_llm_end(_span("i1"), outputs={"outputs": _assistant_message()})
    await handler.on_plugin_start(
        _span("i2"), inputs={"inputs": {"q": "x"}}, instance_info={"class_name": "search", "type": "tool"}
    )
    await handler.on_plugin_end(_span("i2"), outputs={"outputs": "found it"})

    trajectory = await _read(collector, tmp_path)
    observation_step = trajectory.steps[1]
    assert observation_step.role == "system"
    result = observation_step.observation.results[0]
    assert result.source_call_id == "call-1"
    assert result.content == "found it"
    assert observation_step.extra["function_name"] == "search"
    assert trajectory.final_metrics.tool_call_count == 1


async def test_unmatched_tool_result_has_no_source_call_id(handler, collector, tmp_path):
    await handler.on_plugin_start(
        _span("i1"), inputs={"inputs": {}}, instance_info={"class_name": "lonely", "type": "tool"}
    )
    await handler.on_plugin_end(_span("i1"), outputs={"outputs": "ok"})

    trajectory = await _read(collector, tmp_path)
    assert trajectory.steps[0].observation.results[0].source_call_id is None


async def test_same_tool_called_twice_claims_calls_in_order(handler, collector, tmp_path):
    message = _assistant_message(
        tool_calls=[
            ToolCall(id="call-1", type="function", name="search", arguments="{}"),
            ToolCall(id="call-2", type="function", name="search", arguments="{}"),
        ]
    )
    await handler.on_llm_end(_span("i1"), outputs={"outputs": message})
    for invoke_id in ("i2", "i3"):
        await handler.on_plugin_start(
            _span(invoke_id), inputs={"inputs": {}}, instance_info={"class_name": "search", "type": "tool"}
        )
        await handler.on_plugin_end(_span(invoke_id), outputs={"outputs": "r"})

    trajectory = await _read(collector, tmp_path)
    assert trajectory.steps[1].observation.results[0].source_call_id == "call-1"
    assert trajectory.steps[2].observation.results[0].source_call_id == "call-2"


async def test_llm_error_is_recorded_with_error_code(handler, collector, tmp_path):
    error = build_error(StatusCode.WORKFLOW_EXECUTION_ERROR, reason="boom", workflow="w")
    await handler.on_llm_start(_span("i1"), inputs={"inputs": []}, instance_info={"class_name": "m", "type": "llm"})
    await handler.on_llm_error(_span("i1"), error=error)

    trajectory = await _read(collector, tmp_path)
    step = trajectory.steps[0]
    assert step.error.error_code == StatusCode.WORKFLOW_EXECUTION_ERROR.code
    assert "boom" in step.error.message
    assert trajectory.final_metrics.error_count == 1
    assert trajectory.final_metrics.success is False
    assert trajectory.final_metrics.errors[0].step_id == step.step_id


async def test_plain_exception_error_has_no_code(handler, collector, tmp_path):
    await handler.on_plugin_start(_span("i1"), inputs={"inputs": {}}, instance_info={"class_name": "t", "type": "tool"})
    await handler.on_plugin_error(_span("i1"), error=RuntimeError("tool blew up"))

    trajectory = await _read(collector, tmp_path)
    assert trajectory.steps[0].error.error_code is None
    assert trajectory.steps[0].error.message == "tool blew up"


async def test_stream_chunks_are_coalesced(handler, collector, tmp_path):
    chunks = [
        AssistantMessage(content="the "),
        AssistantMessage(content="answer"),
        _assistant_message(content=""),
    ]
    await handler.on_llm_end(_span("i1"), outputs={"outputs": chunks})

    trajectory = await _read(collector, tmp_path)
    step = trajectory.steps[0]
    assert step.message == "the answer"
    assert step.metrics.total_tokens == 120
    assert step.tool_calls[0].tool_call_id == "call-1"


async def test_streamed_tool_call_fragments_reassemble(handler, collector, tmp_path):
    # Providers stream one tool call as deltas: id/name arrive in the first
    # fragment, the argument JSON is sliced across the rest. The recorded step
    # must hold the reassembled call, not the last fragment.
    chunks = [
        AssistantMessageChunk(
            content="let me ",
            reasoning_content="thinking ",
            tool_calls=[ToolCall(id="call-7", type="function", name="search", arguments='{"q": ')],
        ),
        AssistantMessageChunk(
            content="look that up",
            reasoning_content="hard",
            tool_calls=[ToolCall(id="", type="function", name="", arguments='"deltas"')],
        ),
        AssistantMessageChunk(
            content="",
            tool_calls=[ToolCall(id="", type="function", name="", arguments="}")],
            usage_metadata=UsageMetadata(
                model_name="test-model",
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
                cache_tokens=80,
                total_cost=0.5,
                total_latency=1.2,
            ),
        ),
    ]
    await handler.on_llm_end(_span("i1"), outputs={"outputs": chunks})

    trajectory = await _read(collector, tmp_path)
    step = trajectory.steps[0]
    assert len(step.tool_calls) == 1, "fragments must merge into one call, not one call per delta"
    call = step.tool_calls[0]
    assert call.tool_call_id == "call-7"
    assert call.function_name == "search"
    assert call.arguments == '{"q": "deltas"}'
    assert step.message == "let me look that up"
    assert step.reasoning_content == "thinking hard"
    assert step.metrics.total_tokens == 120


async def test_truncation_guard_flags_oversized_content(tmp_path):
    collector = TrajectoryCollector(SESSION_ID, TrajectoryConfig(root=str(tmp_path), max_content_chars=10))
    handler = TrajectoryAgentHandler(collector)
    await handler.on_llm_end(
        _span("i1"),
        outputs={"outputs": _assistant_message(content="x" * 100, reasoning_content=None, tool_calls=None)},
    )

    trajectory = await _read(collector, tmp_path)
    step = trajectory.steps[0]
    assert step.message == "x" * 10
    assert step.extra["truncated_fields"] == ["message"]


async def test_totals_accumulate_across_steps(handler, collector, tmp_path):
    for invoke_id in ("i1", "i2"):
        await handler.on_llm_end(_span(invoke_id), outputs={"outputs": _assistant_message(tool_calls=None)})

    trajectory = await _read(collector, tmp_path)
    metrics = trajectory.final_metrics
    assert metrics.llm_call_count == 2
    assert metrics.total_input_tokens == 200
    assert metrics.total_output_tokens == 40
    assert metrics.total_tokens == 240
    assert metrics.total_cache_tokens == 160
    assert metrics.total_cost == 1.0
    assert metrics.started_at != ""
    assert metrics.ended_at != ""


async def test_workflow_end_is_recorded(handler, collector, tmp_path):
    await handler.on_workflow_start(
        _span("i1"), inputs={"inputs": {}}, instance_info={"class_name": "wf", "type": "workflow"}
    )
    await handler.on_workflow_end(_span("i1"), outputs={"outputs": {"result": "done"}})

    trajectory = await _read(collector, tmp_path)
    step = trajectory.steps[0]
    assert step.role == "system"
    assert step.extra["invoke_type"] == "workflow"
    assert "done" in step.message


async def test_runs_split_into_timestamped_files(collector, tmp_path):
    # Two runs (distinct trace ids) must land in separate run directories
    # whose names carry the run's start timestamp, so runs are identifiable
    # and sort chronologically by directory name.
    for trace_id in ("trace-a", "trace-b"):
        await collector.add_step(trace_id, role="agent", invoke_type="llm", message=f"run {trace_id}")
    await collector.finalize_all()

    files = sorted((tmp_path / SESSION_ID).glob("*/trajectory.json"))
    assert len(files) == 2
    for path in files:
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})_(.+)", path.parent.name)
        assert match
        stem_trace = match.group(2)
        trajectory = read_trajectory(path)
        assert stem_trace == trajectory.trajectory_id
        assert trajectory.started_at != ""
        assert trajectory.steps[0].message == f"run {stem_trace}"


async def test_subagent_trajectory_nested_under_parent_run(handler, collector, tmp_path):
    # A trace bound while a parent tool span is open is a subagent: its file
    # must land under the parent run's subagents/ dir, and its header must
    # carry the lineage and identity so the file is self-describing even
    # when moved out of the folder structure.
    await handler.on_llm_end(_span("i1"), outputs={"outputs": _assistant_message()})
    await handler.on_plugin_start(
        _span("i2"), inputs={"inputs": {}}, instance_info={"class_name": "TaskTool", "type": "tool"}
    )
    handler.bind_trace("child-trace", session_id="s1_sub_research", agent_name="research_agent")
    await handler.on_llm_end(
        _span("i3", trace_id="child-trace"), outputs={"outputs": _assistant_message(tool_calls=None)}
    )
    await handler.on_plugin_end(_span("i2"), outputs={"outputs": "done"})
    await collector.finalize_all()

    parent_path = _trace_file(tmp_path)
    child_files = list(parent_path.parent.glob("subagents/*_child-trace.json"))
    assert len(child_files) == 1
    child = read_trajectory(child_files[0])
    assert child.parent_trajectory_id == TRACE_ID
    assert child.agent.name == "research_agent"
    assert child.agent.extra["agent_session_id"] == "s1_sub_research"
    assert read_trajectory(parent_path).parent_trajectory_id is None


async def test_run_bound_after_tool_completes_is_top_level(handler, collector, tmp_path):
    # Once a tool span closes, its trace must stop acting as parent: the next
    # sequential run bound in the same async context is a sibling, not a child.
    await handler.on_llm_end(_span("i1"), outputs={"outputs": _assistant_message()})
    await handler.on_plugin_start(
        _span("i2"), inputs={"inputs": {}}, instance_info={"class_name": "search", "type": "tool"}
    )
    await handler.on_plugin_end(_span("i2"), outputs={"outputs": "ok"})
    handler.bind_trace("next-run", session_id="s2", agent_name="a2")
    await handler.on_llm_end(_span("i3", trace_id="next-run"), outputs={"outputs": _assistant_message(tool_calls=None)})
    await collector.finalize_all()

    path = _trace_file(tmp_path, "next-run")
    assert read_trajectory(path).parent_trajectory_id is None
    assert not list(_trace_file(tmp_path).parent.glob("subagents/*"))


async def test_pending_span_state_is_bounded(handler, collector):
    # A start event whose end never fires (e.g. its task is cancelled
    # mid-call) must not leak its record forever; both handlers cap their
    # pending state, evicting oldest first.
    for index in range(_MAX_PENDING_SPANS + 10):
        await handler.on_llm_start(
            _span(f"llm-{index}"), inputs={"inputs": []}, instance_info={"class_name": "m", "type": "llm"}
        )
    assert len(handler._pending) == _MAX_PENDING_SPANS
    assert f"llm-{_MAX_PENDING_SPANS + 9}" in handler._pending, "newest records must survive eviction"

    workflow_handler = TrajectoryWorkflowHandler(collector)
    for index in range(_MAX_PENDING_SPANS + 10):
        await workflow_handler.on_call_start(f"comp-{index}", metadata={"component_name": "c"})
    assert len(workflow_handler._components) == _MAX_PENDING_SPANS


async def test_capture_failure_does_not_raise(handler, collector, tmp_path):
    class Exploding:
        @property
        def content(self):
            raise RuntimeError("broken payload")

    # Must not propagate into the agent loop.
    await handler.on_llm_end(_span("i1"), outputs={"outputs": Exploding()})


async def test_snapshot_cadence_batches_writes(tmp_path):
    # snapshot_every_n_steps=3: nothing hits disk until the third step, and
    # finalization always flushes the tail regardless of cadence position.
    collector = TrajectoryCollector(SESSION_ID, TrajectoryConfig(root=str(tmp_path), snapshot_every_n_steps=3))
    await collector.add_step(TRACE_ID, role="agent", invoke_type="llm", message="one")
    await collector.add_step(TRACE_ID, role="agent", invoke_type="llm", message="two")
    assert list((tmp_path / SESSION_ID).glob("*/trajectory.json")) == [], "no write before the threshold"

    await collector.add_step(TRACE_ID, role="agent", invoke_type="llm", message="three")
    assert len(read_trajectory(_trace_file(tmp_path)).steps) == 3

    await collector.add_step(TRACE_ID, role="agent", invoke_type="llm", message="four")
    assert len(read_trajectory(_trace_file(tmp_path)).steps) == 3, "step four waits for the next threshold"

    await collector.finalize_all()
    trajectory = read_trajectory(_trace_file(tmp_path))
    assert len(trajectory.steps) == 4, "finalization must flush pending steps"
    assert trajectory.final_metrics is not None


async def test_secrets_are_redacted_by_default(tmp_path):
    collector = TrajectoryCollector(SESSION_ID, TrajectoryConfig(root=str(tmp_path)))
    handler = TrajectoryAgentHandler(collector)
    message = _assistant_message(
        content='calling with "api_key": "sk-abc123def456ghi789jkl"',
        reasoning_content=None,
        tool_calls=[
            ToolCall(
                id="c1", type="function", name="fetch", arguments='{"url": "https://x", "token": "supersecretvalue"}'
            )
        ],
    )
    await handler.on_llm_end(_span("i1"), outputs={"outputs": message})

    trajectory = await _read(collector, tmp_path)
    step = trajectory.steps[0]
    assert "sk-abc123def456ghi789jkl" not in step.message
    assert "<REDACTED>" in step.message
    assert "supersecretvalue" not in step.tool_calls[0].arguments
    assert '"url": "https://x"' in step.tool_calls[0].arguments, "non-secret content must survive"


async def test_redaction_cannot_be_disabled(tmp_path):
    # Policy: trajectory files must never contain secrets. There is no
    # config switch, so redaction must apply on a default-constructed config.
    collector = TrajectoryCollector(SESSION_ID, TrajectoryConfig(root=str(tmp_path)))
    handler = TrajectoryAgentHandler(collector)
    await handler.on_llm_end(
        _span("i1"),
        outputs={"outputs": _assistant_message(content="password=hunter2", reasoning_content=None, tool_calls=None)},
    )
    trajectory = await _read(collector, tmp_path)
    assert "hunter2" not in trajectory.steps[0].message
    assert "<REDACTED>" in trajectory.steps[0].message


async def test_plugin_arguments_and_error_messages_are_redacted(tmp_path):
    # Tool inputs land in extra["arguments"] and error messages often quote
    # the failing request (auth failures especially); both must pass the same
    # redaction guard as the primary text fields, including the error copies
    # aggregated into the final_metrics footer.
    collector = TrajectoryCollector(SESSION_ID, TrajectoryConfig(root=str(tmp_path)))
    handler = TrajectoryAgentHandler(collector)
    secret = "sk-abc123def456ghi789jkl"
    await handler.on_plugin_start(
        _span("i1"), inputs={"inputs": {"api_key": secret}}, instance_info={"class_name": "fetch", "type": "tool"}
    )
    await handler.on_plugin_end(_span("i1"), outputs={"outputs": "ok"})
    await handler.on_llm_start(_span("i2"), inputs={"inputs": []}, instance_info={"class_name": "m", "type": "llm"})
    await handler.on_llm_error(_span("i2"), error=RuntimeError(f"401 unauthorized for api_key={secret}"))

    trajectory = await _read(collector, tmp_path)
    plugin_step, error_step = trajectory.steps
    assert secret not in plugin_step.extra["arguments"]
    assert "<REDACTED>" in plugin_step.extra["arguments"]
    assert secret not in error_step.error.message
    assert secret not in trajectory.final_metrics.errors[0].message


async def test_input_digest_off_by_default(handler, collector, tmp_path):
    await handler.on_llm_start(
        _span("i1"),
        inputs={"inputs": [{"role": "user", "content": "hello"}]},
        instance_info={"class_name": "m", "type": "llm"},
    )
    await handler.on_llm_end(_span("i1"), outputs={"outputs": _assistant_message()})
    trajectory = await _read(collector, tmp_path)
    assert "input_digest" not in (trajectory.steps[0].extra or {})


async def test_input_digest_reveals_history_rewrites(tmp_path):
    # The digest is the trajectory's only view of the prompt actually sent to
    # the model. Between two calls, an unchanged message must produce an
    # identical digest entry and a rewritten one must differ — that diff is
    # how a reader detects silent context mutations (compression, rails).
    collector = TrajectoryCollector(SESSION_ID, TrajectoryConfig(root=str(tmp_path), record_llm_input_digest=True))
    handler = TrajectoryAgentHandler(collector)
    turn1 = [{"role": "system", "content": "be helpful"}, {"role": "user", "content": "question"}]
    turn2 = [{"role": "system", "content": "be helpful"}, {"role": "user", "content": "[compressed summary]"}]
    for invoke_id, messages in (("i1", turn1), ("i2", turn2)):
        await handler.on_llm_start(
            _span(invoke_id), inputs={"inputs": messages}, instance_info={"class_name": "m", "type": "llm"}
        )
        await handler.on_llm_end(_span(invoke_id), outputs={"outputs": _assistant_message(tool_calls=None)})

    trajectory = await _read(collector, tmp_path)
    first, second = (step.extra["input_digest"] for step in trajectory.steps)
    assert [entry["role"] for entry in first] == ["system", "user"]
    assert first[0] == second[0], "unchanged message must digest identically"
    assert first[1]["sha256"] != second[1]["sha256"], "rewritten message must digest differently"
    assert first[1]["chars"] == len("question")
    assert "question" not in str(trajectory.steps[1].extra), "digest must not embed content"


async def test_input_digest_attached_to_llm_error_steps(tmp_path):
    collector = TrajectoryCollector(SESSION_ID, TrajectoryConfig(root=str(tmp_path), record_llm_input_digest=True))
    handler = TrajectoryAgentHandler(collector)
    await handler.on_llm_start(
        _span("i1"),
        inputs={"inputs": [{"role": "user", "content": "oversized prompt"}]},
        instance_info={"class_name": "m", "type": "llm"},
    )
    await handler.on_llm_error(_span("i1"), error=RuntimeError("context length exceeded"))
    trajectory = await _read(collector, tmp_path)
    assert trajectory.steps[0].extra["input_digest"][0]["chars"] == len("oversized prompt")


async def test_child_of_never_stepped_parent_is_top_level(handler, collector, tmp_path):
    # The parent tool span is open but the parent never produced a step, so
    # no parent run dir exists. The child must be top-level with no parent
    # pointer: the header always matches the file's actual placement.
    await handler.on_plugin_start(_span("i1"), inputs={"inputs": {}}, instance_info={"class_name": "spawn"})
    handler.bind_trace("orphan-child", session_id="sub", agent_name="expert")
    await handler.on_llm_end(
        _span("i2", trace_id="orphan-child"), outputs={"outputs": _assistant_message(tool_calls=None)}
    )
    await handler.on_plugin_end(_span("i1"), outputs={"outputs": "done"})
    await collector.finalize_all()

    child = read_trajectory(_trace_file(tmp_path, "orphan-child"))
    assert child.parent_trajectory_id is None


class TestWorkflowHandler:
    async def test_component_done_and_error_steps(self, collector, tmp_path):
        handler = TrajectoryWorkflowHandler(collector)
        handler.set_trace_id(TRACE_ID)
        await handler.on_call_start("inv-1", metadata={"component_name": "Start", "component_type": "Start"})
        await handler.on_post_invoke("inv-1", outputs={"ok": True})
        await handler.on_call_done("inv-1", outputs={"ok": True})
        await handler.on_pre_invoke("inv-2", inputs={}, component_metadata={"component_name": "LLM1"})
        await handler.on_invoke("inv-2", exception=RuntimeError("component failed"))

        trajectory = await _read(collector, tmp_path)
        done_step, error_step = trajectory.steps
        assert done_step.extra["component_name"] == "Start"
        assert '"ok": true' in done_step.message
        assert error_step.error.message == "component failed"
        assert error_step.extra["component_name"] == "LLM1"
        assert trajectory.final_metrics.error_count == 1

    async def test_component_events_route_by_invoking_context(self, collector, tmp_path):
        # One workflow handler instance serves every session, and its
        # instance-level trace id is last-writer-wins. Routing must come
        # from the async-context marker set around each agent's workflow
        # span, or concurrent sessions interleave into the wrong file.
        agent_handler = TrajectoryAgentHandler(collector)
        workflow_handler = TrajectoryWorkflowHandler(collector)
        workflow_handler.set_trace_id("stale-instance-id")

        async def run_workflow(trace_id: str, invoke_id: str):
            span = _span(invoke_id, trace_id=trace_id)
            await agent_handler.on_workflow_start(span, inputs={"inputs": {}}, instance_info={"class_name": "wf"})
            await workflow_handler.on_call_start(invoke_id, metadata={"component_name": f"comp-{trace_id}"})
            await workflow_handler.on_call_done(invoke_id, outputs={"ok": trace_id})
            await agent_handler.on_workflow_end(span, outputs={"outputs": "done"})

        await asyncio.gather(run_workflow("trace-a", "i1"), run_workflow("trace-b", "i2"))
        await collector.finalize_all()

        for trace_id in ("trace-a", "trace-b"):
            trajectory = read_trajectory(_trace_file(tmp_path, trace_id))
            component_steps = [
                s for s in trajectory.steps if (s.extra or {}).get("invoke_type") == "workflow_component"
            ]
            assert len(component_steps) == 1
            assert component_steps[0].extra["component_name"] == f"comp-{trace_id}"
        stale = list((tmp_path / SESSION_ID).glob("*_stale-instance-id/trajectory.json"))
        assert stale == [], "the stale instance-level trace id must receive nothing"


async def test_exclude_session_prefixes_drops_trace_and_descendants(tmp_path):
    # Hosts whose infrastructure runs system turns (heartbeats, probes)
    # through the recorded agent must be able to keep those sessions out of
    # the trajectory root entirely - no buffer, no file, and no recording of
    # subagent sessions spawned inside the excluded turn.
    collector = TrajectoryCollector(
        SESSION_ID,
        TrajectoryConfig(root=str(tmp_path), exclude_session_prefixes=("heartbeat_",)),
    )
    handler = TrajectoryAgentHandler(collector)

    collector.bind_trace("hb-trace", session_id="heartbeat_1", agent_name="main")
    await handler.on_llm_end(_span("i1", trace_id="hb-trace"), outputs={"outputs": _assistant_message()})

    from openjiuwen.core.session.trajectory.collector import current_parent_trace_id

    token = current_parent_trace_id.set("hb-trace")
    try:
        collector.bind_trace("hb-child", session_id="sub_1", agent_name="sub")
    finally:
        current_parent_trace_id.reset(token)
    await handler.on_llm_end(_span("i2", trace_id="hb-child"), outputs={"outputs": _assistant_message()})

    collector.bind_trace(TRACE_ID, session_id="web_1", agent_name="main")
    await handler.on_llm_end(_span("i3"), outputs={"outputs": _assistant_message()})
    await collector.finalize_all()

    files = list(tmp_path.rglob("trajectory.json")) + list(tmp_path.rglob("subagents/*.json"))
    assert files == [_trace_file(tmp_path)]
    assert len(read_trajectory(files[0]).steps) == 1


async def test_exclusion_beats_agent_allow_list(tmp_path):
    # The heartbeat turn runs through the very agent that attach() allowed;
    # the session prefix must win or the filter is useless for that case.
    collector = TrajectoryCollector(
        SESSION_ID,
        TrajectoryConfig(root=str(tmp_path), exclude_session_prefixes=("heartbeat_",)),
    )
    collector.allow_agent("main")
    handler = TrajectoryAgentHandler(collector)

    collector.bind_trace("hb-trace", session_id="heartbeat_1", agent_name="main")
    await handler.on_llm_end(_span("i1", trace_id="hb-trace"), outputs={"outputs": _assistant_message()})
    await collector.finalize_all()

    assert list(tmp_path.rglob("*.json")) == []


async def test_recording_enabled_toggle_pauses_live_trace(tmp_path):
    # Runtime pause (TrajectoryRecorder.set_enabled) must silence traces that
    # are ALREADY recording - detach only stops future sessions, which is not
    # enough for a host-level "turn recording off" toggle - and resuming must
    # continue into the same trajectory.
    collector = TrajectoryCollector(SESSION_ID, TrajectoryConfig(root=str(tmp_path)))
    handler = TrajectoryAgentHandler(collector)

    collector.bind_trace(TRACE_ID, session_id="web_1", agent_name="main")
    await handler.on_llm_end(_span("i1"), outputs={"outputs": _assistant_message()})

    collector.recording_enabled = False
    await handler.on_llm_end(_span("i2"), outputs={"outputs": _assistant_message()})
    collector.bind_trace("paused-trace", session_id="web_2", agent_name="main")

    collector.recording_enabled = True
    await handler.on_llm_end(_span("i3"), outputs={"outputs": _assistant_message()})
    await collector.finalize_all()

    trajectory = read_trajectory(_trace_file(tmp_path))
    assert len(trajectory.steps) == 2, "step captured while paused must be dropped"
    assert list((tmp_path / SESSION_ID).glob("*_paused-trace/*")) == []
