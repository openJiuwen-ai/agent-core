# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Client-executed tools: the connected client runs the call, its result is the tool output.

Most tests drive a real DeepAgent (real rails, permission engine, interrupt
handler and resume path) with a scripted model.
"""

from __future__ import annotations

import json
from typing import Any, List
from unittest.mock import patch

import pytest
import pytest_asyncio

from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import (
    ClientTool,
    ClientToolResult,
    ClientToolStatus,
    ClientToolUnavailable,
    Tool,
    ToolCard,
    ToolExecution,
    ToolOutput,
    default_client_output,
    is_client_tool,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.interrupt.response import InterruptKind
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.harness.rails.interrupt.interrupt_base import BaseInterruptRail
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from tests.unit_tests.fixtures.deep_agent_interrupts import (
    APPROVE,
    CountingTool,
    ask_permissions,
    by_call_id,
    by_interrupt_id,
    make_deep_agent,
    messages_seen,
    pending,
    run,
    scripted,
    tool_results_seen,
)
from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response, create_tool_call_response

PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAwS2OUAAAAABJRU5ErkJggg=="
)

_SCHEMA = {"type": "object", "properties": {"section": {"type": "integer"}}, "required": []}


class SectionTool(ClientTool):
    """A client tool that tells the client which document to touch."""

    def __init__(self, name: str = "edit_section"):
        super().__init__(ToolCard(
            id=name, name=name, description="Edit a section in the open document", input_params=_SCHEMA,
        ))
        self.requests: List[dict] = []

    def build_request(self, arguments, context):
        request = {"document": "doc-1", "section": arguments.get("section")}
        self.requests.append(request)
        return request


class ServerToolMarkedClient(Tool):
    """A plain Tool whose card says the client executes it; invoke must never run."""

    def __init__(self, name: str = "card_client_tool"):
        super().__init__(ToolCard(
            id=name, name=name, description="d", input_params=_SCHEMA, execution="client",
        ))
        self.invoke_count = 0

    async def invoke(self, inputs, **kwargs):
        self.invoke_count += 1
        return "ran on the server"

    async def stream(self, inputs, **kwargs):
        yield await self.invoke(inputs, **kwargs)


class RecordingRail(AgentRail):
    priority = 5

    def __init__(self):
        super().__init__()
        self.results: List[Any] = []
        self.exceptions: List[Any] = []
        self.pauses = 0

    async def after_tool_call(self, ctx):
        if ctx.is_paused:
            self.pauses += 1
        elif ctx.exception is None:
            self.results.append(ctx.inputs.tool_result)

    async def on_tool_exception(self, ctx):
        self.exceptions.append(ctx.exception)


class RewriteArgsRail(BaseInterruptRail):
    """Approves the call with rewritten arguments (as a policy rail might)."""

    async def resolve_interrupt(self, ctx, tool_call, user_input, auto_confirm_config=None):
        args = json.loads(tool_call.arguments)
        args["section"] = args.get("section", 0) + 100
        return self.approve(new_args=json.dumps(args))


@pytest_asyncio.fixture
async def runner():
    await Runner.start()
    try:
        yield
    finally:
        await Runner.stop()


def _script(*responses):
    llm = MockLLMModel()
    llm.set_responses(list(responses) + [create_text_response("done")])
    return llm


def _image_urls(llm) -> List[str]:
    """URLs of the image parts in the messages the model received."""
    urls = []
    for message in messages_seen(llm):
        content = getattr(message, "content", None)
        if isinstance(content, list):
            urls.extend(
                part["image_url"]["url"]
                for part in content
                if isinstance(part, dict) and part.get("type") == "image_url"
            )
    return urls


def test_client_execution_is_declared_on_the_card_and_kept_out_of_the_model_schema():
    assert is_client_tool(ToolCard(id="a", name="a", execution="client"))
    assert not is_client_tool(ToolCard(id="c", name="c"))
    assert SectionTool().card.execution == ToolExecution.CLIENT
    card = ToolCard(id="d", name="d")
    assert ClientTool(card).card.execution == ToolExecution.CLIENT
    assert card.execution == ToolExecution.SERVER, "the caller's card is not modified"
    assert "execution" not in ToolCard(id="a", name="a", execution="client").tool_info().model_dump()


def test_client_result_coercion_and_default_output():
    assert ClientToolResult.coerce("hello").content == "hello"
    assert ClientToolResult.coerce({"status": "client_gone"}).status == ClientToolStatus.CLIENT_GONE
    assert ClientToolResult.coerce({"content": "x", "is_error": True}).failed
    plain = ClientToolResult.coerce({"rows": 3})
    assert not plain.failed and plain.data == {"rows": 3} and plain.content == '{"rows": 3}'
    status_json = ClientToolResult.coerce({"status": "ok", "sections": 3})
    assert not status_json.failed and json.loads(status_json.content) == {"status": "ok", "sections": 3}
    assert not ClientToolResult.coerce({"status": "exploded"}).failed
    assert json.loads(ClientToolResult.coerce({"content": {"section": 1}}).content) == {"section": 1}
    single_block = ClientToolResult.coerce({"content": {"type": "text", "text": "hi"}})
    assert default_client_output(single_block).data["content"] == "hi"

    # A dict with keys beyond the envelope is the whole result: nothing is dropped.
    done = ClientToolResult.coerce({"status": "completed", "section_ids": [3, 4]})
    assert not done.failed and done.data == {"status": "completed", "section_ids": [3, 4]}
    assert json.loads(done.content) == {"status": "completed", "section_ids": [3, 4]}
    missing = default_client_output(ClientToolResult.coerce({"status": "error", "message": "section 9 not found"}))
    assert not missing.success and "section 9 not found" in missing.error
    assert ClientToolResult.coerce({"is_error": True, "message": "boom"}).failed

    ok = default_client_output(ClientToolResult(content=[
        {"type": "text", "text": "rendered"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
    ]), tool_name="render")
    assert ok.success and ok.data["content"] == "rendered"
    assert ok.data["multimodal"][0]["data_url"] == "data:image/png;base64,AAAA"

    gone = default_client_output(ClientToolResult(status="client_gone"), tool_name="render")
    assert not gone.success and gone.data["retryable"] is True and "disconnected" in gone.error


@pytest.mark.asyncio
async def test_first_call_pauses_with_a_client_tool_interrupt(tmp_path, runner):
    tool = SectionTool()
    agent = make_deep_agent(tmp_path, tools=[tool])
    llm = _script(create_tool_call_response("edit_section", '{"section": 3}', tool_call_id="call_1"))
    with scripted(llm):
        [(call_id, request)] = pending(await run(agent, "edit section 3", "c1"))

    assert call_id == "call_1"
    assert request.interrupt_kind == InterruptKind.CLIENT_TOOL
    assert request.interrupt_id
    assert request.tool_name == "edit_section"
    assert request.arguments == {"section": 3}
    assert request.client_request == {"document": "doc-1", "section": 3}
    assert request.payload_schema["properties"]["status"]


@pytest.mark.asyncio
async def test_client_result_becomes_the_normal_tool_output(tmp_path, runner):
    tool = SectionTool()
    recorder = RecordingRail()
    agent = make_deep_agent(tmp_path, tools=[tool], rails=[recorder])
    llm = _script(create_tool_call_response("edit_section", '{"section": 3}', tool_call_id="call_1"))
    with scripted(llm):
        [(_, request)] = pending(await run(agent, "edit section 3", "c2"))
        exceptions_while_paused = len(recorder.exceptions)
        result = await run(agent, by_interrupt_id(request, {"content": "section 3 updated"}), "c2")

    assert result["result_type"] == "answer"
    assert tool_results_seen(llm) == [("call_1", "section 3 updated")]
    [output] = recorder.results
    assert isinstance(output, ToolOutput) and output.success
    assert len(recorder.exceptions) == exceptions_while_paused


@pytest.mark.asyncio
async def test_error_result_is_a_failed_output_not_an_exception(tmp_path, runner):
    tool = SectionTool()
    recorder = RecordingRail()
    agent = make_deep_agent(tmp_path, tools=[tool], rails=[recorder])
    llm = _script(create_tool_call_response("edit_section", '{"section": 9}', tool_call_id="call_1"))
    with scripted(llm):
        [(_, request)] = pending(await run(agent, "edit section 9", "c3"))
        exceptions_while_paused = len(recorder.exceptions)
        result = await run(agent, by_interrupt_id(request, {"is_error": True, "content": "section 9 not found"}), "c3")

    assert result["result_type"] == "answer"
    assert tool_results_seen(llm) == [("call_1", "section 9 not found")]
    [output] = recorder.results
    assert output.success is False and output.error == "section 9 not found"
    assert len(recorder.exceptions) == exceptions_while_paused, "a failed client result must not raise"


@pytest.mark.asyncio
async def test_image_result_reaches_the_model_through_the_multimodal_path(tmp_path, runner):
    tool = SectionTool("render_section")
    agent = make_deep_agent(tmp_path, tools=[tool])
    llm = _script(create_tool_call_response("render_section", '{"section": 1}', tool_call_id="call_1"))
    answer = {"content": [{"type": "text", "text": "rendered section 1"}, {"type": "image", "data_url": PNG_DATA_URL}]}
    with scripted(llm):
        [(_, request)] = pending(await run(agent, "render section 1", "c4"))
        await run(agent, by_interrupt_id(request, answer), "c4")

    assert tool_results_seen(llm) == [("call_1", "rendered section 1")]
    assert _image_urls(llm) == [PNG_DATA_URL]


@pytest.mark.asyncio
async def test_parallel_client_calls_can_be_answered_in_batches(tmp_path, runner):
    tool = SectionTool()
    agent = make_deep_agent(tmp_path, tools=[tool])
    two_calls = AssistantMessage(content="", tool_calls=[
        ToolCall(id="call_a", type="function", name="edit_section", arguments='{"section": 1}'),
        ToolCall(id="call_b", type="function", name="edit_section", arguments='{"section": 2}'),
    ])
    llm = _script(two_calls)
    with scripted(llm):
        first = dict(pending(await run(agent, "edit two sections", "c5")))
        assert set(first) == {"call_a", "call_b"}

        still = dict(pending(await run(agent, by_interrupt_id(first["call_a"], "section 1 done"), "c5")))
        assert set(still) == {"call_b"}
        assert still["call_b"].interrupt_id == first["call_b"].interrupt_id
        assert still["call_b"].client_request == {"document": "doc-1", "section": 2}
        assert len(tool.requests) == 2, "the unanswered call is not rebuilt while the client works on it"

        result = await run(agent, by_interrupt_id(still["call_b"], "section 2 done"), "c5")

    assert result["result_type"] == "answer"
    assert sorted(tool_results_seen(llm)) == [("call_a", "section 1 done"), ("call_b", "section 2 done")]
    assert llm.call_count == 2


@pytest.mark.asyncio
async def test_client_gone_answer_becomes_a_retryable_tool_error(tmp_path, runner):
    tool = SectionTool()
    recorder = RecordingRail()
    agent = make_deep_agent(tmp_path, tools=[tool], rails=[recorder])
    llm = _script(create_tool_call_response("edit_section", '{"section": 3}', tool_call_id="call_1"))
    with scripted(llm):
        [(_, request)] = pending(await run(agent, "edit section 3", "c6"))
        await run(agent, by_interrupt_id(request, {"status": "client_gone"}), "c6")

    [(_, content)] = tool_results_seen(llm)
    assert "disconnected" in content and "call it again" in content
    [output] = recorder.results
    assert output.success is False
    assert output.data["status"] == "client_gone" and output.data["retryable"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("answer_by", ["interrupt_id", "tool_call_id"])
async def test_a1_permission_ask_then_client_execution_on_the_same_call(tmp_path, runner, answer_by):
    """Bug A1 with a native client tool: approve the permission ask, then the client
    result arrives and must become the output instead of re-triggering the ask."""
    tool = SectionTool()
    agent = make_deep_agent(tmp_path, tools=[tool], permissions=ask_permissions("edit_section"))
    llm = _script(create_tool_call_response("edit_section", '{"section": 3}', tool_call_id="call_A1"))

    def _answer(call_id, value, request):
        return by_interrupt_id(request, value) if answer_by == "interrupt_id" else by_call_id(call_id, value)

    with scripted(llm), patch.object(
        PermissionInterruptRail, "_build_message", autospec=True, side_effect=PermissionInterruptRail._build_message
    ) as asks:
        [(call_id, ask)] = pending(await run(agent, "edit section 3", "a1"))
        assert ask.interrupt_kind == InterruptKind.PERMISSION
        assert tool.requests == [], "the client must not be asked before the permission is granted"

        [(call_id, client)] = pending(await run(agent, _answer(call_id, APPROVE, ask), "a1"))
        assert client.interrupt_kind == InterruptKind.CLIENT_TOOL
        assert client.interrupt_id != ask.interrupt_id

        result = await run(agent, _answer(call_id, {"content": "section 3 updated"}, client), "a1")

    assert result["result_type"] == "answer"
    assert asks.call_count == 1
    assert tool_results_seen(llm) == [("call_A1", "section 3 updated")]


@pytest.mark.asyncio
async def test_denied_client_call_never_reaches_the_client(tmp_path, runner):
    tool = SectionTool()
    agent = make_deep_agent(
        tmp_path,
        tools=[tool],
        permissions={"enabled": True, "defaults": {"*": "allow"}, "tools": {"edit_section": {"*": "deny"}}},
    )
    llm = _script(create_tool_call_response("edit_section", '{"section": 3}', tool_call_id="call_1"))
    with scripted(llm):
        result = await run(agent, "edit section 3", "deny")

    assert result["result_type"] == "answer"
    assert tool.requests == []
    [(_, content)] = tool_results_seen(llm)
    assert "PERMISSION_DENIED" in content


@pytest.mark.asyncio
async def test_client_receives_the_arguments_after_rails_rewrote_them(tmp_path, runner):
    tool = SectionTool()
    agent = make_deep_agent(tmp_path, tools=[tool], rails=[RewriteArgsRail(tool_names=["edit_section"])])
    llm = _script(create_tool_call_response("edit_section", '{"section": 3}', tool_call_id="call_1"))
    with scripted(llm):
        [(_, request)] = pending(await run(agent, "edit section 3", "rewrite"))
        assert request.arguments == {"section": 103}
        result = await run(agent, by_interrupt_id(request, "ok"), "rewrite")

    assert result["result_type"] == "answer"
    assert [r["section"] for r in tool.requests] == [103]


@pytest.mark.asyncio
async def test_card_declared_client_tool_never_invokes_the_server_body(tmp_path, runner):
    tool = ServerToolMarkedClient()
    agent = make_deep_agent(tmp_path, tools=[tool])
    llm = _script(create_tool_call_response("card_client_tool", "{}", tool_call_id="call_1"))
    with scripted(llm):
        [(_, request)] = pending(await run(agent, "go", "props"))
        await run(agent, by_interrupt_id(request, "client ran it"), "props")

    assert tool.invoke_count == 0
    assert tool_results_seen(llm) == [("call_1", "client ran it")]


@pytest.mark.asyncio
async def test_plain_text_while_waiting_for_the_client_cancels_the_call(tmp_path, runner):
    tool = SectionTool()
    agent = make_deep_agent(tmp_path, tools=[tool])
    llm = _script(create_tool_call_response("edit_section", '{"section": 3}', tool_call_id="call_1"))
    with scripted(llm):
        pending(await run(agent, "edit section 3", "text"))
        result = await run(agent, "never mind, stop", "text")

    assert result["result_type"] == "answer"
    [(_, content)] = tool_results_seen(llm)
    assert "cancelled" in content and "never mind, stop" in content


@pytest.mark.asyncio
async def test_a_result_sent_as_the_whole_resume_input_answers_the_waiting_call(tmp_path, runner):
    agent = make_deep_agent(tmp_path, tools=[SectionTool()])
    llm = _script(create_tool_call_response("edit_section", '{"section": 3}', tool_call_id="call_1"))
    with scripted(llm):
        pending(await run(agent, "edit section 3", "whole-input"))
        await run(agent, ClientToolResult(content="section 3 updated"), "whole-input")

    assert tool_results_seen(llm) == [("call_1", "section 3 updated")]


class ContextAwareTool(ClientTool):
    """Uses the call context in both hooks and refuses calls without an open document."""

    def __init__(self):
        super().__init__(ToolCard(id="doc_tool", name="doc_tool", description="d", input_params=_SCHEMA))
        self.contexts: List[Any] = []

    def build_request(self, arguments, context):
        self.contexts.append(("build", context.session_id, context.tool_call.id))
        document = context.session.get_state("open_document") if context.session else None
        if not document:
            raise ClientToolUnavailable("No document is open; open one and try again.")
        context.session.update_state({"pending_review": arguments.get("section")})
        return {"document": document}

    async def complete(self, arguments, result, context):
        self.contexts.append(("complete", context.session.get_state("pending_review"), context.tool_call.id))
        return default_client_output(result, tool_name="doc_tool")


class OpenDocumentRail(AgentRail):
    """Stands in for a host binding a document to the conversation."""

    async def before_tool_call(self, ctx):
        if ctx.session is not None and ctx.inputs.tool_name == "doc_tool":
            ctx.session.update_state({"open_document": "doc-7"})


@pytest.mark.asyncio
async def test_hooks_receive_the_call_context(tmp_path, runner):
    tool = ContextAwareTool()
    agent = make_deep_agent(tmp_path, tools=[tool], rails=[OpenDocumentRail()])
    llm = _script(create_tool_call_response("doc_tool", '{"section": 4}', tool_call_id="call_ctx"))
    with scripted(llm):
        [(_, request)] = pending(await run(agent, "go", "ctx"))
        assert request.client_request == {"document": "doc-7"}
        await run(agent, by_interrupt_id(request, "done on client"), "ctx")

    (build, build_session, build_call), (complete, pending_review, complete_call) = tool.contexts
    assert build == "build" and build_session and build_call == "call_ctx"
    assert complete == "complete" and pending_review == 4 and complete_call == "call_ctx"


@pytest.mark.asyncio
async def test_build_request_can_fail_the_call_without_contacting_the_client(tmp_path, runner):
    tool = ContextAwareTool()
    recorder = RecordingRail()
    agent = make_deep_agent(tmp_path, tools=[tool], rails=[recorder])
    llm = _script(create_tool_call_response("doc_tool", '{"section": 4}', tool_call_id="call_none"))
    with scripted(llm):
        result = await run(agent, "go", "unavailable")

    assert result["result_type"] == "answer", "no client_tool interrupt must be raised"
    assert tool_results_seen(llm) == [("call_none", "No document is open; open one and try again.")]
    [output] = recorder.results
    assert output.success is False and output.data["status"] == "unavailable"


@pytest.mark.asyncio
async def test_deny_added_while_the_client_runs_applies_to_later_calls(tmp_path, runner):
    from openjiuwen.harness.security.core import PermissionEngine
    from openjiuwen.harness.security.permission_engine.models import PermissionLevel, PermissionResult

    tool = SectionTool()
    recorder = RecordingRail()
    agent = make_deep_agent(tmp_path, tools=[tool], rails=[recorder], permissions=ask_permissions("unrelated"))
    llm = MockLLMModel()
    llm.set_responses([
        create_tool_call_response("edit_section", '{"section": 3}', tool_call_id="call_1"),
        create_tool_call_response("edit_section", '{"section": 4}', tool_call_id="call_2"),
        create_text_response("done"),
    ])
    denied = PermissionResult(permission=PermissionLevel.DENY, matched_rule="tools.edit_section", reason="now blocked")
    with scripted(llm):
        [(_, request)] = pending(await run(agent, "edit section 3", "late"))
        with patch.object(PermissionEngine, "check_permission", return_value=denied):
            result = await run(agent, by_interrupt_id(request, "section 3 updated"), "late")

    assert result["result_type"] == "answer"
    first, second = tool_results_seen(llm, call_index=2)
    assert first == ("call_1", "section 3 updated"), "the answered call is not gated again"
    assert second[0] == "call_2" and "PERMISSION_DENIED" in second[1]
    assert [r["section"] for r in tool.requests] == [3]
    assert recorder.results[0].success


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "text"), [("timeout", "did not return a result in time"),
                                              ("cancelled", "was cancelled")])
async def test_timeout_and_cancelled_answers_become_retryable_tool_errors(tmp_path, runner, status, text):
    recorder = RecordingRail()
    agent = make_deep_agent(tmp_path, tools=[SectionTool()], rails=[recorder])
    llm = _script(create_tool_call_response("edit_section", '{"section": 3}', tool_call_id="call_1"))
    with scripted(llm):
        [(_, request)] = pending(await run(agent, "edit", f"st-{status}"))
        await run(agent, by_interrupt_id(request, {"status": status}), f"st-{status}")

    [(_, content)] = tool_results_seen(llm)
    assert text in content
    [output] = recorder.results
    assert output.success is False and output.data == {"content": "", "status": status, "retryable": True}


@pytest.mark.asyncio
async def test_a_pause_is_not_reported_to_rails_as_a_tool_exception(tmp_path, runner):
    recorder = RecordingRail()
    agent = make_deep_agent(tmp_path, tools=[SectionTool()], rails=[recorder])
    llm = _script(create_tool_call_response("edit_section", '{"section": 3}', tool_call_id="call_1"))
    with scripted(llm):
        [(_, request)] = pending(await run(agent, "edit", "pause"))
        await run(agent, by_interrupt_id(request, "ok"), "pause")

    assert recorder.exceptions == []
    assert recorder.pauses == 1
    assert len(recorder.results) == 1


@pytest.mark.asyncio
async def test_a_rail_pause_also_reaches_rails_as_a_pause(tmp_path, runner):
    from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmInterruptRail

    recorder = RecordingRail()
    tool = ShotTool()
    agent = make_deep_agent(tmp_path, tools=[tool], rails=[recorder, ConfirmInterruptRail(tool_names=["shot"])])
    llm = _script(create_tool_call_response("shot", "{}", tool_call_id="call_1"))
    with scripted(llm):
        [(_, request)] = pending(await run(agent, "go", "rail-pause"))
        await run(agent, by_interrupt_id(request, APPROVE), "rail-pause")

    assert recorder.exceptions == [] and recorder.pauses == 1
    assert tool.invoke_count == 1 and recorder.results[0].success


@pytest.mark.asyncio
async def test_dict_resume_keyed_by_call_id_is_an_answer(tmp_path, runner):
    agent = make_deep_agent(tmp_path, tools=[SectionTool()])
    llm = _script(create_tool_call_response("edit_section", '{"section": 3}', tool_call_id="call_1"))
    with scripted(llm):
        pending(await run(agent, "edit", "dict"))
        result = await run(agent, {"call_1": {"content": "section 3 updated"}}, "dict")

    assert result["result_type"] == "answer"
    assert tool_results_seen(llm) == [("call_1", "section 3 updated")]


@pytest.mark.asyncio
async def test_dict_resume_keyed_by_one_call_id_leaves_the_other_client_call_waiting(tmp_path, runner):
    tool = SectionTool()
    agent = make_deep_agent(tmp_path, tools=[tool])
    llm = _script(AssistantMessage(content="", tool_calls=[
        ToolCall(id="call_a", type="function", name="edit_section", arguments='{"section": 1}'),
        ToolCall(id="call_b", type="function", name="edit_section", arguments='{"section": 2}'),
    ]))
    with scripted(llm):
        first = dict(pending(await run(agent, "edit two sections", "dict-one")))
        still = dict(pending(await run(agent, {"call_a": {"content": "section 1 done"}}, "dict-one")))
        assert set(still) == {"call_b"}
        assert still["call_b"].interrupt_id == first["call_b"].interrupt_id
        await run(agent, by_interrupt_id(still["call_b"], "section 2 done"), "dict-one")

    assert sorted(tool_results_seen(llm)) == [("call_a", "section 1 done"), ("call_b", "section 2 done")]


class LifecycleRail(AgentRail):
    """Records the tool-call events one client call fires."""

    def __init__(self):
        super().__init__()
        self.events: List[str] = []

    async def before_tool_call(self, ctx):
        self.events.append("before")

    async def after_tool_call(self, ctx):
        self.events.append("after:paused" if ctx.is_paused else "after")


@pytest.mark.asyncio
async def test_a_client_call_passes_its_before_tool_call_rails_once(tmp_path, runner):
    lifecycle = LifecycleRail()
    agent = make_deep_agent(tmp_path, tools=[SectionTool()], rails=[lifecycle])
    llm = _script(create_tool_call_response("edit_section", '{"section": 3}', tool_call_id="call_1"))
    with scripted(llm):
        [(_, request)] = pending(await run(agent, "edit", "lifecycle"))
        await run(agent, by_interrupt_id(request, "section 3 updated"), "lifecycle")

    assert lifecycle.events == ["before", "after:paused", "after"]
    assert tool_results_seen(llm) == [("call_1", "section 3 updated")]


@pytest.mark.asyncio
async def test_a_held_client_call_is_paused_again_with_the_same_payload(tmp_path, runner):
    agent = make_deep_agent(tmp_path, tools=[SectionTool()])
    llm = _script(AssistantMessage(content="", tool_calls=[
        ToolCall(id="call_a", type="function", name="edit_section", arguments='{"section":1}', index=0),
        ToolCall(id="call_b", type="function", name="edit_section", arguments='{ "section" :  2 }', index=1),
    ]))
    with scripted(llm):
        first = dict(pending(await run(agent, "edit two sections", "held")))
        still = dict(pending(await run(agent, by_interrupt_id(first["call_a"], "done"), "held")))

    assert still["call_b"].model_dump() == first["call_b"].model_dump()


@pytest.mark.asyncio
async def test_json_result_with_a_status_field_is_a_success(tmp_path, runner):
    recorder = RecordingRail()
    agent = make_deep_agent(tmp_path, tools=[SectionTool()], rails=[recorder])
    llm = _script(create_tool_call_response("edit_section", '{"section": 3}', tool_call_id="call_1"))
    with scripted(llm):
        [(_, request)] = pending(await run(agent, "edit", "json"))
        await run(agent, by_interrupt_id(request, {"status": "ok", "sections": 3}), "json")

    [(_, content)] = tool_results_seen(llm)
    assert json.loads(content) == {"status": "ok", "sections": 3}
    assert recorder.results[0].success is True


class ShotTool(Tool):
    """Server tool returning an image."""

    def __init__(self):
        super().__init__(ToolCard(id="shot", name="shot", description="d", input_params=_SCHEMA))
        self.invoke_count = 0

    async def invoke(self, inputs, **kwargs):
        self.invoke_count += 1
        return ToolOutput(success=True, data={
            "content": "server shot", "multimodal": [{"type": "image", "data_url": PNG_DATA_URL}],
        })

    async def stream(self, inputs, **kwargs):
        yield await self.invoke(inputs, **kwargs)


def _roles(messages) -> List[str]:
    roles = []
    for message in messages:
        role = getattr(message, "role", None)
        roles.append(f"tool:{message.tool_call_id}" if role == "tool" else str(role))
    return roles


@pytest.mark.asyncio
@pytest.mark.parametrize("batch", ["two_client_calls", "server_and_client"])
async def test_images_wait_until_every_call_of_the_turn_has_its_result(tmp_path, runner, batch):
    tools = [SectionTool("render_section"), ShotTool()]
    first_call = "render_section" if batch == "two_client_calls" else "shot"
    agent = make_deep_agent(tmp_path, tools=tools)
    llm = _script(AssistantMessage(content="", tool_calls=[
        ToolCall(id="call_a", type="function", name=first_call, arguments='{"section": 1}'),
        ToolCall(id="call_b", type="function", name="render_section", arguments='{"section": 2}'),
    ]))
    image = {"content": [{"type": "text", "text": "section"}, {"type": "image", "data_url": PNG_DATA_URL}]}
    with scripted(llm):
        paused = dict(pending(await run(agent, "render", f"img-{batch}")))
        if batch == "two_client_calls":
            paused = dict(pending(await run(agent, by_interrupt_id(paused["call_a"], image), f"img-{batch}")))
        await run(agent, by_interrupt_id(paused["call_b"], image), f"img-{batch}")

    roles = _roles(messages_seen(llm))
    tail = roles[roles.index("assistant"):]
    assert tail == ["assistant", "tool:call_a", "tool:call_b", "user"]


@pytest.mark.asyncio
async def test_a_server_image_waits_for_a_permission_ask_in_the_same_batch(tmp_path, runner):
    """Without client tools: the image of a finished call comes after the asked call's tool message, once."""
    shot, asked = ShotTool(), CountingTool("doc_tool")
    agent = make_deep_agent(tmp_path, tools=[shot, asked], permissions=ask_permissions("doc_tool"))
    llm = _script(AssistantMessage(content="", tool_calls=[
        ToolCall(id="call_a", type="function", name="shot", arguments="{}"),
        ToolCall(id="call_b", type="function", name="doc_tool", arguments="{}"),
    ]))
    with scripted(llm):
        [(_, request)] = pending(await run(agent, "shoot", "img-ask"))
        result = await run(agent, by_interrupt_id(request, APPROVE), "img-ask")

    assert result["result_type"] == "answer"
    assert (shot.invoke_count, asked.invoke_count) == (1, 1)
    roles = _roles(messages_seen(llm))
    assert roles[roles.index("assistant"):] == ["assistant", "tool:call_a", "tool:call_b", "user"]
    assert _image_urls(llm) == [PNG_DATA_URL]


class CompleteRecordingTool(SectionTool):
    def __init__(self):
        super().__init__()
        self.completed_with: List[dict] = []

    def complete(self, arguments, result, context):
        self.completed_with.append(dict(arguments))
        return "done: " + default_client_output(result).data["content"]


@pytest.mark.asyncio
async def test_complete_gets_the_arguments_the_client_ran(tmp_path, runner):
    tool = CompleteRecordingTool()
    agent = make_deep_agent(tmp_path, tools=[tool], rails=[RewriteArgsRail(tool_names=["edit_section"])])
    llm = _script(create_tool_call_response("edit_section", '{"section": 3}', tool_call_id="call_1"))
    with scripted(llm):
        [(_, request)] = pending(await run(agent, "edit", "client-args"))
        await run(agent, by_interrupt_id(request, "ok"), "client-args")

    assert tool.completed_with == [{"section": 103}]
    assert tool_results_seen(llm) == [("call_1", "done: ok")]


@pytest.mark.asyncio
async def test_complete_returning_text_keeps_the_images(tmp_path, runner):
    tool = CompleteRecordingTool()
    agent = make_deep_agent(tmp_path, tools=[tool])
    llm = _script(create_tool_call_response("edit_section", '{"section": 1}', tool_call_id="call_1"))
    image = {"content": [{"type": "text", "text": "section"}, {"type": "image", "data_url": PNG_DATA_URL}]}
    with scripted(llm):
        [(_, request)] = pending(await run(agent, "render", "text-img"))
        await run(agent, by_interrupt_id(request, image), "text-img")

    assert tool_results_seen(llm) == [("call_1", "done: section")]
    assert _image_urls(llm) == [PNG_DATA_URL]


@pytest.mark.asyncio
async def test_non_object_arguments_fail_the_call(tmp_path, runner):
    tool = SectionTool()
    agent = make_deep_agent(tmp_path, tools=[tool])
    llm = _script(create_tool_call_response("edit_section", "[1, 2]", tool_call_id="call_1"))
    with scripted(llm):
        result = await run(agent, "edit", "array-args")

    assert result["result_type"] == "answer"
    assert tool.requests == []
    [(_, content)] = tool_results_seen(llm)
    assert "must be a JSON object" in content


@pytest.mark.asyncio
@pytest.mark.parametrize(("arguments", "path"), [('{"section": "three"}', "$.section"),
                                                 ('{"section": 3, "doc": "d1"}', "$")])
async def test_arguments_that_break_the_input_schema_never_reach_the_client(tmp_path, runner, arguments, path):
    tool = SectionTool()
    tool.card.input_params = {**_SCHEMA, "additionalProperties": False}
    agent = make_deep_agent(tmp_path, tools=[tool])
    llm = _script(create_tool_call_response("edit_section", arguments, tool_call_id="call_1"))
    with scripted(llm):
        result = await run(agent, "edit", "schema-args")

    assert result["result_type"] == "answer"
    assert tool.requests == []
    [(_, content)] = tool_results_seen(llm)
    assert f"arguments are invalid at {path}:" in content


@pytest.mark.asyncio
async def test_parallel_safe_false_does_not_hold_back_later_calls_across_a_client_pause(tmp_path, runner):
    """Documented behaviour: a client pause ends a non-parallel-safe call's turn
    in the batch; later calls of the batch run while the client works."""
    exclusive = SectionTool("exclusive_edit")
    exclusive.card.parallel_safe = False
    shot = ShotTool()
    agent = make_deep_agent(tmp_path, tools=[exclusive, shot])
    llm = _script(AssistantMessage(content="", tool_calls=[
        ToolCall(id="call_a", type="function", name="exclusive_edit", arguments='{"section": 1}'),
        ToolCall(id="call_b", type="function", name="shot", arguments="{}"),
    ]))
    with scripted(llm):
        [(call_id, request)] = pending(await run(agent, "go", "barrier"))
        assert call_id == "call_a"
        assert shot.invoke_count == 1, "the later call ran while the client call was paused"
        await run(agent, by_interrupt_id(request, "edited"), "barrier")

    assert sorted(call for call, _ in tool_results_seen(llm)) == ["call_a", "call_b"]
