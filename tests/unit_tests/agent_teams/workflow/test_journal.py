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
import hashlib
import json
from pathlib import Path

from openjiuwen.agent_teams.workflow.engine.journal import Journal, call_signature, key_str


def _rec(path: list, sig: str = "s", result=None, run_id: str | None = None) -> dict:
    """Build a journal record whose ``key`` is the serialised structural path."""
    ks = key_str(path)
    return {"key": ks, "sig": sig, "run_id": run_id, "kind": "dict", "result": result or {"v": ks}}


def _keys_in_file(path: Path) -> list[str]:
    return [json.loads(line)["key"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def _use_all(j: Journal, paths: list, run_id: str | None = None) -> None:
    for p in paths:
        r = _rec(p, run_id=run_id)
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


def test_finalize_keeps_wal_forever(tmp_path):
    """Terminal finalize() writes the journal and NEVER deletes the WAL.

    The WAL is an append-only log: like any log it ages out (whole-tree
    delete_team sweep / future rolling policy), never a per-run unlink. A
    finalize deleting it would also clobber a concurrent sibling run's
    records sharing the legacy sidecar file.
    """
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
    assert wal.exists()  # WAL survives finalize — it is a log, not a temp file
    assert len(wal.read_text(encoding="utf-8").splitlines()) == 2


def test_save_keeps_wal_for_checkpoint(tmp_path):
    """save() is a pure write: it never deletes the WAL (nothing does)."""
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
    # The WAL is left byte-identical: load never rewrites the log (no compaction).
    assert wal.read_text(encoding="utf-8") == good + "\n" + '{"key": "[[\\"call\\", 1]]", "sig": "s", "resu'


def test_load_leaves_wal_untouched_with_sealed_runs(tmp_path):
    """Sealed-run call records in the WAL are kept — the WAL is never compacted.

    Compaction (dropping sealed runs' call records on load) was removed: a
    shared-file rewrite races a concurrent sibling run's appends, and the
    per-run_id WAL split makes cross-run dead records impossible anyway — each
    run's WAL only ever holds its own records.
    """
    journal = tmp_path / "journal.jsonl"
    wal = tmp_path / "journal.jsonl.wal"

    async def _build():
        j = await Journal.load(str(journal), wal_path=str(wal))
        await _use_all(j, [[["call", 0]]], run_id="run-A")  # run-A computes one call
        await j.write_run_record("run-A", "seal", {"terminal_status": "completed"})
        # run-B paused mid-run: its records must survive too
        await _use_all(j, [[["call", 1]]], run_id="run-B")
        await j.write_run_record("run-B", "pause", {"pause_reason": "paused"})
        # No save/finalize — everything lives in the WAL only.

    asyncio.run(_build())
    before = wal.read_text(encoding="utf-8")

    loaded = asyncio.run(Journal.load(str(journal), wal_path=str(wal)))
    assert wal.read_text(encoding="utf-8") == before  # byte-identical: no rewrite
    assert loaded.find_run_record("run-A", "seal") is not None
    assert loaded.find_run_record("run-B", "pause") is not None
    # Sealed records stay unusable as cross-run hits (run_id isolation), and
    # unsealed ones still recover for their own run's resume.
    assert loaded.get_cached(key_str([["call", 0]]), "s", "run-new") is None
    assert loaded.get_cached(key_str([["call", 1]]), "s", "run-B") is not None


# ---------------------------------------------------------------------------
# run_id isolation — cache hit requires sig AND run_id match
# ---------------------------------------------------------------------------

def _rec_with_run_id(path: list, sig: str, run_id: str, result=None) -> dict:
    """Record carrying an explicit ``run_id`` field (the new isolation key)."""
    ks = key_str(path)
    return {"key": ks, "sig": sig, "run_id": run_id, "kind": "dict", "result": result or {"v": ks}}


def test_get_cached_requires_run_id_match():
    """Same key + sig but different run_id → cache miss (no cross-run bleed)."""
    j = Journal()
    rec_a = _rec_with_run_id([["call", 0]], "s", "run-A")
    j.prior[rec_a["key"]] = rec_a
    # Run B reuses the same key + sig — must NOT hit A's record.
    assert j.get_cached(rec_a["key"], "s", "run-B") is None
    # Same run_id hits.
    assert j.get_cached(rec_a["key"], "s", "run-A") is rec_a


def test_get_cached_old_record_without_run_id_naturally_misses():
    """A legacy record (no run_id field) does not match a run with a run_id set."""
    j = Journal()
    legacy = _rec([["call", 0]])  # no run_id field
    j.prior[legacy["key"]] = legacy
    # New run carries run_id → legacy (run_id=None) must not hit.
    assert j.get_cached(legacy["key"], "s", "run-new") is None


def test_get_cached_run_id_optional_backcompat():
    """When run_id is None (caller omits it), the old sig-only behaviour holds."""
    j = Journal()
    rec = _rec([["call", 0]])  # no run_id field
    j.prior[rec["key"]] = rec
    # Caller passes no run_id (or None) → sig-only match, back-compat path.
    assert j.get_cached(rec["key"], "s") is rec
    assert j.get_cached(rec["key"], "s", None) is rec


# ---------------------------------------------------------------------------
# call_signature isolation folding
# ---------------------------------------------------------------------------

def test_call_signature_byte_stable_without_isolation():
    """No isolation → the exact legacy byte sequence (existing caches stay valid).

    The reference is the pre-change formula spelled out inline — a three-key
    identity dict (phase/model default to None) over the same parts — so any
    accidental re-key of the legacy path fails loudly here.
    """
    legacy = hashlib.sha256(
        "\x00".join(
            [
                "task A",
                json.dumps(
                    {"label": "A", "model": None, "phase": None},
                    sort_keys=True,
                    ensure_ascii=False,
                ),
                json.dumps(None, sort_keys=True, ensure_ascii=False),
            ]
        ).encode("utf-8")
    ).hexdigest()
    assert call_signature("task A", {"label": "A"}, None) == legacy


def test_call_signature_folds_isolation_when_set():
    """isolation='worktree' re-keys the call; different isolation values differ too."""
    plain = call_signature("task A", {"label": "A"}, None)
    iso = call_signature("task A", {"label": "A", "isolation": "worktree"}, None)
    assert iso != plain
    # Explicit None is the same as absent (option bag strips None before opts).
    assert call_signature("task A", {"label": "A", "isolation": None}, None) == plain


# ---------------------------------------------------------------------------
# run-level pause/seal records — written to journal, recovered by run_id
# ---------------------------------------------------------------------------

async def _write_pause(j: Journal, run_id: str, spent: int, phase_tokens: dict) -> None:
    await j.write_run_record(run_id, "pause", {
        "spent": spent, "phase_tokens": phase_tokens,
        "budget_snapshot": None, "pause_reason": "paused",
    })


def test_write_run_record_persists_pause_and_finds_by_run_id():
    """A pause record is written to the journal and found by run_id + type."""
    j = Journal()
    asyncio.run(_write_pause(j, "run-A", spent=1000, phase_tokens={"p1": 1000}))
    rec = j.find_run_record("run-A", "pause")
    assert rec is not None
    assert rec["spent"] == 1000
    assert rec["phase_tokens"] == {"p1": 1000}
    assert rec["pause_reason"] == "paused"


def test_find_run_record_misses_other_run_id():
    """find_run_record returns None for a run_id with no such record."""
    j = Journal()
    asyncio.run(_write_pause(j, "run-A", spent=1000, phase_tokens={}))
    assert j.find_run_record("run-B", "pause") is None
    # seal not written for A either
    assert j.find_run_record("run-A", "seal") is None


def test_pause_record_survives_save_and_reload(tmp_path):
    """A pause record round-trips through save → load (find_run_record recovers)."""
    journal = tmp_path / "journal.jsonl"
    wal = tmp_path / "journal.jsonl.wal"

    async def _write():
        j = await Journal.load(str(journal), wal_path=str(wal))
        await _use_all(j, [[["call", 0]]])  # a call record
        await _write_pause(j, "run-A", spent=500, phase_tokens={"p": 500})
        await j.save(str(journal))

    asyncio.run(_write())

    loaded = asyncio.run(Journal.load(str(journal), wal_path=str(wal)))
    rec = loaded.find_run_record("run-A", "pause")
    assert rec is not None
    assert rec["spent"] == 500
    # call record also recovered
    assert loaded.get_cached(key_str([["call", 0]]), "s") is not None


def test_seal_record_written_on_terminal():
    """A seal record is written and findable — relaunch detects terminal via this."""
    j = Journal()
    asyncio.run(j.write_run_record("run-A", "seal", {"terminal_status": "completed", "final_spent": 2000}))
    rec = j.find_run_record("run-A", "seal")
    assert rec is not None
    assert rec["terminal_status"] == "completed"
    assert rec["final_spent"] == 2000


# ---------------------------------------------------------------------------
# per-run journal + WAL isolation (concurrent runs never share files)
# ---------------------------------------------------------------------------

def test_two_journals_on_separate_wal_files_never_interfere(tmp_path):
    """Two runs with per-run WAL paths append/finalize without clobbering each other.

    This is the concurrency-race fix at the unit level: run B's finalize (and
    any append) touches only ``wal/{run-B}.wal``; run A's WAL file stays whole,
    so a crash of A after B completed still recovers A's records.
    """
    ja = tmp_path / "journal-run-A.jsonl"
    jb = tmp_path / "journal-run-B.jsonl"
    wa = tmp_path / "wal" / "run-A.wal"
    wb = tmp_path / "wal" / "run-B.wal"
    wa.parent.mkdir(parents=True, exist_ok=True)
    wb.parent.mkdir(parents=True, exist_ok=True)

    async def _run_a():
        j = await Journal.load(str(ja), wal_path=str(wa))
        await _use_all(j, [[["call", 0]]], run_id="run-A")

    async def _run_b():
        j = await Journal.load(str(jb), wal_path=str(wb))
        await _use_all(j, [[["call", 0]], [["call", 1]]], run_id="run-B")
        # Run B completes and finalizes while run A is still in flight.
        await j.finalize(str(jb))

    asyncio.run(_run_b())
    asyncio.run(_run_a())

    # Run A's WAL survived run B's finalize — the crash-durability guarantee
    # holds under concurrency.
    assert wa.exists()
    assert len(wa.read_text(encoding="utf-8").splitlines()) == 1
    # Run B's journal snapshot exists; its WAL also survives (never deleted).
    assert jb.exists()
    assert len(_keys_in_file(jb)) == 2
    assert wb.exists()
    # Run A's records recover from its own WAL alone.
    rec_a = asyncio.run(Journal.load(str(ja), wal_path=str(wa)))
    assert rec_a.get_cached(key_str([["call", 0]]), "s", "run-A") is not None
    assert rec_a.get_cached(key_str([["call", 1]]), "s", "run-A") is None



# ---------------------------------------------------------------------------
# legacy shared-journal read-side back-compat (pre-per-run sessions)
# ---------------------------------------------------------------------------

def test_load_seeds_prior_from_legacy_shared_files(tmp_path):
    """A per-run resume still replays records from the pre-split shared journal.

    Sessions created before the per-run split kept everything in one shared
    ``journal.jsonl`` (+ ``.wal`` sidecar). The upgraded run reads those as a
    seed under the per-run sources: same-key conflicts resolve per-run-first,
    and records of a *different* run_id load into prior but naturally miss
    get_cached's triple check.
    """
    legacy_journal = tmp_path / "journal.jsonl"
    legacy_wal = tmp_path / "journal.jsonl.wal"
    run_journal = tmp_path / "journal-run-A.jsonl"
    run_wal = tmp_path / "wal" / "run-A.wal"
    run_wal.parent.mkdir(parents=True)

    async def _seed():
        # Legacy shared files: run-A records + a foreign run-B record + a seal
        # for run-A (old layout also kept seal records in the shared WAL).
        jl = Journal(wal_path=None)
        await _use_all(jl, [[["call", 0]], [["call", 1]]], run_id="run-A")
        await jl.save(str(legacy_journal))
        lw = Journal(wal_path=str(legacy_wal))
        await _use_all(lw, [[["call", 2]]], run_id="run-B")
        await lw.write_run_record("run-A", "seal", {"terminal_status": "completed"})

    asyncio.run(_seed())
    assert not run_journal.exists() and not run_wal.exists()

    loaded = asyncio.run(
        Journal.load(
            str(run_journal),
            wal_path=str(run_wal),
            legacy_path=str(legacy_journal),
        )
    )
    # run-A call records from the legacy shared journal are replayable.
    assert loaded.get_cached(key_str([["call", 0]]), "s", "run-A") is not None
    assert loaded.get_cached(key_str([["call", 1]]), "s", "run-A") is not None
    # The legacy WAL sidecar seeds too — the foreign run-B record is visible
    # to its own run_id but never serves a run-A query (triple check).
    assert loaded.get_cached(key_str([["call", 2]]), "s", "run-B") is not None
    assert loaded.get_cached(key_str([["call", 2]]), "s", "run-A") is None
    # The seal of run-A is found through the legacy path (seal-guard back-compat).
    assert loaded.find_run_record("run-A", "seal") is not None


def test_load_per_run_sources_win_over_legacy_on_key_conflict(tmp_path):
    """Per-run journal/WAL records overlay the legacy seed on key conflicts."""
    legacy_journal = tmp_path / "journal.jsonl"
    run_journal = tmp_path / "journal-run-A.jsonl"
    run_wal = tmp_path / "wal" / "run-A.wal"
    run_wal.parent.mkdir(parents=True)

    async def _seed():
        jl = Journal(wal_path=None)
        await _use_all(jl, [[["call", 0]]], run_id="run-A")
        await jl.save(str(legacy_journal))
        # The per-run snapshot exists with a NEWER sig for the same key.
        rj = Journal(wal_path=None)
        await _use_all(rj, [[["call", 0]]], run_id="run-A")
        await rj.save(str(run_journal))

    asyncio.run(_seed())

    loaded = asyncio.run(
        Journal.load(
            str(run_journal),
            wal_path=str(run_wal),
            legacy_path=str(legacy_journal),
        )
    )
    assert loaded.get_cached(key_str([["call", 0]]), "s", "run-A") is not None
    # legacy-only file is never written by the per-run journal (frozen read-only).
    assert legacy_journal.read_text(encoding="utf-8").count('"key"') == 1


def test_new_records_go_to_per_run_wal_not_legacy(tmp_path):
    """After a legacy-seeded load, fresh records append to the per-run WAL only."""
    legacy_journal = tmp_path / "journal.jsonl"
    legacy_wal = tmp_path / "journal.jsonl.wal"
    run_journal = tmp_path / "journal-run-A.jsonl"
    run_wal = tmp_path / "wal" / "run-A.wal"
    run_wal.parent.mkdir(parents=True)

    async def _seed_and_extend():
        jl = Journal(wal_path=None)
        await _use_all(jl, [[["call", 0]]], run_id="run-A")
        await jl.save(str(legacy_journal))
        j = await Journal.load(
            str(run_journal), wal_path=str(run_wal), legacy_path=str(legacy_journal)
        )
        assert j.get_cached(key_str([["call", 0]]), "s", "run-A") is not None  # HIT
        await _use_all(j, [[["call", 1]]], run_id="run-A")  # fresh record
        await j.save(str(run_journal))

    asyncio.run(_seed_and_extend())

    # New record went to the per-run WAL; the legacy files are byte-frozen.
    assert not legacy_wal.exists()
    assert _keys_in_file(run_wal) == [key_str([["call", 1]])]
    # The per-run snapshot carries the fresh call (hit records enter it only
    # via journal.use, which is agent()'s job — this test drives the journal
    # layer directly, so only the explicitly used record is snapshotted).
    assert _keys_in_file(run_journal) == [key_str([["call", 1]])]
    # And the next load (with legacy seed) still resolves both.
    reloaded = asyncio.run(
        Journal.load(str(run_journal), wal_path=str(run_wal), legacy_path=str(legacy_journal))
    )
    assert reloaded.get_cached(key_str([["call", 0]]), "s", "run-A") is not None
    assert reloaded.get_cached(key_str([["call", 1]]), "s", "run-A") is not None
