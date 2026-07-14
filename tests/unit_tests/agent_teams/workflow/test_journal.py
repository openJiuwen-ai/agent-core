# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Engine-layer tests for the resume journal + crash-durable WAL.

Pure and offline (no backend / LLM): exercise the content-addressed cache, the
program-order serialisation, and the write-ahead-log durability/recovery contract
of ``workflow/engine/journal.py``. The journal's I/O methods (``load`` / ``use`` /
``save`` / ``finalize``) are async (``aiofiles``), so tests drive them through
``asyncio.run`` — the same style the other ``workflow`` engine tests use.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from openjiuwen.agent_teams.workflow.engine.journal import Journal, key_str


def _rec(path: list, sig: str = "s", result=None) -> dict:
    """Build a journal record whose ``key`` is the serialised structural path."""
    ks = key_str(path)
    return {"key": ks, "sig": sig, "kind": "dict", "result": result or {"v": ks}}


def _keys_in_file(path: Path) -> list[str]:
    return [json.loads(line)["key"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def _use_all(j: Journal, paths: list) -> None:
    for p in paths:
        r = _rec(p)
        await j.use(r["key"], r)


# ---------------------------------------------------------------------------
# Program-order serialisation
# ---------------------------------------------------------------------------
def test_save_orders_by_program_order_not_key_string(tmp_path):
    """save() writes records in script execution order, not key lexical order."""
    j = Journal(wal_path=None)
    # Insert in a deliberately scrambled order; save must reorder to program order.
    paths = [
        [["wf", 6, "invite"], ["call", 0]],
        [["call", 0]],
        [["par", 4, 1], ["call", 0]],
        [["pipe", 1, 2, 0], ["call", 0]],
        [["call", 5]],
        [["pipe", 1, 0, 0], ["call", 0]],
        [["par", 4, 0], ["call", 0]],
        [["call", 2]],
    ]
    out = tmp_path / "journal.jsonl"

    async def _run():
        await _use_all(j, paths)
        await j.save(str(out))

    asyncio.run(_run())

    # Ordinal-first ordering: call0 < pipe(block 1) < call2 < par(block 4) < call5 < wf(block 6).
    assert _keys_in_file(out) == [
        key_str([["call", 0]]),
        key_str([["pipe", 1, 0, 0], ["call", 0]]),
        key_str([["pipe", 1, 2, 0], ["call", 0]]),
        key_str([["call", 2]]),
        key_str([["par", 4, 0], ["call", 0]]),
        key_str([["par", 4, 1], ["call", 0]]),
        key_str([["call", 5]]),
        key_str([["wf", 6, "invite"], ["call", 0]]),
    ]


def test_save_is_byte_stable_regardless_of_insertion_order(tmp_path):
    """Two journals with the same records inserted in different orders save identically."""
    paths = [[["call", 0]], [["par", 2, 0], ["call", 0]], [["call", 1]]]
    a, b = Journal(), Journal()
    fa, fb = tmp_path / "a.jsonl", tmp_path / "b.jsonl"

    async def _run():
        await _use_all(a, paths)
        await _use_all(b, list(reversed(paths)))
        await a.save(str(fa))
        await b.save(str(fb))

    asyncio.run(_run())
    assert fa.read_text(encoding="utf-8") == fb.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# WAL durability + recovery
# ---------------------------------------------------------------------------
def test_wal_appends_fresh_records_and_persists_without_save(tmp_path):
    """Fresh records are appended to the WAL immediately; a crash (no save) keeps them."""
    journal = tmp_path / "journal.jsonl"
    wal = tmp_path / "journal.jsonl.wal"

    async def _run():
        j = await Journal.load(str(journal), wal_path=str(wal))
        await _use_all(j, [[["call", 0]], [["call", 1]]])
        # Simulate a process crash: save() is never called.

    asyncio.run(_run())
    assert not journal.exists()
    assert wal.exists()
    assert len(wal.read_text(encoding="utf-8").splitlines()) == 2


def test_load_recovers_from_residual_wal_only(tmp_path):
    """With no journal (or an incomplete one), load() seeds prior from the WAL."""
    journal = tmp_path / "journal.jsonl"  # never written (crash before save)
    wal = tmp_path / "journal.jsonl.wal"

    async def _crash():
        crashed = await Journal.load(str(journal), wal_path=str(wal))
        await _use_all(crashed, [[["call", 0]], [["call", 1]]])

    asyncio.run(_crash())

    recovered = asyncio.run(Journal.load(str(journal), wal_path=str(wal)))
    assert set(recovered.prior.keys()) == {key_str([["call", 0]]), key_str([["call", 1]])}
    # The recovered records are usable as cache hits.
    assert recovered.get_cached(key_str([["call", 0]]), "s") is not None


def test_load_wal_overlays_journal(tmp_path):
    """A residual WAL is newer than the journal and wins on the same key."""
    journal = tmp_path / "journal.jsonl"
    wal = tmp_path / "journal.jsonl.wal"
    ks = key_str([["call", 0]])
    journal.write_text(json.dumps({"key": ks, "sig": "old", "result": {"v": "old"}}) + "\n", encoding="utf-8")
    wal.write_text(json.dumps({"key": ks, "sig": "new", "result": {"v": "new"}}) + "\n", encoding="utf-8")

    j = asyncio.run(Journal.load(str(journal), wal_path=str(wal)))
    assert j.prior[ks]["sig"] == "new"
    assert j.prior[ks]["result"] == {"v": "new"}


def test_cache_hit_is_not_reappended_to_wal(tmp_path):
    """Reusing a prior record (cache hit) does not append to the WAL again."""
    journal = tmp_path / "journal.jsonl"
    wal = tmp_path / "journal.jsonl.wal"
    ks = key_str([["call", 0]])
    wal.write_text(json.dumps({"key": ks, "sig": "s", "result": {"v": "x"}}) + "\n", encoding="utf-8")

    async def _run():
        j = await Journal.load(str(journal), wal_path=str(wal))
        cached = j.get_cached(ks, "s")
        await j.use(ks, cached)  # hit — reuses the prior object

    asyncio.run(_run())
    # Still a single WAL line: the hit was not re-appended.
    assert len(wal.read_text(encoding="utf-8").splitlines()) == 1


def test_finalize_deletes_wal_once_durable(tmp_path):
    """Terminal finalize() writes the journal then drops the WAL (work is durable)."""
    journal = tmp_path / "journal.jsonl"
    wal = tmp_path / "journal.jsonl.wal"

    async def _run():
        j = await Journal.load(str(journal), wal_path=str(wal))
        await _use_all(j, [[["call", 0]], [["call", 1]]])
        assert wal.exists()
        await j.finalize(str(journal))

    asyncio.run(_run())
    assert journal.exists()
    assert len(_keys_in_file(journal)) == 2
    assert not wal.exists()  # deleted after verifying used ⊆ saved journal


def test_save_keeps_wal_for_checkpoint(tmp_path):
    """save() is a pure write: it never deletes the WAL (only finalize() does)."""
    journal = tmp_path / "journal.jsonl"
    wal = tmp_path / "journal.jsonl.wal"

    async def _run():
        j = await Journal.load(str(journal), wal_path=str(wal))
        await _use_all(j, [[["call", 0]], [["call", 1]]])
        await j.save(str(journal))  # a mid-run checkpoint, not terminal

    asyncio.run(_run())
    assert journal.exists()
    assert len(_keys_in_file(journal)) == 2
    assert wal.exists()  # WAL kept — a later crash can still recover the increment


def test_save_is_atomic_no_temp_left(tmp_path):
    """save() writes via temp + os.replace, leaving no stray temp file behind."""
    journal = tmp_path / "journal.jsonl"
    j = Journal()

    async def _run():
        r = _rec([["call", 0]])
        await j.use(r["key"], r)
        await j.save(str(journal))

    asyncio.run(_run())
    assert journal.exists()
    assert not (tmp_path / "journal.jsonl.tmp").exists()


def test_load_tolerates_torn_wal_line(tmp_path):
    """A torn trailing WAL line (crash mid-append) is skipped; good records load."""
    journal = tmp_path / "journal.jsonl"
    wal = tmp_path / "journal.jsonl.wal"
    good = json.dumps({"key": key_str([["call", 0]]), "sig": "s", "result": {"v": "ok"}})
    wal.write_text(good + "\n" + '{"key": "[[\\"call\\", 1]]", "sig": "s", "resu', encoding="utf-8")

    j = asyncio.run(Journal.load(str(journal), wal_path=str(wal)))
    assert set(j.prior.keys()) == {key_str([["call", 0]])}  # good line kept, torn line skipped


def test_finalize_keeps_wal_if_saved_journal_missing_a_used_record(tmp_path):
    """If the saved journal does not reflect a used record, the WAL is kept as a net."""
    journal = tmp_path / "journal.jsonl"
    wal = tmp_path / "journal.jsonl.wal"

    async def _run():
        j = await Journal.load(str(journal), wal_path=str(wal))
        r = _rec([["call", 0]])
        await j.use(r["key"], r)  # appends to the WAL
        assert wal.exists()
        # Simulate a corrupt / partial journal write: file exists but is missing
        # the used record. The WAL must be kept as the safety net.
        partial = tmp_path / "partial.jsonl"
        partial.write_text("", encoding="utf-8")
        await j._discard_wal_if_durable(str(partial))

    asyncio.run(_run())
    assert wal.exists()
