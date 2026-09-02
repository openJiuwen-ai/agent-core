# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tail Codex's opt-in rollout trace without importing Codex internals.

Recent Codex App Server builds write an append-only diagnostic bundle when the
``CODEX_ROLLOUT_TRACE_ROOT`` environment variable is set.  Unlike its generic
OTel spans, rollout events carry stable inference/tool identifiers and payload
references.  This reader keeps that data local, resolves only the payloads
needed by Jiuwen observability, and forwards plain dictionaries to the bridge.
"""

from __future__ import annotations

import atexit
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openjiuwen.core.common.logging import team_logger

_EVENT_LOG = "trace.jsonl"
_ROOT_PREFIX = "openjiuwen-codex-rollout-"
_OWNER_FILE = ".openjiuwen-owner.json"
_MIN_POLL_INTERVAL_S = 0.02
_MAX_POLL_INTERVAL_S = 0.5
_POLL_BACKOFF = 2.0
_STALE_OWNED_ROOT_AGE_S = 60.0
_STALE_UNOWNED_ROOT_AGE_S = 24 * 60 * 60.0
_MAX_PAYLOAD_BYTES = 32 * 1024 * 1024
_PAYLOAD_FIELDS = (
    "request_payload",
    "response_payload",
    "partial_response_payload",
    "invocation_payload",
    "result_payload",
    "metadata_payload",
)


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # On Windows, os.kill(pid, 0) does not reliably behave like POSIX
        # signal 0 (existence check). Use OpenProcess instead to avoid
        # accidentally terminating the host process.
        import ctypes
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _remove_root(root: Path) -> None:
    shutil.rmtree(root, ignore_errors=True)


def _cleanup_stale_roots(
    *,
    base_dir: Path | None = None,
    now: float | None = None,
) -> int:
    """Remove abandoned private trace roots without touching live owners."""
    base = base_dir or Path(tempfile.gettempdir())
    current_time = time.time() if now is None else now
    removed = 0
    for root in base.glob(f"{_ROOT_PREFIX}*"):
        if not root.is_dir():
            continue
        try:
            age_s = max(0.0, current_time - root.stat().st_mtime)
        except OSError:
            continue
        marker = root / _OWNER_FILE
        owner_pid: int | None = None
        try:
            owner = json.loads(marker.read_text(encoding="utf-8"))
            owner_pid = int(owner.get("pid") or 0)
        except (OSError, TypeError, ValueError):
            pass
        if owner_pid is not None and _pid_is_running(owner_pid):
            continue
        minimum_age = _STALE_OWNED_ROOT_AGE_S if owner_pid is not None else _STALE_UNOWNED_ROOT_AGE_S
        if age_s < minimum_age:
            continue
        _remove_root(root)
        if not root.exists():
            removed += 1
    if removed:
        team_logger.info(
            "otel: removed {} abandoned Codex rollout trace root(s)",
            removed,
        )
    return removed


def _load_payload(bundle_dir: Path, reference: Any) -> Any:
    """Read one bundle-local payload reference with path and size checks."""
    if not isinstance(reference, dict):
        return None
    relative_path = reference.get("path")
    if not isinstance(relative_path, str) or not relative_path:
        return None
    try:
        bundle_root = bundle_dir.resolve()
        payload_path = (bundle_dir / relative_path).resolve()
        payload_path.relative_to(bundle_root)
        if payload_path.stat().st_size > _MAX_PAYLOAD_BYTES:
            team_logger.warning(
                "otel: skipped oversized Codex rollout payload path={}",
                payload_path,
            )
            return None
        with payload_path.open(encoding="utf-8") as payload_file:
            return json.load(payload_file)
    except (OSError, ValueError) as exc:
        team_logger.warning(
            "otel: failed to read Codex rollout payload {}: {}",
            relative_path,
            exc,
        )
        return None


def _resolve_event_payloads(event: dict[str, Any], bundle_dir: Path) -> dict[str, Any]:
    """Attach resolved payload values while preserving the raw event."""
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return event
    resolved: dict[str, Any] = {}
    for field_name in _PAYLOAD_FIELDS:
        value = _load_payload(bundle_dir, payload.get(field_name))
        if value is not None:
            resolved[field_name] = value
    enriched = dict(event)
    enriched["bundle_dir"] = str(bundle_dir)
    enriched["resolved_payloads"] = resolved
    return enriched


class CodexRolloutTraceReader:
    """Poll one private rollout root and deliver ordered raw events."""

    def __init__(
        self,
        *,
        root: Path,
        callback: Callable[[dict[str, Any]], None],
    ) -> None:
        self.root = root
        self._callback = callback
        self._offsets: dict[Path, int] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._exit_cleanup: Callable[[], None] | None = None
        self._closed = False

    @classmethod
    async def start(
        cls,
        callback: Callable[[dict[str, Any]], None],
    ) -> CodexRolloutTraceReader:
        """Create an isolated trace root and start its lightweight tailer."""
        _cleanup_stale_roots()
        root = Path(tempfile.mkdtemp(prefix=_ROOT_PREFIX))
        try:
            (root / _OWNER_FILE).write_text(
                json.dumps({"pid": os.getpid(), "created_at": time.time()}),
                encoding="utf-8",
            )
        except OSError:
            _remove_root(root)
            raise
        reader = cls(root=root, callback=callback)

        def cleanup_root() -> None:
            _remove_root(root)

        reader._exit_cleanup = cleanup_root
        atexit.register(reader._exit_cleanup)
        reader._task = asyncio.create_task(
            reader._watch(),
            name="openjiuwen-codex-rollout-reader",
        )
        team_logger.info("otel: Codex rollout trace reader started root={}", root)
        return reader

    async def _watch(self) -> None:
        delay = _MIN_POLL_INTERVAL_S
        while not self._closed:
            events = await asyncio.to_thread(self._collect_once)
            self._deliver(events)
            delay = _MIN_POLL_INTERVAL_S if events else min(_MAX_POLL_INTERVAL_S, delay * _POLL_BACKOFF)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
            except TimeoutError:
                continue
            return

    def _collect_once(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for event_log in sorted(self.root.glob(f"trace-*/{_EVENT_LOG}")):
            events.extend(self._read_appended_lines(event_log))
        return events

    def _deliver(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            try:
                self._callback(event)
            except Exception as exc:  # noqa: BLE001 - tracing is best effort
                team_logger.warning(
                    "otel: Codex rollout callback failed: {}",
                    exc,
                )

    def _poll_once(self) -> int:
        """Synchronously poll once for tests and final shutdown draining."""
        events = self._collect_once()
        self._deliver(events)
        return len(events)

    def _read_appended_lines(self, event_log: Path) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        offset = self._offsets.get(event_log, 0)
        try:
            with event_log.open("rb") as stream:
                stream.seek(offset)
                while True:
                    line_start = stream.tell()
                    line = stream.readline()
                    if not line:
                        break
                    if not line.endswith(b"\n"):
                        stream.seek(line_start)
                        break
                    try:
                        event = json.loads(line)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        team_logger.warning(
                            "otel: ignored invalid Codex rollout event path={} offset={}: {}",
                            event_log,
                            line_start,
                            exc,
                        )
                        continue
                    if isinstance(event, dict):
                        events.append(
                            _resolve_event_payloads(event, event_log.parent),
                        )
                self._offsets[event_log] = stream.tell()
        except OSError as exc:
            team_logger.warning(
                "otel: failed to tail Codex rollout trace {}: {}",
                event_log,
                exc,
            )
        return events

    async def aclose(self) -> None:
        """Consume final events, stop the task, then remove sensitive payloads."""
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        task = self._task
        self._task = None
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        try:
            events = await asyncio.to_thread(self._collect_once)
            self._deliver(events)
        finally:
            cleanup = self._exit_cleanup
            self._exit_cleanup = None
            if cleanup is not None:
                atexit.unregister(cleanup)
            _remove_root(self.root)


__all__ = ["CodexRolloutTraceReader"]
