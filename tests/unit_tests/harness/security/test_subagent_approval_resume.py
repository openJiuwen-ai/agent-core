"""Nested replies must not become delegation permission arguments."""

import json
from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session.interaction.interaction import InteractionOutput
from openjiuwen.core.session.stream.base import OutputSchema
from openjiuwen.core.single_agent.interrupt.handler import ResumeContext, ToolInterruptHandler
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest, ToolCallInterruptRequest
from openjiuwen.core.single_agent.interrupt.state import ToolInterruptEntry, ToolInterruptionState
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ToolCallInputs
from openjiuwen.harness.rails.interrupt.interrupt_base import ApproveResult, RejectResult
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.host import ToolPermissionHost


def _config(decision="allow"):
    return {
        "enabled": True,
        "package_builtin_rules": False,
        "tools": {"task_tool": decision},
        "defaults": {"*": "allow"},
        "file_guard": {"enabled": False},
        "net_guard": {"enabled": False},
    }


def _state(*labels):
    calls = [ToolCall(id=label, name="task_tool", type="function", arguments=json.dumps({
        "subagent_type": "general-purpose", "task_description": label,
    })) for label in labels]
    return ToolInterruptionState(
        ai_message=AssistantMessage(content="", tool_calls=calls), iteration=0,
        interrupted_tools={call.id: ToolInterruptEntry(
            tool_call=call, is_sub_agent=True,
            interrupt_requests={"inner-" + call.id: InterruptRequest(message="Approve?")},
        ) for call in calls},
    )


def _pending(inner_id):
    call = ToolCall(id=inner_id, name="write_file", type="function", arguments='{"file_path":"result.txt"}')
    request = ToolCallInterruptRequest.from_tool_call(InterruptRequest(message="Approve?"), call)
    output = OutputSchema(type="interaction", index=0, payload=InteractionOutput(id=inner_id, value=request))
    return {"result_type": "interrupt", "interrupt_ids": [inner_id], "state": [output]}


async def _resume(state, reply, execute):
    ctx = AgentCallbackContext(agent=object(), inputs=ToolCallInputs())
    handler = ToolInterruptHandler(SimpleNamespace(context_engine=SimpleNamespace(save_contexts=_save)))
    await handler.handle_resume(ResumeContext(
        state=state, user_input=reply, ctx=ctx, context=None, session=None,
        invoke_inputs=InvokeInputs(query=reply), execute_tool_call=execute,
    ))
    return ctx


async def _save(session):
    pass


def _tool_context(ctx, call):
    return AgentCallbackContext(agent=ctx.agent, extra=dict(ctx.extra), inputs=ToolCallInputs(
        tool_call=call, tool_name=call.name, tool_args=call.arguments,
    ))


@pytest.mark.asyncio
async def test_nested_reply_preserves_original_arguments_for_engine_and_host():
    seen = []

    async def inspect(request):
        seen.append((request.tool_args, request.ctx.inputs.tool_args))
        # Host integrations must be able to encode the original operation.
        json.dumps(request.tool_args)
        json.dumps(request.ctx.inputs.tool_args)
        request.ctx.request_force_finish({"reason": "host-stop"})

    rail = PermissionInterruptRail(config=_config(), host=ToolPermissionHost(permission_scene_hook=inspect))
    reply = InteractiveInput()
    reply.update("inner-A", {"approved": True})

    async def execute(ctx, calls, session, context):
        call = calls[0]
        local = _tool_context(ctx, call)
        await rail.before_tool_call(local)
        assert local.has_force_finish_request
        assert isinstance(local.extra["_interrupt_decision"], ApproveResult)
        assert call.arguments["query"] is reply
        assert local.inputs.tool_args["query"] is reply
        return [("done", ToolMessage(content="done", tool_call_id=call.id))]

    await _resume(_state("A"), reply, execute)
    expected = {"subagent_type": "general-purpose", "task_description": "A"}
    assert seen == [(expected, expected)]


@pytest.mark.asyncio
async def test_repeated_nested_interrupt_keeps_original_delegation():
    state = _state("A")
    rail = PermissionInterruptRail(config=_config())

    async def execute(ctx, calls, session, context):
        call = calls[0]
        await rail.before_tool_call(_tool_context(ctx, call))
        return [(_pending("inner-A"), ToolMessage(content="pending", tool_call_id=call.id))]

    for choice in (True, False):
        reply = InteractiveInput()
        reply.update("inner-A", {"approved": choice})
        await _resume(state, reply, execute)
        arguments = state.interrupted_tools["A"].tool_call.arguments
        assert json.loads(arguments) == {"subagent_type": "general-purpose", "task_description": "A"}


@pytest.mark.asyncio
async def test_nested_parent_deny_still_blocks_resume():
    rail = PermissionInterruptRail(config=_config("deny"))

    async def execute(ctx, calls, session, context):
        call = calls[0]
        local = _tool_context(ctx, call)
        await rail.before_tool_call(local)
        assert isinstance(local.extra["_interrupt_decision"], RejectResult)
        assert local.extra["_skip_tool"] is True
        assert "PERMISSION_DENIED" in local.inputs.tool_result
        return [(local.inputs.tool_result, local.inputs.tool_msg)]

    reply = InteractiveInput()
    reply.update("inner-A", {"approved": True})
    await _resume(_state("A"), reply, execute)


@pytest.mark.asyncio
async def test_two_children_keep_allow_and_reject_separate_across_resumes():
    state = _state("A", "B")
    parent_rail = PermissionInterruptRail(config=_config())
    child_rail = PermissionInterruptRail(config={**_config(), "tools": {"write_file": "ask"}})
    performed, rejected = [], []

    async def execute(ctx, calls, session, context):
        results = []
        for call in calls:
            await parent_rail.before_tool_call(_tool_context(ctx, call))
            inner_id = "inner-" + call.id
            response = call.arguments["query"].user_inputs.get(inner_id)
            if response is None:
                result = _pending(inner_id)
            else:
                inner_call = ToolCall(id=inner_id, name="write_file", type="function",
                                      arguments=json.dumps({"file_path": call.id + ".txt"}))
                decision = await child_rail.resolve_interrupt(
                    AgentCallbackContext(agent=object()), inner_call, response,
                )
                if isinstance(decision, ApproveResult):
                    performed.append(call.id)
                else:
                    assert isinstance(decision, RejectResult)
                    rejected.append(call.id)
                result = "finished"
            results.append((result, ToolMessage(content=str(result), tool_call_id=call.id)))
        return results

    reply = InteractiveInput()
    reply.update("inner-A", {"approved": True})
    await _resume(state, reply, execute)
    assert performed == ["A"] and rejected == []
    assert set(state.interrupted_tools) == {"B"}
    reply = InteractiveInput()
    reply.update("inner-B", {"approved": False})
    await _resume(state, reply, execute)
    assert performed == ["A"] and rejected == ["B"]
    assert not state.interrupted_tools
