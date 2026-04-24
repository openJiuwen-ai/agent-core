# -*- coding: utf-8 -*-
"""LSP diagnostic registry — pending queue, dedup, cap, and sort.

Server-push ``textDocument/publishDiagnostics`` notifications arrive
asynchronously.  This module buffers them in a pending queue keyed by UUID,
then delivers a deduplicated, capped, severity-sorted snapshot on each
``get_and_clear()`` call.

Public surface
--------------
- :class:`LspDiagnosticItem`   — one diagnostic entry
- :class:`LspDiagnosticFile`   — all diagnostics for one file URI
- :class:`LspDiagnosticRegistry` — singleton registry
- :func:`_parse_raw`           — parse raw LSP diagnostic dicts (exposed for tests)
- :func:`_diag_key`            — stable dedup key for a :class:`LspDiagnosticItem`
- :data:`MAX_DIAG_PER_FILE`    — default per-file cap
- :data:`MAX_DIAG_TOTAL`       — default global cap
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from openjiuwen.harness.lsp.core.utils.file_uri import file_uri_to_path

MAX_DIAG_PER_FILE: int = 10
MAX_DIAG_TOTAL: int = 30


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class LspDiagnosticItem:
    """A single LSP diagnostic entry."""

    message: str
    severity: int  # 1=Error, 2=Warning, 3=Info, 4=Hint
    range: dict[str, Any]
    source: str | None = None
    code: str | int | None = None


@dataclass
class LspDiagnosticFile:
    """All diagnostics for a single file URI in one delivery batch."""

    uri: str
    diagnostics: list[LspDiagnosticItem] = field(default_factory=list)
    server_name: str = ""
    local_path: str = ""  # resolved filesystem path, e.g. D:\...\test.py


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_raw(raw_list: list[Any]) -> list[LspDiagnosticItem]:
    """Parse a raw LSP ``diagnostics`` array into :class:`LspDiagnosticItem` objects.

    Non-dict entries and entries with an empty/missing ``message`` are dropped.
    """
    items: list[LspDiagnosticItem] = []
    for entry in raw_list:
        if not isinstance(entry, dict):
            continue
        message = entry.get("message", "")
        if not message:
            continue
        severity = entry.get("severity", 3)  # default: Info
        diag_range = entry.get("range", {})
        raw_source = entry.get("source", None)
        source = raw_source if raw_source else None
        code = entry.get("code", None)
        items.append(
            LspDiagnosticItem(
                message=message,
                severity=severity,
                range=diag_range,
                source=source,
                code=code,
            )
        )
    return items


def _diag_key(item: LspDiagnosticItem) -> str:
    """Return a stable dedup key for *item*.

    The key is built from message, severity, range start line/character, and
    code so that two identical diagnostics from different notification batches
    produce the same key.
    """
    start = item.range.get("start", {}) if isinstance(item.range, dict) else {}
    line = start.get("line", 0)
    char = start.get("character", 0)
    return f"{item.message}|{item.severity}|{line}:{char}|{item.code}"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class LspDiagnosticRegistry:
    """Process-global singleton that buffers and delivers LSP diagnostics.

    Thread / async safety
    ---------------------
    All public methods are called from the asyncio event-loop thread only
    (notification handler callbacks run in the read-loop task, and
    ``get_and_clear`` is called from coroutines).  No additional locking is
    therefore needed.

    Deduplication strategy
    ----------------------
    - **Intra-call dedup** (within a single ``get_and_clear``): identical
      diagnostics that arrived in separate notification batches for the same
      URI are merged by key.
    - **Cross-round dedup**: keys already returned by a previous
      ``get_and_clear`` call are suppressed so the caller does not see the
      same diagnostic twice in successive rounds.
    """

    _instance: LspDiagnosticRegistry | None = None

    # ------------------------------------------------------------------
    # Singleton lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> LspDiagnosticRegistry:
        """Return the process-global singleton, creating it if necessary."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Destroy the singleton (primarily for test isolation)."""
        cls._instance = None

    # ------------------------------------------------------------------
    # Instance
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        # UUID → (server_name, uri, [LspDiagnosticItem])
        self._pending: dict[str, tuple[str, str, list[LspDiagnosticItem]]] = {}
        # uri → set(diag_key) — diagnostics already delivered to the caller
        self._delivered: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def pending_count(self) -> int:
        """Number of pending (not yet delivered) notification batches."""
        return len(self._pending)

    def register(
        self,
        server_name: str,
        uri: str,
        raw_diagnostics: list[Any],
    ) -> str:
        """Buffer a ``publishDiagnostics`` notification batch.

        Args:
            server_name:     Identifies the LSP server (e.g. ``"pyright"``).
            uri:             The file URI from the notification.
            raw_diagnostics: The raw ``diagnostics`` array from the LSP params.

        Returns:
            A UUID string that identifies this batch, or ``""`` when the batch
            contained no valid diagnostic entries.
        """
        items = _parse_raw(raw_diagnostics)
        if not items:
            return ""
        batch_id = str(uuid.uuid4())
        self._pending[batch_id] = (server_name, uri, items)
        return batch_id

    def get_and_clear(
        self,
        max_per_file: int = MAX_DIAG_PER_FILE,
        max_total: int = MAX_DIAG_TOTAL,
    ) -> list[LspDiagnosticFile]:
        """Return pending diagnostics and clear the queue.

        Steps applied before returning:

        1. Merge all pending batches per URI.
        2. Intra-call dedup — keep only the first occurrence of each key per URI.
        3. Cross-round dedup — suppress keys already delivered in a prior call.
        4. Sort by severity ascending (Error=1 first).
        5. Per-file cap (``max_per_file``), keeping the most severe entries.
        6. Global cap (``max_total``), applied across all files.

        Returns:
            A list of :class:`LspDiagnosticFile` objects; empty when nothing
            new is pending.
        """
        if not self._pending:
            return []

        # --- Step 1 & 2: merge batches per URI, intra-call dedup ----------
        # uri → {key: LspDiagnosticItem}  (ordered insertion to preserve stability)
        by_uri: dict[str, tuple[str, dict[str, LspDiagnosticItem]]] = {}
        for server_name, uri, items in self._pending.values():
            if uri not in by_uri:
                by_uri[uri] = (server_name, {})
            _, key_map = by_uri[uri]
            for item in items:
                k = _diag_key(item)
                if k not in key_map:
                    key_map[k] = item

        self._pending.clear()

        # --- Step 3: cross-round dedup ------------------------------------
        new_by_uri: dict[str, tuple[str, list[LspDiagnosticItem]]] = {}
        new_keys_by_uri: dict[str, set[str]] = {}

        for uri, (server_name, key_map) in by_uri.items():
            delivered_keys = self._delivered.get(uri, set())
            fresh_items = [
                item for key, item in key_map.items() if key not in delivered_keys
            ]
            if fresh_items:
                new_by_uri[uri] = (server_name, fresh_items)
                new_keys_by_uri[uri] = {_diag_key(i) for i in fresh_items}

        if not new_by_uri:
            return []

        # --- Step 4: sort by severity -------------------------------------
        for uri in new_by_uri:
            server_name, items = new_by_uri[uri]
            new_by_uri[uri] = (server_name, sorted(items, key=lambda d: d.severity))

        # --- Step 5: per-file cap (keep most severe = lowest severity int) -
        for uri in new_by_uri:
            server_name, items = new_by_uri[uri]
            if len(items) > max_per_file:
                new_by_uri[uri] = (server_name, items[:max_per_file])

        # --- Step 6: global cap -------------------------------------------
        result: list[LspDiagnosticFile] = []
        total = 0
        for uri, (server_name, items) in new_by_uri.items():
            if total >= max_total:
                break
            remaining = max_total - total
            clipped = items[:remaining]
            result.append(
                LspDiagnosticFile(
                    uri=uri,
                    diagnostics=clipped,
                    server_name=server_name,
                    local_path=file_uri_to_path(uri),
                )
            )
            total += len(clipped)

        # --- Update delivered history -------------------------------------
        for f in result:
            uri = f.uri
            if uri not in self._delivered:
                self._delivered[uri] = set()
            for item in f.diagnostics:
                self._delivered[uri].add(_diag_key(item))

        return result

    def clear_all(self) -> None:
        """Clear both the pending queue and the delivered-key history."""
        self._pending.clear()
        self._delivered.clear()
