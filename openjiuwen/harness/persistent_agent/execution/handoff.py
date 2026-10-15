# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Candidate handoff (PA-08): what an ending hands to the host, under which identity and in which order.

Candidates come only from the final answer and from the files share_file listed. The whole batch is
prepared before anything is recorded; when any part cannot be prepared, nothing is handed off.
"""

import asyncio
import errno
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from typing import Any

from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage

from ..schema.events import CAPTION_MAX_CHARS, EVENT_MAX_BYTES, AgentEvent, EventType
from ..schema.execution import ExecutionContext
from ..schema.integration import EXECUTION_END, WINDOW_PART_KEY
from ..workspace.access import WorkspaceAccessPort, WorkspaceReadScope, validate_read_scope
from ..workspace.path_policy import ResolvedWorkspacePath, WorkspacePathError, resolve_read_path
from .emitter import EventEmitter, longest_prefix, widest_size

SHARE_FILE = "share_file"
SHARE_MODES = ("upload", "attachment")
# Files one ending may hand off, counted after repeated listings are merged.
MAX_REGISTRATIONS = 64
# References one ending event may carry.
MAX_OUTPUTS = 128
FILE_PATH_MAX_CHARS = 1024
REGISTERED_NOTE = "已列入本次交付；执行结束时按文件当时的内容交给服务端；登记不等于已发送"
_REGISTERED = "registered"
_RESULT_KEYS = frozenset({SHARE_FILE, "file_path", "share_mode", "caption", "note", "receipt"})
_READ_CHUNK = 1 << 20
# Same length as every real output_id, so sizing does not depend on the identity it derives.
_OUTPUT_ID_PLACEHOLDER = "out_" + "0" * 32
_FAILURES_SHOWN = 5
_DETAIL_LIMIT = 4096


class HandoffFileError(ValueError):
    """Safe file-check rejection; carries a category, never a host path or host diagnostics."""

    def __init__(self, status: str):
        self.status = status
        super().__init__(status)


@dataclass(frozen=True)
class Registration:
    """One file listed for handoff: its logical path, how it is shared and its public caption."""

    file_path: str
    share_mode: str
    caption: str | None = None


@dataclass(frozen=True)
class PreparedBatch:
    """One ending's candidates in delivery order: answer segments first, then files in listed order."""

    payloads: tuple[dict, ...] = ()
    answer_segments: int = 0
    # (logical path, share mode, output_id) of each file candidate.
    files: tuple[tuple[str, str, str], ...] = ()
    # (logical path, or "" for the batch, and category) of each part not prepared; then nothing is handed off.
    failures: tuple[tuple[str, str], ...] = ()

    @property
    def output_refs(self) -> tuple[str, ...]:
        """Output IDs in delivery order, as the ending event references them."""
        return tuple(payload["output_id"] for payload in self.payloads)


def is_logical_path(value: Any) -> bool:
    """Whether a resolved relative path can be carried by an output event (see OutputPayload.file_path)."""
    if not isinstance(value, str) or not 0 < len(value) <= FILE_PATH_MAX_CHARS or value.startswith("/"):
        return False
    if any(ord(character) < 32 or ord(character) == 127 or character == "\\" for character in value):
        return False
    return _encodable(value) and all(part not in ("", ".", "..") for part in value.split("/"))


def _encodable(value: str) -> bool:
    """Whether the text can be written as UTF-8, as every event field must be."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def valid_caption(value: Any) -> bool:
    """A public caption is absent, or nonblank text within the event limit."""
    if value is None:
        return True
    return isinstance(value, str) and bool(value.strip()) and len(value) <= CAPTION_MAX_CHARS and _encodable(value)


def merge_registration(registrations: dict, registration: Registration) -> bool:
    """Add or update one listing; the latest caption wins and the first position stays.

    Returns False, leaving the table unchanged, when a new file would exceed the limit.
    """
    key = (registration.file_path, registration.share_mode)
    if key not in registrations and len(registrations) >= MAX_REGISTRATIONS:
        return False
    registrations[key] = registration
    return True


def _receipt(file_path: str, share_mode: str, caption: str | None) -> str:
    """Content check value over what a listing says.

    A result whose path, share mode or caption was changed after the tool returned, while the check
    value was left as it was, no longer matches. It is not a signature: it cannot prove a result came
    from this tool, and code that recomputes it is not detected; rails are trusted host code.
    """
    canonical = json.dumps(
        {"file_path": file_path, "share_mode": share_mode, "caption": caption}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def registration_result(registration: Registration) -> str:
    """The fixed tool result of a successful listing; rebuild_registrations recognizes only this form."""
    return json.dumps(
        {
            SHARE_FILE: _REGISTERED,
            "file_path": registration.file_path,
            "share_mode": registration.share_mode,
            "caption": registration.caption,
            "note": REGISTERED_NOTE,
            "receipt": _receipt(registration.file_path, registration.share_mode, registration.caption),
        },
        ensure_ascii=False,
    )


def _parse_result(content: Any) -> Registration | None:
    """The listing a result stands for, or None unless it keeps the fixed success form and its check value.

    The check value is a content check, not a signature: it rejects a result whose path, share mode or
    caption changed while the old check value was kept, but cannot prove the result came from this tool.
    """
    try:
        data = json.loads(content) if isinstance(content, str) else None
    except ValueError:
        return None
    if not isinstance(data, dict) or set(data) != _RESULT_KEYS:
        return None
    file_path, share_mode, caption = data["file_path"], data["share_mode"], data["caption"]
    unchanged = (
        data[SHARE_FILE] == _REGISTERED
        and data["note"] == REGISTERED_NOTE
        and share_mode in SHARE_MODES
        and is_logical_path(file_path)
        and valid_caption(caption)
        and data["receipt"] == _receipt(file_path, share_mode, caption)
    )
    return Registration(file_path, share_mode, caption) if unchanged else None


def rebuild_registrations(messages: list) -> list[Registration]:
    """Listings of the current confirmation chain, rebuilt from share_file's own results in the window.

    Only messages after the last end marker are read, so another Request's listings never reach this
    reply target. A result counts only right after its own call group, in the fixed success form and
    with a matching content check value; notes, placeholders, interruption results and results whose
    content was changed without recomputing the check value count as not listed. See _parse_result for
    what the check value does not prove.
    """
    start = 0
    for index, message in enumerate(messages):
        if (getattr(message, "metadata", None) or {}).get(WINDOW_PART_KEY) == EXECUTION_END:
            start = index + 1
    chain = messages[start:]
    merged: dict[tuple[str, str], Registration] = {}
    index = 0
    while index < len(chain):
        message = chain[index]
        index += 1
        if not (isinstance(message, AssistantMessage) and message.tool_calls):
            continue
        names = {call.id: call.name for call in message.tool_calls}
        while index < len(chain) and isinstance(chain[index], ToolMessage):
            result = chain[index]
            index += 1
            if names.get(result.tool_call_id) == SHARE_FILE:
                registration = _parse_result(result.content)
                if registration is not None:
                    merge_registration(merged, registration)
    return list(merged.values())


def candidate_output_id(execution: ExecutionContext, target_ref: str, share_mode: str, content: dict) -> str:
    """Identity of one candidate within its Request: the same outcome to the same target keeps its ID."""
    identity = {
        "namespace_id": execution.namespace_id,
        "agent_id": execution.agent_id,
        "session_id": execution.session_id,
        "request_id": execution.request_id,
        "target_ref": target_ref,
        "share_mode": share_mode,
        "content": content,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "out_" + hashlib.sha256(canonical.encode("utf-8", "surrogatepass")).hexdigest()[:32]


def _last_visible(text: str) -> int:
    index = len(text) - 1
    while index >= 0 and text[index].isspace():
        index -= 1
    return index


def split_answer(text: str, room: int) -> list[str] | None:
    """Ordered segments within both event limits that join back to the answer.

    A segment ends after its last line break when one fits, otherwise at the widest prefix, and never
    leaves only whitespace behind; the same answer and room always give the same segments. None when
    a segment would be blank, which only a whitespace run longer than one event causes.
    """
    segments: list[str] = []
    rest = text
    while rest:
        size = longest_prefix(rest, room)
        if size < len(rest):
            size = min(size, _last_visible(rest))
            newline = rest.rfind("\n", 0, size)
            if newline >= 0 and rest[: newline + 1].strip():
                size = newline + 1
        segment = rest[:size]
        if not segment.strip():
            return None
        segments.append(segment)
        rest = rest[size:]
    return segments


async def read_scope(execution: ExecutionContext, access: WorkspaceAccessPort) -> WorkspaceReadScope:
    """Current read authorization bound to this Execution; any lookup or identity failure fails closed."""
    try:
        return validate_read_scope(await access.resolve_read_scope(execution), execution)
    except Exception:  # noqa: BLE001 - no host detail reaches the model or the event
        raise HandoffFileError("unknown") from None


def resolve_shared_path(scope: WorkspaceReadScope, requested: str) -> ResolvedWorkspacePath:
    """PA-14 path rules: inside an allowed root or this Session's directory, never a sibling Session's."""
    try:
        return resolve_read_path(scope, requested)
    except WorkspacePathError as exc:
        raise HandoffFileError(exc.status) from None
    except Exception:  # noqa: BLE001 - path resolution must fail closed
        raise HandoffFileError("unavailable") from None


def _open_regular(path) -> tuple[int, int]:
    """Open a regular file without following a final symlink; returns the descriptor and its size.

    The opened file must be the one checked before opening, so a file swapped in between is refused.
    """
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        raise HandoffFileError("not_found") from None
    except OSError:
        raise HandoffFileError("unavailable") from None
    if not stat.S_ISREG(before.st_mode):
        raise HandoffFileError("not_a_file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise HandoffFileError("not_found") from None
    except OSError as exc:
        raise HandoffFileError("not_a_file" if exc.errno == errno.ELOOP else "unavailable") from None
    try:
        opened = os.fstat(descriptor)
    except OSError:
        os.close(descriptor)
        raise HandoffFileError("unavailable") from None
    if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
        os.close(descriptor)
        raise HandoffFileError("not_a_file")
    return descriptor, opened.st_size


def check_shared_file(resolved: ResolvedWorkspacePath, max_bytes: int) -> None:
    """Listing-time file check: a regular file whose current size is within the limit; nothing is read."""
    descriptor, size = _open_regular(resolved.path)
    os.close(descriptor)
    if size > max_bytes:
        raise HandoffFileError("too_large")


def read_for_handoff(resolved: ResolvedWorkspacePath, max_bytes: int) -> tuple[str, int]:
    """Digest and size of the bytes actually read, in chunks; reading stops one byte past the limit.

    Returns ("sha256:<hex>", bytes read). File content is never returned or logged.
    """
    descriptor, _ = _open_regular(resolved.path)
    digest = hashlib.sha256()
    size = 0
    try:
        while chunk := os.read(descriptor, min(_READ_CHUNK, max_bytes + 1 - size)):
            size += len(chunk)
            if size > max_bytes:
                raise HandoffFileError("too_large")
            digest.update(chunk)
    except OSError:
        raise HandoffFileError("unavailable") from None
    finally:
        os.close(descriptor)
    return "sha256:" + digest.hexdigest(), size


def _answer_payloads(execution: ExecutionContext, target_ref: str, answer: str) -> list[dict] | None:
    probe = {"output_id": _OUTPUT_ID_PLACEHOLDER, "target_ref": target_ref, "share_mode": "message", "text": "x"}
    room = EVENT_MAX_BYTES - (widest_size(execution, EventType.OUTPUT_PROPOSED, probe, any_execution=True) - 1)
    segments = split_answer(answer, room)
    if segments is None:
        return None
    answer_sha256 = hashlib.sha256(answer.encode("utf-8", "surrogatepass")).hexdigest()
    payloads = []
    for index, segment in enumerate(segments):
        content = {"answer_sha256": answer_sha256, "segment_index": index, "segment_count": len(segments)}
        payloads.append(
            {
                "output_id": candidate_output_id(execution, target_ref, "message", content),
                "target_ref": target_ref,
                "share_mode": "message",
                "text": segment,
            }
        )
    return payloads


async def _file_payloads(
    execution: ExecutionContext,
    access: WorkspaceAccessPort,
    target_ref: str,
    registrations: list[Registration],
    max_bytes: int,
) -> tuple[list[dict], list[tuple[str, str, str]], list[tuple[str, str]]]:
    payloads, files, failures = [], [], []
    try:
        scope = await read_scope(execution, access)
    except HandoffFileError as exc:
        return [], [], [(registration.file_path, exc.status) for registration in registrations]
    for registration in registrations:
        try:
            resolved = resolve_shared_path(scope, registration.file_path)
            read = await asyncio.to_thread(read_for_handoff, resolved, max_bytes)
        except HandoffFileError as exc:
            failures.append((registration.file_path, exc.status))
            continue
        payload = _file_payload(execution, target_ref, registration, resolved.relative_path, read)
        payloads.append(payload)
        files.append((resolved.relative_path, registration.share_mode, payload["output_id"]))
    return payloads, files, failures


def _file_payload(
    execution: ExecutionContext, target_ref: str, registration: Registration, file_path: str, read: tuple[str, int]
) -> dict:
    """The output payload of one file read for handoff, under its content-derived identity."""
    file_ref, file_size = read
    content = {"file_sha256": file_ref, "file_size": file_size, "file_path": file_path, "caption": registration.caption}
    payload = {
        "output_id": candidate_output_id(execution, target_ref, registration.share_mode, content),
        "target_ref": target_ref,
        "share_mode": registration.share_mode,
        "file_ref": file_ref,
        "file_path": file_path,
        "file_size": file_size,
    }
    if registration.caption is not None:
        payload["text"] = registration.caption
    return payload


async def prepare_batch(
    execution: ExecutionContext,
    access: WorkspaceAccessPort | None,
    target_ref: str,
    answer: str | None,
    registrations: list[Registration],
    max_bytes: int,
) -> PreparedBatch:
    """Prepare every candidate of one ending, or report every part that failed; nothing is recorded here."""
    payloads: list[dict] = []
    failures: list[tuple[str, str]] = []
    answer_segments = 0
    if answer is not None:
        answer_payloads = _answer_payloads(execution, target_ref, answer)
        if answer_payloads is None:
            failures.append(("", "answer_unsplittable"))
        else:
            payloads.extend(answer_payloads)
            answer_segments = len(answer_payloads)
    files: list[tuple[str, str, str]] = []
    if registrations:
        if access is None:
            failures.extend((registration.file_path, "unknown") for registration in registrations)
        else:
            file_payloads, files, file_failures = await _file_payloads(
                execution, access, target_ref, registrations, max_bytes
            )
            payloads.extend(file_payloads)
            failures.extend(file_failures)
    if len(payloads) > MAX_OUTPUTS:
        failures.append(("", "too_many_outputs"))
    if failures:
        return PreparedBatch(failures=tuple(failures))
    return PreparedBatch(tuple(payloads), answer_segments, tuple(files))


def describe_failures(failures: tuple[tuple[str, str], ...]) -> str:
    """Logical paths and categories of the parts not prepared; the first few, then how many in all."""
    shown = "; ".join(f"{path or 'batch'}: {category}" for path, category in failures[:_FAILURES_SHOWN])
    if len(failures) > _FAILURES_SHOWN:
        shown += f" ({len(failures)} in all)"
    return shown[:_DETAIL_LIMIT]


async def hand_off(emitter: EventEmitter, batch: PreparedBatch) -> tuple[AgentEvent, ...]:
    """Record the candidates in delivery order; the events are kept so a failed ending can hand them in again."""
    # One after another, in delivery order; tuple(await ... for ...) would build an async generator instead.
    events = []
    for payload in batch.payloads:
        events.append(await emitter.emit(EventType.OUTPUT_PROPOSED, payload))
    return tuple(events)
