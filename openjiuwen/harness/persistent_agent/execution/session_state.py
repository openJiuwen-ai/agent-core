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
    HANDOFF_TEXT,
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
HANDOFF_TEXT_PREFIX = "【实际交出的最终回答】\n"
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


def _call_groups(messages: list) -> tuple[list[tuple[AssistantMessage, list[str]]], int]:
    """Each tool call group with its calls still without a result, and the number of stray results.

    A result answers a call only among the tool messages right after the call's own group, so a
    call id reused by a later group is paired on its own; a result anywhere else, for a call of
    another group, or repeated, is stray.
    """
    groups, stray, index = [], 0, 0
    while index < len(messages):
        message = messages[index]
        index += 1
        if isinstance(message, ToolMessage):
            stray += 1
            continue
        if not (isinstance(message, AssistantMessage) and message.tool_calls):
            continue
        calls = [call.id for call in message.tool_calls]
        answered: set[str] = set()
        while index < len(messages) and isinstance(messages[index], ToolMessage):
            call_id = messages[index].tool_call_id
            if call_id in calls and call_id not in answered:
                answered.add(call_id)
            else:
                stray += 1
            index += 1
        groups.append((message, [call for call in calls if call not in answered]))
    return groups, stray


def unpaired_tool_calls(messages: list) -> list[str]:
    """Tool calls without a result right after their group, in window order.

    Placeholders and native interruption results count as results.
    """
    groups, _ = _call_groups(messages)
    return [call for _, unpaired in groups for call in unpaired]


def patch_stopped_calls(messages: list) -> list:
    """Add a placeholder after each call group's results for its calls still without one; idempotent."""
    patched, index = [], 0
    while index < len(messages):
        message = messages[index]
        patched.append(message)
        index += 1
        if not (isinstance(message, AssistantMessage) and message.tool_calls):
            continue
        answered = set()
        while index < len(messages) and isinstance(messages[index], ToolMessage):
            answered.add(messages[index].tool_call_id)
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


def _handed_off(ending: ExecutionEnding) -> str:
    if not ending.output_refs:
        return "无"
    parts = [f"最终回答 {ending.answer_segments} 段"] if ending.answer_segments else []
    parts.extend(f"文件 {path}（{mode}，{output_id}）" for path, mode, output_id in ending.shared_files)
    return "、".join(parts) + "（以结束记录为准）"


def _end_marker(ending: ExecutionEnding, execution_id: str) -> UserMessage:
    outcome = _OUTCOME_LABELS.get(ending.outcome, f"暂停（{ending.outcome}）")
    fields = [
        ("request_id", ending.request_id),
        ("execution_id", execution_id),
        ("结局", outcome),
        ("交出候选", _handed_off(ending)),
    ]
    if ending.handoff_failure:
        fields.append(("未交接原因", ending.handoff_failure))
    fields.append(("群消息已扫描到", ending.last_group_message_id or "无"))
    text = "【执行结束】" + "；".join(f"{name}：{value}" for name, value in fields)
    return UserMessage(content=text, metadata={WINDOW_PART_KEY: EXECUTION_END})


def _handoff_text(messages: list, answer: str | None) -> UserMessage | None:
    """The answer as handed off, when after_invoke rails changed it from this Execution's last native reply."""
    if answer is None:
        return None
    for message in reversed(messages):
        if _part(message) == EXECUTION_END:
            break
        if isinstance(message, AssistantMessage):
            if not message.tool_calls and message.content == answer:
                return None
            break
    return UserMessage(content=HANDOFF_TEXT_PREFIX + answer, metadata={WINDOW_PART_KEY: HANDOFF_TEXT})


def tidy_window(messages: list, ending: ExecutionEnding, execution_id: str) -> list:
    """Drop the temporary background; keep history and the one-line header; add the end marker.

    A final answer that after_invoke rails changed is kept, as handed off, right before the marker.
    """
    kept = [message for message in messages if _part(message) != TEMPORARY_BACKGROUND]
    if ending.outcome != WAITING:
        handoff_text = _handoff_text(kept, ending.answer_text)
        if handoff_text is not None:
            kept.append(handoff_text)
        kept.append(_end_marker(ending, execution_id))
    return kept


# Processors that write the offload buffer. ToolResultBudgetProcessor and MessageSummaryOffloader
# derive from MessageOffloader; the forked MessageSummaryOffloader does not and replaces the core one
# under the same type name. Classes are named by path: importing them would register them again.
_OFFLOADER_TYPES = frozenset({"MessageOffloader", "MessageSummaryOffloader", "ToolResultBudgetProcessor"})
_OFFLOADER_CLASSES = frozenset(
    {
        "openjiuwen.core.context_engine.processor.offloader.message_offloader.MessageOffloader",
        "openjiuwen.core.context_engine.processor.forked.offloader.message_offloader.MessageSummaryOffloader",
    }
)


def _derives_from_offloader(processor_class) -> bool:
    return any(
        f"{base.__module__}.{base.__qualname__}" in _OFFLOADER_CLASSES
        for base in getattr(processor_class, "__mro__", ())
    )


def offloading_processors(agent) -> list[str]:
    """Context processors on the native agent that move messages out of the window (not supported).

    A configured type counts by its name or by its registered class deriving from an offloader,
    so a subclass registered under a new name is found too.
    """
    from openjiuwen.core.context_engine.context_engine import ContextEngine

    registered = getattr(ContextEngine, "_PROCESSOR_MAP", {})
    react_agent = getattr(agent, "react_agent", None)
    configured = getattr(getattr(react_agent, "config", None), "context_processors", None) or ()
    return sorted(
        {
            entry[0]
            for entry in configured
            if entry and (entry[0] in _OFFLOADER_TYPES or _derives_from_offloader(registered.get(entry[0])))
        }
    )


def _call_facts(call) -> tuple:
    """What a call does: the native resume replays the interrupt entry's call, so all of it must match.

    ``index`` is left out: it only orders streamed chunks and the message format does not keep it.
    """
    return call.id, call.type, call.name, call.arguments


def _matches_waiting_scene(groups: list, interruption) -> bool:
    """The last call group is the interrupted batch, call for call, and only its interrupted calls lack results."""
    if not groups or any(unpaired for _, unpaired in groups[:-1]):
        return False
    group, unpaired = groups[-1]
    shown = [_call_facts(call) for call in group.tool_calls]
    if shown != [_call_facts(call) for call in interruption.ai_message.tool_calls or ()]:
        return False
    by_id = {facts[0]: facts for facts in shown}
    tools = interruption.interrupted_tools
    return set(unpaired) == set(tools) and all(
        _call_facts(entry.tool_call) == by_id.get(call_id) for call_id, entry in tools.items()
    )


def _check_window(messages: list, ending_outcome: str, interruption, error) -> None:
    """Results follow their own call group; a waiting scene's unpaired calls are exactly its interrupted ones."""
    groups, stray = _call_groups(messages)
    if stray:
        raise error("window has tool results outside their call group")
    waiting = ending_outcome == WAITING
    if waiting != (interruption is not None):
        raise error("window and interruption state disagree")
    if waiting:
        if not _matches_waiting_scene(groups, interruption):
            raise error("window and interruption state disagree")
    elif any(unpaired for _, unpaired in groups) and ending_outcome not in INTERRUPTED_ENDINGS:
        raise error("window has tool calls without results")


class ExecutionStateStore:
    """The SessionStateStore of one Execution; the entry uses it for one load and one save."""

    def __init__(self, store: WindowStore):
        self._store = store

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
                last_group_message_id=ending.last_group_message_id,
            ),
        )
        await self._store.save_state(execution.agent_id, execution.session_id, dump_record(record))

    @staticmethod
    async def _sync_native(context: "PersistentBuildContext") -> None:
        await sync_native(context)


async def sync_native(context: "PersistentBuildContext") -> None:
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


def window_messages(session) -> list:
    """The default window as it stands in Session state; synced first when the native agent has run."""
    contexts = session.get_state(CONTEXT_STATE_KEY) or {}
    return list((contexts.get(CONTEXT_ID) or {}).get("messages") or [])


def _auto_confirm(value) -> dict:
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, bool) for k, v in value.items()):
        raise unsavable("confirmation choices are not supported")
    return dict(value)


def _deepagent_state(value) -> dict | None:
    if value is not None and not isinstance(value, dict):
        raise unsavable("DeepAgent state is not supported")
    return value
