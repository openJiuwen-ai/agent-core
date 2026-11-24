# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import MagicMock

import pytest

from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.checkpointer.step_storage import StepResume
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from openjiuwen.core.session.interaction.base import AgentInterrupt
from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)
from tests.unit_tests.fixtures.step_resume_agent import make_step_agent


class _Recorder:
    """Records step/context calls made by the agent loop, in order."""

    def __init__(self):
        self.events = []

    def install(self, session, begin=None):
        async def save_agent_step(iteration, query="", final=False):
            self.events.append(("save", iteration, query, final))

        async def end_agent_steps(interrupted):
            self.events.append(("end", interrupted))

        session.save_agent_step = save_agent_step
        session.begin_agent_steps = begin if begin is not None else (lambda: None)
        session.peek_agent_step_resume = lambda: None
        session.end_agent_steps = end_agent_steps

    def step_events(self):
        return [e for e in self.events if e != "save_contexts"]


def _role(message):
    return message.get("role") if isinstance(message, dict) else message.role


def _content(message):
    return message.get("content") if isinstance(message, dict) else message.content


async def _run(monkeypatch, card_id="step-loop", *, llm, query="1+2?", max_iterations=5, begin=None):
    agent = make_step_agent(card_id, max_iterations=max_iterations)
    session = create_agent_session(session_id=f"sess-{card_id}", card=agent.card)
    recorder = _Recorder()
    recorder.install(session, begin=begin)

    original_save_contexts = agent.context_engine.save_contexts

    async def save_contexts(sess, *args, **kwargs):
        recorder.events.append("save_contexts")
        return await original_save_contexts(sess, *args, **kwargs)

    monkeypatch.setattr(agent.context_engine, "save_contexts", save_contexts)
    monkeypatch.setattr(agent, "_get_llm", lambda: llm)
    return agent, session, recorder


@pytest.mark.asyncio
async def test_each_completed_iteration_writes_one_step_after_save_contexts(monkeypatch):
    llm = MockLLMModel()
    llm.set_responses([
        create_tool_call_response("add", '{"a": 1, "b": 2}'),
        create_text_response("1+2=3"),
    ])
    agent, session, recorder = await _run(monkeypatch, llm=llm)

    await agent.invoke({"query": "1+2?"}, session=session)

    assert recorder.step_events() == [
        ("save", 0, "1+2?", False),
        ("save", 1, "1+2?", True),
        ("end", False),
    ]
    for i, event in enumerate(recorder.events):
        if isinstance(event, tuple) and event[0] == "save":
            assert recorder.events[i - 1] == "save_contexts"


@pytest.mark.asyncio
async def test_resume_starts_at_next_iteration_without_readmitting_query(monkeypatch):
    llm = MockLLMModel()
    llm.set_responses([create_text_response("3")])
    agent, session, recorder = await _run(
        monkeypatch, llm=llm, begin=lambda: StepResume(iteration=0, query="1+2?")
    )

    await agent.invoke({"query": "1+2?"}, session=session)

    assert recorder.step_events() == [("save", 1, "1+2?", True), ("end", False)]
    assert not any(
        _role(m) == "user" and "1+2?" in str(_content(m)) for m in llm.call_history[0]
    )


@pytest.mark.asyncio
async def test_resume_with_different_query_admits_new_message(monkeypatch):
    llm = MockLLMModel()
    llm.set_responses([create_text_response("ok")])
    agent, session, recorder = await _run(
        monkeypatch, llm=llm, query="继续", begin=lambda: StepResume(iteration=0, query="原问题")
    )

    await agent.invoke({"query": "继续"}, session=session)

    assert any(_role(m) == "user" and _content(m) == "继续" for m in llm.call_history[0])
    assert ("save", 1, "继续", True) in recorder.events


@pytest.mark.asyncio
async def test_resume_keeps_max_iterations_budget(monkeypatch):
    llm = MockLLMModel()
    llm.set_responses([])
    agent, session, _ = await _run(
        monkeypatch, card_id="step-budget", llm=llm, max_iterations=2,
        begin=lambda: StepResume(iteration=1, query="1+2?"),
    )

    result = await agent.invoke({"query": "1+2?"}, session=session)

    assert result.get("termination_reason", result.get("finish_reason")) == "max_iterations"
    assert llm.call_history == []


@pytest.mark.asyncio
async def test_agent_interrupt_marks_steps_interrupted(monkeypatch):
    llm = MockLLMModel()
    llm.set_responses([create_tool_call_response("add", '{"a": 1, "b": 2}')])
    agent, session, recorder = await _run(monkeypatch, card_id="step-interrupt", llm=llm)

    async def _raise(*args, **kwargs):
        raise AgentInterrupt("need input")

    monkeypatch.setattr(agent, "_execute_tool_call", _raise)

    with pytest.raises(AgentInterrupt):
        await agent.invoke({"query": "1+2?"}, session=session)

    assert recorder.events[-1] == ("end", True)


@pytest.mark.asyncio
async def test_step_write_failure_does_not_abort_invoke(monkeypatch):
    llm = MockLLMModel()
    llm.set_responses([create_text_response("1+2=3")])
    agent, session, recorder = await _run(monkeypatch, card_id="step-fail", llm=llm)

    async def broken_save(*args, **kwargs):
        raise OSError("disk full")

    session.save_agent_step = broken_save

    result = await agent.invoke({"query": "1+2?"}, session=session)

    assert result["result_type"] == "answer"
    assert recorder.events[-1] == ("end", False)


def test_mock_session_is_ignored():
    assert ReActAgent._begin_agent_steps(MagicMock()) is None


@pytest.mark.asyncio
async def test_run_that_never_starts_does_not_close_steps(monkeypatch):
    """A rejected invoke must not turn a pending resume into a finished task.

    The empty-query ValueError is raised before ``begin_agent_steps``; closing
    steps there would append a ``final`` record and destroy the resume point.
    """
    llm = MockLLMModel()
    agent, session, recorder = await _run(monkeypatch, card_id="step-noquery", llm=llm)

    with pytest.raises(ValueError):
        await agent.invoke({"query": ""}, session=session)

    assert not any(isinstance(e, tuple) and e[0] == "end" for e in recorder.events)


@pytest.mark.asyncio
async def test_resume_with_different_query_keeps_original_question_once(monkeypatch):
    """The resumed context already holds the original question; it must not repeat."""
    from openjiuwen.core.foundation.llm.schema.message import UserMessage

    llm = MockLLMModel()
    llm.set_responses([create_text_response("ok")])
    agent, session, _ = await _run(
        monkeypatch, card_id="step-diffq", llm=llm, query="继续",
        begin=lambda: StepResume(iteration=0, query="原问题"),
    )
    session.update_state({
        "context": {"default_context_id": {"messages": [UserMessage(content="原问题")], "offload_messages": {}}}
    })

    await agent.invoke({"query": "继续"}, session=session)

    first_call = llm.call_history[0]
    assert sum(
        1 for m in first_call if _role(m) == "user" and _content(m) == "原问题"
    ) == 1
    assert any(_role(m) == "user" and _content(m) == "继续" for m in first_call)


@pytest.mark.asyncio
async def test_abort_closes_steps_before_committing_the_cleanup(monkeypatch):
    """The abort cleanup must be committed after the steps are closed.

    ``post_agent_execute`` only writes the full state once the agent is idle;
    committing while the track is still active silently skips it.
    """
    llm = MockLLMModel()
    llm.set_responses([create_tool_call_response("add", '{"a": 1, "b": 2}')])
    agent, session, recorder = await _run(monkeypatch, card_id="step-abort", llm=llm)

    async def commit():
        recorder.events.append("commit")

    session.commit = commit

    async def _boom(*args, **kwargs):
        raise RuntimeError("model down")

    monkeypatch.setattr(agent, "_execute_tool_call", _boom)

    with pytest.raises(RuntimeError):
        await agent.invoke({"query": "1+2?"}, session=session)

    ends = [i for i, e in enumerate(recorder.events) if isinstance(e, tuple) and e[0] == "end"]
    commits = [i for i, e in enumerate(recorder.events) if e == "commit"]
    assert ends and commits
    assert ends[0] < commits[0]


@pytest.mark.asyncio
async def test_card_less_session_with_persistence_checkpointer_still_invokes(monkeypatch):
    """Sessions without an agent card have no agent id; the loop must still run."""
    from openjiuwen.core.session.agent import create_agent_session as make_session
    from openjiuwen.core.session.checkpointer import CheckpointerFactory
    from openjiuwen.core.session.checkpointer.persistence import PersistenceCheckpointer
    from tests.unit_tests.core.session.checkpointer.test_persistence_storage import MockKVStore

    cp = PersistenceCheckpointer(MockKVStore())
    original = CheckpointerFactory._default_checkpointer
    CheckpointerFactory.set_default_checkpointer(cp)
    try:
        llm = MockLLMModel()
        llm.set_responses([create_text_response("ok")])
        agent = make_step_agent("step-nocard")
        session = make_session(session_id="sess-nocard")
        monkeypatch.setattr(agent, "_get_llm", lambda: llm)

        result = await agent.invoke({"query": "hi"}, session=session)

        assert result["result_type"] == "answer"
    finally:
        CheckpointerFactory.set_default_checkpointer(original)


@pytest.mark.asyncio
async def test_card_less_session_stream_does_not_crash(monkeypatch):
    """The stream path calls pre_run/commit; a card-less session must survive it."""
    from openjiuwen.core.session.agent import create_agent_session as make_session
    from openjiuwen.core.session.checkpointer import CheckpointerFactory
    from openjiuwen.core.session.checkpointer.persistence import PersistenceCheckpointer
    from tests.unit_tests.core.session.checkpointer.test_persistence_storage import MockKVStore

    cp = PersistenceCheckpointer(MockKVStore())
    original = CheckpointerFactory._default_checkpointer
    CheckpointerFactory.set_default_checkpointer(cp)
    try:
        llm = MockLLMModel()
        llm.set_responses([create_text_response("ok")])
        agent = make_step_agent("step-nocard-stream")
        session = make_session(session_id="sess-nocard-stream")
        monkeypatch.setattr(agent, "_get_llm", lambda: llm)

        chunks = [chunk async for chunk in agent.stream({"query": "hi"}, session=session)]

        assert chunks
    finally:
        CheckpointerFactory.set_default_checkpointer(original)


@pytest.mark.asyncio
async def test_switch_off_writes_no_step_records(monkeypatch):
    """With enable_iteration_checkpoint off the loop keeps its pre-feature behavior."""
    from openjiuwen.core.session.checkpointer import CheckpointerFactory
    from openjiuwen.core.session.checkpointer.persistence import PersistenceCheckpointer
    from tests.unit_tests.core.session.checkpointer.test_persistence_storage import MockKVStore

    kv = MockKVStore()
    cp = PersistenceCheckpointer(kv)
    original = CheckpointerFactory._default_checkpointer
    CheckpointerFactory.set_default_checkpointer(cp)
    try:
        llm = MockLLMModel()
        llm.set_responses([
            create_tool_call_response("add", '{"a": 1, "b": 2}'),
            create_text_response("3"),
        ])
        agent = make_step_agent("step-switch-off", enable_iteration_checkpoint=False)
        session = create_agent_session(session_id="sess-switch-off", card=agent.card)
        monkeypatch.setattr(agent, "_get_llm", lambda: llm)

        result = await agent.invoke({"query": "1+2?"}, session=session)

        assert result["result_type"] == "answer"
        assert [k for k in kv._store if "agent-step" in k] == []
    finally:
        CheckpointerFactory.set_default_checkpointer(original)


def test_iteration_checkpoint_is_on_by_default():
    """The step checkpoint is opt-out: the config default must stay on."""
    from openjiuwen.core.context_engine.schema.config import ContextEngineConfig

    assert ContextEngineConfig().enable_iteration_checkpoint is True
