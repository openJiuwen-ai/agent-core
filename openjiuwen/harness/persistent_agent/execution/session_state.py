# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Session state record (PA-10): the only state carried from one Execution to the next.

Load decodes the record before writing anything into the new Session. Save first makes the native
agent write its live window and DeepAgent state into Session state, then tidies the window and stores
the whitelisted fields in one set. Backend faults are runtime errors; an unusable record is a
validation error.
"""

from typing import TYPE_CHECKING
from urllib.parse import quote

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.single_agent.interrupt.state import (
    INTERRUPT_AUTO_CONFIRM_KEY,
    INTERRUPTION_KEY,
    ToolInterruptionState,
)
from openjiuwen.harness.schema.state import _SESSION_STATE_KEY as DEEPAGENT_STATE_KEY
from openjiuwen.harness.schema.state import DeepAgentState

from ..schema.integration import (
    CONTEXT_ID,
    CONTEXT_STATE_KEY,
    EXECUTION_END,
    STOPPED_TOOL_PLACEHOLDER,
    TEMPORARY_BACKGROUND,
    WINDOW_PART_KEY,
    ExecutionEnding,
    LoadedRecord,
    WaitingScene,
)
from ..schema.session_record import (
    RecordEnding,
    SessionStateRecord,
    decode_interruption,
    decode_messages,
    dump_record,
    encode_interruption,
    encode_messages,
    invalid_record,
    parse_record,
    unsavable,
)

if TYPE_CHECKING:
    from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore

    from ..schema.build_context import PersistentBuildContext

WAITING = "awaiting_confirmation"
# Endings that may leave tool calls without results; the window is made safe to append to.
INTERRUPTED_ENDINGS = frozenset({"stopped", "cancelled", "error"})
PLACEHOLDER_TEXT = "结果未取得，实际效果需核实"
_OUTCOME_LABELS = {"completed": "已完成", "cancelled": "已取消"}


def session_key_prefix(agent_id: str, session_id: str) -> str:
    """Key prefix of one Session's records; the host deletes by it.

    Each ID is percent-encoded on its own, colons and percent signs included, so no two identity
    pairs share a prefix. Archives (PA-11, PA-12) use the same prefix.
    """
    return f"pa:{quote(agent_id, safe='')}:{quote(session_id, safe='')}:"


def _runtime(message: str, cause: BaseException | None = None) -> BaseError:
    return build_error(StatusCode.DEEPAGENT_RUNTIME_ERROR, error_msg=message, cause=cause)


class WindowStore:
    """Reads and writes whole records; any backend failure is a runtime error, never an invalid record."""

    def __init__(self, backend: "BaseKVStore"):
        self._backend = backend

    @staticmethod
    def state_key(agent_id: str, session_id: str) -> str:
        """Key of the Session's state record."""
        return session_key_prefix(agent_id, session_id) + "state"

    async def load_state(self, agent_id: str, session_id: str) -> str | bytes | None:
        """The stored record as returned by the backend, or None when the Session has none."""
        try:
            return await self._backend.get(self.state_key(agent_id, session_id))
        except Exception as exc:
            raise _runtime("session state record could not be read", exc) from exc

    async def save_state(self, agent_id: str, session_id: str, payload: str) -> None:
        """Replace the Session's record with one set."""
        try:
            await self._backend.set(self.state_key(agent_id, session_id), payload)
        except Exception as exc:
            raise _runtime("session state record could not be written", exc) from exc


def _part(message) -> str | None:
    return (getattr(message, "metadata", None) or {}).get(WINDOW_PART_KEY)


def unpaired_tool_calls(messages: list) -> list[str]:
    """Tool calls without a result, in call order; placeholders and native interruption results count."""
    pending: dict[str, None] = {}
    for message in messages:
        for call in getattr(message, "tool_calls", None) or ():
            pending[call.id] = None
        if isinstance(message, ToolMessage):
            pending.pop(message.tool_call_id, None)
    return list(pending)


def patch_stopped_calls(messages: list) -> list:
    """Add a placeholder result after each call group for calls still without one; idempotent."""
    answered = {message.tool_call_id for message in messages if isinstance(message, ToolMessage)}
    patched, index = [], 0
    while index < len(messages):
        message = messages[index]
        patched.append(message)
        index += 1
        if not (isinstance(message, AssistantMessage) and message.tool_calls):
            continue
        while index < len(messages) and isinstance(messages[index], ToolMessage):
            patched.append(messages[index])
            index += 1
        for call in message.tool_calls:
            if call.id not in answered:
                answered.add(call.id)
                patched.append(
                    ToolMessage(
                        content=PLACEHOLDER_TEXT,
                        tool_call_id=call.id,
                        metadata={WINDOW_PART_KEY: STOPPED_TOOL_PLACEHOLDER},
                    )
                )
    return patched


def _end_marker(ending: ExecutionEnding, execution_id: str) -> UserMessage:
    outcome = _OUTCOME_LABELS.get(ending.outcome, f"暂停（{ending.outcome}）")
    outputs = "、".join(ending.output_refs) + "（以结束记录为准）" if ending.output_refs else "无"
    fields = [
        ("request_id", ending.request_id),
        ("execution_id", execution_id),
        ("结局", outcome),
        ("交出候选", outputs),
        ("群消息已扫描到", ending.last_group_message_id or "无"),
    ]
    text = "【执行结束】" + "；".join(f"{name}：{value}" for name, value in fields)
    return UserMessage(content=text, metadata={WINDOW_PART_KEY: EXECUTION_END})


def tidy_window(messages: list, ending: ExecutionEnding, execution_id: str) -> list:
    """Drop the temporary background; keep history and the one-line header; add the end marker."""
    kept = [message for message in messages if _part(message) != TEMPORARY_BACKGROUND]
    if ending.outcome != WAITING:
        kept.append(_end_marker(ending, execution_id))
    return kept


def offloading_processors(agent) -> list[str]:
    """Context processors on the native agent that move messages out of the window (not supported)."""
    from openjiuwen.core.context_engine.processor.offloader.message_offloader import MessageOffloader
    from openjiuwen.core.context_engine.processor.offloader.message_summary_offloader import (
        MessageSummaryOffloader,
    )
    from openjiuwen.core.context_engine.processor.offloader.tool_result_budget_processor import (
        ToolResultBudgetProcessor,
    )

    # The forked MessageSummaryOffloader registers under the same type name.
    offloading = {
        processor.processor_type()
        for processor in (MessageOffloader, MessageSummaryOffloader, ToolResultBudgetProcessor)
    }
    react_agent = getattr(agent, "react_agent", None)
    configured = getattr(getattr(react_agent, "config", None), "context_processors", None) or ()
    return sorted({entry[0] for entry in configured if entry and entry[0] in offloading})


def _check_window(messages: list, ending_outcome: str, interruption, error) -> None:
    """A waiting scene's unpaired calls are its interrupted ones; interrupted endings get placeholders."""
    unpaired = unpaired_tool_calls(messages)
    waiting = ending_outcome == WAITING
    if waiting != (interruption is not None):
        raise error("window and interruption state disagree")
    if waiting:
        if set(unpaired) - set(interruption.interrupted_tools):
            raise error("window and interruption state disagree")
    elif unpaired and ending_outcome not in INTERRUPTED_ENDINGS:
        raise error("window has tool calls without results")


class ExecutionStateStore:
    """The SessionStateStore of one Execution; the entry uses it for one load and one save."""

    def __init__(self, store: WindowStore):
        self._store = store
        self._loaded: RecordEnding | None = None

    async def load(self, context: "PersistentBuildContext") -> LoadedRecord:
        """Validate the latest record, then write it into the new Session; no record gives an empty window."""
        execution = context.execution
        raw = await self._store.load_state(execution.agent_id, execution.session_id)
        if raw is None:
            return LoadedRecord()
        try:
            record = parse_record(raw)
            if record.agent_id != execution.agent_id or record.session_id != execution.session_id:
                raise invalid_record("identity does not match this Session")
            messages = decode_messages(record.messages)
            interruption = decode_interruption(record.interruption) if record.interruption is not None else None
            _check_window(messages, record.ending.outcome, interruption, invalid_record)
            if record.deepagent_state is not None:
                try:
                    DeepAgentState.from_session_dict(record.deepagent_state)
                except Exception:
                    raise invalid_record("DeepAgent state cannot be restored") from None
        except BaseError as exc:
            # Diagnostics name the Session and the category only; the record text stays out of logs.
            logger.error(
                "[PersistentAgent] session state record rejected: session=%s execution=%s reason=%s",
                execution.session_id,
                execution.execution_id,
                exc.message,
            )
            raise
        if record.ending.outcome in INTERRUPTED_ENDINGS:
            # A record saved before its window was made safe is repaired in memory, never written back.
            messages = patch_stopped_calls(messages)
        state = {
            CONTEXT_STATE_KEY: {CONTEXT_ID: {"messages": messages}},
            INTERRUPT_AUTO_CONFIRM_KEY: dict(record.auto_confirm),
        }
        if record.deepagent_state is not None:
            state[DEEPAGENT_STATE_KEY] = dict(record.deepagent_state)
        if interruption is not None:
            state[INTERRUPTION_KEY] = interruption
        context.session.update_state(state)
        self._loaded = record.ending
        ending = record.ending
        waiting = WaitingScene(ending.request_id, ending.reply_target) if ending.outcome == WAITING else None
        return LoadedRecord(waiting=waiting, last_group_message_id=ending.last_group_message_id)

    async def save(self, context: "PersistentBuildContext", ending: ExecutionEnding) -> None:
        """Sync the native state, tidy the window and write the whole record once; any failure writes nothing."""
        execution, session = context.execution, context.session
        await self._sync_native(context)
        contexts = session.get_state(CONTEXT_STATE_KEY) or {}
        if set(contexts) - {CONTEXT_ID}:
            raise unsavable("only the default window is supported")
        window = contexts.get(CONTEXT_ID) or {}
        if window.get("offload_messages"):
            raise unsavable("offloaded messages are not supported")
        messages = list(window.get("messages") or [])
        interruption = session.get_state(INTERRUPTION_KEY) if ending.outcome == WAITING else None
        if ending.outcome == WAITING and not isinstance(interruption, ToolInterruptionState):
            raise unsavable("a waiting Execution has no interruption state")
        if ending.outcome in INTERRUPTED_ENDINGS:
            messages = patch_stopped_calls(messages)
        _check_window(messages, ending.outcome, interruption, unsavable)
        messages = tidy_window(messages, ending, execution.execution_id)
        auto_confirm = session.get_state(INTERRUPT_AUTO_CONFIRM_KEY) or {}
        deepagent_state = session.get_state(DEEPAGENT_STATE_KEY)
        record = SessionStateRecord(
            agent_id=execution.agent_id,
            session_id=execution.session_id,
            messages=encode_messages(messages),
            interruption=encode_interruption(interruption) if interruption is not None else None,
            auto_confirm=_auto_confirm(auto_confirm),
            deepagent_state=_deepagent_state(deepagent_state),
            ending=RecordEnding(
                outcome=ending.outcome,
                request_id=ending.request_id,
                execution_id=execution.execution_id,
                reply_target=ending.reply_target,
                output_refs=list(ending.output_refs),
                pending_answer=ending.pending_answer,
                last_group_message_id=ending.last_group_message_id,
            ),
        )
        self._warn_pending_answer_overwrite(execution, ending)
        await self._store.save_state(execution.agent_id, execution.session_id, dump_record(record))

    @staticmethod
    async def _sync_native(context: "PersistentBuildContext") -> None:
        """Have the native agent write its live window and DeepAgent state into Session state.

        Without a native context for this Session the loaded or assembled window is kept: syncing
        then would write an empty snapshot over it.
        """
        reference = context.binding.agent_ref
        agent = reference() if reference is not None else None
        if agent is None:
            raise unsavable("the native agent is no longer available")
        session = context.session
        try:
            engine = agent.react_agent.context_engine
            if engine.get_context(CONTEXT_ID, session.get_session_id()) is not None:
                await engine.save_contexts(session)
            agent.save_state(session)
        except Exception as exc:
            raise unsavable("the native window could not be synced", exc) from exc

    def _warn_pending_answer_overwrite(self, execution, ending: ExecutionEnding) -> None:
        loaded = self._loaded
        if loaded is None or loaded.pending_answer is None or ending.pending_answer == loaded.pending_answer:
            return
        logger.error(
            "[PersistentAgent] pending answer overwritten: session=%s "
            "previous_request=%s previous_execution=%s request=%s execution=%s",
            execution.session_id,
            loaded.request_id,
            loaded.execution_id,
            ending.request_id,
            execution.execution_id,
        )


def _auto_confirm(value) -> dict:
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, bool) for k, v in value.items()):
        raise unsavable("confirmation choices are not supported")
    return dict(value)


def _deepagent_state(value) -> dict | None:
    if value is not None and not isinstance(value, dict):
        raise unsavable("DeepAgent state is not supported")
    return value
