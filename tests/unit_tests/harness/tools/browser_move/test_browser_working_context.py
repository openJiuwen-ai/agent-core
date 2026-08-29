#!/usr/bin/env python
# coding: utf-8

"""Focused lifecycle tests for durable browser-agent working context."""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any

from openjiuwen.core.context_engine import ContextEngine, ContextWindow
from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackEvent,
    AgentCallbackContext,
    InvokeInputs,
    ModelCallInputs,
    ToolCallInputs,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_working_context import (
    BROWSER_TASK_STATE_KEY,
    BROWSER_TOOL_MEMORY_METADATA_KEY,
    BROWSER_WORKING_MEMORY_RECORD_BEGIN,
    BROWSER_WORKING_MEMORY_RECORD_END,
    BrowserWorkingContextStore,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_working_context_processor import (
    BrowserWorkingContextProcessor,
    BrowserWorkingContextProcessorConfig,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_working_context_rail import (
    BrowserWorkingContextRail,
)


class _FakeSession:
    def __init__(self, session_id: str = "browser-working-context") -> None:
        self._session_id = session_id
        self._state: dict[str, Any] = {}

    def get_session_id(self) -> str:
        return self._session_id

    def get_state(self, key: str):
        return self._state.get(key)

    def update_state(self, payload: dict[str, Any]) -> None:
        self._state.update(payload)


class _FakeContext:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session
        self.messages: list[Any] = []

    def get_session_ref(self) -> _FakeSession:
        return self.session

    def get_messages(self) -> list[Any]:
        return self.messages


def _run(coro):
    return asyncio.run(coro)


def _memory(
    task: str,
    status: str = "pending",
    **fields: list[str],
) -> dict[str, Any]:
    return {
        "task_list": [{"task": task, "status": status}],
        "errors": fields.get("errors", []),
        "failures": fields.get("failures", []),
        "blockers": fields.get("blockers", []),
        "key_facts": fields.get("key_facts", []),
        "important_information": fields.get("important_information", []),
    }


def _response(
    memory: dict[str, Any],
    *,
    tool_calls: list[ToolCall] | None = None,
    visible_text: str = "",
) -> AssistantMessage:
    content = (
        f"{visible_text}\n{BROWSER_WORKING_MEMORY_RECORD_BEGIN}\n"
        f"{json.dumps(memory)}\n{BROWSER_WORKING_MEMORY_RECORD_END}"
    )
    return AssistantMessage(content=content, tool_calls=tool_calls)


def _model_ctx(
    rail: BrowserWorkingContextRail,
    session: _FakeSession,
    context: _FakeContext,
    response: AssistantMessage,
) -> AgentCallbackContext:
    del rail
    return AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(response=response),
        session=session,
        context=context,
    )


def _tool_call(call_id: str, name: str) -> ToolCall:
    return ToolCall(
        id=call_id,
        type="function",
        name=name,
        arguments="{}",
    )


def _record_tool_result(
    rail: BrowserWorkingContextRail,
    session: _FakeSession,
    context: _FakeContext,
    tool_call: ToolCall,
    tool_result: ToolOutput,
    *,
    raw_content: str,
) -> ToolMessage:
    tool_message = ToolMessage(
        content=raw_content,
        tool_call_id=str(tool_call.id),
    )
    ctx = AgentCallbackContext(
        agent=None,
        inputs=ToolCallInputs(
            tool_call=tool_call,
            tool_name=tool_call.name,
            tool_args=tool_call.arguments,
            tool_result=tool_result,
            tool_msg=tool_message,
        ),
        session=session,
        context=context,
    )
    _run(rail.after_tool_call(ctx))
    context.messages.append(tool_message)
    return tool_message


def _inject(
    processor: BrowserWorkingContextProcessor,
    context: _FakeContext,
) -> ContextWindow:
    window = ContextWindow(context_messages=list(context.messages))
    _, rendered = _run(processor.on_get_context_window(context, window))
    return rendered


def test_model_memory_survives_and_internal_update_is_not_user_facing() -> None:
    config = BrowserWorkingContextProcessorConfig()
    rail = BrowserWorkingContextRail(config)
    assert AgentCallbackEvent.AFTER_REACT_ITERATION in rail.get_callbacks()
    session = _FakeSession()
    context = _FakeContext(session)
    response = _response(
        _memory(
            "Collect the account status",
            key_facts=["The account page is reachable."],
        ),
        visible_text="Continuing.",
    )

    _run(rail.after_model_call(_model_ctx(rail, session, context, response)))

    assert response.content == "Continuing."
    state = BrowserWorkingContextStore(config).load(session)
    assert len(state.recent_steps) == 1
    assert state.current.task_list == []
    assert state.current.key_facts == ["The account page is reachable."]

    processor = BrowserWorkingContextProcessor(config)
    prompt = _inject(processor, context).context_messages[-1].content
    assert "The account page is reachable." in prompt


def test_missing_model_update_does_not_erase_last_valid_state() -> None:
    config = BrowserWorkingContextProcessorConfig()
    rail = BrowserWorkingContextRail(config)
    session = _FakeSession()
    context = _FakeContext(session)
    valid_response = _response(
        _memory("Keep this confirmed task", status="completed", key_facts=["Confirmed fact"])
    )
    _run(rail.after_model_call(_model_ctx(rail, session, context, valid_response)))

    missing_update = AssistantMessage(content="Visible answer without internal state")
    _run(rail.after_model_call(_model_ctx(rail, session, context, missing_update)))

    state = BrowserWorkingContextStore(config).load(session)
    assert state.current.task_list == []
    assert state.current.key_facts == ["Confirmed fact"]
    assert len(state.recent_steps) == 1
    assert state.recent_steps[-1].model_update_error is None
    assert missing_update.content == "Visible answer without internal state"


def test_tool_call_without_record_carries_memory_forward_without_an_error() -> None:
    config = BrowserWorkingContextProcessorConfig()
    rail = BrowserWorkingContextRail(config)
    session = _FakeSession()
    context = _FakeContext(session)
    _run(
        rail.before_invoke(
            AgentCallbackContext(
                agent=None,
                inputs=InvokeInputs(query="Inspect the checkout flow"),
                session=session,
            )
        )
    )
    tool_call = _tool_call("call-1", "browser_navigate")
    response = AssistantMessage(content="", tool_calls=[tool_call])
    model_ctx = _model_ctx(rail, session, context, response)

    _run(rail.after_model_call(model_ctx))

    pending_state = BrowserWorkingContextStore(config).load(session)
    assert pending_state.pending_step is not None
    assert pending_state.pending_step.model_update_error is None
    assert pending_state.pending_step.model_memory is None
    assert response.content == ""

    context.messages.append(ToolMessage(content="navigation complete", tool_call_id="call-1"))
    _run(rail.after_react_iteration(model_ctx))

    committed_state = BrowserWorkingContextStore(config).load(session)
    assert committed_state.pending_step is None
    assert committed_state.recent_steps == []
    assert committed_state.current.task_list == []


def test_rail_enforces_fallback_update_when_model_omits_block() -> None:
    config = BrowserWorkingContextProcessorConfig()
    rail = BrowserWorkingContextRail(config)
    session = _FakeSession()
    context = _FakeContext(session)
    response = AssistantMessage(content="Inspecting checkout now.")
    model_ctx = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(
            messages=[UserMessage(content="Inspect the checkout flow")],
            response=response,
        ),
        session=session,
        context=context,
    )

    _run(rail.after_model_call(model_ctx))

    state = BrowserWorkingContextStore(config).load(session)
    assert state.current.task_list == []
    assert state.recent_steps == []
    assert response.content == "Inspecting checkout now."


def test_rail_rejects_incomplete_update_and_enforces_complete_fallback() -> None:
    config = BrowserWorkingContextProcessorConfig()
    rail = BrowserWorkingContextRail(config)
    session = _FakeSession()
    context = _FakeContext(session)
    incomplete_record = {
        "task_list": [{"task": "Inspect checkout", "status": "pending"}],
    }
    response = AssistantMessage(
        content=(
            f"{BROWSER_WORKING_MEMORY_RECORD_BEGIN}\n"
            f"{json.dumps(incomplete_record)}\n"
            f"{BROWSER_WORKING_MEMORY_RECORD_END}"
        )
    )
    model_ctx = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(
            messages=[UserMessage(content="Inspect checkout")],
            response=response,
        ),
        session=session,
        context=context,
    )

    _run(rail.after_model_call(model_ctx))

    state = BrowserWorkingContextStore(config).load(session)
    assert state.current.task_list == []
    assert state.recent_steps[-1].model_update_error is None
    assert response.content == ""


def test_one_model_record_aggregates_multiple_tool_results() -> None:
    config = BrowserWorkingContextProcessorConfig()
    rail = BrowserWorkingContextRail(config)
    session = _FakeSession()
    context = _FakeContext(session)
    calls = [
        _tool_call("call-1", "browser_navigate"),
        _tool_call("call-2", "browser_probe_cards"),
    ]
    response = _response(_memory("Inspect products"), tool_calls=calls)

    model_ctx = _model_ctx(rail, session, context, response)
    _run(rail.after_model_call(model_ctx))
    state = BrowserWorkingContextStore(config).load(session)
    assert state.recent_steps == []
    assert state.pending_step is not None

    _record_tool_result(
        rail,
        session,
        context,
        calls[0],
        ToolOutput(
            success=True,
            long_term_memory="Navigation reached the product page.",
        ),
        raw_content="complete navigation result",
    )
    _record_tool_result(
        rail,
        session,
        context,
        calls[1],
        ToolOutput(
            success=True,
            long_term_memory="Three product cards were identified.",
        ),
        raw_content="complete card inventory",
    )
    _run(rail.after_react_iteration(model_ctx))

    state = BrowserWorkingContextStore(config).load(session)
    assert len(state.recent_steps) == 1
    assert [item.tool_name for item in state.recent_steps[0].tool_memories] == [
        "browser_navigate",
        "browser_probe_cards",
    ]


def test_long_term_memory_precedes_extracted_content_and_survives_later_steps() -> None:
    config = BrowserWorkingContextProcessorConfig()
    rail = BrowserWorkingContextRail(config)
    session = _FakeSession()
    context = _FakeContext(session)
    tool_call = _tool_call("call-1", "browser_probe_cards")
    first_response = _response(_memory("Collect product facts"), tool_calls=[tool_call])
    first_ctx = _model_ctx(rail, session, context, first_response)
    _run(rail.after_model_call(first_ctx))

    _record_tool_result(
        rail,
        session,
        context,
        tool_call,
        ToolOutput(
            success=True,
            extracted_content="large complete product result",
            long_term_memory="Three products matched the requested filters.",
        ),
        raw_content="raw authoritative product payload",
    )
    _run(rail.after_react_iteration(first_ctx))

    second_response = _response(
        _memory(
            "Collect product facts",
            status="completed",
            key_facts=["Recorded the matching count."],
        )
    )
    _run(rail.after_model_call(_model_ctx(rail, session, context, second_response)))

    state = BrowserWorkingContextStore(config).load(session)
    first_tool_memory = state.recent_steps[0].tool_memories[0]
    assert first_tool_memory.content_source == "long_term_memory"
    assert first_tool_memory.durable_content == ("Three products matched the requested filters.")
    prompt = (
        _inject(
            BrowserWorkingContextProcessor(config),
            context,
        )
        .context_messages[-1]
        .content
    )
    assert "Three products matched the requested filters." in prompt
    assert "large complete product result" not in prompt


def test_one_step_content_is_injected_exactly_once() -> None:
    config = BrowserWorkingContextProcessorConfig()
    rail = BrowserWorkingContextRail(config)
    session = _FakeSession()
    context = _FakeContext(session)
    tool_call = _tool_call("call-1", "browser_snapshot")
    response = _response(_memory("Read the current result"), tool_calls=[tool_call])
    model_ctx = _model_ctx(rail, session, context, response)
    _run(rail.after_model_call(model_ctx))
    raw_message = _record_tool_result(
        rail,
        session,
        context,
        tool_call,
        ToolOutput(
            success=True,
            extracted_content="ONE-STEP-AUTHORITATIVE-CONTENT",
            include_extracted_content_only_once=True,
        ),
        raw_content="ONE-STEP-AUTHORITATIVE-CONTENT",
    )
    _run(rail.after_react_iteration(model_ctx))
    processor = BrowserWorkingContextProcessor(config)

    first_window = _inject(processor, context)
    second_window = _inject(processor, context)
    first_text = "\n".join(str(message.content) for message in first_window.context_messages)
    second_text = "\n".join(str(message.content) for message in second_window.context_messages)

    assert first_text.count("ONE-STEP-AUTHORITATIVE-CONTENT") == 1
    assert "ONE-STEP-AUTHORITATIVE-CONTENT" not in second_text
    assert raw_message.content == "ONE-STEP-AUTHORITATIVE-CONTENT"
    assert raw_message.metadata[BROWSER_TOOL_MEMORY_METADATA_KEY]


def test_tool_errors_are_retained_without_explicit_tool_metadata() -> None:
    config = BrowserWorkingContextProcessorConfig()
    rail = BrowserWorkingContextRail(config)
    session = _FakeSession()
    context = _FakeContext(session)
    tool_call = _tool_call("call-error", "browser_navigate")
    response = _response(_memory("Open the destination"), tool_calls=[tool_call])
    model_ctx = _model_ctx(rail, session, context, response)
    _run(rail.after_model_call(model_ctx))
    context.messages.append(
        ToolMessage(
            content="Ability execution error: navigation timed out",
            tool_call_id="call-error",
        )
    )

    _run(rail.after_react_iteration(model_ctx))

    state = BrowserWorkingContextStore(config).load(session)
    assert state.recent_steps[0].tool_memories[0].error == ("Ability execution error: navigation timed out")
    prompt = (
        _inject(
            BrowserWorkingContextProcessor(config),
            context,
        )
        .context_messages[-1]
        .content
    )
    assert "navigation timed out" in prompt


def test_raw_diagnostic_history_is_separate_from_prompt_memory() -> None:
    config = BrowserWorkingContextProcessorConfig()
    rail = BrowserWorkingContextRail(config)
    session = _FakeSession()
    context = _FakeContext(session)
    tool_call = _tool_call("call-raw", "browser_probe_cards")
    response = _response(_memory("Inspect cards"), tool_calls=[tool_call])
    model_ctx = _model_ctx(rail, session, context, response)
    _run(rail.after_model_call(model_ctx))
    raw_message = _record_tool_result(
        rail,
        session,
        context,
        tool_call,
        ToolOutput(
            success=True,
            long_term_memory="Two matching cards were retained.",
        ),
        raw_content="VERY-LARGE-RAW-DIAGNOSTIC-PAYLOAD",
    )
    _run(rail.after_react_iteration(model_ctx))

    window = _inject(BrowserWorkingContextProcessor(config), context)
    prompt_messages = "\n".join(str(message.content) for message in window.context_messages)

    assert context.messages == [raw_message]
    assert context.messages[0].content == "VERY-LARGE-RAW-DIAGNOSTIC-PAYLOAD"
    assert "VERY-LARGE-RAW-DIAGNOSTIC-PAYLOAD" not in prompt_messages
    assert "Two matching cards were retained." in prompt_messages
    assert not any(message.metadata.get("browser_working_context") for message in context.messages)


def test_context_engine_injection_does_not_persist_as_execution_history() -> None:
    config = BrowserWorkingContextProcessorConfig()
    session = _FakeSession()
    engine = ContextEngine()
    context = _run(
        engine.create_context(
            "durable-working-context",
            session=session,
            processors=[
                (
                    "BrowserWorkingContextProcessor",
                    config,
                )
            ],
        )
    )
    _run(context.add_messages(UserMessage(content="original browser task")))

    first_window = _run(context.get_context_window())
    second_window = _run(context.get_context_window())

    assert [message.content for message in context.get_messages()] == ["original browser task"]
    assert len(first_window.context_messages) == 2
    assert len(second_window.context_messages) == 2
    assert first_window.context_messages[-1].metadata["browser_working_context"] is True
    assert second_window.context_messages[-1].metadata["browser_working_context"] is True


def test_processor_guidance_defines_each_working_memory_field() -> None:
    processor = BrowserWorkingContextProcessor(BrowserWorkingContextProcessorConfig(language="en"))
    prompt = _inject(processor, _FakeContext(_FakeSession())).context_messages[-1].content

    assert "runtime-maintained browser task context" in prompt
    assert "field coverage, blockers, evidence, and recent actions as authoritative" in prompt
    assert "Do not rewrite or echo this context" in prompt
    assert '"key_facts"' in prompt
    assert '"important_information"' in prompt
    assert '"task_list"' not in prompt
    assert BROWSER_WORKING_MEMORY_RECORD_BEGIN in prompt
    assert BROWSER_WORKING_MEMORY_RECORD_END in prompt
    assert "<browser_context_update>" not in prompt


def test_processor_renders_chinese_guidance_with_stable_schema_keys() -> None:
    config = BrowserWorkingContextProcessorConfig(language="cn")
    processor = BrowserWorkingContextProcessor(config)
    session = _FakeSession()
    context = _FakeContext(session)

    prompt = _inject(processor, context).context_messages[-1].content

    assert "这是由 runtime 维护的浏览器任务上下文" in prompt
    assert "不要重写或复述这些内容" in prompt
    assert BROWSER_WORKING_MEMORY_RECORD_BEGIN in prompt
    assert BROWSER_WORKING_MEMORY_RECORD_END in prompt
    assert "<browser_context_update>" not in prompt
    assert '"task_list"' not in prompt
    assert '"key_facts"' in prompt
    assert '"important_information"' in prompt


def test_history_limit_discards_old_steps_without_local_compaction() -> None:
    config = BrowserWorkingContextProcessorConfig(max_recent_steps=2)
    rail = BrowserWorkingContextRail(config)
    session = _FakeSession()
    context = _FakeContext(session)

    for index in range(1, 5):
        response = _response(
            _memory(
                f"Task {index}",
                status="completed" if index < 4 else "pending",
                key_facts=[f"Fact {index}"],
            )
        )
        _run(rail.after_model_call(_model_ctx(rail, session, context, response)))

    state = BrowserWorkingContextStore(config).load(session)
    assert [step.step_number for step in state.recent_steps] == [3, 4]
    assert state.current.task_list == []
    assert state.current.key_facts == ["Fact 4"]

    prompt = (
        _inject(
            BrowserWorkingContextProcessor(config),
            context,
        )
        .context_messages[-1]
        .content
    )
    assert "Task 1" not in prompt
    assert "Fact 1" not in prompt
    assert "unverified_compacted_history" not in prompt


def test_follow_up_and_new_agent_instance_reuse_completed_session_memory() -> None:
    config = BrowserWorkingContextProcessorConfig()
    first_rail = BrowserWorkingContextRail(config)
    session = _FakeSession("shared-external-session")
    context = _FakeContext(session)
    _run(
        first_rail.before_invoke(
            AgentCallbackContext(
                agent=None,
                inputs=InvokeInputs(query="Check the order"),
                session=session,
            )
        )
    )
    final_response = _response(
        _memory(
            "Check the order",
            status="completed",
            important_information=["Order 123 is shipped."],
        ),
        visible_text="Order 123 is shipped.",
    )
    _run(first_rail.after_model_call(_model_ctx(first_rail, session, context, final_response)))
    context.messages.append(AssistantMessage(content=final_response.content))

    second_rail = BrowserWorkingContextRail(config)
    _run(
        second_rail.before_invoke(
            AgentCallbackContext(
                agent=None,
                inputs=InvokeInputs(query="Now check its tracking link"),
                session=session,
            )
        )
    )

    state = BrowserWorkingContextStore(config).load(session)
    assert state.request_sequence == 2
    assert state.request_kind == "follow_up"
    assert state.current.task_list == []
    assert state.current.important_information == ["Order 123 is shipped."]
    assert context.messages[-1].content == "Order 123 is shipped."

    prompt = (
        _inject(
            BrowserWorkingContextProcessor(config),
            context,
        )
        .context_messages[-1]
        .content
    )
    assert "Now check its tracking link" in prompt
    assert "Order 123 is shipped." in prompt
    assert "runtime-maintained" in prompt


def test_inner_model_boundary_restores_and_reconciles_follow_up_when_outer_session_is_absent() -> None:
    config = BrowserWorkingContextProcessorConfig()
    first_rail = BrowserWorkingContextRail(config)
    session = _FakeSession("shared-inner-session")
    first_context = _FakeContext(session)
    _run(
        first_rail.before_invoke(
            AgentCallbackContext(
                agent=None,
                inputs=InvokeInputs(query="Check the order"),
                session=session,
            )
        )
    )
    first_response = _response(
        _memory(
            "Check the order",
            status="completed",
            key_facts=["Order 123 belongs to Alice."],
        ),
        visible_text="Order checked.",
    )
    _run(first_rail.after_model_call(_model_ctx(first_rail, session, first_context, first_response)))

    second_rail = BrowserWorkingContextRail(config)
    outer_ctx = AgentCallbackContext(
        agent=None,
        inputs=InvokeInputs(query="Now check its tracking link"),
        session=None,
    )
    _run(second_rail.before_invoke(outer_ctx))

    second_context = _FakeContext(session)
    second_context.messages.append(UserMessage(content="Now check its tracking link"))
    inner_ctx = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(messages=list(second_context.messages)),
        session=session,
        context=second_context,
    )
    _run(second_rail.before_model_call(inner_ctx))

    state = BrowserWorkingContextStore(config).load(session)
    assert state.request_sequence == 2
    assert state.request_kind == "follow_up"
    assert state.active_request == "Now check its tracking link"
    assert state.current.task_list == []
    assert state.current.key_facts == ["Order 123 belongs to Alice."]


def test_explicit_deep_agent_session_begins_request_once_at_inner_model_boundary() -> None:
    config = BrowserWorkingContextProcessorConfig()
    rail = BrowserWorkingContextRail(config)
    session = _FakeSession("explicit-deep-agent-session")
    outer_ctx = AgentCallbackContext(
        agent=SimpleNamespace(react_agent=object()),
        inputs=InvokeInputs(query="Inspect the page"),
        session=session,
    )
    _run(rail.before_invoke(outer_ctx))
    assert BrowserWorkingContextStore(config).load(session).request_sequence == 0

    context = _FakeContext(session)
    context.messages.append(UserMessage(content="Inspect the page"))
    inner_ctx = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(messages=list(context.messages)),
        session=session,
        context=context,
    )
    _run(rail.before_model_call(inner_ctx))

    state = BrowserWorkingContextStore(config).load(session)
    assert state.request_sequence == 1
    assert state.request_kind == "initial"


def test_missing_model_update_carries_forward_reconciled_state_with_an_explicit_error() -> None:
    config = BrowserWorkingContextProcessorConfig()
    rail = BrowserWorkingContextRail(config)
    session = _FakeSession()
    context = _FakeContext(session)
    _run(
        rail.before_invoke(
            AgentCallbackContext(
                agent=None,
                inputs=InvokeInputs(query="Inspect the account"),
                session=session,
            )
        )
    )

    response = AssistantMessage(content="I will inspect it.", tool_calls=[])
    _run(rail.after_model_call(_model_ctx(rail, session, context, response)))

    state = BrowserWorkingContextStore(config).load(session)
    assert state.current.task_list == []
    assert state.recent_steps == []


def test_checkpointed_state_is_restored_by_a_reconstructed_agent_session() -> None:
    config = BrowserWorkingContextProcessorConfig()
    session_id = f"browser-working-context-{uuid.uuid4().hex}"
    card = AgentCard(id="openjiuwen.browser_agent", name="browser_agent")
    first_session = create_agent_session(session_id=session_id, card=card)
    _run(first_session.pre_run(inputs={"query": "Find the order"}))
    first_rail = BrowserWorkingContextRail(config)
    first_context = _FakeContext(first_session)
    _run(
        first_rail.before_invoke(
            AgentCallbackContext(
                agent=None,
                inputs=InvokeInputs(query="Find the order"),
                session=first_session,
            )
        )
    )
    first_response = _response(
        _memory(
            "Find the order",
            status="completed",
            key_facts=["Order 123 was found."],
        )
    )
    _run(first_rail.after_model_call(_model_ctx(first_rail, first_session, first_context, first_response)))
    _run(first_session.commit())

    second_session = create_agent_session(
        session_id=session_id,
        card=AgentCard(id=card.id, name=card.name),
    )
    _run(second_session.pre_run(inputs={"query": "Track it"}))
    second_rail = BrowserWorkingContextRail(config)
    second_context = _FakeContext(second_session)
    second_context.messages.append(UserMessage(content="Track it"))
    inner_ctx = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(messages=list(second_context.messages)),
        session=second_session,
        context=second_context,
    )
    _run(second_rail.before_model_call(inner_ctx))

    restored = BrowserWorkingContextStore(config).load(second_session)
    assert restored.request_sequence == 2
    assert restored.request_kind == "follow_up"
    assert restored.current.key_facts == ["Order 123 was found."]
    assert restored.current.task_list == []
    assert len(restored.recent_steps) == 1
    prompt = (
        _inject(
            BrowserWorkingContextProcessor(config),
            second_context,
        )
        .context_messages[-1]
        .content
    )
    assert '"kind": "follow_up"' in prompt
    assert "Order 123 was found." in prompt
    assert '"model_notes": {' in prompt


def test_retained_values_are_length_bounded() -> None:
    config = BrowserWorkingContextProcessorConfig(max_item_chars=128)
    rail = BrowserWorkingContextRail(config)
    session = _FakeSession()
    context = _FakeContext(session)
    tool_call = _tool_call("call-large", "browser_probe_cards")
    long_fact = "f" * 200
    long_tool_memory = "m" * 200
    response = _response(
        _memory(
            "Inspect a large result",
            key_facts=[long_fact],
        ),
        tool_calls=[tool_call],
    )
    model_ctx = _model_ctx(rail, session, context, response)
    _run(rail.after_model_call(model_ctx))
    _record_tool_result(
        rail,
        session,
        context,
        tool_call,
        ToolOutput(
            success=True,
            long_term_memory=long_tool_memory,
        ),
        raw_content="raw large result",
    )
    _run(rail.after_react_iteration(model_ctx))

    state = BrowserWorkingContextStore(config).load(session)
    retained_fact = state.current.key_facts[0]
    retained_tool_memory = state.recent_steps[0].tool_memories[0].durable_content
    assert retained_fact.startswith("f" * 128)
    assert retained_fact.endswith("[truncated 72 characters]")
    assert retained_tool_memory is not None
    assert retained_tool_memory.startswith("m" * 128)
    assert retained_tool_memory.endswith("[truncated 72 characters]")


def test_processor_replaces_only_its_own_ephemeral_message() -> None:
    config = BrowserWorkingContextProcessorConfig()
    processor = BrowserWorkingContextProcessor(config)
    session = _FakeSession()
    context = _FakeContext(session)
    browser_state = UserMessage(
        name="current_browser_state",
        metadata={"browser_state_context": True},
        content="<browser_state>fresh observation</browser_state>",
    )
    stale_working_state = UserMessage(
        name="browser_working_context",
        metadata={"browser_working_context": True},
        content="stale durable view",
    )
    window = ContextWindow(context_messages=[browser_state, stale_working_state])

    _, rendered = _run(processor.on_get_context_window(context, window))

    assert len(rendered.context_messages) == 2
    assert rendered.context_messages[1] is browser_state
    assert "fresh observation" in rendered.context_messages[1].content
    assert "stale durable view" not in rendered.context_messages[0].content
    assert "<browser_working_context>" in rendered.context_messages[0].content


def test_processor_projects_runtime_task_state_before_current_page_state() -> None:
    config = BrowserWorkingContextProcessorConfig()
    processor = BrowserWorkingContextProcessor(config)
    session = _FakeSession()
    session.update_state(
        {
            BROWSER_TASK_STATE_KEY: {
                "task_id": "task-1",
                "goal": "Find the product title and price",
                "task_type": "simple",
                "status": "replan_required",
                "current_phase": "extraction",
                "phases": {
                    "extraction": {
                        "status": "replan_required",
                        "attempts": 3,
                        "budget": 20,
                        "completion_condition": "requested fields have evidence",
                    }
                },
                "required_fields": ["title", "price"],
                "field_coverage": ["title"],
                "blockers": [],
                "replan_required": True,
                "replan_count": 1,
                "failed_strategies": ["script_exploration"],
                "next_action_class": "materially_different_strategy",
                "recent_actions": [
                    {
                        "seq": 3,
                        "phase": "extraction",
                        "action_class": "script_exploration",
                        "target_summary": '{"tool":"browser_evaluate","expression_sha256":"abcd"}',
                        "outcome": "success",
                        "semantic_delta": "no_progress",
                        "new_evidence_fields": [],
                        "elapsed_ms": 40,
                    }
                ],
                "structured_evidence": [],
            }
        }
    )
    context = _FakeContext(session)
    current_state = UserMessage(
        name="current_browser_state",
        metadata={"browser_state_context": True},
        content="<browser_state>current</browser_state>",
    )
    window = ContextWindow(context_messages=[current_state])

    _, rendered = _run(processor.on_get_context_window(context, window))

    assert rendered.context_messages[-1] is current_state
    prompt = rendered.context_messages[-2].content
    assert '"runtime_directive": "replan_before_browser_action"' in prompt
    assert '"field_coverage": [' in prompt
    assert '"semantic_delta": "no_progress"' in prompt
    assert "script_exploration" in prompt
