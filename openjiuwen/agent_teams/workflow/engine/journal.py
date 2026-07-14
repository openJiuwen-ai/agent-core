# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resume journal — content-addressed by *structural call path*.

Each ``agent()`` call is keyed by where it sits in the orchestration tree
(``("call", k)`` / ``("par", k, i)`` / ``("pipe", k, i, s)`` / ``("wf", k,
name)``), which is a deterministic, latency-independent function of the script
— unlike a global entry counter, which reorders under ``pipeline`` streaming.
The key answers "did this call's *position* change?"; a SHA-256 of ``prompt +
opts + schema`` answers "did its *content* change?".

Replay is purely content-addressed: a call is a cache **hit** iff its key is in
the prior journal *and* its signature matches. No global "live latch" — the
cascade is automatic, because a downstream prompt that embeds an upstream
result changes its own signature once the upstream re-runs. This is both
simpler and deterministic under concurrency.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Sequence

import aiofiles


def key_str(key: tuple) -> str:
    """Serialise a structural path tuple to a stable string."""
    return json.dumps(key, ensure_ascii=False)


def _program_order(ks: str) -> list[int]:
    """Sort coordinates that order journal records in script execution order.

    Each path segment is ``[kind, ordinal, *sub_indices]`` (``("call", n)`` /
    ``("par", k, branch)`` / ``("pipe", k, item, stage)`` / ``("wf", k, name)``).
    The per-scope ``ordinal`` is assigned sequentially in program order, and the
    sub-indices order children within a ``parallel`` / ``pipeline`` block — so the
    flattened tuple of integer coordinates (kind label dropped, ``name`` skipped)
    sorts records exactly the way the script ran, depth-first. This is
    deterministic (ordinals do not depend on wall-clock or completion timing), so
    the journal stays byte-stable across runs while reading top-to-bottom in
    execution order.
    """
    coords: list[int] = []
    for seg in json.loads(ks):
        for item in seg[1:]:  # drop kind (seg[0]); keep ordinal + integer sub-indices
            if isinstance(item, int):
                coords.append(item)
    return coords


def call_signature(
    prompt: str,
    opts: dict,
    json_schema: dict | None,
    history: Sequence[dict] | None = None,
) -> str:
    """SHA-256 over the call's *content* (prompt + identity opts + schema [+ history]).

    ``history`` participates **only when non-empty**: a stateless ``agent()``
    call (whose history is always empty) yields a byte-identical signature to
    before this parameter existed, so worker resume is unaffected. A stateful
    session turn folds its prior turns in, so a changed upstream turn cascades a
    re-run of every turn that depends on it.
    """
    parts = [
        prompt,
        json.dumps(
            {k: opts.get(k) for k in ("label", "phase", "model")},
            sort_keys=True,
            ensure_ascii=False,
        ),
        json.dumps(json_schema, sort_keys=True, ensure_ascii=False),
    ]
    if history:
        parts.append(json.dumps(list(history), sort_keys=True, ensure_ascii=False))
    blob = "\x00".join(parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class Journal:
    """Content-addressed call cache with a crash-durable write-ahead log (WAL).

    Two on-disk artifacts share one stem (``<journal>`` and ``<journal>.wal``):

    - The **journal** (``<journal>``) is the canonical, program-ordered snapshot.
      :meth:`save` writes it **atomically** (temp file + ``os.replace``) and never
      touches the WAL, so it is crash-safe and safe to call repeatedly (e.g. a
      mid-run checkpoint).
    - The **WAL** (``<journal>.wal``) is an append-only log: every freshly computed
      record is appended the instant it is produced, so a mid-run process crash
      (no chance to commit) still leaves the completed work recoverable.
      :meth:`load` replays a residual WAL over the journal (WAL wins — it is newer)
      and tolerates a torn trailing line (a crash mid-append).

    Invariant — **WAL removal is terminal-only**: only :meth:`finalize` (called
    once, after the workflow fully completes) deletes the WAL, and only after
    verifying the journal durably holds every used record. A mid-run checkpoint
    MUST use :meth:`save` (which keeps the WAL) so a later crash can still recover
    the increment. Never delete the WAL from a non-terminal path.
    """

    def __init__(self, prior: dict[str, dict] | None = None, wal_path: str | None = None) -> None:
        self.prior = prior or {}
        # Records actually used this run (cache-hit -> reused prior; miss -> fresh).
        self.used: dict[str, dict] = {}
        # Append-only WAL path (``<journal>.wal``); None disables durability (e.g.
        # the offline preview path that passes no journal_path).
        self._wal_path = wal_path
        # Serialises WAL appends so concurrent parallel()/pipeline() completions
        # never interleave on disk; held across the aiofiles write, so the event
        # loop stays free while one append runs.
        self._wal_lock = asyncio.Lock()

    @staticmethod
    def _parse_records(text: str) -> "list[dict]":
        """Parse JSONL into records, tolerating a torn trailing line.

        A crash mid WAL-append can leave a partial final record; that line fails to
        parse and is skipped — its call simply re-executes on the next run.
        """
        records: list[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and "key" in rec:
                records.append(rec)
        return records

    @classmethod
    async def load(cls, path: str | None, wal_path: str | None = None) -> "Journal":
        """Load prior records, replaying a residual WAL on top (WAL is newer).

        Reads the canonical journal first, then overlays any leftover WAL from a
        crashed prior run — so if the journal is missing or incomplete, the WAL's
        records still seed ``prior`` (last record wins across both sources). Reads
        are async (``aiofiles``) so they never stall the shared event loop.
        """
        prior: dict[str, dict] = {}
        for src in (path, wal_path):
            if src and Path(src).exists():
                async with aiofiles.open(src, "r", encoding="utf-8") as f:
                    text = await f.read()
                for rec in cls._parse_records(text):
                    prior[rec["key"]] = rec  # last record wins (WAL overlays journal)
        return cls(prior, wal_path=wal_path)

    def get_cached(self, ks: str, sig: str) -> dict | None:
        rec = self.prior.get(ks)
        return rec if rec is not None and rec.get("sig") == sig else None

    async def use(self, ks: str, record: dict) -> None:
        """Record a used result, durably appending FRESH ones to the WAL first.

        A cache hit reuses the very prior object (already durable in the journal /
        old WAL), so only a fresh record is appended — the ``is`` identity check is
        the same one ``hits`` uses. The append is awaited so the WAL is durable
        before the call proceeds.
        """
        if self.prior.get(ks) is not record:
            await self._append_wal(record)
        self.used[ks] = record

    async def _append_wal(self, record: dict) -> None:
        """Durably append one fresh record to the WAL (crash-safe checkpoint).

        Async file I/O (``aiofiles``) keeps the write off the event loop; the lock
        serialises concurrent appends so parallel()/pipeline() completions never
        interleave on disk.
        """
        if not self._wal_path:
            return
        line = json.dumps(record, ensure_ascii=False) + "\n"
        async with self._wal_lock:
            async with aiofiles.open(self._wal_path, "a", encoding="utf-8") as f:
                await f.write(line)
                await f.flush()

    async def save(self, path: str) -> None:
        """Atomically write the program-ordered journal snapshot (does NOT touch the WAL).

        Ordered by structural program order (not key string / completion time): the
        file reads top-to-bottom in execution order yet stays byte-stable across
        runs, since ordinals are deterministic regardless of concurrency timing.

        Crash-safe: writes a temp file (async) then ``os.replace`` (atomic same-dir
        rename — a fast metadata syscall), so a crash mid-write leaves either the
        previous journal or the new one, never a torn file. Pure write with no
        destructive side effect, so it is safe to call repeatedly (e.g. a mid-run
        checkpoint); WAL removal is the separate, terminal-only :meth:`finalize`.
        """
        lines = [json.dumps(self.used[k], ensure_ascii=False) for k in sorted(self.used, key=_program_order)]
        body = ("\n".join(lines) + "\n") if lines else ""
        tmp = f"{path}.tmp"
        async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
            await f.write(body)
            await f.flush()
        os.replace(tmp, path)

    async def finalize(self, path: str) -> None:
        """Terminal commit: snapshot the journal, then drop the WAL once durable.

        Call ONLY when the workflow has fully completed. Deleting the WAL is what
        makes this terminal — a mid-run checkpoint must use :meth:`save` (which
        keeps the WAL) so a later crash can still recover the increment.
        """
        await self.save(path)
        await self._discard_wal_if_durable(path)

    async def _discard_wal_if_durable(self, path: str) -> None:
        """Drop the WAL once the saved journal durably holds every used record.

        Verifies ``used ⊆ saved journal`` by ``(key, sig)`` (not ``WAL ⊆ journal``,
        so stale WAL entries from a since-edited script never block cleanup). On a
        mismatch (e.g. a partial/corrupt write) the WAL is kept as the safety net.
        """
        if not self._wal_path:
            return
        wal = Path(self._wal_path)
        if not wal.exists():
            return
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            text = await f.read()
        saved = {rec["key"]: rec.get("sig") for rec in self._parse_records(text)}
        for ks, record in self.used.items():
            if saved.get(ks) != record.get("sig"):
                return  # saved journal does not yet reflect this record — keep WAL
        wal.unlink()

    # --- stats helpers (for tests / CLI) ---
    @property
    def hits(self) -> int:
        return sum(1 for k, r in self.used.items() if self.prior.get(k) is r)
