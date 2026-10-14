"""Ordered bounded accounting WAL, separate from trainable checkpoints.

Only immutable event copies enter the single writer. A durable acknowledgement
is required before POST/GET; an unfinished WAL cannot certify workflow resume.
The full JSON ledger is a boundary snapshot; replay is read-only and lossless.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import hashlib
import json
import os
from pathlib import Path
import queue
import threading
import time
from typing import Any, Mapping

PERSISTENCE_PROTOCOL = "ordered_accounting_progress_v1"
QUEUE_CAPACITY = 64
PERSISTENCE_DEADLINE = 5.0
INVARIANT_FIELDS = frozenset(
    {
        "binding",
        "request_parameters",
        "response_identity_evidence",
        "billing_tariff",
        "effective_input",
        "native_schema",
    }
)


class PersistenceFailure(RuntimeError):
    """A hard dispatch stop; prior durable reservations remain outstanding."""

    failure_kind = "integration-error"


def _json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _atomic(path: Path, value: Any, *, before_publish: Any = None) -> int:
    raw = _json(value) + b"\n"
    temporary = path.with_name(path.name + ".pending")
    with temporary.open("wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if before_publish is not None:
        before_publish()
    temporary.replace(path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return len(raw)


def _apply(state: dict[str, Any], operation: str, value: Mapping[str, Any]) -> None:
    if operation == "initialize":
        if state:
            raise PersistenceFailure(
                "duplicate journal initialization cannot reset accounting"
            )
        state.update(copy.deepcopy(value["state"]))
    elif operation == "ledger":
        for key in value.get("removed", []):
            state.pop(key, None)
        state.update(copy.deepcopy(value.get("header", {})))
        for item in value.get("entries", []):
            index = item["dispatch_id"]
            if index == len(state["entries"]):
                state["entries"].append(copy.deepcopy(item))
            elif 0 <= index < len(state["entries"]):
                state["entries"][index].update(copy.deepcopy(item))
            else:
                raise PersistenceFailure("noncontiguous physical dispatch journal")
    elif operation == "observation":
        item = state["entries"][value["dispatch_id"]]
        events = item.setdefault("recovery_events", [])
        if value["record"]["event_id"] != len(events):
            raise PersistenceFailure("noncontiguous accounting observation")
        events.append(copy.deepcopy(value["record"]))
    elif operation not in {
        "progress",
        "safe-boundary",
        "unsafe-boundary",
        "checkpoint-intent",
    }:
        raise PersistenceFailure("unknown accounting journal operation")


def _unpack(value: Any, objects: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$literal"}:
            return {key: _unpack(item, objects) for key, item in value["$literal"]}
        if set(value) == {"$object"}:
            key = value["$object"]
            if key not in objects:
                raise PersistenceFailure("missing immutable journal object")
            return copy.deepcopy(objects[key])
        return {key: _unpack(item, objects) for key, item in value.items()}
    if isinstance(value, list):
        return [_unpack(item, objects) for item in value]
    return value


def read_journal(directory: str | Path) -> dict[str, Any] | None:
    """Verify every committed record. Torn tails require review, never truncation."""
    directory = Path(directory)
    path = directory / "accounting_progress.jsonl"
    if not path.exists():
        return None
    state: dict[str, Any] = {}
    objects: dict[str, Any] = {}
    previous = "0" * 64
    boundary = None
    sequence = 0
    progress = []
    operations: set[str] = set()
    for raw in path.open("rb"):
        if not raw.endswith(b"\n"):
            raise PersistenceFailure(
                "unfinished accounting journal record; automatic replay disabled"
            )
        try:
            item = json.loads(raw)
            digest = item.pop("sha256")
            if (
                item["protocol"] != PERSISTENCE_PROTOCOL
                or item["seq"] != sequence
                or item["previous"] != previous
            ):
                raise ValueError("journal sequence or protocol mismatch")
            if hashlib.sha256(_json(item)).hexdigest() != digest:
                raise ValueError("journal hash mismatch")
            for key, value in item.get("objects", {}).items():
                if hashlib.sha256(_json(value)).hexdigest() != key or (
                    key in objects and objects[key] != value
                ):
                    raise ValueError("immutable object mismatch")
                objects[key] = value
            value = _unpack(item["data"], objects)
            _apply(state, item["operation"], value)
            operations.add(item["operation"])
            if item["operation"] == "progress":
                progress.append({"sequence": sequence, **value})
            boundary = value if item["operation"] == "safe-boundary" else None
            previous = digest
            sequence += 1
        except (ValueError, KeyError, TypeError, IndexError) as error:
            raise PersistenceFailure("corrupt accounting progress journal") from error
    if not state:
        raise PersistenceFailure("empty accounting progress journal")
    if (
        boundary is not None
        and boundary["ledger_sha256"] != hashlib.sha256(_json(state)).hexdigest()
    ):
        raise PersistenceFailure(
            "safe boundary does not match reconstructed accounting state"
        )
    return {
        "state": state,
        "boundary": boundary,
        "sequence": sequence,
        "sha256": previous,
        "objects": objects,
        "progress": progress,
        "operations": sorted(operations),
    }


class DurableJournal:
    """A bounded single-writer queue; no filesystem work occurs per frame on the loop."""

    def __init__(
        self,
        directories: list[Path],
        state: Mapping[str, Any],
        *,
        allow_empty_initialization: bool = False,
    ) -> None:
        self.directories = list(dict.fromkeys(Path(path) for path in directories))
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=QUEUE_CAPACITY)
        self._lock = threading.RLock()
        self._failure: BaseException | None = None
        self._last: concurrent.futures.Future[Any] | None = None
        self._closed = False
        self._invariant_cache: dict[tuple[str, str], tuple[Any, str]] = {}
        self.metrics = {
            "wal_writes": 0,
            "wal_bytes": 0,
            "snapshot_writes": 0,
            "snapshot_bytes": 0,
            "persistence_seconds": 0.0,
            "queue_peak": 0,
            "invariant_serializations": 0,
        }
        existing = [read_journal(path) for path in self.directories]
        if any(item is not None for item in existing):
            if (
                any(item is None for item in existing)
                or len({item["sha256"] for item in existing}) != 1
            ):
                raise PersistenceFailure(
                    "campaign journals diverge; automatic replay disabled"
                )
            saved = existing[0]
            # Only the locked, registered initializer may finish publishing an
            # untouched initial boundary. This never permits workflow recovery.
            empty_initialization = (
                allow_empty_initialization
                and not state.get("entries")
                and not state.get("halted")
                and not state.get("failure_policy")
                and not state.get("scopes")
                and not saved["progress"]
                and set(saved["operations"])
                <= {
                    "initialize",
                    "checkpoint-intent",
                    "safe-boundary",
                    "unsafe-boundary",
                }
            )
            if (saved["boundary"] is None and not empty_initialization) or _json(
                saved["state"]
            ) != _json(state):
                raise PersistenceFailure(
                    "unfinished workflow journal cannot be rebound"
                )
            self._sequence, self._previous, self._objects = (
                saved["sequence"],
                saved["sha256"],
                saved["objects"],
            )
        else:
            self._sequence, self._previous, self._objects = 0, "0" * 64, {}
        self._header = copy.deepcopy(
            {key: value for key, value in state.items() if key != "entries"}
        )
        self._entry_count = len(state["entries"])
        self._thread = threading.Thread(
            target=self._worker, name="adaptive-accounting-writer", daemon=True
        )
        self._thread.start()
        if not any(item is not None for item in existing):
            self.submit("initialize", {"state": copy.deepcopy(dict(state))})
            self.flush_sync()

    def assert_healthy(self) -> None:
        if self._failure is not None:
            raise PersistenceFailure(
                "accounting persistence failed; new dispatch is prohibited"
            ) from self._failure
        if self._closed:
            raise PersistenceFailure("accounting writer is closed")

    def _pack(self, value: Any, objects: dict[str, Any], scope: str = "global") -> Any:
        if isinstance(value, dict):
            if set(value) in ({"$object"}, {"$literal"}):
                return {
                    "$literal": [
                        [key, self._pack(item, objects, scope)]
                        for key, item in value.items()
                    ]
                }
            if "dispatch_id" in value:
                scope = "dispatch:" + str(value["dispatch_id"])
            result = {}
            for key, item in value.items():
                if key in INVARIANT_FIELDS and isinstance(item, (dict, list)):
                    cache_key = (scope, key)
                    previous = self._invariant_cache.get(cache_key)
                    if previous is not None and previous[0] == item:
                        digest = previous[1]
                    else:
                        digest = hashlib.sha256(_json(item)).hexdigest()
                        self._invariant_cache[cache_key] = (item, digest)
                        self.metrics["invariant_serializations"] += 1
                    if digest not in self._objects:
                        objects[digest] = item
                    result[key] = {"$object": digest}
                else:
                    result[key] = self._pack(item, objects, scope)
            return result
        if isinstance(value, (list, tuple)):
            return [self._pack(item, objects, scope) for item in value]
        return value

    def _write(self, operation: str, value: dict[str, Any]) -> None:
        if operation == "checkpoint":
            from .training import CheckpointManager

            # The intent remains unsafe even if serialization later hangs or
            # the process dies before atomic checkpoint/result publication.
            self._write(
                "checkpoint-intent",
                {"checkpoint": value["checkpoint"], "safe_requested": value["safe"]},
            )
            CheckpointManager.write_payload(
                value["checkpoint"],
                value["payload"],
                before_publish=self.assert_healthy,
            )
            self.assert_healthy()
            self._write(
                "publish",
                {
                    "state": value["payload"]["hook_state"]["expenditure_ledger"],
                    "checkpoint": value["checkpoint"],
                    "safe": value["safe"],
                },
            )
            return
        if operation == "artifact":
            count = _atomic(
                Path(value["path"]), value["value"], before_publish=self.assert_healthy
            )
            self.metrics["snapshot_writes"] += 1
            self.metrics["snapshot_bytes"] += count
            return
        if operation == "publish":
            state, checkpoint, safe = (
                value["state"],
                Path(value["checkpoint"]),
                value["safe"],
            )
            for directory in self.directories:
                count = _atomic(
                    directory / "expenditure_ledger.json",
                    state,
                    before_publish=self.assert_healthy,
                )
                self.metrics["snapshot_writes"] += 1
                self.metrics["snapshot_bytes"] += count
            operation = "safe-boundary" if safe else "unsafe-boundary"
            value = {
                "checkpoint_sha256": hashlib.sha256(
                    checkpoint.read_bytes()
                ).hexdigest(),
                "checkpoint": str(checkpoint.resolve()),
                "ledger_sha256": hashlib.sha256(_json(state)).hexdigest(),
            }
        self.assert_healthy()
        objects: dict[str, Any] = {}
        packed = self._pack(value, objects)
        record = {
            "protocol": PERSISTENCE_PROTOCOL,
            "seq": self._sequence,
            "previous": self._previous,
            "operation": operation,
            "data": packed,
            "objects": objects,
        }
        digest = hashlib.sha256(_json(record)).hexdigest()
        raw = _json({**record, "sha256": digest}) + b"\n"
        for directory in self.directories:
            path = directory / "accounting_progress.jsonl"
            with path.open("ab") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if self._sequence == 0:
                directory_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            self.metrics["wal_writes"] += 1
            self.metrics["wal_bytes"] += len(raw)
        self._objects.update(objects)
        self._sequence += 1
        self._previous = digest

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            operation, value, future = item
            start = time.perf_counter()
            try:
                if self._failure is not None:
                    raise PersistenceFailure(
                        "prior durable write failed"
                    ) from self._failure
                self._write(operation, value)
                future.set_result(None)
            except BaseException as error:
                self._failure = error
                future.set_exception(
                    PersistenceFailure("ordered accounting write failed")
                )
            finally:
                self.metrics["persistence_seconds"] += time.perf_counter() - start
                self._queue.task_done()

    def submit(
        self, operation: str, value: Mapping[str, Any]
    ) -> concurrent.futures.Future[Any]:
        """Submit an already isolated snapshot; overload is a hard stop, never a dropped event."""
        with self._lock:
            self.assert_healthy()
            future: concurrent.futures.Future[Any] = concurrent.futures.Future()
            try:
                self._queue.put_nowait((operation, value, future))
            except queue.Full as error:
                self._failure = error
                raise PersistenceFailure(
                    "bounded accounting writer capacity exhausted"
                ) from error
            self._last = future
            self.metrics["queue_peak"] = max(
                self.metrics["queue_peak"], self._queue.qsize()
            )
            return future

    def changed(self, header: Mapping[str, Any], entries: list[dict[str, Any]]) -> None:
        with self._lock:
            changed = {
                key: copy.deepcopy(value)
                for key, value in header.items()
                if self._header.get(key) != value
            }
            removed = list(self._header.keys() - header.keys())
            self._header = copy.deepcopy(dict(header))
            self.submit(
                "ledger", {"header": changed, "removed": removed, "entries": entries}
            )

    async def _acknowledge(self, future: concurrent.futures.Future[Any]) -> None:
        from .failure_policy import CALL_TIMING, cancellation_cause

        timing = CALL_TIMING.get()
        phase = timing.get("phase") if timing is not None else None
        if timing is not None:
            timing["phase"] = "accounting-persistence"
        wrapped = asyncio.wrap_future(future)
        # A cancelled waiter does not cancel a durable write; consume its later
        # exception while the journal retains the sticky failure for admission.
        wrapped.add_done_callback(
            lambda done: None if done.cancelled() else done.exception()
        )
        try:
            await asyncio.wait_for(
                asyncio.shield(wrapped), timeout=PERSISTENCE_DEADLINE
            )
            self.assert_healthy()
        except asyncio.CancelledError as error:
            if cancellation_cause(error) not in {
                "call-deadline",
                "session-deadline",
                "transport-timeout",
            }:
                raise
            self._failure = error
            failure = PersistenceFailure(
                "original deadline expired during durable accounting acknowledgement"
            )
            if timing is not None:
                timing["hard_failure"] = failure
            raise failure from error
        except TimeoutError as error:
            self._failure = error
            failure = PersistenceFailure(
                "accounting writer acknowledgement deadline; dispatch stopped"
            )
            if timing is not None:
                timing["hard_failure"] = failure
            raise failure from error
        else:
            if timing is not None:
                timing["phase"] = phase

    async def observation(self, dispatch_id: int, record: Mapping[str, Any]) -> None:
        # Ledger has already isolated this record and interns immutable fields.
        future = self.submit(
            "observation", {"dispatch_id": dispatch_id, "record": record}
        )
        await self._acknowledge(future)

    async def progress(self, kind: str, payload: Any) -> None:
        future = self.submit(
            "progress", {"kind": kind, "payload": copy.deepcopy(payload)}
        )
        await self._acknowledge(future)

    async def flush(self) -> None:
        self.assert_healthy()
        future = self._last
        if future is not None:
            await self._acknowledge(future)
        self.assert_healthy()

    def flush_sync(self) -> None:
        self.assert_healthy()
        if self._last is not None:
            try:
                self._last.result(timeout=PERSISTENCE_DEADLINE)
            except concurrent.futures.TimeoutError as error:
                self._failure = error
                raise PersistenceFailure(
                    "accounting writer acknowledgement deadline"
                ) from error
        self.assert_healthy()

    def publish(
        self, state: Mapping[str, Any], checkpoint: Path, *, safe: bool
    ) -> None:
        self.submit(
            "publish",
            {
                "state": copy.deepcopy(dict(state)),
                "checkpoint": str(checkpoint),
                "safe": safe,
            },
        )
        self.flush_sync()

    async def checkpoint(
        self, payload: Mapping[str, Any], path: Path, *, safe: bool
    ) -> None:
        # capture() already froze tensors, RNG, hooks and numerical state on
        # their owning thread. The writer never consults live trainer state.
        future = self.submit(
            "checkpoint", {"payload": payload, "checkpoint": str(path), "safe": safe}
        )
        await self._acknowledge(future)

    async def artifact(self, path: Path, value: Any) -> None:
        future = self.submit(
            "artifact", {"path": str(path), "value": copy.deepcopy(value)}
        )
        await self._acknowledge(future)

    async def aclose(self) -> None:
        await self.flush()
        self._closed = True
        self._queue.put_nowait(None)


async def write_checkpoint_async(path: Path, payload: Mapping[str, Any]) -> None:
    """Bounded offline/no-ledger checkpoint path; production uses DurableJournal."""
    from .training import CheckpointManager

    abandoned = threading.Event()
    future: concurrent.futures.Future[Any] = concurrent.futures.Future()

    def guard() -> None:
        if abandoned.is_set():
            raise PersistenceFailure("late checkpoint publication refused")

    def work() -> None:
        try:
            CheckpointManager.write_payload(path, payload, before_publish=guard)
            future.set_result(None)
        except BaseException as error:
            future.set_exception(error)

    threading.Thread(
        target=work, name="adaptive-checkpoint-writer", daemon=True
    ).start()
    wrapped = asyncio.wrap_future(future)
    wrapped.add_done_callback(
        lambda done: None if done.cancelled() else done.exception()
    )
    try:
        await asyncio.wait_for(asyncio.shield(wrapped), timeout=PERSISTENCE_DEADLINE)
    except BaseException:
        abandoned.set()
        raise
