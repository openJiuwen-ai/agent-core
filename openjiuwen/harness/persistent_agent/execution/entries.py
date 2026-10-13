# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Formal execute / resume / abort entries around one single-round native DeepAgent.

Each execution entry runs the native agent in its own runner task, so an abort
cancels that task instead of the host's consumer, and the runner still saves
state and records the ending. The generator only relays recorded events.
"""

import asyncio
import hashlib
import json
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any, AsyncGenerator, AsyncIterator

from pydantic import ValidationError

from openjiuwen.core.common.constants.constant import INTERACTION
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.checkpointer import NoopCheckpointer
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY, ToolInterruptionState

from ..factory import create_persistent_agent
from ..schema.build_context import PersistentBuildContext
from ..schema.events import AgentEvent, EventType
from ..schema.protocol import TEXT_MAX_CHARS
from ..schema.request import ConfirmReply, RequestInput
from ..schema.spec import PersistentAgentSpec
from . import seams
from .binding import InvocationState, abort_bound, close_bound, stream_bound
from .control import AbortReason, ExecutionControl
from .emitter import EventEmitter
from .model_rail import PAUSE_MARKER
from .participation import judge_participation

_UNSUPPORTED_FLAGS = ("enable_async_subagent", "enable_subagent_runtime", "add_general_purpose_agent")
_SENSITIVE_KEYS = ("password", "secret", "token", "key", "credential", "authorization")
_DETAIL_LIMIT = 4096
_END: Any = object()
_CAPACITY_DETAIL = "final answer exceeds the event size limit; kept in the session state record for handoff"


@dataclass(frozen=True)
class _Outcome:
    """How the native run ended, before it becomes a paused or final event."""

    kind: str
    text: str = ""
    detail: str | None = None
    intercepted: tuple[dict, ...] = ()


def redact_arguments(arguments: Any) -> str:
    """Readable tool arguments with values under sensitive key names masked."""

    def scrub(value):
        if isinstance(value, dict):
            return {
                key: "***" if any(mark in str(key).lower() for mark in _SENSITIVE_KEYS) else scrub(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return arguments.strip()[:TEXT_MAX_CHARS] or "{}"
    text = json.dumps(scrub(arguments if arguments is not None else {}), ensure_ascii=False, default=str)
    return text[:TEXT_MAX_CHARS] or "{}"


def _reject(code: StatusCode, message: str, cause: BaseException | None = None):
    return build_error(code, cause=cause, error_msg=message)


def _detail(text: Any, fallback: str) -> str:
    return text.strip()[:_DETAIL_LIMIT] if isinstance(text, str) and text.strip() else fallback


def _require_supported_modes(spec) -> None:
    """Phase 1 opens only single-round execution; published switches are never flipped silently."""
    if not isinstance(spec, PersistentAgentSpec):
        raise _reject(StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, "PersistentAgentSpec required")
    if spec.enable_task_loop is not False:
        raise _reject(
            StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, "only single-round execution; set enable_task_loop=False"
        )
    if spec.subagents or any(getattr(spec, flag) for flag in _UNSUPPORTED_FLAGS):
        raise _reject(StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, "subagents are not opened for Persistent execution yet")


def _require_assembled_modes(agent) -> None:
    config = agent.deep_config
    if config.enable_task_loop or config.subagents or any(getattr(config, flag, False) for flag in _UNSUPPORTED_FLAGS):
        raise _reject(StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, "assembled agent enables an unsupported execution mode")


def _require_isolation(context) -> None:
    if not isinstance(context, PersistentBuildContext):
        raise _reject(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, "PersistentBuildContext required")
    if not isinstance(context.session.checkpointer(), NoopCheckpointer):
        raise _reject(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, "Session must be created with a NoopCheckpointer")


def _require_request(context: PersistentBuildContext, inputs) -> RequestInput:
    try:
        request = RequestInput.model_validate(inputs)
    except ValidationError as exc:
        raise _reject(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, "invalid RequestInput", exc) from exc
    recovery_ref = context.execution.recovery_ref
    if request.continue_instruction is not None and recovery_ref is None:
        raise _reject(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, "continue_instruction requires a recovery_ref")
    if recovery_ref is not None:
        raise _reject(StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, "continuation is not integrated yet (PA-18)")
    return request


def _start(context: PersistentBuildContext) -> ExecutionControl:
    control = context.binding.control
    if control.emitter is not None:
        raise _reject(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, "construction context already used")
    control.emitter = EventEmitter(context.execution, context.ports.commit, control)
    return control


async def _store_call(operation: str, call) -> Any:
    """Session state record failures are runtime errors; there is no empty-window fallback."""
    try:
        return await call
    except Exception as exc:
        raise _reject(StatusCode.DEEPAGENT_RUNTIME_ERROR, f"session state record {operation} failed", exc) from exc


async def _load(context: PersistentBuildContext, store) -> seams.LoadedRecord:
    record = await _store_call("load", store.load(context))
    if not isinstance(record, seams.LoadedRecord):
        raise _reject(StatusCode.DEEPAGENT_RUNTIME_ERROR, "session state record load returned no LoadedRecord")
    return record


async def _assemble(assembler, context: PersistentBuildContext, request: RequestInput, record) -> seams.WindowAssembly:
    """Window assembly failures are runtime errors; the native call never runs on a partial window."""
    try:
        assembly = await assembler.assemble(context, request, record)
    except BaseError:
        raise
    except Exception as exc:
        raise _reject(StatusCode.DEEPAGENT_RUNTIME_ERROR, "window assembly failed", exc) from exc
    if not isinstance(assembly, seams.WindowAssembly):
        raise _reject(StatusCode.DEEPAGENT_RUNTIME_ERROR, "window assembly returned no WindowAssembly")
    return assembly


def _chunk_parts(chunk) -> tuple[Any, Any]:
    if isinstance(chunk, dict):
        return chunk.get("type"), chunk.get("payload")
    return getattr(chunk, "type", None), getattr(chunk, "payload", None)


def _intercepted_call(payload) -> dict:
    """Interaction ID, tool name and redacted arguments of one call awaiting confirmation."""
    request = getattr(payload, "value", None)
    return {
        "tool_call_id": str(getattr(payload, "id", "")),
        "tool_name": getattr(request, "tool_name", "") or "unknown_tool",
        "arguments": redact_arguments(getattr(request, "tool_args", None)),
    }


def _abort_outcome(reason: AbortReason) -> _Outcome:
    return _Outcome("cancelled" if reason == "cancel" else "stopped")


def _outcome_from_result(result: dict | None, intercepted: list[dict]) -> _Outcome:
    if intercepted:
        return _Outcome("awaiting_confirmation", intercepted=tuple(intercepted))
    result_type = result.get("result_type") if result else None
    output = result.get("output") if result else None
    if result_type == PAUSE_MARKER:
        reason = result.get("reason") or "error"
        return _Outcome(reason, detail=_detail(result.get("detail"), f"model call skipped: {reason}"))
    if result_type == "answer":
        return _Outcome("completed", text=output if isinstance(output, str) else "")
    fallback = f"native result_type {result_type!r}" if result else "native execution ended without a result"
    # Errors, including an exhausted iteration budget, are never handed off as an answer.
    return _Outcome("error", detail=_detail(output, fallback))


def _absorb_cancel() -> None:
    """Our own abort cancelled this runner; finish the Execution instead of unwinding."""
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        task.uncancel()


def _lock(control: ExecutionControl, outcome: _Outcome) -> _Outcome:
    """Lock the ending synchronously; a control accepted before the lock decides it."""
    reason = control.lock_outcome()
    return _abort_outcome(reason) if reason is not None else outcome


async def _run_native(agent, context: PersistentBuildContext, native_input) -> _Outcome:
    """Run one native stream and map it; the returned outcome is locked against later controls."""
    control = context.binding.control
    if control.abort_reason is not None:
        return _lock(control, _abort_outcome(control.abort_reason))
    emitter = control.emitter
    result, intercepted = None, []
    try:
        async with aclosing(stream_bound(agent, native_input, context=context)) as chunks:
            async for chunk in chunks:
                chunk_type, payload = _chunk_parts(chunk)
                if chunk_type == "llm_output" and isinstance(payload, dict):
                    text = payload.get("content")
                    if isinstance(text, str):
                        await emitter.emit_text(EventType.REPLY_DELTA, text)
                elif chunk_type == INTERACTION:
                    intercepted.append(_intercepted_call(payload))
                elif chunk_type == "answer" and isinstance(payload, dict):
                    result = dict(payload)
    except asyncio.CancelledError:
        if control.abort_reason is None:
            raise
    except Exception as exc:
        if control.fatal is not None:
            raise control.fatal from exc
        if control.abort_reason is None:
            return _lock(control, _Outcome("error", detail=_detail(f"{type(exc).__name__}: {exc}", type(exc).__name__)))
    if control.fatal is not None:
        raise control.fatal
    if control.abort_reason is not None:
        _absorb_cancel()
    return _lock(control, _outcome_from_result(result, intercepted))


def _output_id(context: PersistentBuildContext) -> str:
    digest = hashlib.sha256(f"{context.execution.execution_id}:final-answer".encode("utf-8")).hexdigest()
    return "out_" + digest[:32]


async def _finish(
    context: PersistentBuildContext,
    store,
    outcome: _Outcome,
    reply_target: str | None,
    last_group_message_id: str | None,
) -> None:
    """Hand off the answer, save the state record, then record the ending, in this order."""
    emitter = context.binding.control.emitter
    output_refs: tuple[str, ...] = ()
    pending_answer = None
    try:
        if outcome.kind == "completed" and outcome.text.strip():
            output_id = _output_id(context)
            payload = {
                "output_id": output_id,
                "target_ref": reply_target,
                "share_mode": "message",
                "text": outcome.text,
            }
            if await emitter.emit_if_fits(EventType.OUTPUT_PROPOSED, payload) is None:
                # Only exceeding capacity pauses; the answer is never truncated and is kept for a later handoff.
                pending_answer = outcome.text
                outcome = _Outcome("error", detail=_CAPACITY_DETAIL)
            else:
                output_refs = (output_id,)
        ending = seams.ExecutionEnding(
            outcome.kind,
            context.execution.request_id,
            reply_target,
            output_refs,
            pending_answer=pending_answer,
            last_group_message_id=last_group_message_id,
        )
        await _store_call("save", store.save(context, ending))
        if outcome.kind in ("completed", "cancelled"):
            final = {"status": outcome.kind, "output_refs": output_refs}
            if outcome.kind == "cancelled":
                final["reason"] = "cancelled by request"
            await emitter.emit(EventType.EXECUTION_FINAL, final)
        else:
            await emitter.emit(
                EventType.EXECUTION_PAUSED,
                {"reason": outcome.kind, "intercepted": outcome.intercepted, "detail": outcome.detail},
            )
    except ValidationError as exc:
        raise _reject(StatusCode.DEEPAGENT_RUNTIME_ERROR, "execution ending could not be recorded", exc) from exc


async def _close(agent, context: PersistentBuildContext, *, failing: bool) -> None:
    """Release the instance after any native stop has returned; never mask an earlier error."""
    settled = context.binding.control.stop_settled
    if settled is not None:
        await asyncio.wait({settled})
    try:
        await close_bound(agent, context=context)
    except Exception as exc:
        if failing:
            logger.warning(
                "[PersistentAgent] cleanup after a failed execution failed: execution=%s error=%s",
                context.execution.execution_id,
                exc,
            )
            return
        raise _reject(StatusCode.DEEPAGENT_RUNTIME_ERROR, "execution cleanup failed", exc) from exc


async def _run(context: PersistentBuildContext, spec, body) -> None:
    """Construct, run the entry body and always close the instance."""
    agent = None
    try:
        agent = create_persistent_agent(spec, context=context)
        _require_assembled_modes(agent)
        await body(agent)
    except BaseException:
        # Stop accepting controls before any awaited cleanup; the original error and reason stay as they are.
        context.binding.control.lock_outcome()
        if agent is not None:
            await _close(agent, context, failing=True)
        raise
    await _close(agent, context, failing=False)


async def _settle(task: asyncio.Task) -> None:
    """Wait until the runner has finished its cleanup, even if the caller is cancelled again."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except BaseException:  # noqa: BLE001 - only waiting; the runner outcome is consumed below
            continue
    if not task.cancelled():
        task.exception()


async def _drive(control: ExecutionControl, runner) -> AsyncGenerator[AgentEvent, None]:
    """Relay recorded events until the runner ends; closing early cancels and drains the runner."""
    outbox = control.emitter.outbox
    task = asyncio.create_task(runner)
    task.add_done_callback(lambda _: outbox.put_nowait(_END))
    try:
        while (event := await outbox.get()) is not _END:
            yield event
        task.result()
    finally:
        if not task.done():
            task.cancel()
            await _settle(task)


async def execute_persistent_request(spec, context, inputs) -> AsyncIterator[AgentEvent]:
    """Run a new Request; the host consumes the stream to its end (architecture §13)."""
    _require_supported_modes(spec)
    _require_isolation(context)
    request = _require_request(context, inputs)
    if request.kind == "group_message" and context.ports.group_chat is None:
        raise _reject(StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, "group chat port is required for group participation")
    control = _start(context)

    async def runner():
        decision_event_id = None
        if request.kind == "group_message":
            # Judgment reads GroupChatPort itself and must finish before the window store is touched.
            judged = await judge_participation(spec, context, request, control.emitter)
            if judged.terminal:
                return
            decision_event_id = judged.event_id
        store = seams.session_state_store(context)
        assembler = seams.window_assembler(context)

        async def body(agent):
            started = {"goal": request.text}
            if decision_event_id is not None:
                started["decision_event_id"] = decision_event_id
            await control.emitter.emit(EventType.EXECUTION_STARTED, started)
            record = await _load(context, store)
            assembly = await _assemble(assembler, context, request, record)
            outcome = await _run_native(agent, context, assembly.native_input)
            await _finish(context, store, outcome, request.reply_target, assembly.last_group_message_id)

        await _run(context, spec, body)

    async with aclosing(_drive(control, runner())) as events:
        async for event in events:
            yield event


def _question_texts(request) -> set[str] | None:
    questions = getattr(request, "questions", None)
    if questions is None:
        return None
    return {str(item.get("question", "")) for item in questions if isinstance(item, dict)}


def _native_resume_input(context: PersistentBuildContext, reply: ConfirmReply, waiting, adapter) -> InteractiveInput:
    """Validate the reply against the waiting scene, then key native input by interaction ID."""
    if not isinstance(waiting, seams.WaitingScene):
        raise _reject(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, "no Execution is waiting for a reply in this Session")
    if waiting.request_id != context.execution.request_id:
        raise _reject(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, "reply targets a different Request than the waiting one")
    state = context.session.get_state(INTERRUPTION_KEY)
    if not isinstance(state, ToolInterruptionState):
        raise _reject(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, "no interrupted call is waiting")
    waiting_requests = {
        call_id: request
        for entry in state.interrupted_tools.values()
        for call_id, request in entry.interrupt_requests.items()
    }
    native = InteractiveInput()
    for response in reply.responses:
        request = waiting_requests.get(response.tool_call_id)
        if request is None:
            raise _reject(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, "tool_call_id is not part of the waiting scene")
        questions = _question_texts(request)
        if response.kind == "answer":
            if questions is None:
                raise _reject(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, "answer reply sent to a permission request")
            if set(response.answers) - questions:
                raise _reject(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, "answer keys must match the asked questions")
        elif questions is not None:
            raise _reject(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, "permission reply sent to a question")
        native.update(response.tool_call_id, adapter.to_native(response, request))
    return native


async def resume_persistent_request(spec, context, confirm_reply) -> AsyncIterator[AgentEvent]:
    """Resume a confirmation pause with the initiator's reply; nothing is appended to the window."""
    _require_supported_modes(spec)
    _require_isolation(context)
    try:
        reply = ConfirmReply.model_validate(confirm_reply)
    except ValidationError as exc:
        raise _reject(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, "invalid ConfirmReply", exc) from exc
    store = seams.session_state_store(context)
    adapter = seams.native_reply_adapter()
    control = _start(context)

    async def body(agent):
        record = await _load(context, store)
        # An invalid reply is rejected before anything is recorded or saved; the scene keeps waiting.
        native_input = _native_resume_input(context, reply, record.waiting, adapter)
        outcome = await _run_native(agent, context, native_input)
        # Resume reads no group messages, so the previous cursor is kept.
        await _finish(context, store, outcome, record.waiting.reply_target, record.last_group_message_id)

    async with aclosing(_drive(control, _run(context, spec, body))) as events:
        async for event in events:
            yield event


async def abort_persistent_request(context, *, reason: AbortReason) -> None:
    """Record why the Execution must end, then request native interruption; the entry finishes it.

    Once the outcome is locked, the control is not accepted: no reason is recorded and abort_bound is not called.
    """
    if reason not in ("cancel", "stop"):
        raise _reject(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, "abort reason must be 'cancel' or 'stop'")
    if not isinstance(context, PersistentBuildContext):
        raise _reject(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, "PersistentBuildContext required")
    binding = context.binding
    control = binding.control
    if not control.request_abort(reason):
        return
    agent = binding.agent_ref() if binding.agent_ref is not None else None
    state = binding.runtime
    if agent is None or not isinstance(state, InvocationState):
        return  # The entry checks the reason before it enters the native call.
    if state.closed or state.closing or state.stopping or state.stopped:
        return
    # Published before abort_bound marks the binding as stopping, so cleanup always finds it.
    settled = asyncio.get_running_loop().create_future()
    control.stop_settled = settled
    try:
        await abort_bound(agent, context=context)
    finally:
        settled.set_result(None)
