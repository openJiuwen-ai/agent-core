# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Per-session finalization and grouped layout.

Long-lived hosts (e.g. JiuwenSwarm) never close the recorder until process
shutdown, so trajectories must be finalized — footer stamped, step buffer
released — when each owning session finishes (``Session.post_run``). These
tests encode that contract plus the opt-in per-agent-session directory
grouping such hosts use to keep concurrent sessions apart.
"""
import pytest

from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.tracer.tracer import Tracer
from openjiuwen.core.session.trajectory.config import TrajectoryConfig
from openjiuwen.core.session.trajectory.recorder import TrajectoryRecorder
from openjiuwen.core.session.trajectory.writer import read_trajectory
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

from tests.unit_tests.core.session.trajectory.helpers import emit_llm_step

pytestmark = pytest.mark.asyncio

SESSION_LABEL = "s1"
AGENT_CARD = AgentCard(id="agent-1")


def _make_session():
    return create_agent_session(session_id="conv-1", card=AGENT_CARD)


def _collector_buffers(recorder: TrajectoryRecorder) -> dict:
    return recorder._collector._buffers


async def test_post_run_finalizes_footer_and_releases_buffer(tmp_path):
    # Hosts keep the process (and recording) alive across many sessions:
    # the footer must land when the session ends, not at recorder.close(),
    # and the step buffer must be released so memory does not grow with
    # every completed session.
    recorder = TrajectoryRecorder(SESSION_LABEL, TrajectoryConfig(root=str(tmp_path))).attach(AGENT_CARD)
    try:
        session = _make_session()
        await session.pre_run()
        await emit_llm_step(session.tracer())

        await session.post_run()

        files = list((tmp_path / SESSION_LABEL).glob("*/trajectory.json"))
        assert len(files) == 1
        trajectory = read_trajectory(files[0])
        assert trajectory.final_metrics is not None, "footer must be stamped at session end, before close()"
        assert trajectory.final_metrics.success is True
        assert trajectory.final_metrics.total_tokens == 4
        assert _collector_buffers(recorder) == {}, "completed session's step buffer must be evicted"
    finally:
        await recorder.close()


async def test_post_run_twice_and_close_keep_single_finalized_run(tmp_path):
    # post_run is idempotent and recorder.close() must not corrupt or
    # duplicate an already finalized run.
    recorder = TrajectoryRecorder(SESSION_LABEL, TrajectoryConfig(root=str(tmp_path))).attach(AGENT_CARD)
    session = _make_session()
    await session.pre_run()
    await emit_llm_step(session.tracer())
    await session.post_run()
    await session.post_run()
    await recorder.close()

    files = list((tmp_path / SESSION_LABEL).glob("*/trajectory.json"))
    assert len(files) == 1
    assert read_trajectory(files[0]).final_metrics.success is True


async def test_steps_after_close_keep_agent_identity(tmp_path):
    # Runner.run_agent closes the session it was handed after each run;
    # callers may keep using it. Late steps start a fresh run but must not
    # lose the agent identity header.
    recorder = TrajectoryRecorder(SESSION_LABEL, TrajectoryConfig(root=str(tmp_path))).attach(AGENT_CARD)
    session = _make_session()
    await session.pre_run()
    await emit_llm_step(session.tracer())
    await session.post_run()

    await emit_llm_step(session.tracer(), content="late turn")
    await recorder.close()

    files = list((tmp_path / SESSION_LABEL).glob("*/trajectory.json"))
    assert files, "late steps must still be recorded"
    for file in files:
        trajectory = read_trajectory(file)
        assert trajectory.agent is not None
        assert trajectory.agent.extra["agent_session_id"] == "conv-1"


async def test_group_by_agent_session_layout(tmp_path):
    # A host serving many agent sessions per process groups runs by the
    # real agent session id so concurrent sessions stay apart on disk.
    recorder = TrajectoryRecorder(
        SESSION_LABEL, TrajectoryConfig(root=str(tmp_path), group_by_agent_session=True)
    ).attach(AgentCard(id="worker"))
    tracer = Tracer()
    tracer.init(session_id="conv-9", agent_name="worker")
    await emit_llm_step(tracer)
    await recorder.close()

    files = list((tmp_path / SESSION_LABEL / "conv-9").glob("*/trajectory.json"))
    assert len(files) == 1, "run dir must nest under the agent session id"
    assert read_trajectory(files[0]).agent.extra["agent_session_id"] == "conv-9"


async def test_grouping_sanitizes_hostile_session_id(tmp_path):
    # Agent session ids are caller-supplied; path separators must never let
    # a trajectory escape the recording root.
    recorder = TrajectoryRecorder(
        SESSION_LABEL, TrajectoryConfig(root=str(tmp_path), group_by_agent_session=True)
    ).attach(AgentCard(id="worker"))
    tracer = Tracer()
    tracer.init(session_id="../evil\\x", agent_name="worker")
    await emit_llm_step(tracer)
    await recorder.close()

    assert {p.name for p in tmp_path.iterdir()} == {SESSION_LABEL}, "nothing may be written outside the root"
    group_dirs = {p.name for p in (tmp_path / SESSION_LABEL).iterdir()}
    assert group_dirs == {".._evil_x"}


async def test_subagent_still_nests_under_parent_run_when_grouped(tmp_path):
    # Grouping must not break lineage: a subagent spawned inside a parent
    # tool call lands in the parent run's subagents/ dir, inside the
    # parent's agent-session group. Only the parent is attached; the child
    # is captured through lineage.
    recorder = TrajectoryRecorder(
        SESSION_LABEL, TrajectoryConfig(root=str(tmp_path), group_by_agent_session=True)
    ).attach(AgentCard(id="parent"))

    parent = Tracer()
    parent.init(session_id="conv-1", agent_name="parent")
    # Parent declares the tool call first so its buffer (and run dir) exist.
    await emit_llm_step(parent)
    parent_span = parent.tracer_agent_span_manager.create_agent_span()
    await parent.trigger(
        "tracer_agent", "on_plugin_start",
        span=parent_span, inputs={"inputs": {"q": "?"}}, instance_info={"class_name": "consult"},
    )

    child = Tracer()
    child.init(session_id="sub-1", agent_name="expert")
    await emit_llm_step(child, content="expert opinion")

    await parent.trigger("tracer_agent", "on_plugin_end", span=parent_span, outputs={"outputs": "done"})
    await recorder.close()

    parent_files = list((tmp_path / SESSION_LABEL / "conv-1").glob("*/trajectory.json"))
    assert len(parent_files) == 1
    child_files = list(parent_files[0].parent.glob("subagents/*.json"))
    assert len(child_files) == 1, "subagent run must nest inside the grouped parent run dir"
    child_trajectory = read_trajectory(child_files[0])
    assert child_trajectory.parent_trajectory_id == read_trajectory(parent_files[0]).trajectory_id
