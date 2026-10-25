# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Session state record (PA-10): the only state carried from one Execution to the next.

Load decodes the record before writing anything into the new Session. Save first makes the native
agent write its live window and DeepAgent state into Session state, then tidies the window and stores
the whitelisted fields in one set. Backend faults are runtime errors; an unusable record is a
validation error.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import quote
from uuid import uuid4

from pydantic import ValidationError as PydanticValidationError

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
    RolloverCheckpoint,
    SessionCheckpoint,
    WaitingScene,
)
from ..schema.session_record import (
    RecordArchiveIndex,
    RecordArchiveIndexEntry,
    RecordCheckpoint,
    RecordEnding,
    RecordRollover,
    RecordWindowArchive,
    SessionHistorySearchHit,
    SessionHistorySearchQuery,
    SessionHistorySearchResult,
    SessionStateRecord,
    WindowArchivePayload,
    decode_interruption,
    decode_messages,
    dump_record,
    encode_interruption,
    encode_messages,
    invalid_record,
    parse_record,
    unsavable,
)
from ..schema.window import WindowRollover, WindowSeed

if TYPE_CHECKING:
    from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore

    from ..schema.build_context import PersistentBuildContext

WAITING = "awaiting_confirmation"
# Endings that may leave tool calls without results; the window is made safe to append to.
INTERRUPTED_ENDINGS = frozenset({"stopped", "cancelled", "error"})
PLACEHOLDER_TEXT = "结果未取得，实际效果需核实"
HANDOFF_TEXT_PREFIX = "【实际交出的最终回答】\n"
_OUTCOME_LABELS = {"completed": "已完成", "cancelled": "已取消"}


@dataclass(frozen=True)
class _WindowMetadata:
    current_window_id: str | None = None
    window_seq: int | None = None
    last_rollover: RolloverCheckpoint | None = None


def session_key_prefix(agent_id: str, session_id: str) -> str:
    """Key prefix of one Session's records; the host deletes by it.

    Each ID is percent-encoded on its own, colons and percent signs included, so no two identity
    pairs share a prefix. Archives (PA-11, PA-12) use the same prefix.
    """
    return f"pa:{quote(agent_id, safe='')}:{quote(session_id, safe='')}:"


def _runtime(message: str, cause: BaseException | None = None) -> BaseError:
    return build_error(StatusCode.DEEPAGENT_RUNTIME_ERROR, error_msg=message, cause=cause)


def _message_fingerprint(messages: list) -> str:
    """Stable digest used to verify a recovery candidate rebuilt from archived references."""
    encoded = json.dumps(encode_messages(messages), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _text_fields(value, path: str):
    """Yield only plain strings and structured text blocks, never arbitrary metadata."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _text_fields(item, f"{path}[{index}]")
    elif isinstance(value, dict) and isinstance(value.get("text"), str):
        yield f"{path}.text", value["text"]


def _matched_excerpt(text: str, query: str) -> tuple[str, bool] | None:
    """Return a bounded original excerpt around the first case-folded literal match."""
    folded_query = query.casefold()
    match_start = text.casefold().find(folded_query)
    if match_start < 0:
        return None

    match_end = match_start + len(folded_query)
    original_start = original_end = None
    folded_position = 0
    for index, character in enumerate(text):
        next_position = folded_position + len(character.casefold())
        if original_start is None and folded_position <= match_start < next_position:
            original_start = index
        if original_end is None and folded_position < match_end <= next_position:
            original_end = index + 1
        folded_position = next_position
        if original_start is not None and original_end is not None:
            break

    if original_start is None or original_end is None:
        return None
    excerpt_limit = 2048
    if len(text) <= excerpt_limit:
        return text, False
    start = max(0, original_start - excerpt_limit // 2)
    end = min(len(text), start + excerpt_limit)
    if end < original_end:
        start = max(0, original_end - excerpt_limit)
        end = min(len(text), start + excerpt_limit)
    if end == len(text) and end - start < excerpt_limit:
        start = max(0, end - excerpt_limit)
    return text[start:end], start > 0 or end < len(text)


def _message_search_fields(message):
    """Yield the searchable text fields of one user, assistant, or tool message."""
    role = message.role
    if role not in {"user", "assistant", "tool"}:
        return
    content_path = "tool_result.content" if role == "tool" else "content"
    tool_name = message.name if role == "tool" else None
    if role == "tool" and isinstance(tool_name, str):
        yield role, "tool_result.name", tool_name, tool_name
    for path, text in _text_fields(message.content, content_path):
        yield role, path, tool_name, text
    if role == "assistant":
        for index, call in enumerate(message.tool_calls or []):
            yield role, f"tool_calls[{index}].function.name", call.name, call.name
            yield role, f"tool_calls[{index}].function.arguments", call.name, call.arguments


def _archive_index(archives: list[RecordWindowArchive]) -> RecordArchiveIndex:
    """Build the navigation-only index from validated archive records."""
    return RecordArchiveIndex(
        entries=[
            RecordArchiveIndexEntry(
                archive_ref=archive.archive_ref,
                old_window_seq=archive.old_window_seq,
                request_id=archive.request_id,
                execution_id=archive.execution_id,
                created_at=archive.created_at,
            )
            for archive in archives
        ]
    )


class WindowStore:
    """Reads and writes whole records; any backend failure is a runtime error, never an invalid record."""

    def __init__(self, backend: "BaseKVStore"):
        self._backend = backend

    @staticmethod
    def state_key(agent_id: str, session_id: str) -> str:
        """Key of the Session's state record."""
        return session_key_prefix(agent_id, session_id) + "state"

    @staticmethod
    def archive_key(agent_id: str, session_id: str, window_seq: int) -> str:
        """Key of one archived old window; derived from the Session and its sequence."""
        if isinstance(window_seq, bool) or not isinstance(window_seq, int) or window_seq < 1:
            raise ValueError("window sequence must be a positive integer")
        return session_key_prefix(agent_id, session_id) + f"archive:{window_seq}"

    @staticmethod
    def archive_index_key(agent_id: str, session_id: str) -> str:
        """Key of the rebuildable Session archive index."""
        return session_key_prefix(agent_id, session_id) + "archive_index"

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

    @staticmethod
    def _archive_ref(window_seq: int) -> str:
        return f"archive:{window_seq}"

    @staticmethod
    def _parse_archive(raw: str | bytes, agent_id: str, session_id: str, window_seq: int) -> RecordWindowArchive:
        try:
            archive = RecordWindowArchive.model_validate_json(raw)
        except (PydanticValidationError, TypeError, ValueError):
            raise invalid_record("window archive structure is not valid") from None
        if (
            archive.agent_id != agent_id
            or archive.session_id != session_id
            or archive.old_window_seq != window_seq
            or archive.archive_ref != WindowStore._archive_ref(window_seq)
        ):
            raise invalid_record("window archive identity does not match its Session key")
        normalized_messages = encode_messages(decode_messages(archive.messages))
        try:
            WindowSeed.model_validate(archive.seed)
        except (PydanticValidationError, TypeError, ValueError):
            raise invalid_record("window archive seed is not valid") from None
        return archive.model_copy(update={"messages": normalized_messages})

    async def load_archive(self, agent_id: str, session_id: str, window_seq: int) -> RecordWindowArchive | None:
        """Load and validate one full archived window; a missing key is distinct from a broken backend."""
        key = self.archive_key(agent_id, session_id, window_seq)
        try:
            raw = await self._backend.get(key)
        except Exception as exc:
            raise _runtime("window archive could not be read", exc) from exc
        if raw is None:
            return None
        return self._parse_archive(raw, agent_id, session_id, window_seq)

    async def find_pending_rollover(
        self,
        agent_id: str,
        session_id: str,
        *,
        current_window_id: str | None,
        window_seq: int | None,
        checkpoint: SessionCheckpoint | None,
        request_id: str,
        recovery_ref: str | None,
        spec_fingerprint: str,
    ) -> RecordWindowArchive | None:
        """Find an archive left before a failed state commit, accepting only its exact recovery identity."""
        if current_window_id is None and window_seq is None:
            return None
        if current_window_id is None or window_seq is None:
            raise invalid_record("current window identity is incomplete")
        archive = await self.load_archive(agent_id, session_id, window_seq)
        if archive is None:
            return None
        if (
            checkpoint is None
            or archive.old_window_id != current_window_id
            or archive.request_id != request_id
            or archive.request_id != checkpoint.request_id
            or archive.execution_id != checkpoint.execution_id
            or archive.execution_id != recovery_ref
            or archive.spec_fingerprint != spec_fingerprint
            or checkpoint.kind != ("request_start" if archive.trigger_kind == "request_entry" else "react_iteration")
            or archive.iteration_seq != checkpoint.iteration_seq
        ):
            raise invalid_record("pending rollover does not match the active Session checkpoint")
        return archive

    async def archive_window(self, agent_id: str, session_id: str, payload: dict) -> RecordWindowArchive:
        """Store one immutable archive idempotently, then refresh its navigation index."""
        try:
            candidate = WindowArchivePayload.model_validate(payload)
            WindowSeed.model_validate(candidate.seed)
            # Decode and re-encode to ensure the archived source snapshot is restorable.
            normalized_messages = encode_messages(decode_messages(candidate.messages))
            archive = RecordWindowArchive(
                **candidate.model_dump(exclude={"messages"}),
                messages=normalized_messages,
                agent_id=agent_id,
                session_id=session_id,
                archive_ref=self._archive_ref(candidate.old_window_seq),
                created_at=datetime.now(timezone.utc),
            )
        except (PydanticValidationError, BaseError, TypeError, ValueError) as exc:
            raise unsavable("window archive payload is invalid", exc) from exc

        key = self.archive_key(agent_id, session_id, archive.old_window_seq)
        serialized = archive.model_dump_json()
        try:
            created = await self._backend.exclusive_set(key, serialized)
        except Exception as exc:
            raise _runtime("window archive could not be written", exc) from exc
        if created is True:
            stored = archive
        elif created is False:
            try:
                existing_raw = await self._backend.get(key)
            except Exception as exc:
                raise _runtime("existing window archive could not be checked", exc) from exc
            if existing_raw is None:
                raise _runtime("window archive write outcome is unknown")
            existing = self._parse_archive(existing_raw, agent_id, session_id, archive.old_window_seq)
            if existing.model_dump(exclude={"created_at"}) != archive.model_dump(exclude={"created_at"}):
                raise _runtime("window archive conflicts with the existing transition")
            stored = existing
        else:
            raise _runtime("window archive backend returned an invalid write result")

        await self._rebuild_archive_index(agent_id, session_id)
        return stored

    async def _read_archive_entries(self, agent_id: str, session_id: str) -> tuple[str, dict[str, str | bytes]]:
        """Enumerate authoritative archive keys and values for one Session."""
        prefix = session_key_prefix(agent_id, session_id) + "archive:"
        try:
            entries = await self._backend.get_by_prefix(prefix)
        except Exception as exc:
            raise _runtime("window archives could not be listed", exc) from exc
        if not isinstance(entries, dict):
            raise _runtime("window archive backend returned an invalid listing")
        return prefix, entries

    async def _read_archives(self, agent_id: str, session_id: str) -> list[RecordWindowArchive]:
        prefix, entries = await self._read_archive_entries(agent_id, session_id)
        archives = []
        for key, raw in entries.items():
            suffix = key.removeprefix(prefix)
            try:
                window_seq = int(suffix)
            except ValueError:
                raise invalid_record("window archive key is malformed") from None
            if key != self.archive_key(agent_id, session_id, window_seq):
                raise invalid_record("window archive key is malformed")
            archives.append(self._parse_archive(raw, agent_id, session_id, window_seq))
        return sorted(archives, key=lambda item: item.old_window_seq)

    async def _rebuild_archive_index(self, agent_id: str, session_id: str) -> RecordArchiveIndex:
        archives = await self._read_archives(agent_id, session_id)
        return await self._write_archive_index(agent_id, session_id, archives)

    async def _write_archive_index(
        self, agent_id: str, session_id: str, archives: list[RecordWindowArchive]
    ) -> RecordArchiveIndex:
        index = _archive_index(archives)
        try:
            await self._backend.set(self.archive_index_key(agent_id, session_id), index.model_dump_json())
        except Exception as exc:
            raise _runtime("window archive index could not be written", exc) from exc
        return index

    async def list_archives(
        self, agent_id: str, session_id: str, execution_id: str | None = None
    ) -> list[RecordWindowArchive]:
        """Return authoritative archives and rebuild the navigation index from them."""
        archives = await self._read_archives(agent_id, session_id)
        await self._write_archive_index(agent_id, session_id, archives)
        if execution_id is None:
            return archives
        return [archive for archive in archives if archive.execution_id == execution_id]

    async def search(
        self, agent_id: str, session_id: str, query: str, *, limit: int = 50
    ) -> SessionHistorySearchResult:
        """Search retained archived message text, preserving partial-scan and failure semantics."""
        try:
            search = SessionHistorySearchQuery(query=query, limit=limit)
        except (PydanticValidationError, TypeError, ValueError):
            return SessionHistorySearchResult(
                status="failed",
                query_complete=False,
                reason="历史搜索参数无效。",
            )

        try:
            prefix, raw_entries = await self._read_archive_entries(agent_id, session_id)
        except Exception:
            return SessionHistorySearchResult(
                status="failed",
                query_complete=False,
                reason="历史归档列表读取失败，不能据此判断是否有匹配记录。",
            )
        try:
            raw_index = await self._backend.get(self.archive_index_key(agent_id, session_id))
        except Exception:
            return SessionHistorySearchResult(
                status="failed",
                query_complete=False,
                reason="历史归档索引读取失败，不能据此判断是否有匹配记录。",
            )

        index = None
        if raw_index is not None:
            try:
                index = RecordArchiveIndex.model_validate_json(raw_index)
            except (PydanticValidationError, TypeError, ValueError):
                pass

        archive_keys: list[tuple[int, str, str | bytes]] = []
        scan_incomplete = False
        for key, raw in raw_entries.items():
            suffix = key.removeprefix(prefix) if isinstance(key, str) else ""
            try:
                window_seq = int(suffix)
                if window_seq < 1 or key != self.archive_key(agent_id, session_id, window_seq):
                    raise ValueError
            except (TypeError, ValueError):
                scan_incomplete = True
                continue
            archive_keys.append((window_seq, key, raw))

        archive_keys.sort(key=lambda item: item[0], reverse=True)
        actual_sequences = {window_seq for window_seq, _, _ in archive_keys}
        indexed_by_seq = {}
        index_stale = index is None
        missing_archives = False
        if index is not None:
            for entry in index.entries:
                if entry.old_window_seq in indexed_by_seq:
                    index_stale = True
                indexed_by_seq[entry.old_window_seq] = entry
            if set(indexed_by_seq) - actual_sequences:
                missing_archives = True
            if actual_sequences - set(indexed_by_seq):
                index_stale = True

        archives: list[RecordWindowArchive] = []
        hits: list[SessionHistorySearchHit] = []
        results_truncated = False
        for window_seq, key, raw in archive_keys:
            try:
                archive = self._parse_archive(raw, agent_id, session_id, window_seq)
            except Exception:
                scan_incomplete = True
                continue
            archives.append(archive)
            indexed_entry = indexed_by_seq.get(window_seq)
            if indexed_entry is None or (
                indexed_entry.archive_ref != archive.archive_ref
                or indexed_entry.request_id != archive.request_id
                or indexed_entry.execution_id != archive.execution_id
                or indexed_entry.created_at != archive.created_at
            ):
                index_stale = True

            try:
                messages = decode_messages(archive.messages)
            except Exception:
                scan_incomplete = True
                continue
            for message_index in range(len(messages) - 1, -1, -1):
                message = messages[message_index]
                for role, matched_field, tool_name, text in _message_search_fields(message):
                    excerpt = _matched_excerpt(text, search.query)
                    if excerpt is None:
                        continue
                    if len(hits) >= search.limit:
                        results_truncated = True
                        continue
                    excerpt_text, excerpt_truncated = excerpt
                    hits.append(
                        SessionHistorySearchHit(
                            archive_ref=archive.archive_ref,
                            old_window_id=archive.old_window_id,
                            old_window_seq=archive.old_window_seq,
                            message_index=message_index,
                            role=role,
                            matched_field=matched_field,
                            tool_name=tool_name,
                            excerpt=excerpt_text,
                            excerpt_truncated=excerpt_truncated,
                        )
                    )

        if missing_archives:
            scan_incomplete = True
        if index_stale and not scan_incomplete:
            try:
                await self._write_archive_index(
                    agent_id, session_id, sorted(archives, key=lambda item: item.old_window_seq)
                )
            except Exception:
                return SessionHistorySearchResult(
                    status="failed",
                    query_complete=False,
                    reason="历史归档索引无法恢复，不能确认搜索结果。",
                )

        if scan_incomplete:
            if not archives:
                return SessionHistorySearchResult(
                    status="failed",
                    query_complete=False,
                    reason="历史归档无法读取，不能确认搜索结果。",
                )
            return SessionHistorySearchResult(
                status="incomplete",
                query_complete=False,
                hits=hits,
                results_truncated=results_truncated,
                reason="部分历史归档无法读取或核验，搜索结果不完整。",
            )
        if hits:
            return SessionHistorySearchResult(
                status="found",
                query_complete=True,
                hits=hits,
                results_truncated=results_truncated,
            )
        return SessionHistorySearchResult(status="empty", query_complete=True)


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


def _check_window(messages: list, ending_outcome: str | None, interruption, error) -> None:
    """Results follow their own call group; a waiting scene's unpaired calls are exactly its interrupted ones."""
    groups, stray = _call_groups(messages)
    if stray:
        raise error("window has tool results outside their call group")
    if ending_outcome is None:
        if interruption is not None or any(unpaired for _, unpaired in groups):
            raise error("active checkpoint has an unsettled tool call or interruption")
        return
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
        self._window_metadata: dict[tuple[str, str], _WindowMetadata] = {}

    @staticmethod
    def _identity(context: "PersistentBuildContext") -> tuple[str, str]:
        return context.execution.agent_id, context.execution.session_id

    def _metadata_for(self, context: "PersistentBuildContext") -> _WindowMetadata:
        return self._window_metadata.get(self._identity(context), _WindowMetadata())

    def _remember_metadata(self, context: "PersistentBuildContext", metadata: _WindowMetadata) -> None:
        self._window_metadata[self._identity(context)] = metadata

    @staticmethod
    def _rollover_value(record: RecordRollover | None) -> RolloverCheckpoint | None:
        if record is None:
            return None
        return RolloverCheckpoint(**record.model_dump())

    async def load(self, context: "PersistentBuildContext") -> LoadedRecord:
        """Validate the latest record, then write it into the new Session; no record gives an empty window."""
        execution = context.execution
        raw = await self._store.load_state(execution.agent_id, execution.session_id)
        if raw is None:
            self._remember_metadata(context, _WindowMetadata())
            return LoadedRecord()
        try:
            record = parse_record(raw)
            if record.agent_id != execution.agent_id or record.session_id != execution.session_id:
                raise invalid_record("identity does not match this Session")
            messages = decode_messages(record.messages)
            interruption = decode_interruption(record.interruption) if record.interruption is not None else None
            ending_outcome = record.ending.outcome if record.ending is not None else None
            _check_window(messages, ending_outcome, interruption, invalid_record)
            if record.deepagent_state is not None:
                try:
                    DeepAgentState.from_session_dict(record.deepagent_state)
                except Exception:
                    raise invalid_record("DeepAgent state cannot be restored") from None
            pending_rollover = await self._store.find_pending_rollover(
                execution.agent_id,
                execution.session_id,
                current_window_id=record.current_window_id,
                window_seq=record.window_seq,
                checkpoint=(
                    SessionCheckpoint(
                        **record.checkpoint.model_dump(),
                        last_group_message_id=record.last_group_message_id,
                    )
                    if record.checkpoint is not None
                    else None
                ),
                request_id=execution.request_id,
                recovery_ref=execution.recovery_ref,
                spec_fingerprint=execution.config_fingerprint,
            )
            if pending_rollover is not None:
                # The archive is written before the active record. If the process stopped between
                # those writes, repair only when its source snapshot and rebuilt candidate match.
                if pending_rollover.messages != encode_messages(messages):
                    raise invalid_record("pending rollover archive does not contain the active window snapshot")
                from .window_management import restore_rollover_candidate

                try:
                    recovered_messages = restore_rollover_candidate(
                        messages,
                        WindowSeed.model_validate(pending_rollover.seed),
                        pending_rollover.retained_item_refs,
                        candidate_version=pending_rollover.candidate_version,
                    )
                except (PydanticValidationError, TypeError, ValueError):
                    raise invalid_record("pending rollover candidate cannot be reconstructed") from None
                if _message_fingerprint(recovered_messages) != pending_rollover.candidate_fingerprint:
                    raise invalid_record("pending rollover candidate does not match its archived fingerprint")
                _check_window(recovered_messages, None, None, invalid_record)
                recovered_rollover = RecordRollover(
                    old_window_id=pending_rollover.old_window_id,
                    old_window_seq=pending_rollover.old_window_seq,
                    new_window_id=pending_rollover.new_window_id,
                    new_window_seq=pending_rollover.new_window_seq,
                    archive_ref=pending_rollover.archive_ref,
                    trigger_kind=pending_rollover.trigger_kind,
                    token_usage=pending_rollover.token_usage,
                )
                recovered_record = SessionStateRecord(
                    agent_id=record.agent_id,
                    session_id=record.session_id,
                    messages=encode_messages(recovered_messages),
                    interruption=None,
                    auto_confirm=record.auto_confirm,
                    deepagent_state=record.deepagent_state,
                    checkpoint=RecordCheckpoint(
                        request_id=pending_rollover.request_id,
                        execution_id=pending_rollover.execution_id,
                        iteration_seq=pending_rollover.iteration_seq,
                        kind="rollover",
                    ),
                    last_group_message_id=pending_rollover.last_group_message_id,
                    current_window_id=pending_rollover.new_window_id,
                    window_seq=pending_rollover.new_window_seq,
                    last_rollover=recovered_rollover,
                    ending=None,
                )
                # Persist first; a backend failure must not expose a candidate that is not committed.
                await self._store.save_state(
                    execution.agent_id,
                    execution.session_id,
                    dump_record(recovered_record),
                )
                record = recovered_record
                messages = recovered_messages
                interruption = None
        except BaseError as exc:
            # Diagnostics name the Session and the category only; the record text stays out of logs.
            logger.error(
                "[PersistentAgent] session state record rejected: session=%s execution=%s reason=%s",
                execution.session_id,
                execution.execution_id,
                exc.message,
            )
            raise
        if record.ending is not None and record.ending.outcome in INTERRUPTED_ENDINGS:
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
        waiting = (
            WaitingScene(ending.request_id, ending.reply_target)
            if ending is not None and ending.outcome == WAITING
            else None
        )
        checkpoint = (
            SessionCheckpoint(**record.checkpoint.model_dump(), last_group_message_id=record.last_group_message_id)
            if record.checkpoint is not None
            else None
        )
        metadata = _WindowMetadata(
            current_window_id=record.current_window_id,
            window_seq=record.window_seq,
            last_rollover=self._rollover_value(record.last_rollover),
        )
        self._remember_metadata(context, metadata)
        return LoadedRecord(
            waiting=waiting,
            last_group_message_id=record.last_group_message_id,
            checkpoint=checkpoint,
            current_window_id=metadata.current_window_id,
            window_seq=metadata.window_seq,
            last_rollover=metadata.last_rollover,
        )

    async def checkpoint(self, context: "PersistentBuildContext", checkpoint: SessionCheckpoint) -> None:
        """Save a safe request-start/iteration boundary without terminal cleanup or an end marker."""
        execution, session = context.execution, context.session
        await self._sync_native(context)
        contexts = session.get_state(CONTEXT_STATE_KEY) or {}
        if set(contexts) - {CONTEXT_ID}:
            raise unsavable("only the default window is supported")
        window = contexts.get(CONTEXT_ID) or {}
        if window.get("offload_messages"):
            raise unsavable("offloaded messages are not supported")
        messages = list(window.get("messages") or [])
        if session.get_state(INTERRUPTION_KEY) is not None:
            raise unsavable("an active checkpoint cannot contain interruption state")
        _check_window(messages, None, None, unsavable)
        try:
            encoded_checkpoint = RecordCheckpoint(
                request_id=checkpoint.request_id,
                execution_id=checkpoint.execution_id,
                iteration_seq=checkpoint.iteration_seq,
                kind=checkpoint.kind,
            )
        except (PydanticValidationError, TypeError, ValueError) as exc:
            raise unsavable("checkpoint metadata is invalid", exc) from exc

        metadata = self._metadata_for(context)
        current_window_id = metadata.current_window_id or uuid4().hex
        window_seq = metadata.window_seq or 1
        record = SessionStateRecord(
            agent_id=execution.agent_id,
            session_id=execution.session_id,
            messages=encode_messages(messages),
            interruption=None,
            auto_confirm=_auto_confirm(session.get_state(INTERRUPT_AUTO_CONFIRM_KEY) or {}),
            deepagent_state=_deepagent_state(session.get_state(DEEPAGENT_STATE_KEY)),
            checkpoint=encoded_checkpoint,
            last_group_message_id=checkpoint.last_group_message_id,
            current_window_id=current_window_id,
            window_seq=window_seq,
            last_rollover=(
                RecordRollover(**metadata.last_rollover.__dict__) if metadata.last_rollover is not None else None
            ),
            ending=None,
        )
        await self._store.save_state(execution.agent_id, execution.session_id, dump_record(record))
        self._remember_metadata(
            context,
            _WindowMetadata(current_window_id, window_seq, metadata.last_rollover),
        )

    async def rollover(self, context: "PersistentBuildContext", transition: WindowRollover) -> RolloverCheckpoint:
        """Archive the full old window before one write commits the new active window and checkpoint."""
        execution, session = context.execution, context.session
        reference = context.binding.agent_ref
        agent = reference() if reference is not None else None
        if agent is None:
            raise unsavable("the native agent is no longer available")
        try:
            # Save DeepAgent-owned state without publishing the provisional candidate window.
            agent.save_state(session)
        except Exception as exc:
            raise unsavable("DeepAgent state could not be synced before rollover", exc) from exc
        old_messages = list(transition.old_messages)
        new_messages = list(transition.new_messages)
        if session.get_state(INTERRUPTION_KEY) is not None:
            raise unsavable("a rollover cannot contain interruption state")
        _check_window(old_messages, None, None, unsavable)
        _check_window(new_messages, None, None, unsavable)

        metadata = self._metadata_for(context)
        old_window_id = metadata.current_window_id or uuid4().hex
        old_window_seq = metadata.window_seq or 1
        new_window_id = uuid4().hex
        new_window_seq = old_window_seq + 1
        archive = await self._store.archive_window(
            execution.agent_id,
            execution.session_id,
            {
                "old_window_id": old_window_id,
                "old_window_seq": old_window_seq,
                "new_window_id": new_window_id,
                "new_window_seq": new_window_seq,
                "trigger_kind": transition.trigger_kind,
                "request_id": execution.request_id,
                "execution_id": execution.execution_id,
                "spec_fingerprint": execution.config_fingerprint,
                "seed": transition.seed.model_dump(mode="json"),
                "retained_item_refs": list(transition.retained_item_refs),
                "token_usage": transition.token_usage,
                "messages": encode_messages(old_messages),
                "candidate_fingerprint": _message_fingerprint(new_messages),
                "candidate_version": transition.candidate_version,
                "iteration_seq": transition.iteration_seq,
                "last_group_message_id": transition.last_group_message_id,
            },
        )
        rollover = RecordRollover(
            old_window_id=old_window_id,
            old_window_seq=old_window_seq,
            new_window_id=new_window_id,
            new_window_seq=new_window_seq,
            archive_ref=archive.archive_ref,
            trigger_kind=transition.trigger_kind,
            token_usage=transition.token_usage,
        )
        checkpoint = RecordCheckpoint(
            request_id=execution.request_id,
            execution_id=execution.execution_id,
            iteration_seq=transition.iteration_seq,
            kind="rollover",
        )
        record = SessionStateRecord(
            agent_id=execution.agent_id,
            session_id=execution.session_id,
            messages=encode_messages(new_messages),
            interruption=None,
            auto_confirm=_auto_confirm(session.get_state(INTERRUPT_AUTO_CONFIRM_KEY) or {}),
            deepagent_state=_deepagent_state(session.get_state(DEEPAGENT_STATE_KEY)),
            checkpoint=checkpoint,
            last_group_message_id=transition.last_group_message_id,
            current_window_id=new_window_id,
            window_seq=new_window_seq,
            last_rollover=rollover,
            ending=None,
        )
        await self._store.save_state(execution.agent_id, execution.session_id, dump_record(record))
        committed = RolloverCheckpoint(**rollover.model_dump())
        self._remember_metadata(context, _WindowMetadata(new_window_id, new_window_seq, committed))
        return committed

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
        metadata = self._metadata_for(context)
        record = SessionStateRecord(
            agent_id=execution.agent_id,
            session_id=execution.session_id,
            messages=encode_messages(messages),
            interruption=encode_interruption(interruption) if interruption is not None else None,
            auto_confirm=_auto_confirm(auto_confirm),
            deepagent_state=_deepagent_state(deepagent_state),
            checkpoint=None,
            last_group_message_id=ending.last_group_message_id,
            current_window_id=metadata.current_window_id,
            window_seq=metadata.window_seq,
            last_rollover=(
                RecordRollover(**metadata.last_rollover.__dict__) if metadata.last_rollover is not None else None
            ),
            ending=RecordEnding(
                outcome=ending.outcome,
                request_id=ending.request_id,
                execution_id=execution.execution_id,
                reply_target=ending.reply_target,
                output_refs=list(ending.output_refs),
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
