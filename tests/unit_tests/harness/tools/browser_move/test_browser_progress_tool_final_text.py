# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The final text of a task that the worker ends with a browser_progress tool call."""
# pylint: disable=protected-access

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ModelCallInputs, ToolCallInputs
from openjiuwen.harness.tools.browser_move.playwright_runtime.phase_contract import PHASE_KEY
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime, BrowserRuntimeRail
from tests.unit_tests.harness.tools.browser_move.test_browser_runtime_rail import _FakeSession, _run

PAGE = "http://localhost:8000/"
# A task, the result of its first browser_navigate, and the text of the model response that called
# browser_progress.
TASK = (
    "An issue tracker is running at **http://localhost:8000**. "
    "Open it in the browser and complete the task below. The application keeps its own state; nothing outside it "
    "needs changing.\n\nDelete the markdown preview rendering bug.\n\nDo not modify other settings or data beyond what "
    "the task asks for.\n"
)
NAVIGATED = {
    "result": (
        "### Ran Playwright code\n```js\nawait page.goto('http://localhost:8000');\n```\n### Page\n"
        "- Page URL: http://localhost:8000/\n- Page Title: Issues\n- Console: 1 errors, 0 warnings\n"
    ),
    "page_state": {"url": PAGE, "title": "Issues"},
}
REPORT = (
    'The issue was deleted: the page shows the "Issue deleted" toast, and a search for "markdown" returns "No '
    'issues found".\n\nI deleted issue #21, "Markdown preview renders incorrectly with nested code blocks", from the '
    "Issues list. No other settings or data were modified.\n\n"
)
PROGRESS = {"status": "completed", "next_action": ""}


def session_after_navigation() -> _FakeSession:
    state = BrowserRuntimeRail._build_phase_state(TASK)
    BrowserRuntimeRail._record_tool_evidence(state, dict(NAVIGATED),
                                             tool_name="mcp_playwright-official_browser_navigate",
                                             tool_args={"url": "http://localhost:8000"})
    session = _FakeSession()
    session.update_state({PHASE_KEY: state})
    return session


def _rail() -> BrowserRuntimeRail:
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.service = MagicMock()
    return BrowserRuntimeRail(runtime)


def call(name: str, args: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(id=f"call_{name}", name=name, arguments=args)


def respond(rail: BrowserRuntimeRail, ctx: AgentCallbackContext, text: str, calls: list[SimpleNamespace],
            finish_reason: str) -> None:
    """One model response through after_model_call, then before_tool_call for each of its tool calls."""
    response = AssistantMessage(content=text, tool_calls=[], finish_reason=finish_reason)
    response.tool_calls = calls
    ctx.inputs = ModelCallInputs(response=response)
    _run(rail.after_model_call(ctx))
    for tool_call in calls:
        ctx.inputs = ToolCallInputs(tool_call=tool_call, tool_name=tool_call.name, tool_args=tool_call.arguments)
        _run(rail.before_tool_call(ctx))


def finished(ctx: AgentCallbackContext) -> dict[str, Any]:
    return ctx.consume_force_finish().result["authoritative_browser_result"]


def test_the_text_of_the_response_that_calls_browser_progress_is_the_final_text_and_the_summary() -> None:
    session = session_after_navigation()
    rail = _rail()
    ctx = AgentCallbackContext(agent=MagicMock(), session=session)

    respond(rail, ctx, REPORT, [call("browser_progress", PROGRESS)], "tool_calls")

    finish = ctx.consume_force_finish()
    browser = finish.result["authoritative_browser_result"]
    assert (browser["status"], browser["terminal_reason"]) == ("completed", "worker_completed_with_observations")
    assert session.get_state(PHASE_KEY)["last_worker_final"] == REPORT
    # Core ends the invoke with the forced result as its answer; after_invoke renders the result for the parent.
    result = dict(finish.result)
    ctx.inputs = InvokeInputs(query=TASK, result=result)
    _run(rail.after_invoke(ctx))
    assert result["authoritative_browser_result"]["summary"] == REPORT.strip()
    assert session.get_state(PHASE_KEY)["last_worker_final"] == REPORT


def test_the_text_of_an_earlier_response_is_no_final_text() -> None:
    session = session_after_navigation()
    rail = _rail()
    ctx = AgentCallbackContext(agent=MagicMock(), session=session)
    respond(rail, ctx, REPORT, [call("browser_phase", {"phase": "extraction", "status": "completed"})], "tool_calls")
    assert ctx.consume_force_finish() is None

    respond(rail, ctx, "", [call("browser_progress", PROGRESS)], "tool_calls")

    assert (finished(ctx)["status"], finished_reason(session)) == ("partial", "model_output_empty")


def finished_reason(session: _FakeSession) -> str:
    return str(session.get_state(PHASE_KEY).get("terminal_reason") or "")


def test_a_progress_block_alone_is_no_final_text() -> None:
    session = session_after_navigation()
    rail = _rail()
    ctx = AgentCallbackContext(agent=MagicMock(), session=session)

    respond(rail, ctx, '<browser_progress>{"status": "completed"}</browser_progress>',
            [call("browser_progress", PROGRESS)], "tool_calls")

    assert (finished(ctx)["status"], finished_reason(session)) == ("partial", "model_output_empty")


def test_the_text_of_a_response_cut_at_the_output_limit_is_no_final_text() -> None:
    session = session_after_navigation()
    rail = _rail()
    ctx = AgentCallbackContext(agent=MagicMock(), session=session)

    respond(rail, ctx, REPORT[:60], [call("browser_progress", PROGRESS)], "length")

    # The terminal reason belongs to the cut-reply rule; this test pins only that the cut text is no report.
    assert finished(ctx)["status"] == "partial"
    assert "last_worker_final" not in session.get_state(PHASE_KEY)


def test_the_text_of_a_response_with_other_tool_calls_is_no_final_text() -> None:
    session = session_after_navigation()
    rail = _rail()
    ctx = AgentCallbackContext(agent=MagicMock(), session=session)

    respond(rail, ctx, REPORT, [call("browser_snapshot", {}), call("browser_progress", PROGRESS)], "tool_calls")

    assert (finished(ctx)["status"], finished_reason(session)) == ("partial", "model_output_empty")


def test_a_final_text_that_announces_a_tool_call_ends_as_a_protocol_error() -> None:
    session = session_after_navigation()
    rail = _rail()
    ctx = AgentCallbackContext(agent=MagicMock(), session=session)

    respond(rail, ctx, "The issue is deleted. I'll now call browser_progress to report it.",
            [call("browser_progress", PROGRESS)], "tool_calls")

    assert (finished(ctx)["status"], finished_reason(session)) == ("partial", "model_tool_protocol_error")
