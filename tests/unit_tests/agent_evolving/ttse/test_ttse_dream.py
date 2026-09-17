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
    bump_dream_session_count,
    load_dream_state,
    parse_merge_verdict,
    prune_stale,
    run_dream_pass,
    should_run_dream,
)
from openjiuwen.agent_evolving.ttse.tip_parse import parse_tip, tip_purge_reason
from openjiuwen.agent_evolving.ttse.stores import _new_record


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


def test_tip_purge_malformed_and_unknown_and_generic():
    names = {"grep", "bash"}
    assert tip_purge_reason("always be careful", names) == "tip_fact_shaped"
    assert tip_purge_reason("When x: use nope to do y", names) == "tip_unknown_capability"
    assert tip_purge_reason("When any task: use grep to scan files", names) == "tip_too_generic_condition"
    assert tip_purge_reason("When reading logs: use grep to check", names) == "tip_too_generic_action"
    assert tip_purge_reason("When reading large .log files: use grep to extract matches", names) is None
    assert tip_purge_reason("[PINNED] When any task: use grep to check", names) is None


def test_tip_purge_fullwidth_colon_not_malformed():
    names = {"ask_user", "grep"}
    assert (
        tip_purge_reason(
            "When 估值请求只包含公司名而缺少上市状态、行业或财务数据：use ask_user to "
            "先向用户收集这些信息，再决定安装哪个估值技能",
            names,
        )
        is None
    )


def test_tip_purge_longest_whitelist_match_on_noisy_span():
    names = {"code", "web_search", "fetch_webpage"}
    # Backticks + trailing junk after capability.
    assert (
        tip_purge_reason(
            "When a PDF must be read: use `code` with pdfplumber to parse content",
            names,
        )
        is None
    )
    # Long adverbial between name and "to" — still matches web_search.
    assert (
        tip_purge_reason(
            "When researching a niche product: use web_search with multiple query "
            "variations (a, b, c) to gather information",
            names,
        )
        is None
    )
    assert tip_purge_reason("When fetching a URL: use `fetch_webpage` to get HTML", names) is None
    assert tip_purge_reason("When coding: use decode to transform bytes", names) == "tip_unknown_capability"


# ----------------------------------------------------------------------
# metadata / inject clock
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_new_record_has_metadata(tmp_path):
    store = TTSERecordStore(TTSEConfig(store_path=str(tmp_path / "bank.json")))
    await store.add_fact("a fact")
    rec = store.facts[0]
    assert rec["count"] == 1
    assert rec["created_at"] is not None
    assert rec["last_injected_at"] is None
    assert rec["inject_hits"] == 0


@pytest.mark.asyncio
async def test_legacy_bank_migration_sets_last_injected(tmp_path):
    path = tmp_path / "bank.json"
    path.write_text(
        json.dumps({"facts": [{"text": "legacy", "count": 2}], "tips": [], "retired": []}), encoding="utf-8"
    )
    store = TTSERecordStore(TTSEConfig(store_path=str(path)))
    rec = store.facts[0]
    assert rec["count"] == 2
    assert rec["last_injected_at"] is not None
    assert rec["created_at"] is not None


@pytest.mark.asyncio
async def test_prune_stale_ttl(tmp_path):
    cfg = TTSEConfig(store_path=str(tmp_path / "bank.json"), dream_ttl_days=90)
    store = TTSERecordStore(cfg)
    now = time.time()
    old = _new_record("old fact", now=now - 91 * 86400)
    old["last_injected_at"] = now - 91 * 86400
    fresh = _new_record("fresh fact", now=now - 10 * 86400)
    fresh["last_injected_at"] = now - 10 * 86400
    store.facts = [old, fresh]
    pf, pt = await prune_stale(store, cfg, now=now)
    assert pf == 1 and pt == 0
    assert store.facts_texts() == ["fresh fact"]
    assert store.retired == []


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
    assert rec["last_injected_at"] == pytest.approx(now)

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
    assert reloaded.facts[0]["last_injected_at"] == pytest.approx(now)


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
    rec["last_injected_at"] = stale_ts
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
    assert kept["last_injected_at"] == pytest.approx(now)
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
    assert rec_a["last_injected_at"] == pytest.approx(now)


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
        "KEEP_INDICES:\n",
        3,
    )
    assert v is not None
    assert v.verdict == "MERGE"
    assert v.canonical == "the grader is case-sensitive"
    assert "paraphrases" in v.thinking
    assert v.reason == "paraphrase"


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
        dream_max_llm_merges=5,
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
# purge tips
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
    result, _ = await run_dream_pass(
        store,
        cfg,
        llm=ScriptedLLM(lambda _: "NONE"),
        model="dummy-model",
        capability_names={"grep"},
    )
    assert result.purged_tips == 1
    assert store.tips_texts() == ["When logs are large: use grep to extract matches"]
    assert store.retired == []


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
async def test_dream_without_embedding_still_prunes(tmp_path):
    """No embedding: prune still runs; single stale rule yields no BM25 merge."""
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
    stale["last_injected_at"] = now - 100 * 86400
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
async def test_dream_merge_bm25_without_embedding(tmp_path):
    """Near-duplicate facts cluster via BM25 when no embedding provider is set."""

    def handler(prompt: str):
        return (
            "THINKING:\n"
            "Two facts describe PresentBench slide grading with high BM25 overlap.\n"
            "REASON: near duplicates\n"
            "VERDICT: MERGE\n"
            "CANONICAL: PresentBench grades slides.md not a pptx file\n"
            "KEEP_INDICES:\n"
        )

    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        dream_enabled=True,
        dream_min_hours=0,
        dream_min_rules=1,
        bm25_sim_threshold=0.5,
        dream_max_llm_merges=5,
        dream_purge_tips_enabled=False,
        dream_prune_enabled=False,
    )
    store, cfg = _make_store(tmp_path, cfg=cfg)
    llm = ScriptedLLM(handler)
    for text, count in (
        ("PresentBench grades slides.md not a pptx file", 2),
        ("PresentBench grades slides.md rather than pptx", 2),
    ):
        await store.add_record_direct("fact", text, count=count, save=False)
    await store.save()
    assert len(store.facts) == 2

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
    assert llm.calls


@pytest.mark.asyncio
async def test_dream_merge_bm25_skips_cross_category(tmp_path):
    """BM25-near-duplicate facts in different categories must not share a cluster."""

    def handler(prompt: str):
        raise AssertionError("LLM merge must not run across categories")

    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        dream_enabled=True,
        dream_min_hours=0,
        dream_min_rules=1,
        bm25_sim_threshold=0.5,
        dream_max_llm_merges=5,
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
async def test_induction_bm25_dedup_merges_near_duplicate(tmp_path):
    store = TTSERecordStore(
        TTSEConfig(store_path=str(tmp_path / "bank.json"), bm25_sim_threshold=0.5)
    )
    assert await store.add_fact("PresentBench grades slides.md not a pptx file") is True
    assert await store.add_fact("PresentBench grades slides.md rather than pptx") is False
    assert len(store.facts) == 1
    assert store.facts[0]["count"] == 2


@pytest.mark.asyncio
async def test_induction_bm25_dedup_keeps_unrelated(tmp_path):
    store = TTSERecordStore(
        TTSEConfig(store_path=str(tmp_path / "bank.json"), bm25_sim_threshold=0.5)
    )
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
        dream_max_llm_merges=5,
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
        dream_max_llm_merges=5,
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
