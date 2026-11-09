# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Todo snapshots and same-object read/modify/conditional-publish coordination."""

import asyncio
import copy
import json
import os
import re
import uuid
import weakref
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.harness.schema.task import TodoItem

MAX_REVISION = 2**53 - 1
_UNRESOLVED = object()
_BINDING = ContextVar("todo_plan_binding", default=None)
_TRANSACTION = ContextVar("todo_transaction", default=None)
_LOCKS = weakref.WeakKeyDictionary()


@dataclass
class PlanReference:
    value: object = _UNRESOLVED


@dataclass
class TodoSnapshot:
    todos: list[dict]
    plan_id: str | None
    revision: int
    exists: bool = True
    content_sha256: str | None = None
    versioned: bool = True
    backend_id: str | None = None
    read_only_backend: bool = False
    read_authoritative: bool = True

    def document(self):
        return {"plan_id": self.plan_id, "revision": self.revision, "todos": copy.deepcopy(self.todos)}


def decode_snapshot(content, *, exists=True, content_sha256=None):
    if not exists:
        return TodoSnapshot([], None, 0, False, content_sha256, read_authoritative=False)
    data = content if isinstance(content, (dict, list)) else json.loads(content)
    if isinstance(data, list):
        snapshot = TodoSnapshot(data, "legacy", 0, True, content_sha256, False)
    elif isinstance(data, dict):
        plan, revision, todos = data.get("plan_id"), data.get("revision"), data.get("todos")
        valid_revision_type = isinstance(revision, int) and not isinstance(revision, bool)
        if not isinstance(plan, str) or not plan or not valid_revision_type:
            raise ValueError("Invalid Todo plan or revision")
        if not 0 <= revision <= MAX_REVISION:
            raise ValueError("Invalid Todo plan or revision")
        snapshot = TodoSnapshot(todos, plan, revision, True, content_sha256)
    else:
        raise TypeError("Todo file must be an array or snapshot")
    if not isinstance(snapshot.todos, list) or any(not isinstance(t, dict) for t in snapshot.todos):
        raise ValueError("Invalid Todo entries")
    ids = [t.get("id") for t in snapshot.todos]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("Todo entries require unique nonempty ids")
    return snapshot


class TodoResult(dict):
    """Keep framework metadata out of the model-visible JSON tool result."""
    def __init__(self, result, snapshot=None, *, outcome="success", counts=None, commit_state="not_needed"):
        super().__init__(result)
        self.tool_execution_status = outcome
        self.tool_item_counts = counts or {}
        self.tool_commit_state = commit_state
        if snapshot is not None and snapshot.exists and not snapshot.read_only_backend:
            self.todo_snapshot = snapshot.document()
            self.todo_snapshot_versioned = snapshot.versioned
            self.todo_read_authoritative = snapshot.read_authoritative


def snapshot_from_result(result):
    if isinstance(result, TodoResult) and hasattr(result, "todo_snapshot"):
        snapshot = decode_snapshot(result.todo_snapshot)
        snapshot.versioned = getattr(result, "todo_snapshot_versioned", True)
        snapshot.read_authoritative = getattr(result, "todo_read_authoritative", True)
        return snapshot
    return None


class TodoItems(list):
    """Keep the read baseline with items; never unconditionally save a stale list."""

    def __init__(self, snapshot):
        super().__init__(TodoItem.from_dict(copy.deepcopy(t)) for t in snapshot.todos)
        self.snapshot = snapshot
        self.baseline = {t.id: copy.deepcopy(t.to_dict()) for t in self}

    def records(self, items):
        raw = {t["id"]: t for t in self.snapshot.todos}
        result = []
        for item in items:
            data = item.to_dict()
            if item.id in self.baseline:
                changes = {k: v for k, v in data.items() if v != self.baseline[item.id].get(k)}
                data = {**raw[item.id], **changes}
            result.append(data)
        return result


@dataclass
class Transaction:
    key: tuple
    snapshot: TodoSnapshot
    items: TodoItems
    pending: list[dict] | None = None
    new_plan: str | None = None
    created: bool = False
    write_options: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)


def prepare_todo_batch(ctx):
    """No I/O: freeze known plans; resolve unknown objects on their first Todo read."""
    latest = ctx.extra.setdefault("_todo_latest_plans", {})
    ctx.extra["_todo_batch"] = {key: PlanReference(value) for key, value in latest.items()}


def bind_todo_batch(ctx, tool=None):
    latest = ctx.extra.setdefault("_todo_latest_plans", {})
    batch = ctx.extra.setdefault("_todo_batch", {})
    _BINDING.set((batch, latest, tool, ctx.session.get_session_id() if ctx.session else None))


def reset_todo_binding():
    _BINDING.set(None)


def bound_todo_tool():
    """The Todo rail owns backend selection for asynchronous host callbacks."""
    binding = _BINDING.get()
    return (binding[2], binding[3]) if binding is not None else (None, None)


def object_key(fs, path):
    mode = getattr(fs, "mode", "local")
    if hasattr(fs, "endpoint"):
        endpoint = fs.endpoint
        return ("sandbox-provider", endpoint.base_url,
                endpoint.sandbox_id or endpoint.isolation_key, os.path.normpath(path))
    if str(getattr(mode, "value", mode)) == "local":
        return ("local", os.path.normcase(os.path.realpath(path)))
    config = getattr(fs, "_config", None)
    launcher = getattr(config, "launcher_config", None)
    if launcher is not None:
        # Gateway isolation is stable across lazy creation. Runtime extras may
        # gain a sandbox_id and contain callbacks, so they are not a lock key.
        scope = (launcher.gateway_url, getattr(launcher, "base_url", ""),
                 launcher.sandbox_type, fs.get_resolved_isolation_key())
    else:
        scope = id(fs)
    return ("sandbox", scope, os.path.normpath(path))


def current_transaction(tool, session_id):
    current = _TRANSACTION.get()
    key = object_key(tool.fs, os.path.abspath(tool.get_file_path(session_id)))
    return current if current is not None and current.key == key else None


async def read_snapshot(fs, path, *, allow_read_only=False):
    try:
        result = await fs.read_file(path, mode="text", options={"snapshot": True, "missing_ok": True})
    except BaseError:
        raise
    except Exception as exc:
        raise build_error(StatusCode.TOOL_TODOS_LOAD_FAILED, reason=str(exc), cause=exc,
                          details={"reason": "snapshot_read_failed"}) from exc
    if result.code != 0 or result.data is None:
        raise build_error(StatusCode.TOOL_TODOS_LOAD_FAILED,
                          reason=f"Todo snapshot read failed: {result.message}",
                          details={"reason": "snapshot_read_failed"})
    exists = getattr(result.data, "exists", None)
    digest = getattr(result.data, "content_sha256", None)
    supports_snapshot = isinstance(exists, bool) and isinstance(digest, str)
    if (exists is not None and not isinstance(exists, bool)) or (
        digest is not None and (not isinstance(digest, str) or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None)
    ):
        raise build_error(StatusCode.TOOL_TODOS_LOAD_FAILED,
                          reason="Todo backend returned invalid snapshot metadata",
                          details={"reason": "invalid_snapshot_metadata"})
    if not supports_snapshot:
        if allow_read_only:
            snapshot = _decode_read_result(result.data.content)
            snapshot.read_only_backend = True
            snapshot.read_authoritative = False
            return snapshot
        raise build_error(StatusCode.TOOL_TODOS_LOAD_FAILED,
                          reason=f"Todo backend does not support snapshot reads ({type(fs).__name__}): "
                          "read_file must return exists and content_sha256; "
                          "use a backend with Todo snapshot/conditional atomic replacement support",
                          details={"reason": "unsupported_snapshot_backend"})
    snapshot = _decode_read_result(
        result.data.content, exists=result.data.exists, content_sha256=result.data.content_sha256
    )
    snapshot.backend_id = getattr(result.data, "backend_id", None)
    return snapshot


def _decode_read_result(content, **kwargs):
    try:
        return decode_snapshot(content, **kwargs)
    except (ValueError, TypeError) as exc:
        raise build_error(StatusCode.TOOL_TODOS_LOAD_FAILED, reason=str(exc), cause=exc,
                          details={"reason": "invalid_snapshot"}) from exc


def validate_published_todos(snapshot, records):
    """按即将发布的格式校验，不能把旧代过滤当作新快照的写入资格。"""
    active = [row["id"] for row in records if row.get("status", "pending") == "in_progress"]
    invalid = [row["id"] for row in records
               if row.get("status", "pending") not in ("pending", "in_progress", "completed", "cancelled")]
    if invalid or len(active) > 1:
        reason = "migration_conflict" if snapshot.exists and not snapshot.versioned else "state_conflict"
        message = ("Invalid Todo status" if invalid else
                   "More than one task is marked as 'in_progress' (only one allowed)")
        message += f". Candidate conflicting task IDs: {invalid or active}. The update was not committed."
        raise build_error(StatusCode.TOOL_TODOS_VALIDATION_INVALID, reason=message,
                          details={"reason": reason, "conflicting_ids": invalid or active, "committed": False})


def _serialize_snapshot(snapshot):
    """单独的发布序列化边界，故障注入不应同时破坏 FS/日志使用的全局 json。"""
    return json.dumps(snapshot.document(), ensure_ascii=False)


def _result_metadata(tx, committed, changed):
    before = {row["id"]: row for row in tx.snapshot.todos}
    after = {row["id"]: row for row in committed.todos}
    applied = sum(before.get(key) != after.get(key) for key in before.keys() | after.keys())
    counts = {**tx.counts, "applied_count": applied}
    accepted, failed = counts.get("accepted_count", 0), counts.get("failed_count", 0)
    if failed:
        outcome = "partial" if accepted else "failure"
    elif "returned_count" in counts:
        outcome = "success"
    else:
        outcome = "success" if changed else "noop"
    return outcome, counts, "committed" if changed else "not_needed"


def _record_counts(tool, inputs, result, tx):
    kind = getattr(tool, "_todo_action", None)
    action = inputs.get("action") if kind == "modify" else kind
    if action in ("list", "get"):
        tx.counts = {"returned_count": len(result.get("tasks", [])) if action == "list" else 1}
        return
    group = (inputs.get("tasks", []) if action == "create" else
             inputs.get("ids", []) if action in ("delete", "cancel") else
             inputs.get("todo_data", {}).get("items", []) if action in ("insert_before", "insert_after") else
             inputs.get("todos", []))
    ids = {row["id"] for row in tx.snapshot.todos}
    accepted = len(group)
    failed = unmatched = 0
    if action == "update":
        accepted = sum(bool(item.get("id")) and item["id"] in ids for item in group)
        failed = len(group) - accepted
    elif action in ("delete", "cancel"):
        accepted = sum(ident in ids for ident in group)
        unmatched = len(group) - accepted
    tx.counts = {"requested_count": len(group), "accepted_count": accepted,
                 "failed_count": failed, "unmatched_count": unmatched}


async def check_snapshot_lifecycle(tool, session_id, snapshot, *, committing=False):
    """Ask the owning host for first-creation evidence, not file existence."""
    guard = getattr(tool, "snapshot_guard", None)
    if guard is not None:
        await guard(session_id, snapshot, committing=committing)


def observe_plan(tool, session_id, snapshot):
    binding = _BINDING.get()
    if binding is None:
        return
    key = object_key(tool.fs, os.path.abspath(tool.get_file_path(session_id)))
    reference = binding[0].setdefault(key, PlanReference())
    if reference.value is _UNRESOLVED:
        reference.value = snapshot.plan_id
    binding[1][key] = snapshot.plan_id


async def capture_plan(tool, session_id):
    path = os.path.abspath(tool.get_file_path(session_id))
    key = object_key(tool.fs, path)
    binding = _BINDING.get()
    ref = binding[0].setdefault(key, PlanReference()) if binding else PlanReference()
    if ref.value is _UNRESOLVED:
        observed = await read_snapshot(tool.fs, path)
        if ref.value is _UNRESOLVED:
            await check_snapshot_lifecycle(tool, session_id, observed)
            if ref.value is _UNRESOLVED:
                ref.value = observed.plan_id
    return ref.value


async def transact(
    tool, session_id, callback, *, expected_plan=_UNRESOLVED, check_active=None
):
    path = os.path.abspath(tool.get_file_path(session_id))
    key = object_key(tool.fs, path)
    if current_transaction(tool, session_id) is not None:
        if check_active is not None:
            check_active()
        return await callback()
    loop = asyncio.get_running_loop()
    locks = _LOCKS.setdefault(loop, weakref.WeakValueDictionary())
    lock = locks.setdefault(key, asyncio.Lock())
    binding = _BINDING.get()
    reference = PlanReference(expected_plan)
    if expected_plan is _UNRESOLVED and binding is not None:
        reference = binding[0].setdefault(key, PlanReference())
    async with lock:
        for _ in range(3):
            if check_active is not None:
                check_active()
            allow_read_only = (getattr(tool, "_allow_legacy_reads", False)
                               and getattr(tool, "snapshot_guard", None) is None)
            snapshot = await read_snapshot(tool.fs, path, allow_read_only=allow_read_only)
            await check_snapshot_lifecycle(tool, session_id, snapshot)
            if reference.value is _UNRESOLVED:
                reference.value = snapshot.plan_id
            if reference.value != snapshot.plan_id:
                raise ValueError("Todo plan changed; this operation belongs to an older plan")
            tx = Transaction(key, snapshot, TodoItems(snapshot))
            token = _TRANSACTION.set(tx)
            try:
                result = await callback()
            finally:
                _TRANSACTION.reset(token)
            if tx.pending is None or (tx.new_plan is None and tx.pending == snapshot.todos):
                committed = snapshot
                changed = False
            else:
                if snapshot.read_only_backend:
                    raise RuntimeError("Todo backend does not support conditional writes")
                if snapshot.revision >= MAX_REVISION:
                    raise ValueError("Todo revision exceeds safe integer range")
                ids = [row.get("id") for row in tx.pending]
                if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
                    raise ValueError("Todo entries require unique nonempty ids")
                committed = TodoSnapshot(
                    tx.pending, tx.new_plan or snapshot.plan_id or uuid.uuid4().hex, snapshot.revision + 1
                )
                validate_published_todos(snapshot, committed.todos)
                # The owner can expire while awaiting the lock/read or retrying.
                if check_active is not None:
                    check_active()
                await check_snapshot_lifecycle(tool, session_id, snapshot, committing=True)
                if check_active is not None:
                    check_active()
                publication = tool.fs.write_file(
                    path,
                    _serialize_snapshot(committed),
                    mode="text",
                    prepend_newline=False,
                    options={
                        "atomic_replace": True,
                        "expected_exists": snapshot.exists,
                        "expected_content_sha256": snapshot.content_sha256,
                        **({"expected_backend_id": snapshot.backend_id} if snapshot.backend_id else {}),
                        **tx.write_options,
                    },
                )
                try:
                    response = await publication
                except (asyncio.CancelledError, BaseError) as exc:
                    # 请求已交给后端，取消/异常不证明零写入；保持原控制流。
                    exc.tool_commit_state = "unknown"
                    exc.tool_item_counts = {**tx.counts, "applied_count": None}
                    raise
                except Exception as exc:
                    error = build_error(StatusCode.TOOL_TODOS_SAVE_FAILED, reason=str(exc), cause=exc,
                                        details={"reason": "publication_unknown", "commit_state": "unknown"})
                    error.tool_item_counts = {**tx.counts, "applied_count": None}
                    raise error from exc
                if response.data is not None and response.data.conflict:
                    continue
                if response.code != 0 or response.data is None:
                    raise build_error(StatusCode.TOOL_TODOS_SAVE_FAILED,
                                      reason=f"Todo commit failed or outcome unknown: {response.message}",
                                      details={"reason": "publication_unknown", "commit_state": "unknown"})
                if not response.data.atomic_replace:
                    raise build_error(StatusCode.TOOL_TODOS_SAVE_FAILED,
                                      reason="Todo backend did not confirm conditional publication",
                                      details={"reason": "publication_unknown", "commit_state": "unknown"})
                changed = True
            if tx.created:
                tool.mark_created_in_invoke(session_id)
            if binding is not None:
                binding[1][key] = committed.plan_id
            if isinstance(result, dict):
                outcome, counts, commit_state = _result_metadata(tx, committed, changed)
                result = TodoResult(result, committed, outcome=outcome, counts=counts, commit_state=commit_state)
            return result
    raise RuntimeError("Todo update conflicted in all three attempts; read the current plan and retry")


def todo_operation(method):
    """Wrap the existing business body, including nested compatibility helpers."""

    @wraps(method)
    async def invoke(self, inputs, **kwargs):
        validator = getattr(self, "_validate_inputs", None)
        if validator is not None:
            validator(inputs)
        session = kwargs.get("session")
        if session is None:
            return await method(self, inputs, **kwargs)
        # Generated ids are stable across a conditional-write retry.
        data = copy.deepcopy(inputs)
        if isinstance(data, dict):
            groups = [(data.get("tasks"), True)]
            if data.get("action") == "append":
                groups.append((data.get("todos"), False))
            if isinstance(data.get("todo_data"), dict):
                groups.append((data["todo_data"].get("items"), False))
            for group, optional_id in groups:
                if isinstance(group, list):
                    for item in group:
                        if not isinstance(item, dict):
                            continue
                        if not optional_id and "id" not in item:
                            continue
                        if not str(item.get("id") or "").strip():
                            item["id"] = uuid.uuid4().hex
        try:
            async def apply():
                result = await method(self, data, **kwargs)
                tx = current_transaction(self, session.get_session_id())
                if tx is not None and isinstance(result, dict):
                    _record_counts(self, data, result, tx)
                return result
            return await transact(self, session.get_session_id(), apply)
        except BaseError:
            raise
        except Exception as exc:
            from openjiuwen.core.common.exception.codes import StatusCode
            from openjiuwen.core.common.exception.errors import build_error

            raise build_error(StatusCode.TOOL_TODOS_INVOKE_FAILED, reason=str(exc)) from exc

    return invoke
