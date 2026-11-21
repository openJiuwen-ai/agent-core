# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""A steering message continues the browser task: the rail keeps the task state it built for the request."""
# pylint: disable=protected-access

import copy
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.message import (
    OPENJIUWEN_MESSAGE_ORIGIN_EXTERNAL_USER,
    OPENJIUWEN_MESSAGE_ORIGIN_METADATA,
    OPENJIUWEN_MESSAGE_SOURCE_KIND_METADATA,
    UserMessage,
)
from openjiuwen.core.single_agent.prompts.builder import SystemPromptBuilder
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_working_context import (
    latest_browser_user_request,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import PHASE_KEY
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserRuntimeRail as Rail
from tests.unit_tests.harness.tools.browser_move.test_browser_runtime_rail import _FakeSession

PAGE = "http://localhost:8000/"
TASK = (
    "A mail client is running at **http://localhost:8000**. Open it in the browser and complete "
    "the task below.\n\nRename the Reference label to Resources."
)
# Two steering texts without the prefix the agent loop writes in front of them: one a host sends while the task
# runs, and the markup retry of the browser rail.
HOST_NOTICE = "Keep going with the same task; the page can take a few seconds to load."
MARKUP_NOTICE = (
    "Your previous response described another browser tool call but did not emit a real tool call. Continue in this "
    "same task: emit the actual tool call, or answer concisely using only evidence already collected."
)
# A final report whose completion marker the model wrote as tool-call markup in its text.
MARKUP_REPLY = (
    "The Reference label is now called Resources.\n"
    '<｜DSML｜ invoke name="browser_progress">{"status":"completed","next_action":""}</｜DSML｜ invoke>'
)
NAVIGATED = {
    "result": "### Page\n- Page URL: http://localhost:8000/\n- Page Title: Mail\n",
    "page_state": {"url": PAGE, "title": "Mail"},
}
KEPT = ("phases", "known_urls", "model_protocol_retry_count", "deadline_started_at", "deadline_at")


def admitted(content: str, source: str) -> UserMessage:
    """A user message as ReActAgent writes it into the context, with the input path it came from."""
    return UserMessage(content=content, metadata={
        OPENJIUWEN_MESSAGE_ORIGIN_METADATA: OPENJIUWEN_MESSAGE_ORIGIN_EXTERNAL_USER,
        OPENJIUWEN_MESSAGE_SOURCE_KIND_METADATA: source,
    })


def steering(text: str) -> UserMessage:
    return admitted(f"[STEERING] {text}", "steering")


def rail_with_task(retry_count: int) -> tuple[Rail, _FakeSession, dict[str, Any]]:
    """A rail with a task that read its first page, and a deep copy of that task state."""
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.semantic_progress = {}
    runtime.service = MagicMock()
    rail = Rail(runtime)
    session = _FakeSession()
    state = rail._ensure_task_state(session, TASK, resume=False, original_goal=None)
    Rail._record_tool_evidence(state, dict(NAVIGATED), tool_name="mcp_playwright-official_browser_navigate",
                               tool_args={"url": PAGE})
    now = time.time()
    state.update(model_protocol_retry_count=retry_count, deadline_started_at=now, deadline_at=now + 1470.0)
    session.update_state({PHASE_KEY: state})
    return rail, session, copy.deepcopy(state)


async def model_call(rail: Rail, session: _FakeSession, messages: list[UserMessage]) -> dict[str, Any]:
    agent = MagicMock()
    agent.system_prompt_builder = SystemPromptBuilder(language="en")
    ctx = AgentCallbackContext(agent=agent, session=session, inputs=ModelCallInputs(tools=[MagicMock()],
                                                                                     messages=messages))
    await rail.before_model_call(ctx)
    assert ctx.consume_force_finish() is None, "before_model_call ended the task"
    return session.get_state(PHASE_KEY)


@pytest.mark.parametrize("notice", [HOST_NOTICE, MARKUP_NOTICE], ids=["host steering", "markup retry"])
def test_the_task_request_skips_a_steering_message(notice: str) -> None:
    assert latest_browser_user_request([UserMessage(content=TASK), steering(notice)]) == TASK


@pytest.mark.parametrize("source", ["query", "resume"])
def test_a_request_admitted_by_the_agent_loop_is_the_task_request(source: str) -> None:
    assert latest_browser_user_request([admitted(TASK, source), steering(HOST_NOTICE)]) == TASK


@pytest.mark.asyncio
@pytest.mark.parametrize("notice", [HOST_NOTICE, MARKUP_NOTICE], ids=["host steering", "markup retry"])
async def test_a_steering_message_keeps_the_task_state(notice: str) -> None:
    rail, session, before = rail_with_task(1)

    state = await model_call(rail, session, [UserMessage(content=TASK), steering(notice)])

    assert state["task"] == TASK and state["task_id"] == before["task_id"]
    assert [record["kind"] for record in state["structured_evidence"]] == ["page_metadata"]
    for key in KEPT:
        assert state[key] == before[key], key


@pytest.mark.asyncio
async def test_a_new_user_request_still_starts_a_new_task_state() -> None:
    rail, session, before = rail_with_task(1)

    state = await model_call(rail, session, [UserMessage(content=TASK), UserMessage(content="Archive the newsletter.")])

    assert state["task"] == "Archive the newsletter." and state["task_id"] != before["task_id"]
    assert state["structured_evidence"] == []


@pytest.mark.asyncio
async def test_a_second_markup_reply_in_the_same_task_ends_it_and_keeps_its_state() -> None:
    rail, session, before = rail_with_task(0)
    ctx = AgentCallbackContext(agent=MagicMock(), session=session)
    ctx.inputs = ModelCallInputs(response=AssistantMessage(content=MARKUP_REPLY))

    await rail.after_model_call(ctx)

    assert ctx.consume_force_finish() is None
    assert session.get_state(PHASE_KEY)["model_protocol_retry_count"] == 1

    state = await model_call(rail, session, [UserMessage(content=TASK), steering(MARKUP_NOTICE)])
    assert (state["task_id"], state["model_protocol_retry_count"]) == (before["task_id"], 1)

    ctx.inputs = ModelCallInputs(response=AssistantMessage(content=MARKUP_REPLY))
    await rail.after_model_call(ctx)

    finish = ctx.consume_force_finish()
    assert finish is not None
    assert finish.result["authoritative_browser_result"]["terminal_reason"] == "model_tool_protocol_error"
    end = session.get_state(PHASE_KEY)
    assert (end["task"], end["task_id"]) == (TASK, before["task_id"])
    assert [record["kind"] for record in end["structured_evidence"]] == ["page_metadata"]
