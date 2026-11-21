"""Ordered bounded accounting WAL, separate from trainable checkpoints.

Only immutable event copies enter the single writer. A durable acknowledgement
is required before POST/GET; an unfinished WAL cannot certify workflow resume.
The full JSON ledger is a boundary snapshot; replay is read-only and lossless.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import queue
import threading
import time
import sys
from collections import deque
from typing import Any, Mapping

PERSISTENCE_PROTOCOL = "ordered_accounting_progress_v1"
PUBLICATION_PROTOCOL = "compact_accounting_publication_v1"
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
    elif operation in {"ledger", "ledger-delta-v1"}:
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
        for change in value.get("appends", []):
            container = state
            for key in change["path"]:
                container = container[key]
            if not isinstance(container, list) or len(container) != change["expected_length"]:
                raise PersistenceFailure("noncontiguous accounting history append")
            container.extend(copy.deepcopy(change["values"]))
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


def read_journal(directory: str | Path, *, include_audit: bool = True) -> dict[str, Any] | None:
    """Verify every record; optionally retain audit-only historical payloads.

    Startup needs complete committed accounting, tail/boundary integrity and
    immutable-object identities, but not a second retained copy of every
    historical progress payload. Explicit audit readers keep the full default.
    """
    from .storage import current_startup, freeze_json, load_ledger, read_json, strict_json_equal

    directory = Path(directory).resolve()
    path = directory / "accounting_progress.jsonl"
    context = current_startup()
    cache_key = ("journal", path, include_audit)
    if context is not None:
        cached = context.cached(cache_key)
        if cached is not None:
            return cached
    if not path.exists():
        if context is not None:
            context.pin_absence(path)
        return None
    state: dict[str, Any] = {}
    objects: dict[str, Any] = {}
    previous = "0" * 64
    boundary = None
    sequence = 0
    progress = []
    progress_count = 0
    operations: set[str] = set()
    tail_record = None
    stream_scope = path.open("rb") if context is None else context.verifying_stream(path)
    with stream_scope as stream:
        for raw in stream:
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
                record_bytes = _json(item)
                if context is not None:
                    context.metrics["journal_canonical_bytes"] = context.metrics.get("journal_canonical_bytes", 0) + len(record_bytes)
                if hashlib.sha256(record_bytes).hexdigest() != digest:
                    raise ValueError("journal hash mismatch")
                for key, value in item.get("objects", {}).items():
                    object_bytes = _json(value)
                    if context is not None:
                        context.metrics["journal_canonical_bytes"] += len(object_bytes)
                    if hashlib.sha256(object_bytes).hexdigest() != key or (
                        key in objects and objects[key] != value
                    ):
                        raise ValueError("immutable object mismatch")
                    # Journal objects are immutable by protocol. Freeze once;
                    # unpacking/replay then cannot copy their complete metadata
                    # into every later frame while retaining identical values.
                    objects[key] = value if context is None else freeze_json(value)
                value = _unpack(item["data"], objects)
                _apply(state, item["operation"], value)
                operations.add(item["operation"])
                if item["operation"] == "progress":
                    progress_count += 1
                    if include_audit:
                        progress.append({"sequence": sequence, **value})
                boundary = value if item["operation"] == "safe-boundary" else None
                previous = digest
                tail_record = {"sequence": sequence, "operation": item["operation"],
                               "data": value, "sha256": digest}
                sequence += 1
            except (ValueError, KeyError, TypeError, IndexError) as error:
                raise PersistenceFailure("corrupt accounting progress journal") from error
    if not state:
        raise PersistenceFailure("empty accounting progress journal")
    if boundary is not None:
        if "ledger_storage_sha256" in boundary:
            ledger_path = directory / "expenditure_ledger.json"
            raw_ledger = read_json(ledger_path)
            verified_state = load_ledger(ledger_path)
            if (hashlib.sha256(_json(raw_ledger)).hexdigest() != boundary["ledger_storage_sha256"]
                    or not strict_json_equal(verified_state, state)):
                raise PersistenceFailure("safe storage boundary does not match accounting journal")
            # This replacement is after complete value equality. It retains
            # verified receipt identities for later metadata comparison.
            state = verified_state
        elif boundary["ledger_sha256"] != hashlib.sha256(_json(state)).hexdigest():
            raise PersistenceFailure(
                "safe boundary does not match reconstructed accounting state"
            )
    summary = {
        "state": state,
        "boundary": boundary,
        "sequence": sequence,
        "sha256": previous,
        "object_ids": tuple(objects),
        "progress_count": progress_count,
        "operations": sorted(operations),
        "tail_record": tail_record,
    }
    if context is not None:
        context.metrics["journal_replays"] += 1
        frozen_summary = context.remember(("journal", path, False), freeze_json(summary))
        if not include_audit:
            return frozen_summary
        return context.remember(cache_key, freeze_json(
            {**frozen_summary, "objects": objects, "progress": progress}
        ))
    return {**summary, "objects": objects, "progress": progress} if include_audit else summary


class DurableJournal:
    """A bounded single-writer queue; no filesystem work occurs per frame on the loop."""

    def __init__(
        self,
        directories: list[Path],
        state: Mapping[str, Any],
        *,
        allow_empty_initialization: bool = False,
        recovery_token: Mapping[str, Any] | None = None,
    ) -> None:
        self.directories = list(dict.fromkeys(Path(path) for path in directories))
        from .storage import EvidenceStore, current_startup
        self._startup = current_startup()
        self.evidence_store = EvidenceStore(self.directories[0])
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=QUEUE_CAPACITY)
        self._lock = threading.RLock()
        self._failure: BaseException | None = None
        self.evidence_store.publication_guard = self.assert_healthy
        self._last: concurrent.futures.Future[Any] | None = None
        self._closed = False
        self._invariant_cache: dict[tuple[str, str], tuple[Any, str]] = {}
        self._writer_handles: list[Any] = []
        self.metrics = {
            "wal_writes": 0,
            "wal_bytes": 0,
            "snapshot_writes": 0,
            "snapshot_bytes": 0,
            "persistence_seconds": 0.0,
            "queue_peak": 0,
            "invariant_serializations": 0,
            "queue_wait_seconds": 0.0,
            "queue_wait_max_seconds": 0.0,
            "capture_seconds": 0.0,
            "serialization_seconds": 0.0,
            "serialized_bytes": 0,
            "hash_seconds": 0.0,
            "hashed_bytes": 0,
            "write_seconds": 0.0,
            "fsync_seconds": 0.0,
            "ack_delivery_seconds": 0.0,
            "ack_delivery_max_seconds": 0.0,
        }
        self.operation_timings: deque[dict[str, Any]] = deque(maxlen=128)
        self._failure_diagnostic_emitted = False
        # The independent writer lock survives sticky failure until the worker
        # has actually stopped.  A timed-out acknowledgement cannot authorize a
        # second writer while its physical write is still running.
        try:
            for directory in sorted(self.directories):
                handle = (directory / ".accounting_writer.lock").open("a+b")
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    handle.close()
                    raise PersistenceFailure("another accounting writer owns this campaign") from error
                except BaseException:
                    handle.close()
                    raise
                self._writer_handles.append(handle)
            if self._startup is not None:
                # All campaign/writer locks are now held. Reuse requires exact
                # byte freshness, not a filename/mtime or preflight assumption.
                self._startup.assert_fresh()
            existing = [read_journal(path, include_audit=False) for path in self.directories]
            self._initialize_existing(existing, state, allow_empty_initialization,
                                      recovery_token)
        except BaseException:
            self._release_writer_locks()
            raise
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

    def _release_writer_locks(self) -> None:
        for handle in self._writer_handles:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        self._writer_handles.clear()

    def _initialize_existing(self, existing: list[Any], state: Mapping[str, Any],
                             allow_empty_initialization: bool,
                             recovery_token: Mapping[str, Any] | None) -> None:
        if any(item is not None for item in existing):
            if (
                any(item is None for item in existing)
                or len({item["sha256"] for item in existing}) != 1
            ):
                raise PersistenceFailure(
                    "campaign journals diverge; automatic replay disabled"
                )
            saved = existing[0]
            recovery = False
            if recovery_token is not None:
                tail = saved["tail_record"]
                recovery = (
                    recovery_token.get("protocol") == "audited_publication_recovery_v1"
                    and saved["boundary"] is None
                    and saved["sequence"] == recovery_token.get("expected_sequence")
                    and saved["sha256"] == recovery_token.get("expected_previous")
                    and tail["sequence"] == recovery_token.get("expected_intent_sequence")
                    and tail["operation"] == "checkpoint-intent"
                    and tail["data"].get("safe_requested") is True
                    and tail["data"].get("checkpoint") == recovery_token.get("expected_intent_checkpoint")
                    and hashlib.sha256(_json(saved["state"])).hexdigest()
                    == recovery_token.get("expected_state_sha256")
                    and all(isinstance(recovery_token.get(key), str) and len(recovery_token[key]) == 64
                            for key in ("authorization_sha256", "plan_fingerprint",
                                        "parent_checkpoint_sha256", "interrupted_checkpoint_sha256",
                                        "validation_sha256"))
                )
                if recovery_token.get("protocol") == "audited_predispatch_recovery_v1":
                    from ..operations.predispatch_recovery import token_matches
                    recovery = token_matches(saved, recovery_token)
                if not recovery:
                    raise PersistenceFailure("audited publication recovery token does not match journal tail")
            # Only the locked, registered initializer may finish publishing an
            # untouched initial boundary. This never permits workflow recovery.
            empty_initialization = (
                allow_empty_initialization
                and not state.get("entries")
                and not state.get("halted")
                and not state.get("failure_policy")
                and not state.get("scopes")
                and not saved["progress_count"]
                and set(saved["operations"])
                <= {
                    "initialize",
                    "checkpoint-intent",
                    "safe-boundary",
                    "unsafe-boundary",
                }
            )
            from .storage import strict_json_equal
            if (saved["boundary"] is None and not empty_initialization and not recovery) or not strict_json_equal(
                saved["state"], state
            ):
                raise PersistenceFailure(
                    "unfinished workflow journal cannot be rebound"
                )
            self._sequence, self._previous, self._objects = (
                saved["sequence"],
                saved["sha256"],
                set(saved["object_ids"]),
            )
        else:
            self._sequence, self._previous, self._objects = 0, "0" * 64, set()

    def assert_healthy(self) -> None:
        if self._failure is not None:
            raise PersistenceFailure(
                "accounting persistence failed; new dispatch is prohibited"
            ) from self._failure
        if self._closed:
            raise PersistenceFailure("accounting writer is closed")

    def failure_diagnostics(self) -> dict[str, Any]:
        """Bounded, non-authoritative diagnostics; never a completion receipt."""
        return {"protocol": "writer_failure_diagnostics_v1", "resume_safe": False,
                "stage_complete": False, "authoritative_accounting": False,
                "failure_type": type(self._failure).__name__ if self._failure else None,
                "metrics": dict(self.metrics), "recent_operations": list(self.operation_timings),
                "writer_alive": self._thread.is_alive(), "queue_size": self._queue.qsize()}

    def _record_failure(self, error: BaseException) -> None:
        if self._failure is None:
            self._failure = error
        if not self._failure_diagnostic_emitted:
            self._failure_diagnostic_emitted = True
            try:
                # stderr is already owned by the guarded launch. Reporting does
                # not open another accounting writer or certify a stale result.
                sys.stderr.write(_json(self.failure_diagnostics()).decode() + "\n")
                sys.stderr.flush()
            except Exception:
                # Diagnostic I/O cannot undo the sticky accounting failure.
                pass

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

    def _write(self, operation: str, value: dict[str, Any]) -> Any:
        if self._startup is not None and operation != "prepare-checkpoint-state":
            self._startup.invalidate()
            self._startup = None
        expected = value.get("expected_snapshot")
        if expected is not None and expected != {"sequence": self._sequence, "sha256": self._previous}:
            raise PersistenceFailure("accounting changed after checkpoint snapshot; publication refused")
        if operation == "prepare-checkpoint-state":
            # A boundary caller has stopped mutable workflow work.  Evidence
            # registration shares the ordered bounded writer, never an unbound
            # fire-and-forget thread or event-loop serialization path.
            at = time.perf_counter()
            prepared = value["factory"]()
            self.metrics["capture_seconds"] += time.perf_counter() - at
            self.assert_healthy()
            if value.get("bind_publication"):
                return prepared, {"sequence": self._sequence, "sha256": self._previous}
            return prepared
        if operation in {"checkpoint", "checkpoint-amendment"}:
            from ..learning.training import CheckpointManager
            from .storage import COMPACT_PROTOCOL

            # The intent remains unsafe even if serialization later hangs or
            # the process dies before atomic checkpoint/result publication.
            self._write(
                "checkpoint-intent",
                {"checkpoint": value["checkpoint"], "safe_requested": value["safe"],
                 **({"recovery_fingerprint": value["recovery_fingerprint"]}
                    if value.get("recovery_fingerprint") else {})},
            )
            payload = value["payload"]
            if callable(payload):
                raise PersistenceFailure("checkpoint payload must be prepared before its ordered commit")
            CheckpointManager.write_payload(
                value["checkpoint"],
                ({**payload, "storage_protocol": payload.get("storage_protocol", COMPACT_PROTOCOL),
                  "storage_root": str(self.evidence_store.directory)}
                 if operation == "checkpoint" else payload),
                before_publish=self.assert_healthy,
            )
            self.assert_healthy()
            self._write(
                "publish",
                {
                    "state": (value["ledger_state"] if operation == "checkpoint-amendment"
                              else payload["hook_state"]["expenditure_ledger"]),
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
            from .storage import ledger_envelope

            state, checkpoint, safe = (
                value["state"],
                Path(value["checkpoint"]),
                value["safe"],
            )
            # Compact evidence has already been made immutable and durable by
            # its owner.  A boundary serializes only references and current
            # counters, never re-hashes/re-writes all historical receipts.
            stored_state = ledger_envelope(state, evidence_root=self.evidence_store.directory)
            for directory in self.directories:
                count = _atomic(
                    directory / "expenditure_ledger.json",
                    stored_state,
                    before_publish=self.assert_healthy,
                )
                self.metrics["snapshot_writes"] += 1
                self.metrics["snapshot_bytes"] += count
            operation = "safe-boundary" if safe else "unsafe-boundary"
            value = {
                "publication_protocol": PUBLICATION_PROTOCOL,
                "checkpoint_sha256": hashlib.sha256(
                    checkpoint.read_bytes()
                ).hexdigest(),
                "checkpoint": str(checkpoint.resolve()),
                "ledger_storage_sha256": hashlib.sha256(_json(stored_state)).hexdigest(),
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
        at = time.perf_counter()
        canonical = _json(record)
        self.metrics["serialization_seconds"] += time.perf_counter() - at
        self.metrics["serialized_bytes"] += len(canonical)
        at = time.perf_counter()
        digest = hashlib.sha256(canonical).hexdigest()
        self.metrics["hash_seconds"] += time.perf_counter() - at
        self.metrics["hashed_bytes"] += len(canonical)
        # The digest covers the same canonical object as before. Field order
        # of the enclosing JSON is immaterial; avoid serializing its audit
        # payload twice merely to append the computed hash.
        raw = canonical[:-1] + b',"sha256":"' + digest.encode("ascii") + b'"}\n'
        for directory in self.directories:
            path = directory / "accounting_progress.jsonl"
            at = time.perf_counter()
            with path.open("ab") as stream:
                stream.write(raw)
                stream.flush()
                self.metrics["write_seconds"] += time.perf_counter() - at
                at = time.perf_counter()
                os.fsync(stream.fileno())
                self.metrics["fsync_seconds"] += time.perf_counter() - at
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
                self._release_writer_locks()
                return
            operation, value, future = item
            start = time.perf_counter()
            queued = getattr(future, "_journal_submitted_at", start)
            wait = start - queued
            self.metrics["queue_wait_seconds"] += wait
            self.metrics["queue_wait_max_seconds"] = max(self.metrics["queue_wait_max_seconds"], wait)
            sequence = self._sequence
            try:
                if self._failure is not None:
                    raise PersistenceFailure(
                        "prior durable write failed"
                    ) from self._failure
                result = self._write(operation, value)
                future._journal_completed_at = time.perf_counter()
                future.set_result(result)
            except BaseException as error:
                self._record_failure(error)
                future._journal_completed_at = time.perf_counter()
                future.set_exception(
                    PersistenceFailure("ordered accounting write failed")
                )
            finally:
                self.metrics["persistence_seconds"] += time.perf_counter() - start
                self.operation_timings.append({
                    "operation": operation, "sequence": sequence,
                    "progress_kind": value.get("kind"),
                    "dispatch_id": value.get("dispatch_id"),
                    "dispatch_ids": [entry["dispatch_id"] for entry in value.get("entries", [])],
                    "logical_request_ids": [entry.get("provider_logical_request_id")
                                            for entry in value.get("entries", [])],
                    "query_id": value.get("payload", {}).get("example_id")
                        if isinstance(value.get("payload"), dict) else None,
                    "queue_wait_seconds": wait,
                    "processing_seconds": time.perf_counter() - start,
                    "failed": self._failure is not None,
                })
                self._queue.task_done()
            if self._closed and self._queue.empty():
                self._release_writer_locks()
                return

    def submit(
        self, operation: str, value: Mapping[str, Any]
    ) -> concurrent.futures.Future[Any]:
        """Submit an already isolated snapshot; overload is a hard stop, never a dropped event."""
        with self._lock:
            self.assert_healthy()
            future: concurrent.futures.Future[Any] = concurrent.futures.Future()
            future._journal_submitted_at = time.perf_counter()
            try:
                self._queue.put_nowait((operation, value, future))
            except queue.Full as error:
                self._record_failure(error)
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

    def changed_delta(self, header_delta: Mapping[str, Any], removed: list[str],
                      appends: list[dict[str, Any]], entries: list[dict[str, Any]]) -> None:
        """Submit already-isolated changed fields and append-only suffixes."""
        # A legacy reader must reject rather than silently ignore the new
        # append semantics while a workflow has no safe boundary yet.
        self.submit("ledger-delta-v1", {"header": dict(header_delta), "removed": removed,
                                        "appends": appends, "entries": entries})

    async def _acknowledge(self, future: concurrent.futures.Future[Any]) -> Any:
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
            # Queue residence and capture belong to the same administrative
            # allowance. A delayed event loop must not renew it or certify a
            # late delivery merely because the underlying future is done.
            started = getattr(future, "_journal_submitted_at", time.perf_counter())
            acknowledged = getattr(future, "_journal_acknowledged", False)
            remaining = PERSISTENCE_DEADLINE if acknowledged else (
                PERSISTENCE_DEADLINE - (time.perf_counter() - started))
            if remaining <= 0:
                raise TimeoutError("durable acknowledgement delivery expired")
            result = await asyncio.wait_for(
                asyncio.shield(wrapped), timeout=remaining
            )
            delivered = time.perf_counter()
            if not acknowledged and delivered - started > PERSISTENCE_DEADLINE:
                raise TimeoutError("durable acknowledgement delivery expired")
            future._journal_acknowledged = True
            completed = getattr(future, "_journal_completed_at", delivered)
            delay = delivered - completed
            self.metrics["ack_delivery_seconds"] += delay
            self.metrics["ack_delivery_max_seconds"] = max(self.metrics["ack_delivery_max_seconds"], delay)
            self.assert_healthy()
        except asyncio.CancelledError as error:
            if cancellation_cause(error) not in {
                "call-deadline",
                "session-deadline",
                "transport-timeout",
            }:
                raise
            self._record_failure(error)
            failure = PersistenceFailure(
                "original deadline expired during durable accounting acknowledgement"
            )
            if timing is not None:
                timing["hard_failure"] = failure
            raise failure from error
        except TimeoutError as error:
            self._record_failure(error)
            failure = PersistenceFailure(
                "accounting writer acknowledgement deadline; dispatch stopped"
            )
            if timing is not None:
                timing["hard_failure"] = failure
            raise failure from error
        else:
            if timing is not None:
                timing["phase"] = phase
            return result

    async def observation(self, dispatch_id: int, record: Mapping[str, Any]) -> None:
        # Ledger has already isolated this record and interns immutable fields.
        future = self.submit(
            "observation", {"dispatch_id": dispatch_id, "record": record}
        )
        await self._acknowledge(future)

    async def progress(self, kind: str, payload: Any) -> None:
        at = time.perf_counter()
        isolated = copy.deepcopy(payload)
        self.metrics["capture_seconds"] += time.perf_counter() - at
        future = self.submit(
            "progress", {"kind": kind, "payload": isolated}
        )
        future._journal_submitted_at = at
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
                future = self._last
                started = getattr(future, "_journal_submitted_at", time.perf_counter())
                acknowledged = getattr(future, "_journal_acknowledged", False)
                remaining = PERSISTENCE_DEADLINE if acknowledged else (
                    PERSISTENCE_DEADLINE - (time.perf_counter() - started))
                if remaining <= 0:
                    raise concurrent.futures.TimeoutError()
                future.result(timeout=remaining)
                if not acknowledged and time.perf_counter() - started > PERSISTENCE_DEADLINE:
                    raise concurrent.futures.TimeoutError()
                future._journal_acknowledged = True
            except concurrent.futures.TimeoutError as error:
                self._record_failure(error)
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
        self, payload: Mapping[str, Any], path: Path, *, safe: bool,
        expected_snapshot: Mapping[str, Any] | None = None,
    ) -> None:
        # capture() already froze tensors, RNG, hooks and numerical state on
        # their owning thread. The writer never consults live trainer state.
        future = self.submit(
            "checkpoint", {"payload": payload, "checkpoint": str(path), "safe": safe,
                           "expected_snapshot": expected_snapshot}
        )
        await self._acknowledge(future)

    async def prepare_checkpoint_state(self, factory: Any, *, bind_publication: bool = False) -> Any:
        future = self.submit("prepare-checkpoint-state", {
            "factory": factory, "bind_publication": bind_publication})
        return await self._acknowledge(future)

    async def amendment(self, payload: Mapping[str, Any], path: Path,
                        ledger_state: Mapping[str, Any], *, safe: bool,
                        recovery_fingerprint: str) -> None:
        """Publish a verified parent-bound policy-only checkpoint, then commit."""
        future = self.submit("checkpoint-amendment", {
            "payload": payload, "checkpoint": str(path), "safe": safe,
            "ledger_state": ledger_state, "recovery_fingerprint": recovery_fingerprint,
        })
        await self._acknowledge(future)

    async def artifact(self, path: Path, value: Any, *,
                       expected_snapshot: Mapping[str, Any] | None = None) -> None:
        future = self.submit(
            "artifact", {"path": str(path), "value": copy.deepcopy(value),
                         "expected_snapshot": expected_snapshot}
        )
        await self._acknowledge(future)

    async def aclose(self) -> None:
        try:
            await self.flush()
        finally:
            # If a failed write is still executing, the sentinel follows it;
            # locks remain owned until it actually exits the worker.
            self._closed = True
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                # The worker owns all already queued commits and will release
                # its locks only after those terminal failures are drained.
                pass
            # Completion means this writer has actually relinquished ownership,
            # not just that its sentinel was queued.  A slow abandoned write
            # still retains the lock and fails closed after the same allowance.
            await asyncio.to_thread(self._thread.join, PERSISTENCE_DEADLINE)
            if self._thread.is_alive():
                raise PersistenceFailure("accounting writer did not stop within cleanup deadline")


async def write_checkpoint_async(path: Path, payload: Mapping[str, Any]) -> None:
    """Bounded offline/no-ledger checkpoint path; production uses DurableJournal."""
    from ..learning.training import CheckpointManager

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


def copy_exact(source: str | Path, target: Path) -> None:
    with target.open("xb") as stream:
        stream.write(Path(source).read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
