# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Todo snapshots and per-object, optimistic file transactions.

The in-process lock avoids redundant retries. Correctness across cooperating
processes comes from the backend's atomic compare-and-replace, not this lock.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import threading
import uuid
import weakref
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.harness.schema.task import TodoItem

TODO_CONTEXT_KEY = "todo_call_context_v2"
TODO_EXECUTION_KEY = "todo_execution_id_v2"
_LOCKS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_REGISTRY_LOCK = threading.Lock()
_ACTIVE: ContextVar[TodoTransaction | None] = ContextVar("todo_transaction", default=None)
_COORDINATED: ContextVar[str | None] = ContextVar("todo_legacy_coordination", default=None)
_BINDINGS: ContextVar[dict[str, dict] | None] = ContextVar("todo_call_bindings", default=None)


def todo_v2_enabled() -> bool:
    """Explicit rollout gate; reading an existing v2 file never downgrades it."""
    return os.getenv("OPENJIUWEN_TODO_V2", "").lower() in {"1", "true", "yes"}


def _error(kind: str, message: str):
    return build_error(StatusCode.TOOL_TODOS_VALIDATION_INVALID, reason=message, details={"todo_error": kind})


def _object_lock(key: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    with _REGISTRY_LOCK:
        locks = _LOCKS.setdefault(loop, weakref.WeakValueDictionary())
        lock = locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            locks[key] = lock
        return lock


@dataclass(frozen=True)
class TodoSnapshot:
    todos: list[dict[str, Any]]
    metadata: dict[str, Any] | None = None
    content_sha256: str | None = None
    exists: bool = True

    def event_metadata(self, view: str = "display") -> dict[str, Any] | None:
        if self.metadata is None:
            return None
        return {
            "schema_version": 2,
            "store_id": self.metadata["store_id"],
            "plan_id": self.metadata["plan_id"],
            "revision": self.metadata["revision"],
            "commit_id": self.metadata.get("last_commit_id", ""),
            "view": view,
            "writer_epoch": self.metadata["writer_epoch"],
            "legacy_plan": self.metadata.get("legacy_plan", False),
        }

    def binding(self) -> dict[str, Any] | None:
        if self.metadata is None:
            return None
        return {
            **{key: self.metadata[key] for key in ("store_id", "plan_id", "writer_epoch")},
            "legacy_plan": self.metadata.get("legacy_plan", False),
        }


def decode_todo_snapshot(raw: Any, *, content_sha256: str | None = None, exists: bool = True) -> TodoSnapshot:
    """Dual-format codec shared by live tools and history readers."""
    if isinstance(raw, list):
        items, metadata = raw, None
    elif isinstance(raw, dict) and raw.get("schema_version") == 2 and isinstance(raw.get("todos"), list):
        items = raw["todos"]
        metadata = {key: copy.deepcopy(value) for key, value in raw.items() if key != "todos"}
        revision = metadata.get("revision")
        if (
            not isinstance(revision, str)
            or not revision.isascii()
            or not revision.isdecimal()
            or str(int(revision)) != revision
        ):
            raise _error("invalid_snapshot", "Invalid Todo revision")
        for key in ("store_id", "plan_id", "writer_epoch"):
            if not isinstance(metadata.get(key), str) or not metadata[key]:
                raise _error("invalid_snapshot", f"Missing Todo {key}")
    else:
        raise _error("invalid_snapshot", "Expected a Todo array or schema_version=2 snapshot")
    if not all(isinstance(item, dict) for item in items):
        raise _error("invalid_snapshot", "Todo entries must be objects")
    if metadata is not None:
        ids = set()
        for item in items:
            task_id = item.get("id")
            if (
                not isinstance(task_id, str)
                or not task_id.strip()
                or task_id.strip() in ids
                or not isinstance(item.get("content"), str)
                or item.get("status") not in {"pending", "in_progress", "completed", "cancelled"}
                or (item.get("meta_data") is not None and not isinstance(item["meta_data"], dict))
            ):
                raise _error("invalid_snapshot", "Invalid or duplicate Todo entry")
            ids.add(task_id.strip())
    return TodoSnapshot(copy.deepcopy(items), metadata, content_sha256, exists)


@dataclass
class TodoTransaction:
    key: str
    original: TodoSnapshot
    todos: list[dict[str, Any]]
    metadata: dict[str, Any] | None
    invoke_id: str = ""
    commit_id: str = ""
    owner: asyncio.Task | None = field(default_factory=asyncio.current_task)

    def items(self) -> list[TodoItem]:
        return [TodoItem.from_dict(copy.deepcopy(item)) for item in self.todos]

    def stage(self, todos: list[TodoItem]) -> None:
        self.todos = [item.to_dict() for item in todos]


@dataclass(frozen=True)
class TodoCommitReceipt:
    snapshot: TodoSnapshot
    changed: bool
    attempts: int
    wait_seconds: float
    hold_seconds: float


class TodoResult(dict):
    """Machine receipt stays out of JSON/model-visible dictionary fields."""

    def __init__(self, result: dict, receipt: TodoCommitReceipt):
        super().__init__(result)
        self.todo_receipt = receipt


class TodoItems(list):
    """Retain the read precondition for legacy load-then-save SDK callers."""

    def __init__(self, snapshot: TodoSnapshot):
        super().__init__(TodoItem.from_dict(copy.deepcopy(item)) for item in snapshot.todos)
        self.todo_snapshot = snapshot


class TodoStore:
    def __init__(self, fs: Any, path: str, *, namespace: str = "local"):
        self.fs = fs
        self.path = os.path.abspath(path)
        canonical = os.path.normcase(os.path.realpath(self.path)) if namespace == "local" else self.path
        self.key = f"{namespace}:{canonical}"
        self._capable: bool | None = None

    def active(self) -> TodoTransaction | None:
        transaction = _ACTIVE.get()
        if transaction is None or transaction.key != self.key:
            return None
        if transaction.owner is not asyncio.current_task():
            raise _error("invalid_transaction", "A Todo transaction cannot be inherited by another task")
        return transaction

    async def capable(self, *, strict: bool = False) -> bool:
        if self._capable is None:
            if getattr(type(self.fs), "supports_atomic_compare_replace", False) is True:
                self._capable = True
            else:
                capabilities = getattr(self.fs, "capabilities", None)
                try:
                    value = await capabilities() if callable(capabilities) else {}
                    self._capable = isinstance(value, dict) and value.get("atomic_compare_replace") is True
                except Exception as exc:
                    # A transient capability RPC failure is not permanent lack of support.
                    if strict:
                        raise _error("capability_unavailable", f"Cannot verify Todo backend capability: {exc}") from exc
                    return False
        return self._capable

    async def read(self, *, protect_path: bool = False) -> TodoSnapshot:
        result = await self.fs.read_file(
            self.path,
            mode="text",
            options={
                "include_content_sha256": True,
                "missing_ok": True,
                "lock_timeout": 30,
                **({"expected_resolved_path": self.path} if protect_path else {}),
            },
        )
        if result.code != 0:
            raise build_error(StatusCode.TOOL_TODOS_LOAD_FAILED, reason=result.message)
        data = result.data
        exists = getattr(data, "exists", None) is not False
        content = data.content
        if not isinstance(content, (str, bytes)):
            raise _error("invalid_snapshot", "Todo backend did not return file content")
        try:
            raw = json.loads(content) if exists else []
        except (ValueError, UnicodeError) as exc:
            raise build_error(StatusCode.TOOL_TODOS_LOAD_FAILED, reason="Invalid Todo JSON", cause=exc) from exc
        digest = getattr(data, "content_sha256", None)
        if not isinstance(digest, str):
            digest = hashlib.sha256(content.encode("utf-8") if isinstance(content, str) else content).hexdigest()
        return decode_todo_snapshot(raw, content_sha256=digest, exists=exists)

    @staticmethod
    def _metadata() -> dict[str, Any]:
        return {
            "schema_version": 2,
            "store_id": str(uuid.uuid4()),
            "plan_id": str(uuid.uuid4()),
            "writer_epoch": str(uuid.uuid4()),
            "revision": "0",
            "last_create_invoke_id": "",
        }

    @staticmethod
    def _check_binding(snapshot: TodoSnapshot, binding: dict | None) -> None:
        if not binding:
            return
        meta = snapshot.metadata or {}
        for key in ("store_id", "plan_id", "writer_epoch"):
            if binding.get(key) != meta.get(key):
                kind = "stale_writer" if key == "writer_epoch" else "stale_plan"
                raise _error(kind, "This Todo operation belongs to a superseded plan or execution; re-read the plan")

    async def transact(
        self,
        callback: Callable[[], Awaitable[Any]],
        *,
        binding: dict | None = None,
        invoke_id: str = "",
        expected_parent_identity: tuple[int, int] | None = None,
    ) -> Any:
        if self.active() is not None:
            return await callback()
        if not await self.capable(strict=True):
            raise _error("unsupported_backend", "Todo v2 requires backend atomic compare-and-replace support")
        lock = _object_lock(self.key)
        started = asyncio.get_running_loop().time()
        try:
            await asyncio.wait_for(lock.acquire(), timeout=30)
        except TimeoutError as exc:
            raise _error("lock_timeout", "Timed out waiting for this Todo store") from exc
        acquired = asyncio.get_running_loop().time()
        commit_id = str(uuid.uuid4())
        try:
            for attempt in range(1, 4):
                original = await self.read(protect_path=True) if expected_parent_identity else await self.read()
                if original.metadata is not None and not todo_v2_enabled():
                    raise _error(
                        "writes_disabled", "Todo v2 writes are disabled; keep a v2 reader or complete the rollout"
                    )
                self._check_binding(original, binding)
                meta = copy.deepcopy(original.metadata)
                if meta is None and todo_v2_enabled():
                    meta = self._metadata()
                    meta["legacy_plan"] = original.exists
                items = copy.deepcopy(original.todos)
                if original.metadata is None and meta is not None:
                    items = [TodoItem.from_dict(item).to_dict() for item in items]
                tx = TodoTransaction(self.key, original, items, meta, invoke_id, commit_id)
                token = _ACTIVE.set(tx)
                try:
                    result = await callback()
                finally:
                    _ACTIVE.reset(token)
                changed = tx.todos != original.todos or tx.metadata != original.metadata
                if changed:
                    self._validate_ids(tx.todos)
                    if tx.metadata is not None:
                        tx.metadata["revision"] = str(int((original.metadata or {}).get("revision", "0")) + 1)
                        tx.metadata["last_commit_id"] = commit_id
                        body = {**tx.metadata, "todos": tx.todos}
                    else:
                        body = tx.todos
                    content = json.dumps(body, ensure_ascii=False, indent=2)
                    written = await self.fs.write_file(
                        self.path,
                        content,
                        mode="text",
                        prepend_newline=False,
                        options={
                            "atomic_replace": True,
                            "expected_exists": original.exists,
                            "expected_content_sha256": original.content_sha256,
                            **(
                                {
                                    "expected_parent_identity": expected_parent_identity,
                                    "expected_resolved_path": self.path,
                                }
                                if expected_parent_identity
                                else {}
                            ),
                            "lock_timeout": 30,
                        },
                    )
                    if written.code != 0:
                        if getattr(written, "failure_kind", None) == "conflict":
                            continue
                        raise build_error(StatusCode.TOOL_TODOS_SAVE_FAILED, reason=written.message)
                    if getattr(written.data, "atomic_replace", None) is not True:
                        raise _error(
                            "outcome_unknown", "Backend did not confirm an atomic Todo commit; re-read before retrying"
                        )
                    snapshot = TodoSnapshot(
                        copy.deepcopy(tx.todos),
                        copy.deepcopy(tx.metadata),
                        hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    )
                else:
                    snapshot = original
                receipt = TodoCommitReceipt(
                    snapshot, changed, attempt, acquired - started, asyncio.get_running_loop().time() - acquired
                )
                return TodoResult(result, receipt) if isinstance(result, dict) else (result, receipt)
            raise _error("conflict", "Todo changed repeatedly; re-read and retry the requested changes")
        finally:
            lock.release()

    @staticmethod
    def _validate_ids(items: list[dict]) -> None:
        seen = set()
        for item in items:
            task_id = item.get("id")
            if not isinstance(task_id, str) or not task_id.strip():
                task_id = str(uuid.uuid4())
                item["id"] = task_id
            if task_id.strip() in seen:
                raise _error("duplicate_id", f"Duplicate Todo id: {task_id}")
            seen.add(task_id.strip())

    async def coordinated(self, callback: Callable[[], Awaitable[Any]]) -> Any:
        """Legacy/custom backend fallback: only in-process coordination is promised."""
        if _COORDINATED.get() == self.key:
            return await callback()
        async with _object_lock(self.key):
            token = _COORDINATED.set(self.key)
            try:
                return await callback()
            finally:
                _COORDINATED.reset(token)


def session_todo_binding(session: Any, store_key: str | None = None) -> dict | None:
    if store_key is not None and store_key in (_BINDINGS.get() or {}):
        return dict((_BINDINGS.get() or {})[store_key])
    getter = getattr(session, "get_state", None)
    value = getter(TODO_CONTEXT_KEY) if callable(getter) else None
    return dict(value) if isinstance(value, dict) else None


def bind_todo_context(store_key: str, binding: dict) -> None:
    _BINDINGS.set({**(_BINDINGS.get() or {}), store_key: dict(binding)})
