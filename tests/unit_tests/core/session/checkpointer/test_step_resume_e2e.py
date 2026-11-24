# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Crash resume on a real SQLite checkpoint file.

"Process killed" is simulated by raising a ``BaseException`` from the second
tool execution: ``except Exception`` does not catch it, and the caller owns the
session so no commit runs. The ``finally`` that would close the step records is
stubbed out too, because a really killed process never reaches it.
"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.checkpointer.persistence import PersistenceCheckpointerProvider
from openjiuwen.core.session.checkpointer.step_storage import StepResume
from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)
from tests.unit_tests.fixtures.step_resume_agent import make_step_agent

QUERY = "先算1+2再算3+4"


class _ProcessKilled(BaseException):
    pass


@pytest_asyncio.fixture
async def restore_default_checkpointer():
    original = CheckpointerFactory._default_checkpointer
    yield
    CheckpointerFactory.set_default_checkpointer(original)


def _role(message):
    return message.get("role") if isinstance(message, dict) else message.role


def _content(message):
    return message.get("content") if isinstance(message, dict) else message.content


@pytest.mark.asyncio
async def test_killed_process_resumes_from_last_completed_step(tmp_path, monkeypatch, restore_default_checkpointer):
    db = tmp_path / "checkpoint"

    # --- process A: completes iteration 0, dies inside iteration 1 ---
    cp_a = await PersistenceCheckpointerProvider().create({"db_type": "sqlite", "db_path": str(db)})
    CheckpointerFactory.set_default_checkpointer(cp_a)
    agent_a = make_step_agent("e2e-agent")
    session_a = create_agent_session(session_id="crash-1", card=agent_a.card)
    await session_a.pre_run(inputs=None)

    llm_a = MockLLMModel()
    llm_a.set_responses([
        create_tool_call_response("add", '{"a": 1, "b": 2}'),
        create_tool_call_response("add", '{"a": 3, "b": 4}'),
    ])
    monkeypatch.setattr(agent_a, "_get_llm", lambda: llm_a)

    executions = {"count": 0}
    original_execute = agent_a._execute_tool_call

    async def dying_execute(ctx, tool_calls, session, context):
        executions["count"] += 1
        if executions["count"] >= 2:
            raise _ProcessKilled()
        return await original_execute(ctx, tool_calls, session, context)

    monkeypatch.setattr(agent_a, "_execute_tool_call", dying_execute)
    cp_a.end_agent_steps = AsyncMock()

    with pytest.raises(_ProcessKilled):
        await agent_a.invoke({"query": QUERY}, session=session_a)

    records = await cp_a._kv_store.get_by_prefix("crash-1:agent-step:e2e-agent:")
    assert len(records) == 1

    # --- process B: same db file, same session id, same card id ---
    cp_b = await PersistenceCheckpointerProvider().create({"db_type": "sqlite", "db_path": str(db)})
    CheckpointerFactory.set_default_checkpointer(cp_b)
    agent_b = make_step_agent("e2e-agent")
    session_b = create_agent_session(session_id="crash-1", card=agent_b.card)
    await session_b.pre_run(inputs=None)

    assert session_b.peek_agent_step_resume() == StepResume(iteration=0, query=QUERY)

    llm_b = MockLLMModel()
    llm_b.set_responses([
        create_tool_call_response("add", '{"a": 3, "b": 4}'),
        create_text_response("3 和 7"),
    ])
    monkeypatch.setattr(agent_b, "_get_llm", lambda: llm_b)

    result = await agent_b.invoke({"query": QUERY}, session=session_b)

    assert result["result_type"] == "answer"
    first_call = llm_b.call_history[0]
    assert sum(
        1 for m in first_call if _role(m) == "user" and QUERY in str(_content(m))
    ) == 1
    assert any(_role(m) == "tool" and str(_content(m)) == "3" for m in first_call)

    await session_b.commit()
    assert await cp_b._kv_store.get_by_prefix("crash-1:agent-step:") == {}
