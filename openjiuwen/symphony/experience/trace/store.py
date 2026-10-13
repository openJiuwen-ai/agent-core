"""Persistent store for parsed trace records.

Provides helpers for persisting and loading TraceRecord objects,
as well as a convenience function that parses only new sessions
and stores their traces.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .parser import list_session_ids, parse_session
from ..models import TraceRecord

LOGGER = logging.getLogger(__name__)


def _store_dir(sessions_dir: Path) -> Path:
    """Return the directory where processed traces are stored.

    ``sessions_dir`` is the agent-runtime trace source (one subdirectory per
    session). The processed-record cache lives next to it under ``trace_store``
    so source and parsed cache share a subtree.
    """
    store = sessions_dir.parent / "trace_store"
    store.mkdir(parents=True, exist_ok=True)
    return store


def _processed_index_path(sessions_dir: Path) -> Path:
    """Path to the JSON file tracking which sessions have been processed."""
    return _store_dir(sessions_dir) / "processed_index.json"


def _records_path(sessions_dir: Path) -> Path:
    """Path to the JSONL file holding all stored TraceRecords."""
    return _store_dir(sessions_dir) / "records.jsonl"


def _load_processed_index(sessions_dir: Path) -> set[str]:
    """Load the set of session IDs that have already been processed."""
    path = _processed_index_path(sessions_dir)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("processed_ids", []))
    except Exception:
        LOGGER.warning("Failed to read processed index, starting fresh", exc_info=True)
        return set()


def _save_processed_index(sessions_dir: Path, ids: set[str]) -> None:
    """Save the set of processed session IDs."""
    path = _processed_index_path(sessions_dir)
    data = {"processed_ids": sorted(ids)}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_and_store(sessions_dir: Path) -> list[TraceRecord]:
    """Parse only new (unprocessed) sessions and append their traces to the store.

    ``sessions_dir`` is the agent-runtime trace source (one subdirectory per
    session); the parsed-record cache lives next to it under ``trace_store``.

    Returns the list of newly parsed TraceRecords.

    Idempotency: trace_id is the per-record unique key. Before appending, we
    load the set of trace_ids already in records.jsonl and drop any new
    record whose trace_id is already present. This survives the crash window
    between writing records.jsonl and updating processed_index — a re-run
    would otherwise re-append the same traces and pollute downstream
    clustering/distillation with duplicates.
    """
    all_ids = set(list_session_ids(sessions_dir))
    already_processed = _load_processed_index(sessions_dir)
    new_ids = sorted(all_ids - already_processed)

    if not new_ids:
        LOGGER.info("parse_and_store: no new sessions to process")
        return []

    new_records: list[TraceRecord] = []
    for session_id in new_ids:
        traces = parse_session(session_id, sessions_dir)
        if traces:
            new_records.extend(traces)

    if new_records:
        records_path = _records_path(sessions_dir)
        # Dedup against trace_ids already persisted so a crash-and-rerun
        # cannot append the same record twice.
        existing_ids = _existing_trace_ids(sessions_dir)
        fresh = [r for r in new_records if r.trace_id not in existing_ids]
        if fresh:
            with open(records_path, "a", encoding="utf-8") as f:
                for record in fresh:
                    f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
            LOGGER.info(
                "parse_and_store: stored %d new traces from %d sessions "
                "(%d skipped as duplicates)", len(fresh), len(new_ids),
                len(new_records) - len(fresh),
            )

    # Update processed index AFTER records are on disk. Even if this is
    # interrupted, the dedup above makes the next run idempotent.
    _save_processed_index(sessions_dir, already_processed | set(new_ids))

    return new_records


def _existing_trace_ids(sessions_dir: Path) -> set[str]:
    """Return the set of trace_ids already present in records.jsonl."""
    records_path = _records_path(sessions_dir)
    if not records_path.exists():
        return set()
    ids: set[str] = set()
    try:
        with open(records_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    tid = data.get("trace_id")
                    if tid:
                        ids.add(tid)
                except json.JSONDecodeError:
                    continue
    except OSError:
        LOGGER.warning("parse_and_store: failed to read existing records for dedup", exc_info=True)
    return ids


def load_all_records(sessions_dir: Path) -> list[TraceRecord]:
    """Load all stored TraceRecords from the records.jsonl file."""
    records_path = _records_path(sessions_dir)
    if not records_path.exists():
        return []

    records: list[TraceRecord] = []
    try:
        with open(records_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    records.append(TraceRecord.from_dict(data))
                except json.JSONDecodeError:
                    LOGGER.warning("Skipping malformed record line")
    except Exception:
        LOGGER.warning("Failed to load records", exc_info=True)

    LOGGER.info("load_all_records: loaded %d records", len(records))
    return records


def clear_store(sessions_dir: Path) -> None:
    """Remove all stored traces and the processed index."""
    store = _store_dir(sessions_dir)
    for path in [store / "records.jsonl", store / "processed_index.json"]:
        if path.exists():
            path.unlink()
    LOGGER.info("clear_store: store cleared")


__all__ = ["parse_and_store", "load_all_records", "clear_store"]
