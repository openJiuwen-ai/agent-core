# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Attach-based recording lifecycle and per-agent scoping.

Recording follows the attached agents, not the process: only sessions
carrying an attached agent's identity are captured, everything else —
other agents, anonymous traces — must be ignored. These tests drive the
contract through the real ``Tracer``/``TracerHandlerRegistry`` plumbing.
"""
import gc
import os
from unittest.mock import patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.session.tracer.tracer import Tracer, TracerHandlerRegistry
from openjiuwen.core.session.trajectory import recorder as recorder_module
from openjiuwen.core.session.trajectory.collector import current_parent_trace_id
from openjiuwen.core.session.trajectory.config import TrajectoryConfig
from openjiuwen.core.session.trajectory.recorder import TrajectoryRecorder
from openjiuwen.core.session.trajectory.writer import read_trajectory
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

from tests.unit_tests.core.session.trajectory.helpers import emit_llm_step

pytestmark = pytest.mark.asyncio

AGENT_CARD = AgentCard(id="agent-1")


def _recorder(tmp_path, session_id: str = "s1", **config) -> TrajectoryRecorder:
    return TrajectoryRecorder(session_id, TrajectoryConfig(root=str(tmp_path), **config))


async def test_attach_registers_handlers_and_close_unregisters(tmp_path):
    recorder = _recorder(tmp_path).attach(AGENT_CARD)
    assert len(TracerHandlerRegistry.get_agent_handlers()) == 1
    assert len(TracerHandlerRegistry.get_workflow_handlers()) == 1

    await recorder.close()
    assert TracerHandlerRegistry.get_agent_handlers() == {}
    assert TracerHandlerRegistry.get_workflow_handlers() == {}
    await recorder.close()  # second close is a no-op


async def test_attach_is_idempotent_and_does_not_warn(tmp_path):
    recorder = _recorder(tmp_path)
    with patch.object(recorder_module, "session_logger") as mock_logger:
        assert recorder.attach(AGENT_CARD) is recorder
        recorder.attach(AGENT_CARD)
    assert len(TracerHandlerRegistry.get_agent_handlers()) == 1
    mock_logger.warning.assert_not_called()
    await recorder.close()


async def test_attached_agent_trace_is_recorded(tmp_path):
    recorder = _recorder(tmp_path).attach(AGENT_CARD)
    tracer = Tracer()
    tracer.init(session_id="conv-1", agent_name="agent-1")
    await emit_llm_step(tracer)
    await recorder.close()

    files = list((tmp_path / "s1").glob("*/trajectory.json"))
    assert len(files) == 1
    trajectory = read_trajectory(files[0])
    assert trajectory.session_id == "s1"
    assert trajectory.agent.name == "agent-1"
    assert trajectory.agent.extra["agent_session_id"] == "conv-1"
    assert trajectory.steps[0].message == "hi"
    assert trajectory.final_metrics.total_tokens == 4
    assert trajectory.final_metrics.success is True


async def test_unattached_agent_trace_is_ignored(tmp_path):
    recorder = _recorder(tmp_path).attach(AGENT_CARD)
    tracer = Tracer()
    tracer.init(session_id="conv-1", agent_name="some-other-agent")
    await emit_llm_step(tracer)
    await recorder.close()

    assert list(tmp_path.rglob("*.json")) == [], "only attached agents may be recorded"


async def test_anonymous_trace_is_ignored(tmp_path):
    # A session created without a card has no agent identity; it cannot be
    # matched to any attached agent and must not be recorded.
    recorder = _recorder(tmp_path).attach(AGENT_CARD)
    tracer = Tracer()
    tracer.init(session_id="conv-1")
    await emit_llm_step(tracer)
    await recorder.close()

    assert list(tmp_path.rglob("*.json")) == []


async def test_card_name_takes_precedence_over_id(tmp_path):
    # AgentSession binds traces under card.name when set (id otherwise);
    # attach must resolve the same key or the trace never matches.
    named = AgentCard(id="agent-1", name="friendly-name")
    recorder = _recorder(tmp_path).attach(named)
    tracer = Tracer()
    tracer.init(session_id="conv-1", agent_name="friendly-name")
    await emit_llm_step(tracer)
    await recorder.close()

    files = list((tmp_path / "s1").glob("*/trajectory.json"))
    assert len(files) == 1


async def test_session_created_before_attach_is_not_recorded(tmp_path):
    # Tracers pick up extension handlers at session creation, so attach
    # affects future sessions only.
    recorder = _recorder(tmp_path)
    tracer = Tracer()
    tracer.init(session_id="conv-1", agent_name="agent-1")
    recorder.attach(AGENT_CARD)
    await emit_llm_step(tracer)
    await recorder.close()

    assert list(tmp_path.rglob("*.json")) == []


async def test_detach_stops_new_sessions_but_not_open_ones(tmp_path):
    recorder = _recorder(tmp_path).attach(AGENT_CARD)
    open_tracer = Tracer()
    open_tracer.init(session_id="conv-1", agent_name="agent-1")

    recorder.detach(AGENT_CARD)

    late_tracer = Tracer()
    late_tracer.init(session_id="conv-2", agent_name="agent-1")
    await emit_llm_step(open_tracer, content="still recording")
    await emit_llm_step(late_tracer, content="must be dropped")
    await recorder.close()

    files = list((tmp_path / "s1").glob("*/trajectory.json"))
    assert len(files) == 1, "only the session opened while attached may record"
    assert read_trajectory(files[0]).steps[0].message == "still recording"


async def test_two_recorders_capture_their_own_agents(tmp_path):
    # Recorders are independent: each records exactly its own agents, and
    # both can be live in the same process at once.
    other_card = AgentCard(id="agent-2")
    recorder_a = _recorder(tmp_path / "a").attach(AGENT_CARD)
    recorder_b = _recorder(tmp_path / "b").attach(other_card)

    tracer_a = Tracer()
    tracer_a.init(session_id="conv-1", agent_name="agent-1")
    tracer_b = Tracer()
    tracer_b.init(session_id="conv-2", agent_name="agent-2")
    await emit_llm_step(tracer_a, content="from agent-1")
    await emit_llm_step(tracer_b, content="from agent-2")
    await recorder_a.close()
    await recorder_b.close()

    files_a = list((tmp_path / "a" / "s1").glob("*/trajectory.json"))
    files_b = list((tmp_path / "b" / "s1").glob("*/trajectory.json"))
    assert len(files_a) == 1 and len(files_b) == 1
    assert read_trajectory(files_a[0]).agent.name == "agent-1"
    assert read_trajectory(files_b[0]).agent.name == "agent-2"


async def test_agent_invoked_workflow_components_are_recorded(tmp_path):
    # A workflow session created under an agent session shares the agent's
    # tracer, so component events key under the agent's accepted trace id
    # and must land in the agent's trajectory despite the filtering.
    recorder = _recorder(tmp_path).attach(AGENT_CARD)
    tracer = Tracer()
    tracer.init(session_id="conv-1", agent_name="agent-1")

    workflow_handler = recorder._workflow_handler  # given the trace id by tracer.init
    await workflow_handler.on_call_start("inv-1", metadata={"component_name": "Start"})
    await workflow_handler.on_call_done("inv-1", outputs={"ok": True})
    await recorder.close()

    files = list((tmp_path / "s1").glob("*/trajectory.json"))
    assert len(files) == 1
    step = read_trajectory(files[0]).steps[0]
    assert step.extra["invoke_type"] == "workflow_component"
    assert step.extra["component_name"] == "Start"


async def test_workflow_events_without_agent_trace_are_dropped(tmp_path):
    # A standalone workflow run never binds an agent trace: its component
    # events fall back to the anonymous workflow key and must be ignored.
    recorder = _recorder(tmp_path).attach(AGENT_CARD)

    workflow_handler = recorder._workflow_handler  # no tracer.init: no trace id
    await workflow_handler.on_call_start("inv-1", metadata={"component_name": "Start"})
    await workflow_handler.on_call_done("inv-1", outputs={"ok": True})
    await recorder.close()

    assert list(tmp_path.rglob("*.json")) == []


async def test_attach_without_identity_raises(tmp_path):
    with pytest.raises(BaseError):
        _recorder(tmp_path).attach(object())
    assert TracerHandlerRegistry.get_agent_handlers() == {}


async def test_gc_without_close_unregisters_handlers(tmp_path):
    # A recorder dropped without close() must not keep capturing forever:
    # the GC finalizer defuses the registry entries (the leak), even though
    # open trajectories lose their footer.
    recorder = _recorder(tmp_path).attach(AGENT_CARD)
    assert len(TracerHandlerRegistry.get_agent_handlers()) == 1
    del recorder
    gc.collect()
    assert TracerHandlerRegistry.get_agent_handlers() == {}
    assert TracerHandlerRegistry.get_workflow_handlers() == {}


async def test_close_without_recording_warns(tmp_path):
    # Silent no-record is the design's known failure mode (card missing,
    # attach-after-session, other-process sessions); close() must surface it.
    recorder = _recorder(tmp_path).attach(AGENT_CARD)
    with patch.object(recorder_module, "session_logger") as mock_logger:
        await recorder.close()
    warnings = [str(call) for call in mock_logger.warning.call_args_list]
    assert any("without recording any trace" in w for w in warnings)


async def test_close_after_recording_does_not_warn(tmp_path):
    recorder = _recorder(tmp_path).attach(AGENT_CARD)
    tracer = Tracer()
    tracer.init(session_id="conv-1", agent_name="agent-1")
    await emit_llm_step(tracer)
    with patch.object(recorder_module, "session_logger") as mock_logger:
        await recorder.close()
    mock_logger.warning.assert_not_called()


async def test_attach_warns_when_distinct_agents_share_identity_key(tmp_path):
    # Identity is a string match; two different agents with the same card
    # name would silently interleave into shared output — warn at attach.
    recorder = _recorder(tmp_path)
    with patch.object(recorder_module, "session_logger") as mock_logger:
        recorder.attach(AgentCard(id="id-1", name="shared-name"))
        recorder.attach(AgentCard(id="id-2", name="shared-name"))
    assert mock_logger.warning.called
    await recorder.close()


async def test_context_manager_closes_on_exit(tmp_path):
    async with _recorder(tmp_path) as recorder:
        recorder.attach(AGENT_CARD)
        assert len(TracerHandlerRegistry.get_agent_handlers()) == 1
    assert TracerHandlerRegistry.get_agent_handlers() == {}


async def test_sensitive_root_is_rejected(tmp_path):
    # Trajectory files persist full payloads; the root must pass the same
    # sensitive-path guard the logging sinks use.
    bad_root = "C:\\Windows\\System32\\openjiuwen_traj" if os.name == "nt" else "/proc/openjiuwen_traj"
    recorder = TrajectoryRecorder("s1", TrajectoryConfig(root=bad_root))
    with pytest.raises(BaseError):
        recorder.attach(AGENT_CARD)
    assert TracerHandlerRegistry.get_agent_handlers() == {}


async def test_descendant_of_finalized_run_is_dropped(tmp_path):
    # A task spawned inside a recorded tool call carries the parent marker
    # in its copied context forever; once the parent run finalizes, sessions
    # created from that stale context must not bind to it.
    recorder = _recorder(tmp_path).attach(AGENT_CARD)
    parent = Tracer()
    parent.init(session_id="conv-1", agent_name="agent-1")
    await emit_llm_step(parent)
    span = parent.tracer_agent_span_manager.create_agent_span()
    await parent.trigger(
        "tracer_agent", "on_plugin_start",
        span=span, inputs={"inputs": {}}, instance_info={"class_name": "spawn"},
    )
    leaked_marker = current_parent_trace_id.get()  # what a spawned task's context holds
    await parent.trigger("tracer_agent", "on_plugin_end", span=span, outputs={"outputs": "done"})
    await recorder._agent_handler.on_trace_close(span.trace_id)  # session finished

    token = current_parent_trace_id.set(leaked_marker)
    try:
        late_child = Tracer()
        late_child.init(session_id="late", agent_name="unattached-agent")
        await emit_llm_step(late_child, content="must be dropped")
    finally:
        current_parent_trace_id.reset(token)
    await recorder.close()

    files = list((tmp_path / "s1").rglob("*.json"))
    assert len(files) == 1, "only the parent run may exist"


async def test_finalized_identity_state_is_bounded(tmp_path):
    # Long-lived hosts finalize thousands of sessions; identity/lineage
    # records must not grow without bound.
    recorder = TrajectoryRecorder(
        "s1", TrajectoryConfig(root=str(tmp_path), max_retained_traces=2)
    ).attach(AGENT_CARD)
    for index in range(4):
        tracer = Tracer()
        tracer.init(session_id=f"conv-{index}", agent_name="agent-1")
        await emit_llm_step(tracer)
        span = tracer.tracer_agent_span_manager.create_agent_span()
        await recorder._agent_handler.on_trace_close(span.trace_id)
    collector = recorder._collector
    assert len(collector._finalized_traces) <= 2
    assert len(collector._bindings) <= 2
    await recorder.close()

    files = list((tmp_path / "s1").glob("*/trajectory.json"))
    assert len(files) == 4, "eviction bounds memory, not the files already written"


async def test_recorder_reusable_after_close(tmp_path):
    recorder = _recorder(tmp_path).attach(AGENT_CARD)
    await recorder.close()

    recorder.attach(AGENT_CARD)
    tracer = Tracer()
    tracer.init(session_id="conv-2", agent_name="agent-1")
    await emit_llm_step(tracer, content="second life")
    await recorder.close()

    files = list((tmp_path / "s1").glob("*/trajectory.json"))
    assert len(files) == 1
    assert read_trajectory(files[0]).steps[0].message == "second life"
