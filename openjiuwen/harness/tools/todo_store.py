# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Todo snapshots and same-object read/modify/conditional-publish coordination."""

import asyncio
import copy
import json
import os
import uuid
import weakref
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps

from openjiuwen.core.common.exception.errors import BaseError
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

    def document(self):
        return {"plan_id": self.plan_id, "revision": self.revision, "todos": copy.deepcopy(self.todos)}


def decode_snapshot(content, *, exists=True, content_sha256=None):
    if not exists:
        return TodoSnapshot([], None, 0, False, content_sha256)
    data = content if isinstance(content, (dict, list)) else json.loads(content)
    if isinstance(data, list):
        snapshot = TodoSnapshot(data, "legacy", 0, True, content_sha256, False)
    elif isinstance(data, dict):
        plan, revision, todos = data.get("plan_id"), data.get("revision"), data.get("todos")
        if not isinstance(plan, str) or not plan or type(revision) is not int or not 0 <= revision <= MAX_REVISION:
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
    def __init__(self, result, snapshot):
        super().__init__(result)
        self.todo_snapshot = snapshot.document()


def snapshot_from_result(result):
    if isinstance(result, TodoResult):
        return decode_snapshot(result.todo_snapshot)
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
                 launcher.sandbox_type, fs._get_resolved_isolation_key())
    else:
        scope = id(fs)
    return ("sandbox", scope, os.path.normpath(path))


def current_transaction(tool, session_id):
    current = _TRANSACTION.get()
    key = object_key(tool.fs, tool._get_file_path(session_id))
    return current if current is not None and current.key == key else None


async def read_snapshot(fs, path):
    result = await fs.read_file(path, mode="text", options={"snapshot": True, "missing_ok": True})
    if result.code != 0 or result.data is None:
        raise RuntimeError(f"Todo snapshot read failed: {result.message}")
    if type(result.data.exists) is not bool or not isinstance(result.data.content_sha256, str):
        raise RuntimeError("Todo backend does not support snapshot reads")
    snapshot = decode_snapshot(
        result.data.content, exists=result.data.exists, content_sha256=result.data.content_sha256
    )
    snapshot.backend_id = getattr(result.data, "backend_id", None)
    return snapshot


def observe_plan(tool, session_id, snapshot):
    binding = _BINDING.get()
    if binding is None:
        return
    key = object_key(tool.fs, os.path.abspath(tool._get_file_path(session_id)))
    reference = binding[0].setdefault(key, PlanReference())
    if reference.value is _UNRESOLVED:
        reference.value = snapshot.plan_id
    binding[1][key] = snapshot.plan_id


async def capture_plan(tool, session_id):
    path = os.path.abspath(tool._get_file_path(session_id))
    key = object_key(tool.fs, path)
    binding = _BINDING.get()
    ref = binding[0].setdefault(key, PlanReference()) if binding else PlanReference()
    if ref.value is _UNRESOLVED:
        observed = await read_snapshot(tool.fs, path)
        if ref.value is _UNRESOLVED:
            ref.value = observed.plan_id
    return ref.value


async def transact(tool, session_id, callback, *, expected_plan=_UNRESOLVED):
    path = os.path.abspath(tool._get_file_path(session_id))
    key = object_key(tool.fs, path)
    if current_transaction(tool, session_id) is not None:
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
            snapshot = await read_snapshot(tool.fs, path)
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
            else:
                if snapshot.revision >= MAX_REVISION:
                    raise ValueError("Todo revision exceeds safe integer range")
                ids = [row.get("id") for row in tx.pending]
                if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
                    raise ValueError("Todo entries require unique nonempty ids")
                committed = TodoSnapshot(
                    tx.pending, tx.new_plan or snapshot.plan_id or uuid.uuid4().hex, snapshot.revision + 1
                )
                response = await tool.fs.write_file(
                    path,
                    json.dumps(committed.document(), ensure_ascii=False),
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
                if response.data is not None and response.data.conflict:
                    continue
                if response.code != 0 or response.data is None:
                    raise RuntimeError(f"Todo commit failed or outcome unknown: {response.message}")
                if not response.data.atomic_replace:
                    raise RuntimeError("Todo backend did not confirm conditional publication")
            if tx.created:
                tool._created_in_invoke.add(session_id)
            if binding is not None:
                binding[1][key] = committed.plan_id
            if isinstance(result, dict) and committed.exists:
                result = TodoResult(result, committed)
            return result
    raise RuntimeError("Todo update conflicted in all three attempts; read the current plan and retry")


def todo_operation(method):
    """Wrap the existing business body, including nested compatibility helpers."""

    @wraps(method)
    async def invoke(self, inputs, **kwargs):
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
                        if (
                            isinstance(item, dict)
                            and (optional_id or "id" in item)
                            and not str(item.get("id") or "").strip()
                        ):
                            item["id"] = uuid.uuid4().hex
        try:
            return await transact(self, session.get_session_id(), lambda: method(self, data, **kwargs))
        except BaseError:
            raise
        except Exception as exc:
            from openjiuwen.core.common.exception.codes import StatusCode
            from openjiuwen.core.common.exception.errors import build_error

            raise build_error(StatusCode.TOOL_TODOS_INVOKE_FAILED, reason=str(exc)) from exc

    return invoke
