# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""A subagent spawned inside a tool call must nest under the parent run.

Full-stack check of the lineage chain: the parent's tool span marks the
async context (trajectory handler plugin bracket), the sub-agent session
created inside the tool binds to it at ``Tracer.init``, and the collector
routes the child trajectory into the parent run's ``subagents/`` directory
with ``parent_trajectory_id`` / ``agent`` headers.
"""
from unittest.mock import patch

import pytest

from openjiuwen.core.foundation.tool import tool
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.trajectory import TrajectoryConfig, TrajectoryRecorder, read_trajectory

from tests.unit_tests.core.session.trajectory.helpers import make_react_agent
from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response, create_tool_call_response

pytestmark = pytest.mark.asyncio

SESSION_ID = "subagent-session"
SUB_CONVERSATION_ID = "expert-consult-1"

# The tool body needs the sub-agent instance; module state keeps the @tool
# signature clean (only LLM-facing params).
_sub_agent: dict = {}


@tool
async def consult_expert(question: str) -> str:
    """Forward a question to the expert agent and return its answer.

    Args:
        question: Question for the expert.
    """
    result = await Runner.run_agent(
        agent=_sub_agent["agent"],
        inputs={"query": question, "conversation_id": SUB_CONVERSATION_ID},
    )
    return str(result)


async def test_subagent_run_nests_under_parent(tmp_path):
    parent = make_react_agent("parent_agent")
    sub = make_react_agent("expert_agent")
    # Only the parent is attached: the sub-agent's session must be captured
    # through lineage (it is spawned inside the parent's tool span).
    recorder = TrajectoryRecorder(SESSION_ID, TrajectoryConfig(root=str(tmp_path))).attach(parent)
    registered = False
    try:
        _sub_agent["agent"] = sub

        Runner.resource_mgr.add_tool(consult_expert)
        registered = True
        parent.ability_manager.add(consult_expert.card)

        parent_llm = MockLLMModel()
        parent_llm.set_responses(
            [
                create_tool_call_response("consult_expert", '{"question": "why is the sky blue"}'),
                create_text_response("final answer"),
            ]
        )
        sub_llm = MockLLMModel()
        sub_llm.set_responses([create_text_response("expert opinion")])

        # The parent session must be created after attach (tracers pick up
        # extension handlers at session construction time) and carry the
        # parent's card so the trace matches the attached identity.
        session = create_agent_session(session_id=SESSION_ID, card=parent.card)
        with (
            patch.object(parent, "_get_llm", return_value=parent_llm),
            patch.object(sub, "_get_llm", return_value=sub_llm),
        ):
            await parent.invoke({"conversation_id": "c1", "query": "hi"}, session=session)
    finally:
        await recorder.close()
        if registered:
            Runner.resource_mgr.remove_tool(consult_expert.card.id)
        _sub_agent.clear()

    parent_files = list((tmp_path / SESSION_ID).glob("*/trajectory.json"))
    assert len(parent_files) == 1, "expected exactly one top-level run"
    parent_traj = read_trajectory(parent_files[0])
    assert parent_traj.parent_trajectory_id is None

    child_files = list(parent_files[0].parent.glob("subagents/*.json"))
    assert len(child_files) == 1, "sub-agent run must land in the parent run's subagents/ dir"
    child_traj = read_trajectory(child_files[0])
    assert child_traj.parent_trajectory_id == parent_traj.trajectory_id
    assert child_traj.agent.name == "expert_agent"
    assert child_traj.agent.extra["agent_session_id"] == SUB_CONVERSATION_ID
    # The child's own LLM activity is recorded in its file, not the parent's.
    child_llm_steps = [s for s in child_traj.steps if (s.extra or {}).get("invoke_type") == "llm"]
    assert child_llm_steps, "sub-agent LLM step missing from child trajectory"
