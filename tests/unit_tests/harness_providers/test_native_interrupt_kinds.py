# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The native DeepAgent harness maps each interrupt kind to the right host interaction.

Every test drives ``DeepAgentHarness.start / send / turn_events`` with a real
DeepAgent (real rails and resume path) and a scripted model.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Callable, Dict, List

import pytest
import pytest_asyncio

from openjiuwen.core.common.constants.constant import INTERACTION
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt import InterruptKind, interrupt_answer
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.harness.rails.evolution.evolution_interrupt_rail import EvolutionInterruptRail
from openjiuwen.harness.rails.interrupt.ask_user_rail import AskUserRail
from openjiuwen.harness.rails.interrupt.interrupt_base import BaseInterruptRail
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness_protocol import (
    HarnessContext,
    HarnessInput,
    HostCapability,
    InteractionResponseStatus,
    ToolApprovalDecision,
    ToolApprovalRequest,
    ToolApprovalResponse,
    TurnLifecycleEvent,
    UserInputRequest,
    UserInputResponse,
)
from openjiuwen.harness_providers.io_adapter import TURN_LIFECYCLE, HarnessIOAdapter
from openjiuwen.harness_providers.native import DeepAgentHarness
from tests.unit_tests.fixtures.deep_agent_interrupts import (
    CountingTool,
    UndeclaredConfirmRail,
    ask_permissions,
    make_deep_agent,
    scripted,
    tool_results_seen,
    trip_workflow,
)
from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response, create_tool_call_response

ALL_CAPS = frozenset({HostCapability.USER_INPUT, HostCapability.TOOL_APPROVAL})


class Host:
    """Answers each request with ``answer(request)``; records every request."""

    def __init__(self, answer: Callable[[Any], Any]):
        self.answer = answer
        self.requests: List[Any] = []

    async def handle(self, request):
        self.requests.append(request)
        if len(self.requests) > 5:
            raise AssertionError("the agent keeps asking: the answers do not satisfy it")
        return self.answer(request)

    async def cancel(self, request_id, *, reason=None):
        return None


def allow(request):
    if isinstance(request, ToolApprovalRequest):
        return ToolApprovalResponse(request_id=request.request_id, decision=ToolApprovalDecision.ALLOW)
    return UserInputResponse(request_id=request.request_id, status=InteractionResponseStatus.COMPLETED,
                             content="yes")


@pytest_asyncio.fixture
async def runner():
    await Runner.start()
    try:
        yield
    finally:
        await Runner.stop()


async def _turn(tmp_path, host: Host, capabilities, *, tools=(), rails=(), permissions=None, first_call=None,
                workflows=()):
    llm = MockLLMModel()
    llm.set_responses([first_call, create_text_response("done")])

    def factory(_ctx):
        agent = make_deep_agent(tmp_path, tools=list(tools), rails=list(rails), permissions=permissions)
        for card in workflows:
            agent.ability_manager.add(card)
        return agent

    harness = DeepAgentHarness(factory)
    context = HarnessContext(
        agent_name="native", agent_id="native_agent", host_session_id=f"native-{uuid.uuid4().hex[:6]}",
        system_prompt="", host_capabilities=frozenset(capabilities), interactions=host,
    )
    with scripted(llm):
        await harness.start(context)
        try:
            receipt = await harness.send(HarnessInput(content="go"))
            events = [event async for event in harness.turn_events(receipt.turn_id)]
        finally:
            await harness.stop()
    terminal = events[-1].event
    assert isinstance(terminal, TurnLifecycleEvent)
    return terminal.result, llm


def test_card_declares_the_optional_host_capabilities_it_uses():
    assert {HostCapability.USER_INPUT, HostCapability.TOOL_APPROVAL} <= (
        DeepAgentHarness.card.optional_host_capabilities
    )


@pytest.mark.asyncio
async def test_d_permission_ask_reaches_the_host_as_a_tool_approval(tmp_path, runner):
    """Issue D: the native harness turned a permission ask into a UserInputRequest, so the
    host's typed reply was an invalid approval and the ask repeated."""
    tool = CountingTool("doc_tool")
    host = Host(allow)
    result, _ = await _turn(tmp_path, host, ALL_CAPS, tools=[tool], permissions=ask_permissions("doc_tool"),
                            first_call=create_tool_call_response("doc_tool", '{"x": "1"}', tool_call_id="call_D"))

    [request] = host.requests
    assert isinstance(request, ToolApprovalRequest)
    assert request.call_id == "call_D" and request.arguments == {"x": "1"}
    assert request.request_id == request.provider_data["interrupt_id"] != "call_D"
    assert request.provider_data["kind"] == InterruptKind.PERMISSION
    assert tool.invoke_count == 1 and result.final_output == "done"


@pytest.mark.asyncio
async def test_text_only_host_answers_an_approval_with_a_word(tmp_path, runner):
    tool = CountingTool("doc_tool")
    host = Host(allow)
    await _turn(tmp_path, host, {HostCapability.USER_INPUT}, tools=[tool], permissions=ask_permissions("doc_tool"),
                first_call=create_tool_call_response("doc_tool", "{}", tool_call_id="call_t"))

    [request] = host.requests
    assert isinstance(request, UserInputRequest) and request.choices == ("allow", "allow_always", "deny")
    assert tool.invoke_count == 1


class _StubSubmission:
    async def prepare_simplify_submission(self, subject, actions):
        return None


@pytest.mark.asyncio
async def test_allow_for_session_on_a_skill_evolution_approval_means_allow_always(tmp_path, runner):
    tool = CountingTool("simplify_skill_experiences")
    evolution = EvolutionInterruptRail(
        review_runtime=object(), submission_service=_StubSubmission(), auto_save=False, language="en"
    )
    host = Host(lambda r: ToolApprovalResponse(request_id=r.request_id,
                                               decision=ToolApprovalDecision.ALLOW_FOR_SESSION))
    args = json.dumps({"subject": {"kind": "skill", "name": "demo"}, "actions": [{"action": "delete"}]})
    await _turn(tmp_path, host, ALL_CAPS, tools=[tool], rails=[evolution],
                first_call=create_tool_call_response("simplify_skill_experiences", args, tool_call_id="call_e"))

    assert [r.provider_data["kind"] for r in host.requests] == [InterruptKind.SKILL_EVOLUTION_APPROVAL]
    assert tool.invoke_count == 1


@pytest.mark.asyncio
async def test_approval_with_edited_arguments_is_denied_not_run_with_the_original(tmp_path, runner):
    tool = CountingTool("doc_tool")
    host = Host(lambda r: ToolApprovalResponse(request_id=r.request_id, decision=ToolApprovalDecision.ALLOW,
                                               updated_arguments={"x": "SAFE"}))
    _, llm = await _turn(tmp_path, host, ALL_CAPS, tools=[tool], permissions=ask_permissions("doc_tool"),
                         first_call=create_tool_call_response("doc_tool", '{"x": "1"}', tool_call_id="call_u"))

    assert tool.invoke_count == 0
    [(_, content)] = tool_results_seen(llm)
    assert "editing arguments is not supported" in content


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    {"decision": ToolApprovalDecision.DENY, "interrupt": True},
    {"decision": ToolApprovalDecision.ABORT},
])
async def test_abort_or_interrupt_stops_the_turn_with_the_ask_pending(tmp_path, runner, response):
    tool = CountingTool("doc_tool")
    host = Host(lambda r: ToolApprovalResponse(request_id=r.request_id, **response))
    result, _ = await _turn(tmp_path, host, ALL_CAPS, tools=[tool], permissions=ask_permissions("doc_tool"),
                            first_call=create_tool_call_response("doc_tool", "{}", tool_call_id="call_s"))

    assert tool.invoke_count == 0
    assert result.stop_reason == "interrupt"
    [pending] = result.provider_data["pending_interrupts"]
    assert pending["tool_call_id"] == "call_s" and pending["kind"] == InterruptKind.PERMISSION
    assert list(result.provider_data["pending_interrupt_ids"]) == ["call_s"]


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [None, ""])
async def test_an_approval_answered_with_nothing_is_rejected(tmp_path, runner, content):
    tool = CountingTool("doc_tool")
    host = Host(lambda r: UserInputResponse(request_id=r.request_id, status=InteractionResponseStatus.COMPLETED,
                                            content=content))
    await _turn(tmp_path, host, {HostCapability.USER_INPUT}, tools=[tool], permissions=ask_permissions("doc_tool"),
                first_call=create_tool_call_response("doc_tool", "{}", tool_call_id="call_t"))

    assert tool.invoke_count == 0


ASK_ARGS = json.dumps({"questions": [{"header": "Color", "question": "Which color?", "options": []}]})


def blue(request):
    return UserInputResponse(request_id=request.request_id, status=InteractionResponseStatus.COMPLETED,
                             content={"answers": {"Which color?": "blue"}})


@pytest.mark.asyncio
@pytest.mark.parametrize("capabilities", [frozenset(), {HostCapability.TOOL_APPROVAL}])
async def test_a_host_that_did_not_declare_user_input_is_still_asked_questions(tmp_path, runner, capabilities):
    """As before kinds were mapped: a host with an interaction handler is asked whatever it declared."""
    host = Host(blue)
    result, llm = await _turn(tmp_path, host, capabilities, rails=[AskUserRail()],
                              first_call=create_tool_call_response("ask_user", ASK_ARGS, tool_call_id="call_q"))

    [request] = host.requests
    assert isinstance(request, UserInputRequest)
    assert '"Which color?"="blue"' in tool_results_seen(llm)[-1][1]
    assert result.final_output == "done"


@pytest.mark.asyncio
async def test_a_host_that_declared_nothing_answers_an_approval_with_a_word(tmp_path, runner):
    tool = CountingTool("doc_tool")
    host = Host(allow)
    await _turn(tmp_path, host, frozenset(), tools=[tool], permissions=ask_permissions("doc_tool"),
                first_call=create_tool_call_response("doc_tool", "{}", tool_call_id="call_t"))

    [request] = host.requests
    assert isinstance(request, UserInputRequest) and request.choices == ("allow", "allow_always", "deny")
    assert tool.invoke_count == 1


@pytest.mark.asyncio
async def test_ask_user_goes_to_the_host_as_user_input(tmp_path, runner):
    answers: Dict[str, Any] = {}

    def reply(request):
        answers["request"] = request
        return UserInputResponse(request_id=request.request_id, status=InteractionResponseStatus.COMPLETED,
                                 content={"answers": {"Which color?": "blue"}})

    _, llm = await _turn(tmp_path, Host(reply), ALL_CAPS, rails=[AskUserRail()],
                         first_call=create_tool_call_response("ask_user", ASK_ARGS, tool_call_id="call_q"))

    assert answers["request"].provider_data["kind"] == InterruptKind.ASK_USER
    assert answers["request"].prompt == "Which color?"
    [(_, content)] = tool_results_seen(llm)
    assert '"Which color?"="blue"' in content


@pytest.mark.asyncio
async def test_a_workflow_interaction_is_asked_and_answered_by_its_own_id(tmp_path, runner):
    """A workflow tool's question is not an InterruptRequest: it has no interrupt id or kind."""
    host = Host(lambda r: UserInputResponse(request_id=r.request_id, status=InteractionResponseStatus.COMPLETED,
                                            content="Paris"))
    result, llm = await _turn(tmp_path, host, {HostCapability.USER_INPUT}, workflows=[trip_workflow()],
                              first_call=create_tool_call_response("plan_trip", "{}", tool_call_id="call_w"))

    [request] = host.requests
    assert isinstance(request, UserInputRequest)
    assert request.request_id == "ask_city" and request.provider_data["interrupt_id"] == ""
    assert request.choices == ()
    assert "Paris" in tool_results_seen(llm)[-1][1]
    assert result.final_output == "done"


class UndeclaredTextRail(BaseInterruptRail):
    """Asks for free text and declares no kind, like a rail written before kinds existed."""

    async def resolve_interrupt(self, ctx, tool_call, user_input, auto_confirm_config=None):
        if user_input is None:
            return self.interrupt(InterruptRequest(message="Which city?"))
        return self.reject(tool_result=f"GOT:{json.dumps(user_input)}")


@pytest.mark.asyncio
async def test_an_undeclared_kind_gets_the_host_answer_as_is(tmp_path, runner):
    """The native harness passed every answer through before kinds existed; undeclared kinds still are."""
    host = Host(lambda r: UserInputResponse(request_id=r.request_id, status=InteractionResponseStatus.COMPLETED,
                                            content="Paris") if isinstance(r, UserInputRequest) else allow(r))
    _, llm = await _turn(tmp_path, host, ALL_CAPS, tools=[CountingTool("doc_tool")],
                         rails=[UndeclaredTextRail(tool_names=["doc_tool"])],
                         first_call=create_tool_call_response("doc_tool", "{}", tool_call_id="call_n"))

    [request] = host.requests
    assert isinstance(request, UserInputRequest) and request.choices == ()
    assert request.provider_data["kind"] == InterruptKind.UNDECLARED and request.provider_data["interrupt_id"]
    assert tool_results_seen(llm)[-1][1] == 'GOT:"Paris"'


@pytest.mark.asyncio
async def test_an_undeclared_approval_rail_takes_the_host_approval_dict(tmp_path, runner):
    tool = CountingTool("doc_tool")
    host = Host(lambda r: UserInputResponse(request_id=r.request_id, status=InteractionResponseStatus.COMPLETED,
                                            content={"approved": True}))
    await _turn(tmp_path, host, ALL_CAPS, tools=[tool], rails=[UndeclaredConfirmRail(tool_names=["doc_tool"])],
                first_call=create_tool_call_response("doc_tool", "{}", tool_call_id="call_n"))

    assert isinstance(host.requests[0], UserInputRequest)
    assert tool.invoke_count == 1


async def _adapter_turn(tmp_path, answer, *, tools=(), rails=(), permissions=None, first_call,
                        auto_approve_tools=True):
    """Drive ``HarnessIOAdapter(DeepAgentHarness)``; ``answer(chunk)`` builds the InteractiveInput."""
    llm = MockLLMModel()
    llm.set_responses([first_call, create_text_response("done")])
    harness = DeepAgentHarness(lambda _ctx: make_deep_agent(tmp_path, tools=list(tools), rails=list(rails),
                                                            permissions=permissions))
    adapter = HarnessIOAdapter(harness, emit_turn_lifecycle=True, auto_approve_tools=auto_approve_tools)
    context = HarnessContext(agent_name="native", agent_id="native_agent",
                             host_session_id=f"io-{uuid.uuid4().hex[:6]}", system_prompt="")
    asked = []

    async def drive():
        await adapter.send("go")
        async for chunk in adapter.outputs():
            if chunk.type == INTERACTION:
                asked.append(chunk)
                assert len(asked) <= 2, "the agent keeps asking"
                assert await adapter.send(answer(chunk)) is None, "the answer matched no pending interaction"
            elif chunk.type == TURN_LIFECYCLE and chunk.payload.get("kind") != "started":
                return

    with scripted(llm):
        await adapter.start(context)
        try:
            await asyncio.wait_for(drive(), timeout=30)
        finally:
            await adapter.stop()
    return asked, llm


@pytest.mark.asyncio
async def test_io_adapter_answers_by_tool_call_id_still_resolve(tmp_path, runner):
    def by_call_id(_chunk):
        user_input = InteractiveInput()
        user_input.update("call_q", {"answers": {"Which color?": "blue"}})
        return user_input

    asked, llm = await _adapter_turn(tmp_path, by_call_id, rails=[AskUserRail()],
                                     first_call=create_tool_call_response("ask_user", ASK_ARGS,
                                                                          tool_call_id="call_q"))

    assert len(asked) == 1
    assert '"Which color?"="blue"' in tool_results_seen(llm)[-1][1]


@pytest.mark.asyncio
@pytest.mark.parametrize("value, persisted", [
    ({"approved": True, "auto_confirm": True}, "permanent"),
    ({"approved": True, "auto_confirm": True, "persist_allow": True}, "permanent"),
    (interrupt_answer(InterruptKind.PERMISSION, approved=True, remember=True), "session"),
])
async def test_io_adapter_remember_keeps_its_scope_when_tools_are_asked(tmp_path, runner, monkeypatch, value,
                                                                         persisted):
    seen = []
    always, session = PermissionInterruptRail._persist_allow_always, PermissionInterruptRail._persist_session_allow

    def spy(scope, original):
        def record(self, *args, **kwargs):
            seen.append(scope)
            return original(self, *args, **kwargs)
        return record

    monkeypatch.setattr(PermissionInterruptRail, "_persist_allow_always", spy("permanent", always))
    monkeypatch.setattr(PermissionInterruptRail, "_persist_session_allow", spy("session", session))

    def approve(chunk):
        user_input = InteractiveInput()
        user_input.update(chunk.payload.id, value)
        return user_input

    tool = CountingTool("doc_tool")
    asked, _ = await _adapter_turn(tmp_path, approve, tools=[tool], permissions=ask_permissions("doc_tool"),
                                   auto_approve_tools=False,
                                   first_call=create_tool_call_response("doc_tool", "{}", tool_call_id="call_p"))

    assert asked[0].payload.value["kind"] == "tool_approval"
    assert tool.invoke_count == 1
    assert seen == [persisted]
