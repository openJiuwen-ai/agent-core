# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Each interrupt rail receives only the answers to its own interrupts.

The tests drive a real DeepAgent / ReActAgent (real rails, permission engine,
interrupt handler and resume path) with a scripted model. They are the
regression tests for interrupt rails reading each other's answers on one
tool call.
"""

from __future__ import annotations

import json
from typing import Any, List
from unittest.mock import patch

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.interrupt.response import InterruptKind, InterruptRequest
from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent, AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.rails.evolution.evolution_interrupt_rail import EvolutionInterruptRail
from openjiuwen.harness.rails.interrupt.ask_user_rail import AskUserRail
from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmInterruptRail
from openjiuwen.harness.rails.interrupt.interrupt_base import BaseInterruptRail
from openjiuwen.harness.rails.security.base_security_rail import BaseSecurityRail, SecurityCheckContext
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.permission_engine.host import ToolPermissionHost
from openjiuwen.harness.security.permission_engine.models import PermissionLevel, PermissionResult
from tests.unit_tests.agent.react_agent.interrupt.test_base import NestedAgentConfig, ReadTool, create_nested_agent
from tests.unit_tests.fixtures.deep_agent_interrupts import (
    APPROVE,
    CountingTool,
    ask_permissions,
    by_call_id,
    by_interrupt_id,
    make_deep_agent,
    pending,
    run,
    scripted,
    tool_results_seen,
)
from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response, create_tool_call_response


@pytest_asyncio.fixture
async def runner():
    await Runner.start()
    try:
        yield
    finally:
        await Runner.stop()


def _answer(answer_by: str, call_id: str, payload: Any, value: Any) -> InteractiveInput:
    return by_interrupt_id(payload, value) if answer_by == "interrupt_id" else by_call_id(call_id, value)


def _script(*responses) -> MockLLMModel:
    llm = MockLLMModel()
    llm.set_responses(list(responses) + [create_text_response("done")])
    return llm


class LateRail(BaseInterruptRail):
    """Undeclared second interrupt on the call, after the permission rail (a host's
    client-executed-tool rail had this shape)."""

    priority = 40

    def __init__(self, tool_names, *, kind: str = "", metadata_kind: str = ""):
        super().__init__(tool_names=tool_names)
        self.kind = kind
        self.metadata_kind = metadata_kind
        self.seen: List[Any] = []

    async def resolve_interrupt(self, ctx, tool_call, user_input, auto_confirm_config=None):
        self.seen.append(user_input)
        if isinstance(user_input, dict) and "client_result" in user_input:
            return self.reject(tool_result=f"client says: {user_input['client_result']}")
        request = InterruptRequest(message="run it on the client")
        if self.kind:
            request.interrupt_kind = self.kind
        if self.metadata_kind:
            request.metadata = {"interrupt_kind": self.metadata_kind}
        return self.interrupt(request)


class GuardRail(BaseSecurityRail):
    """A security rail that asks for approval before a tool runs."""

    supported_events = {AgentCallbackEvent.BEFORE_TOOL_CALL}

    def __init__(self, tool_names):
        super().__init__(tool_names=tool_names)
        self.asks = 0

    async def run_security_check(self, security_ctx: SecurityCheckContext):
        if security_ctx.callback_ctx.inputs.tool_name not in self._tool_names:
            return self.allow()
        decision = self._handle_interrupt_resume(security_ctx, "guard")
        if decision is not None:
            return decision
        self.asks += 1
        return self.interrupt(InterruptRequest(message="guard: approve?"))


class LowPriorityConfirmRail(ConfirmInterruptRail):
    priority = 10


# ---------------------------------------------------------------------------
# Issue A1 / A2 / B regressions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("answer_by", ["interrupt_id", "tool_call_id"])
async def test_a1_permission_ask_then_second_interrupt_does_not_reask_permission(tmp_path, runner, answer_by):
    """Issue A1: the answer to a later interrupt on the call was read as a malformed
    permission approval ("Invalid confirmation payload") and permission asked again."""
    tool = CountingTool("doc_tool")
    late = LateRail(["doc_tool"])
    agent = make_deep_agent(tmp_path, tools=[tool], rails=[late], permissions=ask_permissions("doc_tool"))
    llm = _script(create_tool_call_response("doc_tool", '{"x": "1"}', tool_call_id="call_A1"))
    with scripted(llm), patch.object(
        PermissionInterruptRail, "_build_message", autospec=True, side_effect=PermissionInterruptRail._build_message
    ) as permission_asks:
        [(call_id, ask)] = pending(await run(agent, "run doc_tool", "a1"))
        assert ask.interrupt_kind == InterruptKind.PERMISSION

        [(call_id, second)] = pending(await run(agent, _answer(answer_by, call_id, ask, APPROVE), "a1"))
        assert second.interrupt_id != ask.interrupt_id
        assert late.seen == [None], "the second rail must not receive the permission approval"

        result = await run(agent, _answer(answer_by, call_id, second, {"client_result": "section 3"}), "a1")

    assert result["result_type"] == "answer"
    assert permission_asks.call_count == 1
    assert tool_results_seen(llm) == [("call_A1", "client says: section 3")]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer_by", ["interrupt_id", "tool_call_id"])
async def test_a2_permission_and_confirm_on_one_tool_each_ask(tmp_path, runner, answer_by):
    """Issue A2: one {"approved": true} passed both the permission ask and the confirm rail."""
    tool = CountingTool("delete_all")
    agent = make_deep_agent(
        tmp_path,
        tools=[tool],
        rails=[ConfirmInterruptRail(tool_names=["delete_all"])],
        permissions=ask_permissions("delete_all"),
    )
    llm = _script(create_tool_call_response("delete_all", '{"x": "1"}', tool_call_id="call_A2"))
    with scripted(llm):
        [(call_id, ask)] = pending(await run(agent, "delete everything", "a2"))
        assert ask.interrupt_kind == InterruptKind.PERMISSION

        [(call_id, confirm)] = pending(await run(agent, _answer(answer_by, call_id, ask, APPROVE), "a2"))
        assert confirm.interrupt_kind == InterruptKind.CONFIRM
        assert tool.invoke_count == 0, "the confirm gate must ask before the tool runs"

        result = await run(agent, _answer(answer_by, call_id, confirm, APPROVE), "a2")

    assert result["result_type"] == "answer"
    assert tool.invoke_count == 1


@pytest.mark.asyncio
async def test_a2_confirm_rejection_after_permission_approval_skips_the_tool(tmp_path, runner):
    tool = CountingTool("delete_all")
    agent = make_deep_agent(
        tmp_path,
        tools=[tool],
        rails=[ConfirmInterruptRail(tool_names=["delete_all"])],
        permissions=ask_permissions("delete_all"),
    )
    llm = _script(create_tool_call_response("delete_all", "{}", tool_call_id="call_A2r"))
    with scripted(llm):
        [(_, ask)] = pending(await run(agent, "delete everything", "a2r"))
        [(_, confirm)] = pending(await run(agent, by_interrupt_id(ask, APPROVE), "a2r"))
        rejected = {"approved": False, "feedback": "not now", "auto_confirm": False}
        result = await run(agent, by_interrupt_id(confirm, rejected), "a2r")

    assert result["result_type"] == "answer"
    assert tool.invoke_count == 0
    assert tool_results_seen(llm) == [("call_A2r", "not now")]


ASK_ARGS = json.dumps({"questions": [{
    "header": "Color",
    "question": "Which color?",
    "options": [{"label": "red", "description": ""}, {"label": "blue", "description": ""}],
}]})


@pytest.mark.asyncio
@pytest.mark.parametrize("answer_by", ["interrupt_id", "tool_call_id"])
async def test_b_permission_ask_on_ask_user_then_question(tmp_path, runner, answer_by):
    """Issue B: with ask_user under a permission ask, the approval and the answers were
    read by the wrong rails and the two prompts alternated forever."""
    agent = make_deep_agent(tmp_path, rails=[AskUserRail()], permissions=ask_permissions("ask_user"))
    llm = _script(create_tool_call_response("ask_user", ASK_ARGS, tool_call_id="call_B"))
    with scripted(llm):
        [(call_id, ask)] = pending(await run(agent, "ask me", "b"))
        assert ask.interrupt_kind == InterruptKind.PERMISSION

        [(call_id, question)] = pending(await run(agent, _answer(answer_by, call_id, ask, APPROVE), "b"))
        assert question.interrupt_kind == InterruptKind.ASK_USER and question.questions

        result = await run(agent, _answer(answer_by, call_id, question, {"answers": {"Which color?": "blue"}}), "b")

    assert result["result_type"] == "answer"
    assert llm.call_count == 2
    [(_, content)] = tool_results_seen(llm)
    assert '"Which color?"="blue"' in content


# ---------------------------------------------------------------------------
# Routing by the raising rail, not by kind
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["explicit_kind", "metadata_kind"])
async def test_rail_that_labels_its_interrupt_with_another_kind_still_gets_its_answer(tmp_path, runner, variant):
    tool = CountingTool("doc_tool")
    label = {"kind": "host_client_exec"} if variant == "explicit_kind" else {"metadata_kind": "host_client_exec"}
    late = LateRail(["doc_tool"], **label)
    agent = make_deep_agent(tmp_path, tools=[tool], rails=[late])
    llm = _script(create_tool_call_response("doc_tool", "{}", tool_call_id="call_k"))
    with scripted(llm):
        [(_, request)] = pending(await run(agent, "go", "kinds"))
        result = await run(agent, by_interrupt_id(request, {"client_result": "ok"}), "kinds")

    assert result["result_type"] == "answer"
    assert late.seen == [None, {"client_result": "ok"}]


@pytest.mark.asyncio
async def test_security_rail_and_confirm_rail_on_one_tool_each_ask(tmp_path, runner):
    """A security rail and a confirm rail share the "confirm" kind but not their answers."""
    tool = CountingTool("tool_p2")
    guard = GuardRail(["tool_p2"])
    agent = make_deep_agent(tmp_path, tools=[tool], rails=[guard, LowPriorityConfirmRail(tool_names=["tool_p2"])])
    llm = _script(create_tool_call_response("tool_p2", "{}", tool_call_id="call_p2"))
    with scripted(llm):
        [(_, first)] = pending(await run(agent, "go", "same-kind"))
        assert first.message == "guard: approve?"
        [(_, second)] = pending(await run(agent, by_interrupt_id(first, APPROVE), "same-kind"))
        assert second.interrupt_kind == InterruptKind.CONFIRM and second.message != "guard: approve?"
        assert tool.invoke_count == 0
        result = await run(agent, by_interrupt_id(second, APPROVE), "same-kind")

    assert result["result_type"] == "answer"
    assert guard.asks == 1 and tool.invoke_count == 1


@pytest.mark.asyncio
async def test_two_rails_of_one_class_with_distinct_owners_each_ask(tmp_path, runner):
    tool = CountingTool("tool_two")
    first = ConfirmInterruptRail(tool_names=["tool_two"])
    second = ConfirmInterruptRail(tool_names=["tool_two"])
    second.interrupt_owner = "second-confirm"
    agent = make_deep_agent(tmp_path, tools=[tool], rails=[first, second])
    llm = _script(create_tool_call_response("tool_two", "{}", tool_call_id="call_two"))
    with scripted(llm):
        [(_, one)] = pending(await run(agent, "go", "two"))
        [(_, two)] = pending(await run(agent, by_interrupt_id(one, APPROVE), "two"))
        assert two.interrupt_owner != one.interrupt_owner
        result = await run(agent, by_interrupt_id(two, APPROVE), "two")

    assert result["result_type"] == "answer" and tool.invoke_count == 1


class PauseObserver(AgentRail):
    """Records the owner and kind ``after_tool_call`` sees on each paused call; the id
    is given later, when the interrupt handler collects the call."""

    priority = 1

    def __init__(self):
        super().__init__()
        self.seen: List[tuple] = []

    async def after_tool_call(self, ctx):
        if ctx.is_paused:
            request = ctx.exception.request
            self.seen.append((ctx.inputs.tool_call.id, request.interrupt_owner, request.interrupt_kind))


@pytest.mark.asyncio
@pytest.mark.parametrize("make_rail", [ConfirmInterruptRail, GuardRail], ids=["interrupt_rail", "security_rail"])
async def test_parallel_calls_answered_in_batches_keep_their_ids(tmp_path, runner, make_rail):
    from openjiuwen.core.foundation.llm import AssistantMessage
    from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall

    tool = CountingTool("guarded")
    observer = PauseObserver()
    agent = make_deep_agent(tmp_path, tools=[tool], rails=[make_rail(tool_names=["guarded"]), observer])
    llm = _script(AssistantMessage(content="", tool_calls=[
        ToolCall(id="call_a", type="function", name="guarded", arguments="{}"),
        ToolCall(id="call_b", type="function", name="guarded", arguments="{}"),
    ]))
    with scripted(llm):
        first = dict(pending(await run(agent, "go", "batch")))
        assert first["call_a"].interrupt_id and first["call_a"].interrupt_id != first["call_b"].interrupt_id
        identity = (first["call_a"].interrupt_owner, first["call_a"].interrupt_kind)
        assert identity == (make_rail(tool_names=["guarded"]).interrupt_owner, "confirm")
        assert sorted(observer.seen) == [("call_a", *identity), ("call_b", *identity)]
        still = dict(pending(await run(agent, by_interrupt_id(first["call_a"], APPROVE), "batch")))
        assert set(still) == {"call_b"}
        assert still["call_b"].interrupt_id == first["call_b"].interrupt_id
        assert observer.seen[-1] == ("call_b", *identity)
        assert tool.invoke_count == 1
        result = await run(agent, by_interrupt_id(still["call_b"], APPROVE), "batch")

    assert result["result_type"] == "answer" and tool.invoke_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("answer_by", ["interrupt_id", "tool_call_id"])
async def test_gate_on_a_sub_agent_call_is_not_asked_again_after_the_inner_answer(runner, answer_by):
    read_tool = ReadTool()
    sub = await create_nested_agent(NestedAgentConfig(
        agent_id=f"sub_{answer_by}", agent_name="sub_x", system_prompt="sub", tools=[read_tool],
        rail_tool_names=["read"],
    ))
    sub_card = AgentCard(id=f"sub_{answer_by}", name="sub_x", description="sub", input_params={
        "type": "object", "properties": {"query": {"type": "string", "description": "q"}}, "required": ["query"]})
    Runner.resource_mgr.add_agent(sub_card, agent=lambda: sub)
    main = await create_nested_agent(NestedAgentConfig(
        agent_id=f"main_{answer_by}", agent_name="main_x", system_prompt="main", sub_agent_cards=[sub_card],
        rail_tool_names=["sub_x"],
    ))
    llm = MockLLMModel()
    llm.set_responses([
        create_tool_call_response("sub_x", '{"query": "read"}', tool_call_id="outer_call"),
        create_tool_call_response("read", '{"filepath": "/tmp/t"}', tool_call_id="inner_call"),
        create_text_response("sub done"),
        create_text_response("main done"),
    ])
    asked = []
    conversation = {"conversation_id": f"s_{answer_by}"}
    with scripted(llm):
        result = await Runner.run_agent(agent=main, inputs={"query": "go", **conversation})
        for _ in range(4):
            if result.get("result_type") != "interrupt":
                break
            [(call_id, payload)] = pending(result)
            asked.append(payload.tool_name)
            resume = _answer(answer_by, call_id, payload, APPROVE)
            result = await Runner.run_agent(agent=main, inputs={"query": resume, **conversation})

    assert asked == ["sub_x", "read"]
    assert result["result_type"] == "answer"
    assert read_tool.invoke_count == 1


# ---------------------------------------------------------------------------
# Answers carried across resume rounds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remembered_permission_is_persisted_once_when_another_interrupt_follows(tmp_path, runner):
    tool = CountingTool("delete_all")
    agent = make_deep_agent(
        tmp_path, tools=[tool], rails=[LateRail(["delete_all"])], permissions=ask_permissions("delete_all")
    )
    llm = _script(create_tool_call_response("delete_all", "{}", tool_call_id="call_P"))
    remember = {"approved": True, "feedback": "", "auto_confirm": True, "persist_allow": False}
    with scripted(llm), patch.object(
        PermissionInterruptRail,
        "_persist_session_allow",
        autospec=True,
        side_effect=PermissionInterruptRail._persist_session_allow,
    ) as persist:
        [(_, ask)] = pending(await run(agent, "go", "persist"))
        [(_, late)] = pending(await run(agent, by_interrupt_id(ask, remember), "persist"))
        result = await run(agent, by_interrupt_id(late, {"client_result": "ok"}), "persist")

    assert result["result_type"] == "answer"
    assert persist.call_count == 1
    assert tool_results_seen(llm) == [("call_P", "client says: ok")]


@pytest.mark.asyncio
async def test_deny_rule_added_after_approval_still_wins(tmp_path, runner):
    tool = CountingTool("delete_all")
    agent = make_deep_agent(
        tmp_path,
        tools=[tool],
        rails=[LowPriorityConfirmRail(tool_names=["delete_all"])],
        permissions=ask_permissions("delete_all"),
    )
    llm = _script(create_tool_call_response("delete_all", "{}", tool_call_id="call_deny"))
    denied = PermissionResult(permission=PermissionLevel.DENY, matched_rule="tools.delete_all", reason="now denied")
    with scripted(llm):
        [(_, ask)] = pending(await run(agent, "go", "deny-later"))
        [(_, second)] = pending(await run(agent, by_interrupt_id(ask, APPROVE), "deny-later"))
        with patch.object(PermissionEngine, "check_permission", return_value=denied):
            result = await run(agent, by_interrupt_id(second, APPROVE), "deny-later")

    assert result["result_type"] == "answer"
    assert tool.invoke_count == 0
    [(_, content)] = tool_results_seen(llm)
    assert "PERMISSION_DENIED" in content and "now denied" in content


@pytest.mark.asyncio
async def test_deny_rule_added_while_the_approval_was_pending_wins(tmp_path, runner):
    tool = CountingTool("delete_all")
    agent = make_deep_agent(tmp_path, tools=[tool], permissions=ask_permissions("delete_all"))
    llm = _script(create_tool_call_response("delete_all", "{}", tool_call_id="call_wait"))
    denied = PermissionResult(permission=PermissionLevel.DENY, matched_rule="tools.delete_all", reason="now denied")
    with scripted(llm):
        [(_, ask)] = pending(await run(agent, "go", "deny-pending"))
        with patch.object(PermissionEngine, "check_permission", return_value=denied):
            result = await run(agent, by_interrupt_id(ask, APPROVE), "deny-pending")

    assert result["result_type"] == "answer"
    assert tool.invoke_count == 0
    [(_, content)] = tool_results_seen(llm)
    assert "PERMISSION_DENIED" in content and "now denied" in content


@pytest.mark.asyncio
async def test_scene_hook_is_told_when_an_approval_is_carried(tmp_path, runner):
    seen = []

    async def scene_hook(hook_input):
        if hook_input.user_input is not None:
            seen.append(hook_input.answer_carried)
        return None

    tool = CountingTool("delete_all")
    permission = PermissionInterruptRail(
        config=ask_permissions("delete_all"), host=ToolPermissionHost(permission_scene_hook=scene_hook)
    )
    agent = make_deep_agent(tmp_path, tools=[tool], rails=[permission, LateRail(["delete_all"])])
    llm = _script(create_tool_call_response("delete_all", "{}", tool_call_id="call_hook"))
    with scripted(llm):
        [(_, ask)] = pending(await run(agent, "go", "hook"))
        [(_, second)] = pending(await run(agent, by_interrupt_id(ask, APPROVE), "hook"))
        await run(agent, by_interrupt_id(second, {"client_result": "ok"}), "hook")

    assert seen == [False, True]


class _StubSubmission:
    async def prepare_simplify_submission(self, subject, actions):
        return None


@pytest.mark.asyncio
async def test_carried_evolution_approval_still_approves_the_call(tmp_path, runner):
    tool = CountingTool("simplify_skill_experiences")
    evolution = EvolutionInterruptRail(
        review_runtime=object(), submission_service=_StubSubmission(), auto_save=False, language="en"
    )
    agent = make_deep_agent(
        tmp_path, tools=[tool], rails=[evolution, LowPriorityConfirmRail(tool_names=["simplify_skill_experiences"])]
    )
    args = json.dumps({"subject": {"kind": "skill", "name": "demo-skill"},
                       "actions": [{"action": "delete", "record_id": "r1", "reason": "stale"}]})
    llm = _script(create_tool_call_response("simplify_skill_experiences", args, tool_call_id="call_E"))
    with scripted(llm):
        [(_, approval)] = pending(await run(agent, "simplify", "evo"))
        assert approval.interrupt_kind == InterruptKind.SKILL_EVOLUTION_APPROVAL
        [(_, confirm)] = pending(await run(agent, by_interrupt_id(approval, {"action": "allow_always"}), "evo"))
        assert confirm.interrupt_kind == InterruptKind.CONFIRM
        result = await run(agent, by_interrupt_id(confirm, APPROVE), "evo")

    assert result["result_type"] == "answer"
    assert tool.invoke_count == 1
