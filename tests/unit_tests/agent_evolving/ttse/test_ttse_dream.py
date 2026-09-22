# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for TTSE Auto-dream (prune / merge / purge)."""

from __future__ import annotations

import json
import os
import time
from typing import Callable

import pytest

from openjiuwen.agent_evolving.ttse import TTSEConfig, TTSERecordStore
from openjiuwen.agent_evolving.ttse.dream import (
    DreamState,
    _cap_cluster_by_count,
    _sample_rules,
    bump_dream_session_count,
    load_dream_clusters,
    load_dream_state,
    normalize_merge_subset,
    parse_category_merge_decisions,
    parse_cluster_groups,
    parse_merge_verdict,
    parse_purge_verdicts,
    prune_stale,
    run_dream_pass,
    save_dream_state,
    should_run_dream,
)
from openjiuwen.agent_evolving.ttse.tip_parse import parse_tip
from openjiuwen.agent_evolving.ttse.stores import _new_record, format_ts, parse_ts


class ScriptedLLM:
    def __init__(self, handler: Callable[[str], object]):
        self.handler = handler
        self.calls: list[str] = []

    async def invoke(self, *, model, messages, temperature=None, timeout=None, **kwargs):
        prompt = messages[0]["content"] if messages else ""
        self.calls.append(prompt)
        return self.handler(prompt)


class FakeEmbedding:
    """Maps text to a 2-d vector; near-duplicate texts share close vectors."""

    def __init__(self, mapping: dict[str, list[float]] | None = None):
        self.mapping = mapping or {}
        self.model = "fake-emb"
        self.call_times: list[float] = []

    async def embed_query(self, text: str) -> list[float]:
        self.call_times.append(time.monotonic())
        key = " ".join(text.lower().split())
        if key in self.mapping:
            return self.mapping[key]
        # Stable hash-ish fallback so unrelated texts differ.
        return [float(len(key) % 7), float(sum(ord(c) for c in key) % 11)]


def _make_store(tmp_path, *, cfg=None, embedding=None) -> tuple[TTSERecordStore, TTSEConfig]:
    config = cfg or TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        dream_enabled=True,
        embedding_max_rps=0,
    )
    if embedding is not None:
        config.embedding = embedding
    return TTSERecordStore(config, embedding=embedding), config


# ----------------------------------------------------------------------
# tip_parse
# ----------------------------------------------------------------------


def test_parse_tip_happy_path():
    parsed = parse_tip("When logs are large: use grep to scan before reading")
    assert parsed == ("logs are large", "grep", "scan before reading")


def test_parse_tip_strips_backticks_and_takes_first_token():
    parsed = parse_tip("When a PDF file must be read: use `code` with pdfplumber to parse and extract the content")
    assert parsed is not None
    assert parsed[1] == "code"


def test_parse_tip_skips_filler_words():
    parsed = parse_tip("When searching history: use the `session-logs` skill to find prior turns")
    assert parsed is not None
    assert parsed[1] == "session-logs"


def test_parse_tip_accepts_fullwidth_colon():
    parsed = parse_tip("When 估值请求只包含公司名而缺少上市状态、行业或财务数据：use ask_user to 先向用户收集这些信息")
    assert parsed == (
        "估值请求只包含公司名而缺少上市状态、行业或财务数据",
        "ask_user",
        "先向用户收集这些信息",
    )


def test_parse_purge_verdicts_lines():
    raw = (
        "INDEX: 0 | VERDICT: PURGE | REASON: tip_fact_shaped\n"
        "INDEX: 1 | VERDICT: KEEP | REASON: ok\n"
        "INDEX: 2 | VERDICT: PURGE | REASON: tip_too_generic_condition\n"
    )
    verdicts = parse_purge_verdicts(raw, 3)
    assert [(v.index, v.verdict, v.reason) for v in verdicts] == [
        (0, "PURGE", "tip_fact_shaped"),
        (1, "KEEP", "ok"),
        (2, "PURGE", "tip_too_generic_condition"),
    ]


# ----------------------------------------------------------------------
# metadata / inject clock
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_new_record_has_metadata(tmp_path):
    store = TTSERecordStore(TTSEConfig(store_path=str(tmp_path / "bank.json")))
    await store.add_fact("a fact")
    rec = store.facts[0]
    assert rec["count"] == 1
    assert isinstance(rec["created_at"], str)
    assert parse_ts(rec["created_at"]) is not None
    assert rec["created_at"] == format_ts(parse_ts(rec["created_at"]))
    assert rec["last_injected_at"] is None
    assert rec["inject_hits"] == 0
    assert rec["form_checked"] is False


@pytest.mark.asyncio
async def test_legacy_bank_migration_sets_last_injected(tmp_path):
    path = tmp_path / "bank.json"
    path.write_text(
        json.dumps({"facts": [{"text": "legacy", "count": 2}], "tips": [], "retired": []}), encoding="utf-8"
    )
    store = TTSERecordStore(TTSEConfig(store_path=str(path)))
    rec = store.facts[0]
    assert rec["count"] == 2
    assert isinstance(rec["last_injected_at"], str)
    assert isinstance(rec["created_at"], str)
    assert parse_ts(rec["last_injected_at"]) is not None
    assert parse_ts(rec["created_at"]) is not None
    assert rec["form_checked"] is False


@pytest.mark.asyncio
async def test_legacy_float_timestamps_normalized_on_load(tmp_path):
    path = tmp_path / "bank.json"
    epoch = 1_700_000_000.0
    path.write_text(
        json.dumps(
            {
                "facts": [
                    {
                        "text": "float clock",
                        "count": 1,
                        "created_at": epoch,
                        "updated_at": epoch,
                        "last_injected_at": epoch,
                        "inject_hits": 1,
                    }
                ],
                "tips": [],
                "retired": [],
            }
        ),
        encoding="utf-8",
    )
    store = TTSERecordStore(TTSEConfig(store_path=str(path)))
    rec = store.facts[0]
    assert rec["created_at"] == format_ts(epoch)
    assert rec["updated_at"] == format_ts(epoch)
    assert rec["last_injected_at"] == format_ts(epoch)
    assert parse_ts(rec["created_at"]) == pytest.approx(epoch, abs=1)


@pytest.mark.asyncio
async def test_prune_stale_ttl(tmp_path):
    cfg = TTSEConfig(store_path=str(tmp_path / "bank.json"), dream_ttl_days=90)
    store = TTSERecordStore(cfg)
    now = time.time()
    old = _new_record("old fact", now=now - 91 * 86400)
    old["last_injected_at"] = format_ts(now - 91 * 86400)
    fresh = _new_record("fresh fact", now=now - 10 * 86400)
    fresh["last_injected_at"] = format_ts(now - 10 * 86400)
    store.facts = [old, fresh]
    pf, pt = await prune_stale(store, cfg, now=now)
    assert pf == 1 and pt == 0
    assert store.facts_texts() == ["fresh fact"]
    assert store.retired == []


@pytest.mark.asyncio
async def test_prune_accepts_legacy_float_last_injected(tmp_path):
    cfg = TTSEConfig(store_path=str(tmp_path / "bank.json"), dream_ttl_days=90)
    store = TTSERecordStore(cfg)
    now = time.time()
    old = _new_record("old fact", now=now - 91 * 86400)
    old["last_injected_at"] = now - 91 * 86400  # legacy float still in memory
    store.facts = [old]
    pf, pt = await prune_stale(store, cfg, now=now)
    assert pf == 1 and pt == 0


@pytest.mark.asyncio
async def test_legacy_migrated_not_immediately_pruned(tmp_path):
    path = tmp_path / "bank.json"
    path.write_text(
        json.dumps({"facts": [{"text": "legacy", "count": 1}], "tips": [], "retired": []}), encoding="utf-8"
    )
    cfg = TTSEConfig(store_path=str(path), dream_ttl_days=90)
    store = TTSERecordStore(cfg)
    pf, _ = await prune_stale(store, cfg, now=time.time())
    assert pf == 0
    assert store.facts_texts() == ["legacy"]


@pytest.mark.asyncio
async def test_mark_injected_flush_persists_for_new_store(tmp_path):
    path = tmp_path / "bank.json"
    cfg = TTSEConfig(
        store_path=str(path),
        inject_persist_min_hits=16,
        inject_persist_min_secs=10_000,
    )
    store = TTSERecordStore(cfg)
    await store.add_fact("valuable fact")
    now = time.time()
    assert store.mark_injected(store.facts, now=now) == 1
    loaded = TTSERecordStore(TTSEConfig(store_path=str(path)))
    assert loaded.facts[0]["inject_hits"] == 0
    assert loaded.facts[0]["last_injected_at"] is None

    assert await store.flush_inject_metadata() is True
    reloaded = TTSERecordStore(TTSEConfig(store_path=str(path)))
    rec = reloaded.facts[0]
    assert rec["inject_hits"] == 1
    assert parse_ts(rec["last_injected_at"]) == pytest.approx(now, abs=1)
    assert rec["last_injected_at"] == format_ts(now)

    pf, pt = await prune_stale(reloaded, TTSEConfig(store_path=str(path), dream_ttl_days=90), now=now)
    assert pf == 0 and pt == 0
    assert reloaded.facts_texts() == ["valuable fact"]


@pytest.mark.asyncio
async def test_mark_injected_auto_flush_at_min_hits(tmp_path):
    path = tmp_path / "bank.json"
    cfg = TTSEConfig(
        store_path=str(path),
        inject_persist_min_hits=2,
        inject_persist_min_secs=10_000,
    )
    store = TTSERecordStore(cfg)
    await store.add_fact("hit me")
    now = time.time()
    store.mark_injected(store.facts, now=now)
    skipped = TTSERecordStore(TTSEConfig(store_path=str(path)))
    assert skipped.facts[0]["inject_hits"] == 0

    store.mark_injected(store.facts, now=now)
    task = store._inject_save_task
    assert task is not None
    await task
    reloaded = TTSERecordStore(TTSEConfig(store_path=str(path)))
    assert reloaded.facts[0]["inject_hits"] == 2
    assert parse_ts(reloaded.facts[0]["last_injected_at"]) == pytest.approx(now, abs=1)
    assert reloaded.facts[0]["last_injected_at"] == format_ts(now)


@pytest.mark.asyncio
async def test_reload_overlay_prevents_ttl_prune_of_injected_rule(tmp_path):
    path = tmp_path / "bank.json"
    cfg = TTSEConfig(
        store_path=str(path),
        dream_ttl_days=90,
        inject_persist_min_hits=16,
        inject_persist_min_secs=10_000,
    )
    store = TTSERecordStore(cfg)
    now = time.time()
    stale_ts = now - 95 * 86400
    rec = _new_record("high value", now=stale_ts)
    rec["last_injected_at"] = format_ts(stale_ts)
    store.facts = [rec]
    await store.save()

    assert store.mark_injected(store.facts, now=now) == 1
    stale_snapshot = {
        "facts": [
            {
                "text": "high value",
                "count": 1,
                "created_at": stale_ts,
                "updated_at": stale_ts,
                "last_injected_at": stale_ts,
                "inject_hits": 0,
            }
        ],
        "tips": [],
        "retired": [],
    }
    path.write_text(json.dumps(stale_snapshot), encoding="utf-8")
    later = float(store._loaded_mtime) + 10
    os.utime(path, (later, later))

    assert store.reload_if_disk_newer() is True
    pf, pt = await prune_stale(store, cfg, now=now)
    assert pf == 0 and pt == 0
    kept = store.facts[0]
    assert kept["text"] == "high value"
    assert parse_ts(kept["last_injected_at"]) == pytest.approx(now, abs=1)
    assert kept["last_injected_at"] == format_ts(now)
    assert kept["inject_hits"] >= 1
    store.cancel_inject_persist()


@pytest.mark.asyncio
async def test_flush_overlays_concurrent_disk_write(tmp_path):
    path = tmp_path / "bank.json"
    cfg = TTSEConfig(
        store_path=str(path),
        inject_persist_min_hits=16,
        inject_persist_min_secs=10_000,
    )
    store = TTSERecordStore(cfg)
    await store.add_fact("rule a")
    now = time.time()
    store.mark_injected(store.facts, now=now)

    extra = _new_record("rule b", now=now)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["facts"].append(extra)
    path.write_text(json.dumps(data), encoding="utf-8")
    later = float(store._loaded_mtime) + 10
    os.utime(path, (later, later))

    assert await store.flush_inject_metadata() is True
    reloaded = TTSERecordStore(TTSEConfig(store_path=str(path)))
    texts = set(reloaded.facts_texts())
    assert texts == {"rule a", "rule b"}
    rec_a = next(r for r in reloaded.facts if r["text"] == "rule a")
    assert rec_a["inject_hits"] == 1
    assert parse_ts(rec_a["last_injected_at"]) == pytest.approx(now, abs=1)
    assert rec_a["last_injected_at"] == format_ts(now)


@pytest.mark.asyncio
async def test_save_dumps_snapshot_when_live_lists_mutate(tmp_path, monkeypatch):
    """json.dump must not iterate the live facts/tips/retired lists.

    save() yields in asyncio.to_thread while add_fact/dream still mutate those
    lists under a different lock. Persist a copy taken before the worker runs.
    """
    path = tmp_path / "bank.json"
    store = TTSERecordStore(TTSEConfig(store_path=str(path)))
    await store.add_fact("stable fact")
    real_dump = json.dump

    def dump_while_mutating(obj, fh, **kwargs):
        assert obj["facts"] is not store.facts
        assert obj["tips"] is not store.tips
        assert obj["retired"] is not store.retired
        store.facts.append(_new_record("raced fact"))
        del store.facts[0]
        store.tips.append(_new_record("raced tip"))
        store.retired.append({"text": "gone", "rtype": "fact", "reason": "race", "retired_at_task": 1})
        return real_dump(obj, fh, **kwargs)

    monkeypatch.setattr("openjiuwen.agent_evolving.ttse.stores.json.dump", dump_while_mutating)
    await store.save()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [record["text"] for record in saved["facts"]] == ["stable fact"]
    assert saved["tips"] == []
    assert saved["retired"] == []


@pytest.mark.asyncio
async def test_save_swallows_non_serializable_record(tmp_path):
    """json.dump TypeError must not crash add_fact / dream persist."""
    path = tmp_path / "bank.json"
    store = TTSERecordStore(TTSEConfig(store_path=str(path)))
    await store.add_fact("ok")
    store.facts.append({"text": "bad", "count": 1, "blob": object()})

    await store.save()

    assert [record["text"] for record in store.facts] == ["ok", "bad"]
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [record["text"] for record in saved["facts"]] == ["ok"]
    assert not (tmp_path / "bank.json.tmp").exists()


# ----------------------------------------------------------------------
# merge
# ----------------------------------------------------------------------


def test_parse_merge_verdict():
    v = parse_merge_verdict(
        "THINKING:\n"
        "Members 0-2 are paraphrases of the same case-sensitivity fact.\n"
        "Sims are high; conditions do not conflict.\n"
        "REASON: paraphrase\n"
        "VERDICT: MERGE\n"
        "CANONICAL: the grader is case-sensitive\n"
        "MERGE_INDICES:\n"
        "KEEP_INDICES:\n",
        3,
    )
    assert v is not None
    assert v.verdict == "MERGE"
    assert v.canonical == "the grader is case-sensitive"
    assert v.merge_indices == []
    assert "paraphrases" in v.thinking
    assert v.reason == "paraphrase"


def test_parse_merge_verdict_subset():
    v = parse_merge_verdict(
        "THINKING:\n"
        "0 and 1 are paraphrases; 2 has a different condition.\n"
        "REASON: merge pair keep third\n"
        "VERDICT: MERGE\n"
        "CANONICAL: the grader is case-sensitive\n"
        "MERGE_INDICES: 0, 1\n"
        "KEEP_INDICES: 2\n",
        3,
    )
    assert v is not None
    assert v.verdict == "MERGE"
    assert v.merge_indices == [0, 1]
    assert v.keep_indices == [2]


def test_normalize_merge_subset():
    assert normalize_merge_subset(3, "MERGE", [], []) == ([0, 1, 2], [])
    assert normalize_merge_subset(3, "MERGE", [0, 1], [2]) == ([0, 1], [2])
    assert normalize_merge_subset(3, "MERGE", [0, 1], []) == ([0, 1], [2])
    assert normalize_merge_subset(3, "MERGE", [0], [1, 2]) == ([], [0, 1, 2])
    assert normalize_merge_subset(3, "KEEP_DISTINCT", [0, 1], [0]) == ([], [0, 1, 2])


def test_parse_merge_verdict_requires_thinking_and_reason():
    assert (
        parse_merge_verdict(
            "REASON: paraphrase\nVERDICT: MERGE\nCANONICAL: x\nKEEP_INDICES:\n",
            2,
        )
        is None
    )
    assert (
        parse_merge_verdict(
            "THINKING:\nsame idea\nVERDICT: MERGE\nCANONICAL: x\nKEEP_INDICES:\n",
            2,
        )
        is None
    )
    assert (
        parse_merge_verdict(
            "THINKING:\n\nREASON: \nVERDICT: MERGE\nCANONICAL: x\nKEEP_INDICES:\n",
            2,
        )
        is None
    )


@pytest.mark.asyncio
async def test_dream_merge_near_duplicate_facts(tmp_path):
    emb = FakeEmbedding(
        {
            "grader checks case": [1.0, 0.0],
            "grader is case sensitive": [0.99, 0.01],
            "grader cares about case": [0.98, 0.02],
            "unrelated weather": [0.0, 1.0],
        }
    )
    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        embedding=emb,
        embedding_max_rps=0,
        dream_enabled=True,
        dream_min_hours=0,
        dream_min_rules=1,
        dream_soft_lo=0.72,
        dream_purge_tips_enabled=False,
        dream_prune_enabled=False,
    )

    def handler(prompt: str):
        return (
            "THINKING:\n"
            "All three facts describe grader case sensitivity with high pairwise sims.\n"
            "No mutually exclusive conditions; merge into one canonical statement.\n"
            "REASON: near duplicates\n"
            "VERDICT: MERGE\n"
            "CANONICAL: the grader checks column names case-sensitively\n"
            "KEEP_INDICES:\n"
        )

    store, cfg = _make_store(tmp_path, cfg=cfg, embedding=emb)
    llm = ScriptedLLM(handler)
    # Bypass online hard-dedup so soft-cluster can still see near-duplicates.
    for text, count in (
        ("grader checks case", 2),
        ("grader is case sensitive", 2),
        ("grader cares about case", 2),
    ):
        await store.add_record_direct("fact", text, count=count, save=False)
    await store.save()
    assert len(store.facts) == 3

    result, _ = await run_dream_pass(
        store,
        cfg,
        llm=llm,
        model="dummy-model",
        capability_names={"grep"},
    )
    assert not result.skipped
    assert result.merged_clusters >= 1
    assert len(store.facts) == 1
    assert store.facts[0]["count"] >= 6
    assert "grader" in store.facts[0]["text"].lower()
    assert store.retired == []


@pytest.mark.asyncio
async def test_dream_merge_subset_keeps_one_fact(tmp_path):
    """MERGE_INDICES folds two near-dupes; KEEP_INDICES leaves the third."""
    emb = FakeEmbedding(
        {
            "grader checks case": [1.0, 0.0],
            "grader is case sensitive": [0.99, 0.01],
            "grader cares about case": [0.98, 0.02],
            "unrelated weather": [0.0, 1.0],
        }
    )
    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        embedding=emb,
        embedding_max_rps=0,
        dream_enabled=True,
        dream_min_hours=0,
        dream_min_rules=1,
        dream_soft_lo=0.72,
        dream_purge_tips_enabled=False,
        dream_prune_enabled=False,
    )

    def handler(prompt: str):
        return (
            "THINKING:\n"
            "1 and 2 are paraphrases; 0 is related but a distinct wording we keep.\n"
            "REASON: merge pair keep one\n"
            "VERDICT: MERGE\n"
            "CANONICAL: the grader checks column names case-sensitively\n"
            "MERGE_INDICES: 1, 2\n"
            "KEEP_INDICES: 0\n"
        )

    store, cfg = _make_store(tmp_path, cfg=cfg, embedding=emb)
    llm = ScriptedLLM(handler)
    for text, count in (
        ("grader checks case", 2),
        ("grader is case sensitive", 3),
        ("grader cares about case", 4),
    ):
        await store.add_record_direct("fact", text, count=count, save=False)
    await store.save()
    assert len(store.facts) == 3

    result, _ = await run_dream_pass(
        store,
        cfg,
        llm=llm,
        model="dummy-model",
        capability_names={"grep"},
    )
    assert not result.skipped
    assert result.merged_clusters >= 1
    texts = set(store.facts_texts())
    assert "the grader checks column names case-sensitively" in texts
    assert "grader cares about case" in texts
    assert "grader checks case" not in texts
    assert "grader is case sensitive" not in texts
    assert len(store.facts) == 2
    merged = next(r for r in store.facts if "column names" in r["text"])
    assert merged["count"] == 5


@pytest.mark.asyncio
async def test_dream_keep_distinct_tips(tmp_path):
    emb = FakeEmbedding(
        {
            "when csv needs counts: use python_exec to group rows": [0.8, 0.2],
            "when logs are huge: use grep to extract matches": [0.75, 0.25],
        }
    )
    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        embedding=emb,
        embedding_max_rps=0,
        dream_min_hours=0,
        dream_min_rules=1,
        dream_prune_enabled=False,
        dream_purge_tips_enabled=False,
    )

    def handler(prompt: str):
        return (
            "THINKING:\n"
            "TIP 0 targets csv counts via python_exec; TIP 1 targets huge logs via grep.\n"
            "Conditions and capabilities differ; keep both.\n"
            "REASON: different conditions\n"
            "VERDICT: KEEP_DISTINCT\n"
            "CANONICAL:\n"
            "KEEP_INDICES: 0, 1\n"
        )

    store, cfg = _make_store(tmp_path, cfg=cfg, embedding=emb)
    llm = ScriptedLLM(handler)
    await store.add_record_direct("tip", "When csv needs counts: use python_exec to group rows", save=False)
    await store.add_record_direct("tip", "When logs are huge: use grep to extract matches", save=False)
    await store.save()
    before = list(store.tips_texts())
    result, _ = await run_dream_pass(
        store,
        cfg,
        llm=llm,
        model="dummy-model",
        capabilities="- tool `grep`: x\n- tool `python_exec`: y",
        capability_names={"grep", "python_exec"},
    )
    assert result.kept_clusters >= 1 or result.merged_clusters == 0
    assert set(store.tips_texts()) == set(before)


# ----------------------------------------------------------------------
# purge tips (LLM form / over-generic)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dream_purge_bad_tips(tmp_path):
    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        dream_min_hours=0,
        dream_min_rules=100,  # skip merge
        dream_prune_enabled=False,
        dream_purge_tips_enabled=True,
    )
    store, cfg = _make_store(tmp_path, cfg=cfg)
    await store.add_tip("the grader checks exact columns")  # fact-shaped
    await store.add_tip("When logs are large: use grep to extract matches")

    def handler(prompt: str):
        return (
            "INDEX: 0 | VERDICT: PURGE | REASON: tip_fact_shaped\n"
            "INDEX: 1 | VERDICT: KEEP | REASON: ok\n"
        )

    llm = ScriptedLLM(handler)
    result, _ = await run_dream_pass(
        store,
        cfg,
        llm=llm,
        model="dummy-model",
    )
    assert result.purged_tips == 1
    assert store.tips_texts() == ["When logs are large: use grep to extract matches"]
    assert store.tips[0]["form_checked"] is True
    assert store.retired == []
    assert len(llm.calls) == 1
    # Persist form_checked on disk.
    reloaded = TTSERecordStore(cfg)
    assert reloaded.tips[0]["form_checked"] is True


@pytest.mark.asyncio
async def test_dream_purge_skips_form_checked_on_second_pass(tmp_path):
    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        dream_min_hours=0,
        dream_min_rules=100,
        dream_prune_enabled=False,
        dream_purge_tips_enabled=True,
    )
    store, cfg = _make_store(tmp_path, cfg=cfg)
    await store.add_tip("When logs are large: use grep to extract matches")

    def keep_all(prompt: str):
        return "INDEX: 0 | VERDICT: KEEP | REASON: ok\n"

    llm1 = ScriptedLLM(keep_all)
    result1, state = await run_dream_pass(store, cfg, llm=llm1, model="dummy-model", now=1.0)
    assert result1.purged_tips == 0
    assert store.tips[0]["form_checked"] is True
    assert len(llm1.calls) == 1

    llm2 = ScriptedLLM(keep_all)
    # Force gate open with a later timestamp.
    result2, _ = await run_dream_pass(
        store,
        cfg,
        llm=llm2,
        model="dummy-model",
        state=state,
        now=1.0 + 3600 * 25,
    )
    assert not result2.skipped
    assert len(llm2.calls) == 0


@pytest.mark.asyncio
async def test_dream_purge_keeps_unknown_capability(tmp_path):
    """Capability whitelist pruning is removed — unknown cap still KEEP when LLM says so."""
    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        dream_min_hours=0,
        dream_min_rules=100,
        dream_prune_enabled=False,
        dream_purge_tips_enabled=True,
    )
    store, cfg = _make_store(tmp_path, cfg=cfg)
    tip = "When decoding bytes: use decode to transform bytes"
    await store.add_tip(tip)

    llm = ScriptedLLM(lambda _: "INDEX: 0 | VERDICT: KEEP | REASON: ok\n")
    result, _ = await run_dream_pass(
        store,
        cfg,
        llm=llm,
        model="dummy-model",
        capability_names={"grep"},  # decode not listed; must not force purge
    )
    assert result.purged_tips == 0
    assert store.tips_texts() == [tip]
    assert store.tips[0]["form_checked"] is True


@pytest.mark.asyncio
async def test_dream_merge_canonical_form_checked_false(tmp_path):
    emb = FakeEmbedding(
        {
            "when logs are huge: use grep to extract matches": [1.0, 0.0],
            "when log files are large: use grep to find matches": [0.99, 0.01],
        }
    )
    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        embedding=emb,
        embedding_max_rps=0,
        dream_min_hours=0,
        dream_min_rules=1,
        dream_prune_enabled=False,
        dream_purge_tips_enabled=False,
    )

    def handler(prompt: str):
        return (
            "THINKING:\n"
            "Near-duplicate log/grep tips; merge.\n"
            "REASON: paraphrases\n"
            "VERDICT: MERGE\n"
            "CANONICAL: When logs are large: use grep to extract matches\n"
            "KEEP_INDICES:\n"
        )

    store, cfg = _make_store(tmp_path, cfg=cfg, embedding=emb)
    await store.add_record_direct("tip", "When logs are huge: use grep to extract matches", save=False)
    await store.add_record_direct("tip", "When log files are large: use grep to find matches", save=False)
    await store.save()
    result, _ = await run_dream_pass(
        store,
        cfg,
        llm=ScriptedLLM(handler),
        model="dummy-model",
    )
    assert result.merged_clusters >= 1
    assert len(store.tips) == 1
    assert store.tips[0]["form_checked"] is False


# ----------------------------------------------------------------------
# gates + lock
# ----------------------------------------------------------------------


def test_bump_dream_session_count_persists_and_resets_at_interval(tmp_path):
    path = str(tmp_path / "dream-state.json")
    count, reached = bump_dream_session_count(path, interval=3)
    assert (count, reached) == (1, False)
    assert load_dream_state(path).non_followup_count == 1
    count, reached = bump_dream_session_count(path, interval=3)
    assert (count, reached) == (2, False)
    count, reached = bump_dream_session_count(path, interval=3)
    assert (count, reached) == (3, True)
    assert load_dream_state(path).non_followup_count == 0


def test_dream_state_legacy_json_defaults_count_to_zero(tmp_path):
    path = tmp_path / "dream-state.json"
    path.write_text(json.dumps({"last_dream_at": 1.0}), encoding="utf-8")
    state = load_dream_state(str(path))
    assert state.last_dream_at == 1.0
    assert state.non_followup_count == 0


def test_dream_state_roundtrip_uses_display_timestamp(tmp_path):
    path = str(tmp_path / "dream-state.json")
    now = time.time()
    state = DreamState(last_dream_at=now, last_pruned=2)
    save_dream_state(path, state)
    raw = json.loads((tmp_path / "dream-state.json").read_text(encoding="utf-8"))
    assert raw["last_dream_at"] == format_ts(now)
    assert isinstance(raw["last_dream_at"], str)
    loaded = load_dream_state(path)
    assert loaded.last_dream_at == pytest.approx(now, abs=1)
    assert loaded.last_pruned == 2


def test_dream_state_reads_display_timestamp_string(tmp_path):
    path = tmp_path / "dream-state.json"
    stamp = "2026-09-21 11:15:00"
    path.write_text(json.dumps({"last_dream_at": stamp}), encoding="utf-8")
    state = load_dream_state(str(path))
    assert state.last_dream_at == pytest.approx(parse_ts(stamp), abs=1)


def test_should_run_dream_min_hours():
    cfg = TTSEConfig(dream_enabled=True, dream_min_hours=24)
    state = DreamState(last_dream_at=time.time() - 3600)
    ok, reason = should_run_dream(cfg, state)
    assert not ok
    assert "min_hours" in reason


@pytest.mark.asyncio
async def test_dream_skipped_when_min_hours(tmp_path):
    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        dream_enabled=True,
        dream_min_hours=24,
        dream_state_path=str(tmp_path / "dream-state.json"),
    )
    (tmp_path / "dream-state.json").write_text(
        json.dumps({"last_dream_at": time.time()}),
        encoding="utf-8",
    )
    store, cfg = _make_store(tmp_path, cfg=cfg)
    await store.add_fact("x")
    result, _ = await run_dream_pass(
        store,
        cfg,
        llm=ScriptedLLM(lambda _: "NONE"),
        model="dummy-model",
        capability_names=set(),
    )
    assert result.skipped


@pytest.mark.asyncio
async def test_dream_merge_skipped_without_embedding_still_prunes(tmp_path):
    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        dream_min_hours=0,
        dream_min_rules=1,
        dream_ttl_days=90,
        dream_purge_tips_enabled=False,
    )
    store = TTSERecordStore(cfg)  # no embedding
    now = time.time()
    stale = _new_record("stale", now=now - 100 * 86400)
    stale["last_injected_at"] = format_ts(now - 100 * 86400)
    store.facts = [stale]
    result, _ = await run_dream_pass(
        store,
        cfg,
        llm=ScriptedLLM(lambda _: "NONE"),
        model="m",
        capability_names=set(),
        now=now,
    )
    assert not result.skipped
    assert result.pruned_facts == 1
    assert result.merged_clusters == 0


@pytest.mark.asyncio
async def test_dream_merge_llm_without_embedding(tmp_path):
    """Near-duplicate facts cluster via LLM Phase1/2 when no embedding is set."""

    def handler(prompt: str):
        if "You are clustering" in prompt:
            return (
                "THINKING:\n"
                "Two facts describe PresentBench slide grading as paraphrases.\n"
                "REASON: one near-duplicate pair\n"
                "GROUPS:\n"
                "- 0,1\n"
            )
        return (
            "THINKING:\n"
            "Group 0 members are paraphrases; MERGE.\n"
            "REASON: merged PresentBench grading facts\n"
            "DECISIONS:\n"
            "- group=0 | ids=0,1 | VERDICT: MERGE | "
            "CANONICAL: PresentBench grades slides.md not a pptx file | KEEP_INDICES:\n"
        )

    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        dream_enabled=True,
        dream_min_hours=0,
        dream_min_rules=1,
        dream_purge_tips_enabled=False,
        dream_prune_enabled=False,
        dream_llm_cluster_enabled=True,
    )
    store, cfg = _make_store(tmp_path, cfg=cfg)
    assert not store.has_embedding_provider()
    llm = ScriptedLLM(handler)
    for text, count in (
        ("PresentBench grades slides.md not a pptx file", 2),
        ("PresentBench grades slides.md rather than pptx", 2),
    ):
        await store.add_record_direct("fact", text, count=count, save=False)
    await store.save()
    assert len(store.facts) == 2

    soft_calls = {"n": 0}
    original_soft = store.soft_cluster

    async def _guarded_soft(*args, **kwargs):
        soft_calls["n"] += 1
        return await original_soft(*args, **kwargs)

    store.soft_cluster = _guarded_soft  # type: ignore[method-assign]

    result, _ = await run_dream_pass(
        store,
        cfg,
        llm=llm,
        model="dummy-model",
        capability_names=set(),
    )
    assert not result.skipped
    assert result.merged_clusters >= 1
    assert len(store.facts) == 1
    assert store.facts[0]["count"] >= 4
    assert soft_calls["n"] == 0
    assert len(llm.calls) >= 2
    clusters_path = cfg.resolved_dream_clusters_path()
    assert os.path.exists(clusters_path)
    assert "/dream/" in clusters_path.replace("\\", "/") or clusters_path.replace("\\", "/").endswith(
        "dream/dream-clusters.json"
    )


@pytest.mark.asyncio
async def test_dream_merge_llm_subset_keeps_one(tmp_path):
    """LLM Phase2 MERGE_INDICES merges two of three; KEEP_INDICES leaves one."""

    def handler(prompt: str):
        if "You are clustering" in prompt:
            return (
                "THINKING:\n"
                "All three describe PresentBench grading with near-duplicate wording.\n"
                "REASON: one oversized near-duplicate group\n"
                "GROUPS:\n"
                "- 0,1,2\n"
            )
        return (
            "THINKING:\n"
            "1 and 2 are paraphrases; 0 mentions a distinct pptx detail worth keeping.\n"
            "REASON: merge pair keep one\n"
            "DECISIONS:\n"
            "- group=0 | ids=0,1,2 | VERDICT: MERGE | "
            "CANONICAL: PresentBench grades slides.md not a pptx file | "
            "MERGE_INDICES: 1,2 | KEEP_INDICES: 0\n"
        )

    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        dream_enabled=True,
        dream_min_hours=0,
        dream_min_rules=1,
        dream_purge_tips_enabled=False,
        dream_prune_enabled=False,
        dream_llm_cluster_enabled=True,
    )
    store, cfg = _make_store(tmp_path, cfg=cfg)
    llm = ScriptedLLM(handler)
    keep_text = "PresentBench also records whether pptx packaging was attempted"
    for text, count in (
        ("PresentBench grades slides.md not a pptx file", 2),
        ("PresentBench grades slides.md rather than pptx", 3),
        (keep_text, 4),
    ):
        await store.add_record_direct(
            "fact",
            text,
            count=count,
            category="documents-office-and-records",
            save=False,
        )
    await store.save()

    result, _ = await run_dream_pass(
        store,
        cfg,
        llm=llm,
        model="dummy-model",
        capability_names=set(),
    )
    assert not result.skipped
    assert result.merged_clusters >= 1
    texts = set(store.facts_texts())
    assert "PresentBench grades slides.md not a pptx file" in texts
    assert keep_text in texts
    assert "PresentBench grades slides.md rather than pptx" not in texts
    assert len(store.facts) == 2
    merged = next(r for r in store.facts if r["text"].startswith("PresentBench grades slides.md not"))
    assert merged["count"] == 5
    clusters = load_dream_clusters(cfg.resolved_dream_clusters_path())
    assert clusters
    member_set = set(clusters[0].member_texts)
    assert "PresentBench grades slides.md not a pptx file" in member_set
    assert keep_text in member_set


@pytest.mark.asyncio
async def test_dream_merge_llm_skips_cross_category(tmp_path):
    """Single-rule categories must not trigger LLM clustering across categories."""

    def handler(prompt: str):
        raise AssertionError("LLM merge must not run for singleton categories")

    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        dream_enabled=True,
        dream_min_hours=0,
        dream_min_rules=1,
        dream_purge_tips_enabled=False,
        dream_prune_enabled=False,
    )
    store, cfg = _make_store(tmp_path, cfg=cfg)
    llm = ScriptedLLM(handler)
    await store.add_record_direct(
        "fact",
        "PresentBench grades slides.md not a pptx file",
        count=2,
        category="documents-office-and-records",
        save=False,
    )
    await store.add_record_direct(
        "fact",
        "PresentBench grades slides.md rather than pptx",
        count=2,
        category="software-engineering-devops",
        save=False,
    )
    await store.save()
    result, _ = await run_dream_pass(
        store,
        cfg,
        llm=llm,
        model="dummy-model",
        capability_names=set(),
    )
    assert not result.skipped
    assert result.merged_clusters == 0
    assert len(store.facts) == 2
    assert not llm.calls


@pytest.mark.asyncio
async def test_dream_llm_cluster_disabled_skips_soft_cluster(tmp_path):
    """No embedding + dream_llm_cluster_enabled=False must not call soft_cluster."""

    def handler(prompt: str):
        raise AssertionError("LLM must not be called when llm cluster is disabled")

    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        dream_enabled=True,
        dream_min_hours=0,
        dream_min_rules=1,
        dream_llm_cluster_enabled=False,
        dream_purge_tips_enabled=False,
        dream_prune_enabled=False,
    )
    store, cfg = _make_store(tmp_path, cfg=cfg)
    soft_calls = {"n": 0}
    original_soft = store.soft_cluster

    async def _guarded_soft(*args, **kwargs):
        soft_calls["n"] += 1
        return await original_soft(*args, **kwargs)

    store.soft_cluster = _guarded_soft  # type: ignore[method-assign]
    await store.add_record_direct("fact", "alpha fact one", count=2, save=False)
    await store.add_record_direct("fact", "alpha fact two near", count=2, save=False)
    await store.save()
    result, _ = await run_dream_pass(
        store,
        cfg,
        llm=ScriptedLLM(handler),
        model="m",
        capability_names=set(),
    )
    assert not result.skipped
    assert result.merged_clusters == 0
    assert soft_calls["n"] == 0
    assert len(store.facts) == 2


@pytest.mark.asyncio
async def test_dream_llm_incremental_skips_already_clustered(tmp_path):
    """Second dream must not Phase1 re-cluster KEEP_DISTINCT members; only new rules."""

    phase1_prompts: list[str] = []

    def handler(prompt: str):
        if "You are clustering" in prompt:
            phase1_prompts.append(prompt)
            if "brand-new orphan rule about widgets" in prompt:
                return (
                    "THINKING:\nnew singleton alone\n"
                    "REASON: no near duplicates among new rules\n"
                    "GROUPS:\nNONE\n"
                )
            return (
                "THINKING:\nparaphrases\n"
                "REASON: keep distinct wording for now\n"
                "GROUPS:\n- 0,1\n"
            )
        return (
            "THINKING:\nconditions differ slightly\n"
            "REASON: keep both\n"
            "DECISIONS:\n"
            "- group=0 | ids=0,1 | VERDICT: KEEP_DISTINCT | CANONICAL: | KEEP_INDICES: 0,1\n"
        )

    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        dream_enabled=True,
        dream_min_hours=0,
        dream_min_rules=1,
        dream_purge_tips_enabled=False,
        dream_prune_enabled=False,
    )
    store, cfg = _make_store(tmp_path, cfg=cfg)
    llm = ScriptedLLM(handler)
    await store.add_record_direct(
        "fact",
        "PresentBench grades slides.md not a pptx file",
        count=2,
        category="documents-office-and-records",
        save=False,
    )
    await store.add_record_direct(
        "fact",
        "PresentBench grades slides.md rather than pptx",
        count=2,
        category="documents-office-and-records",
        save=False,
    )
    await store.save()

    result1, state1 = await run_dream_pass(
        store, cfg, llm=llm, model="m", capability_names=set(), now=time.time()
    )
    assert not result1.skipped
    assert result1.kept_clusters >= 1 or result1.merged_clusters == 0
    assert len(phase1_prompts) == 1
    clusters = load_dream_clusters(cfg.resolved_dream_clusters_path())
    assert clusters
    assert all(not c.dirty for c in clusters if c.track == "fact")

    await store.add_record_direct(
        "fact",
        "brand-new orphan rule about widgets",
        count=1,
        category="documents-office-and-records",
        save=False,
    )
    await store.save()
    # Reset dream gate so a second pass runs.
    state1.last_dream_at = 0.0
    save_dream_state(cfg.resolved_dream_state_path(), state1)

    result2, _ = await run_dream_pass(
        store, cfg, llm=llm, model="m", capability_names=set(), now=time.time()
    )
    assert not result2.skipped
    assert len(phase1_prompts) == 2
    # Second Phase1 must only see the new rule, not the already-clustered pair.
    assert "PresentBench grades slides.md not a pptx file" not in phase1_prompts[1]
    assert "brand-new orphan rule about widgets" in phase1_prompts[1]


def test_parse_cluster_groups_and_attach():
    text = (
        "THINKING:\n0 and 2 are paraphrases; 1 attaches to existing cluster.\n"
        "REASON: one new group plus attach\n"
        "GROUPS:\n"
        "- 0,2\n"
        "ATTACH:\n"
        "- cluster=c_abc | ids=1\n"
    )
    part = parse_cluster_groups(text, n=3, min_size=2, known_cluster_ids={"c_abc"})
    assert part is not None
    assert part.groups == [[0, 2]]
    assert part.attaches == [("c_abc", [1])]


def test_parse_cluster_groups_none():
    text = "THINKING:\nnone\nREASON: all distinct\nGROUPS:\nNONE\n"
    part = parse_cluster_groups(text, n=3, min_size=2)
    assert part is not None
    assert part.groups == []


def test_parse_cluster_groups_none_reason_block():
    """REASON on its own line then body (common LLM layout) must still parse."""
    text = (
        "THINKING:\n"
        "Rule 0, 1, and 2 are distinct declarative facts.\n"
        "REASON:\n"
        "All three rules concern different topics and should remain separate "
        "singletons.\n"
        "GROUPS:\n"
        "NONE\n"
    )
    part = parse_cluster_groups(text, n=3, min_size=2)
    assert part is not None
    assert part.groups == []
    assert part.attaches == []
    assert "different topics" in part.reason
    assert "distinct declarative" in part.thinking


def test_parse_category_merge_decisions_basic():
    text = (
        "THINKING:\nmerge group 0\n"
        "REASON: paraphrase\n"
        "DECISIONS:\n"
        "- group=0 | ids=0,2 | VERDICT: MERGE | CANONICAL: hello world | KEEP_INDICES:\n"
    )
    result = parse_category_merge_decisions(text, clusters=[[0, 2]], n=3)
    assert result is not None
    assert len(result.decisions) == 1
    assert result.decisions[0].verdict == "MERGE"
    assert result.decisions[0].canonical == "hello world"
    assert result.decisions[0].merge_indices == []


def test_parse_category_merge_decisions_subset():
    text = (
        "THINKING:\nmerge 0+1 keep 2\n"
        "REASON: partial paraphrase\n"
        "DECISIONS:\n"
        "- group=0 | ids=0,1,2 | VERDICT: MERGE | CANONICAL: hello world | "
        "MERGE_INDICES: 0,1 | KEEP_INDICES: 2\n"
    )
    result = parse_category_merge_decisions(text, clusters=[[0, 1, 2]], n=3)
    assert result is not None
    assert len(result.decisions) == 1
    d = result.decisions[0]
    assert d.verdict == "MERGE"
    assert d.merge_indices == [0, 1]
    assert d.keep_indices == [2]
    assert d.canonical == "hello world"


def test_resolved_dream_clusters_path_default(tmp_path):
    cfg = TTSEConfig(store_path=str(tmp_path / "bank.json"))
    path = cfg.resolved_dream_clusters_path()
    assert path.replace("\\", "/").endswith("dream/dream-clusters.json")


def test_sample_rules_random_cap(monkeypatch):
    """Random shuffle keeps first max_rules; omitted covers the rest."""
    records = [{"text": f"r{i}", "count": i} for i in range(5)]

    def _fixed_shuffle(seq):
        # Force order: reverse of original indexed pairs.
        seq[:] = list(reversed(seq))

    monkeypatch.setattr(
        "openjiuwen.agent_evolving.ttse.dream.random.shuffle",
        _fixed_shuffle,
    )
    kept, omitted = _sample_rules(records, max_rules=2)
    assert len(kept) == 2
    # After reverse shuffle of indexed pairs, first 2 kept indices are 4 and 3.
    assert [r["text"] for r in kept] == ["r3", "r4"]  # original relative order
    assert omitted == [0, 1, 2]
    # Under cap: no truncation.
    all_kept, all_omitted = _sample_rules(records, max_rules=10)
    assert len(all_kept) == 5
    assert all_omitted == []


def test_cap_cluster_by_count_prefers_high_count():
    """Embedding-path cap keeps highest count while preserving relative order."""
    records = [
        {"text": "low-a", "count": 1},
        {"text": "high-a", "count": 9},
        {"text": "mid", "count": 5},
        {"text": "high-b", "count": 8},
        {"text": "low-b", "count": 2},
    ]
    kept, omitted = _cap_cluster_by_count(records, max_rules=3)
    assert [r["text"] for r in kept] == ["high-a", "mid", "high-b"]
    assert omitted == [0, 4]
    under, under_omitted = _cap_cluster_by_count(records, max_rules=10)
    assert len(under) == 5
    assert under_omitted == []


@pytest.mark.asyncio
async def test_dream_merge_max_rules_truncates_embedding_cluster(tmp_path):
    """Embedding soft-cluster merge prompt must respect dream_merge_max_rules."""
    # Five near-duplicate vectors so soft_cluster yields one oversized cluster.
    vecs = {
        "rule-low-0": [1.0, 0.0],
        "rule-high-1": [0.99, 0.01],
        "rule-high-2": [0.98, 0.02],
        "rule-mid-3": [0.97, 0.03],
        "rule-low-4": [0.96, 0.04],
    }
    emb = FakeEmbedding(vecs)
    seen: list[str] = []

    def handler(prompt: str):
        seen.append(prompt)
        return (
            "THINKING:\nsubset merge of high-count near-dupes\n"
            "REASON: near duplicates\n"
            "VERDICT: KEEP_DISTINCT\n"
            "CANONICAL:\n"
            "KEEP_INDICES:\n"
        )

    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        embedding=emb,
        embedding_max_rps=0,
        dream_enabled=True,
        dream_min_hours=0,
        dream_min_rules=1,
        dream_soft_lo=0.72,
        dream_merge_max_rules=3,
        dream_purge_tips_enabled=False,
        dream_prune_enabled=False,
    )
    store, cfg = _make_store(tmp_path, cfg=cfg, embedding=emb)
    for text, count in (
        ("rule-low-0", 1),
        ("rule-high-1", 10),
        ("rule-high-2", 9),
        ("rule-mid-3", 5),
        ("rule-low-4", 2),
    ):
        await store.add_record_direct("fact", text, count=count, save=False)
    await store.save()

    await run_dream_pass(
        store,
        cfg,
        llm=ScriptedLLM(handler),
        model="m",
        capability_names=set(),
    )
    assert seen
    prompt = seen[0]
    # Highest-count three kept; lowest-count two omitted from the merge prompt.
    assert "rule-high-1" in prompt
    assert "rule-high-2" in prompt
    assert "rule-mid-3" in prompt
    assert "rule-low-0" not in prompt
    assert "rule-low-4" not in prompt
    # Omitted members remain in the bank.
    texts = {r["text"] for r in store.facts}
    assert "rule-low-0" in texts
    assert "rule-low-4" in texts


@pytest.mark.asyncio
async def test_dream_category_max_rules_truncates_phase1(tmp_path, monkeypatch):
    """Phase1 prompt must not include rules beyond dream_category_max_rules."""

    seen: list[str] = []

    def handler(prompt: str):
        if "You are clustering" in prompt:
            seen.append(prompt)
            return "THINKING:\nnone\nREASON: none\nGROUPS:\nNONE\n"
        raise AssertionError("unexpected Phase2")

    def _keep_low_indices(seq):
        # Indexed pairs stay in original order so first max_rules = 0,1.
        pass

    monkeypatch.setattr(
        "openjiuwen.agent_evolving.ttse.dream.random.shuffle",
        _keep_low_indices,
    )

    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        dream_enabled=True,
        dream_min_hours=0,
        dream_min_rules=1,
        dream_category_max_rules=2,
        dream_purge_tips_enabled=False,
        dream_prune_enabled=False,
    )
    store, cfg = _make_store(tmp_path, cfg=cfg)
    for i in range(5):
        await store.add_record_direct(
            "fact",
            f"rule number {i} about the same office docs domain",
            count=10 - i,
            category="documents-office-and-records",
            save=False,
        )
    await store.save()
    await run_dream_pass(
        store,
        cfg,
        llm=ScriptedLLM(handler),
        model="m",
        capability_names=set(),
    )
    assert seen
    # With identity shuffle, first 2 original rules are kept; later ones omitted.
    assert "rule number 0" in seen[0]
    assert "rule number 1" in seen[0]
    assert "rule number 4" not in seen[0]


@pytest.mark.asyncio
async def test_induction_substring_dedup_merges_contained(tmp_path):
    store = TTSERecordStore(TTSEConfig(store_path=str(tmp_path / "bank.json")))
    assert await store.add_fact("PresentBench grades slides.md") is True
    assert await store.add_fact("PresentBench grades slides.md not a pptx file") is False
    assert len(store.facts) == 1
    assert store.facts[0]["count"] == 2


@pytest.mark.asyncio
async def test_induction_substring_dedup_keeps_unrelated(tmp_path):
    store = TTSERecordStore(TTSEConfig(store_path=str(tmp_path / "bank.json")))
    assert await store.add_fact("PresentBench grades slides.md not a pptx file") is True
    assert await store.add_fact("compile cxx with cl utf-8 flag on windows") is True
    assert len(store.facts) == 2


@pytest.mark.asyncio
async def test_dream_merge_skips_cross_category_near_duplicates(tmp_path):
    """High-similarity facts in different categories must not share a cluster."""
    emb = FakeEmbedding(
        {
            "grader checks case": [1.0, 0.0],
            "grader is case sensitive": [0.99, 0.01],
        }
    )

    def handler(prompt: str):
        raise AssertionError("LLM merge must not run across categories")

    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        embedding=emb,
        embedding_max_rps=0,
        dream_enabled=True,
        dream_min_hours=0,
        dream_min_rules=1,
        dream_soft_lo=0.72,
        dream_purge_tips_enabled=False,
        dream_prune_enabled=False,
    )
    store, cfg = _make_store(tmp_path, cfg=cfg, embedding=emb)
    llm = ScriptedLLM(handler)
    await store.add_record_direct(
        "fact",
        "grader checks case",
        count=2,
        category="documents-office-and-records",
        save=False,
    )
    await store.add_record_direct(
        "fact",
        "grader is case sensitive",
        count=2,
        category="software-engineering-devops",
        save=False,
    )
    await store.save()

    result, _ = await run_dream_pass(
        store,
        cfg,
        llm=llm,
        model="dummy-model",
        capability_names={"grep"},
    )
    assert not result.skipped
    assert result.merged_clusters == 0
    assert len(store.facts) == 2


@pytest.mark.asyncio
async def test_dream_merge_same_category_inherits_category(tmp_path):
    emb = FakeEmbedding(
        {
            "grader checks case": [1.0, 0.0],
            "grader is case sensitive": [0.99, 0.01],
            "grader cares about case": [0.98, 0.02],
        }
    )
    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        embedding=emb,
        embedding_max_rps=0,
        dream_enabled=True,
        dream_min_hours=0,
        dream_min_rules=1,
        dream_soft_lo=0.72,
        dream_purge_tips_enabled=False,
        dream_prune_enabled=False,
    )

    def handler(prompt: str):
        return (
            "THINKING:\n"
            "All three facts describe grader case sensitivity with high pairwise sims.\n"
            "No mutually exclusive conditions; merge into one canonical statement.\n"
            "REASON: near duplicates\n"
            "VERDICT: MERGE\n"
            "CANONICAL: the grader checks column names case-sensitively\n"
            "KEEP_INDICES:\n"
        )

    store, cfg = _make_store(tmp_path, cfg=cfg, embedding=emb)
    llm = ScriptedLLM(handler)
    for text, count in (
        ("grader checks case", 2),
        ("grader is case sensitive", 2),
        ("grader cares about case", 2),
    ):
        await store.add_record_direct(
            "fact",
            text,
            count=count,
            category="software-engineering-devops",
            save=False,
        )
    await store.save()

    result, _ = await run_dream_pass(
        store,
        cfg,
        llm=llm,
        model="dummy-model",
        capability_names={"grep"},
    )
    assert not result.skipped
    assert result.merged_clusters >= 1
    assert len(store.facts) == 1
    assert store.facts[0]["category"] == "software-engineering-devops"


# ----------------------------------------------------------------------
# embedding rate limit
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# embedding rate limit
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embedding_rate_limit_spaces_api_calls(tmp_path):
    emb = FakeEmbedding()
    max_rps = 10.0
    store = TTSERecordStore(
        TTSEConfig(
            store_path=str(tmp_path / "bank.json"),
            embedding=emb,
            embedding_max_rps=max_rps,
        )
    )
    texts = ["alpha rule", "beta rule", "gamma rule"]
    for text in texts:
        assert await store.embedding_of(text) is not None

    assert len(emb.call_times) == 3
    min_interval = 1.0 / max_rps
    for prev, curr in zip(emb.call_times, emb.call_times[1:]):
        assert curr - prev >= min_interval - 0.02


@pytest.mark.asyncio
async def test_embedding_cache_hit_skips_rate_limit(tmp_path):
    emb = FakeEmbedding()
    store = TTSERecordStore(
        TTSEConfig(
            store_path=str(tmp_path / "bank.json"),
            embedding=emb,
            embedding_max_rps=4.0,
        )
    )
    first = await store.embedding_of("cached text")
    t0 = time.monotonic()
    second = await store.embedding_of("cached text")
    elapsed = time.monotonic() - t0

    assert first == second
    assert len(emb.call_times) == 1
    assert elapsed < 0.05


class CountingBatchEmbedding:
    def __init__(self) -> None:
        self.queries = 0
        self.docs = 0
        self.doc_batch_sizes: list[int] = []

    async def embed_query(self, text: str) -> list[float]:
        self.queries += 1
        return [float(len(text)), 1.0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.docs += 1
        self.doc_batch_sizes.append(len(texts))
        return [[float(len(t)), 1.0] for t in texts]


@pytest.mark.asyncio
async def test_find_duplicate_batches_cold_cache_then_one_miss(tmp_path):
    path = tmp_path / "bank.json"
    path.write_text(
        json.dumps(
            {
                "facts": [
                    {"text": "alpha rule", "count": 1},
                    {"text": "beta rule", "count": 1},
                    {"text": "gamma rule", "count": 1},
                ],
                "tips": [],
                "retired": [],
            }
        ),
        encoding="utf-8",
    )
    emb = CountingBatchEmbedding()
    store = TTSERecordStore(
        TTSEConfig(
            store_path=str(path),
            embedding=emb,
            embedding_max_rps=0,
            dedup_threshold=0.99,
        ),
        embedding=emb,
    )
    assert len(store.facts) == 3

    await store.add_fact("delta rule")
    assert emb.docs == 1
    assert emb.doc_batch_sizes == [4]
    assert emb.queries == 0

    await store.add_fact("epsilon rule")
    assert emb.docs == 2
    assert emb.doc_batch_sizes[-1] == 1
    assert emb.queries == 0


@pytest.mark.asyncio
async def test_find_duplicate_query_only_provider_caches_after_first_add(tmp_path):
    path = tmp_path / "bank.json"
    path.write_text(
        json.dumps(
            {
                "facts": [{"text": "alpha rule", "count": 1}, {"text": "beta rule", "count": 1}],
                "tips": [],
                "retired": [],
            }
        ),
        encoding="utf-8",
    )
    emb = FakeEmbedding()
    store = TTSERecordStore(
        TTSEConfig(store_path=str(path), embedding=emb, embedding_max_rps=0, dedup_threshold=0.99),
        embedding=emb,
    )
    await store.add_fact("gamma rule")
    first_pass = len(emb.call_times)
    assert first_pass == 3
    await store.add_fact("delta rule")
    assert len(emb.call_times) == first_pass + 1


@pytest.mark.asyncio
async def test_emb_cache_lru_evicts_when_over_limit(tmp_path):
    emb = FakeEmbedding()
    store = TTSERecordStore(
        TTSEConfig(
            store_path=str(tmp_path / "bank.json"),
            embedding=emb,
            embedding_max_rps=0,
            max_facts=1,
            max_tips=0,
        ),
        embedding=emb,
    )
    limit = store._embedding_cache_limit()
    assert limit == 101
    for i in range(limit + 10):
        await store.embedding_of(f"query-{i}")
    assert len(store._emb_cache) == limit
    assert "query-0" not in store._emb_cache
    assert f"query-{limit + 9}" in store._emb_cache


@pytest.mark.asyncio
async def test_emb_cache_drops_deleted_and_reloaded_texts(tmp_path):
    path = tmp_path / "bank.json"
    emb = FakeEmbedding(
        mapping={
            "keep me": [1.0, 0.0],
            "drop me": [0.0, 1.0],
        }
    )
    store = TTSERecordStore(
        TTSEConfig(store_path=str(path), embedding=emb, embedding_max_rps=0, max_facts=10, max_tips=10),
        embedding=emb,
    )
    await store.add_fact("keep me")
    await store.add_fact("drop me")
    assert "keep me" in store._emb_cache
    assert "drop me" in store._emb_cache

    assert await store.delete_record("drop me", "fact") == 1
    assert "drop me" not in store._emb_cache
    assert "keep me" in store._emb_cache

    path.write_text(
        json.dumps({"facts": [{"text": "replacement", "count": 1}], "tips": [], "retired": []}),
        encoding="utf-8",
    )
    store.reload()
    assert "keep me" not in store._emb_cache
    assert "replacement" not in store._emb_cache
