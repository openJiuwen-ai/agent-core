# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Optional storage for run usage, independently of the fetch history transaction."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from openjiuwen.harness.personal_context.path_safety import assert_existing_chain_is_plain, service_storage_segment


_max_bytes = 256 * 1024
_MAX_PENDING = 32
_QUEUE_TIMEOUT = 0.5
_pending: deque[tuple[float, Callable[[float], None]]] = deque()
_worker: threading.Thread | None = None
_pending_lock = threading.Lock()


def valid_usage_summary(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"total_tokens", "has_estimate", "incomplete"}
        and isinstance(value["has_estimate"], bool)
        and isinstance(value["incomplete"], bool)
        and (
            type(value["total_tokens"]) is int and 0 <= value["total_tokens"] <= 2**53 - 1
            or value["total_tokens"] is None and value["incomplete"] and not value["has_estimate"]
        )
    )


def submit_usage_io(action: Callable[[float], None], *, cleanup: bool = False) -> None:
    """Enqueue optional disk work without waiting on disk, the loop executor or shutdown."""
    global _worker
    if not _pending_lock.acquire(timeout=0.005):
        return
    try:
        now = time.monotonic()
        while _pending and _pending[0][0] <= now:
            _pending.popleft()
        if len(_pending) >= _MAX_PENDING:
            return
        # Deletion must follow a write already in os.replace, even if that
        # write outlives the usual queue budget. It still runs off the caller.
        deadline = float("inf") if cleanup else now + _QUEUE_TIMEOUT
        _pending.append((deadline, action))
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_drain, name="pcs-run-usage", daemon=True)
            _worker.start()
    except Exception:
        _pending.clear()
        _worker = None
    finally:
        _pending_lock.release()


def _drain() -> None:
    global _worker
    while _pending_lock.acquire(timeout=0.05):
        try:
            if not _pending:
                _worker = None
                return
            deadline, action = _pending.popleft()
        finally:
            _pending_lock.release()
        if time.monotonic() < deadline:
            with suppress(Exception):
                action(deadline)


def _usage_path(home: Path, service_id: str) -> Path:
    path = home / "state" / "run-token-usage" / f"{service_storage_segment(service_id)}.json"
    assert_existing_chain_is_plain(path, stop=home)
    return path


def _read_usage(path: Path) -> dict[str, dict[str, object]]:
    try:
        if not path.is_file():
            return {}
        with path.open("rb") as handle:
            raw = handle.read(_max_bytes + 1)
        if len(raw) > _max_bytes:
            return {}
        data = json.loads(raw)
        if data["schema_version"] != 1 or not isinstance(data["runs"], dict) or len(data["runs"]) > 5:
            return {}
        return {key: record for key, record in data["runs"].items()
                if isinstance(record, dict) and valid_usage_summary(record.get("summary"))}
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return {}


def read_usage_summaries(home: Path, service_id: str) -> dict[str, object]:
    return {run_id: record["summary"] for run_id, record in _read_usage(_usage_path(home, service_id)).items()}


def save_run_usage(
    home: Path,
    service_id: str,
    run_id: str,
    retained_ids: tuple[str, ...],
    summary: dict[str, object],
    groups: list[dict[str, object]],
    deadline: float,
) -> None:
    path = _usage_path(home, service_id)
    by_id = {key: value for key, value in _read_usage(path).items() if key in retained_ids}
    by_id[run_id] = {"summary": summary, "groups": groups}
    payload = json.dumps({"schema_version": 1, "runs": by_id}, ensure_ascii=False).encode("utf-8")
    if len(payload) > _max_bytes or time.monotonic() >= deadline:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if time.monotonic() < deadline:
            os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def delete_run_usage(home: Path, service_id: str, deadline: float) -> None:
    path = _usage_path(home, service_id)
    if time.monotonic() < deadline:
        path.unlink(missing_ok=True)
