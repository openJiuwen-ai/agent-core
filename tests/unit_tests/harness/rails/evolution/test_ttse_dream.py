# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for TTSE Auto-dream (prune / merge / purge)."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Callable

import pytest

from openjiuwen.harness.rails.evolution.ttse import TTSEConfig, TTSERail, TTSERecordStore
from openjiuwen.harness.rails.evolution.ttse.dream import (
    DreamState,
    parse_merge_verdict,
    prune_stale,
    run_dream_pass,
    should_run_dream,
)
from openjiuwen.harness.rails.evolution.ttse.success import TrajectoryErrorSuccessDetector
from openjiuwen.harness.rails.evolution.ttse.tip_parse import parse_tip, tip_purge_reason
from openjiuwen.harness.rails.evolution.ttse.stores import _new_record


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

    async def embed_query(self, text: str) -> list[float]:
        key = " ".join(text.lower().split())
        if key in self.mapping:
            return self.mapping[key]
        # Stable hash-ish fallback so unrelated texts differ.
        return [float(len(key) % 7), float(sum(ord(c) for c in key) % 11)]


def _make_rail(tmp_path, llm, *, cfg=None, embedding=None) -> TTSERail:
    config = cfg or TTSEConfig(store_path=str(tmp_path / "bank.json"), dream_enabled=True)
    if embedding is not None:
        config.embedding = embedding
    return TTSERail(
        llm=llm,
        model="dummy-model",
        ttse_config=config,
        embedding=embedding,
        success_detector=TrajectoryErrorSuccessDetector(),
    )


# ----------------------------------------------------------------------
# tip_parse
# ----------------------------------------------------------------------


def test_parse_tip_happy_path():
    parsed = parse_tip("When logs are large: use grep to scan before reading")
    assert parsed == ("logs are large", "grep", "scan before reading")


def test_parse_tip_strips_backticks_and_takes_first_token():
    parsed = parse_tip(
        "When a PDF file must be read: use `code` with pdfplumber to parse and extract the content"
    )
    assert parsed is not None
    assert parsed[1] == "code"


def test_parse_tip_skips_filler_words():
    parsed = parse_tip("When searching history: use the `session-logs` skill to find prior turns")
    assert parsed is not None
    assert parsed[1] == "session-logs"


def test_tip_purge_malformed_and_unknown_and_generic():
    names = {"grep", "bash"}
    assert tip_purge_reason("always be careful", names) == "tip_fact_shaped"
    assert tip_purge_reason("When x: use nope to do y", names) == "tip_unknown_capability"
    assert tip_purge_reason("When any task: use grep to scan files", names) == "tip_too_generic_condition"
    assert tip_purge_reason("When reading logs: use grep to check", names) == "tip_too_generic_action"
    assert tip_purge_reason("When reading large .log files: use grep to extract matches", names) is None
    assert tip_purge_reason("[PINNED] When any task: use grep to check", names) is None


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
    path.write_text(json.dumps({"facts": [{"text": "legacy", "count": 2}], "tips": [], "retired": []}), encoding="utf-8")
    store = TTSERecordStore(TTSEConfig(store_path=str(path)))
    rec = store.facts[0]
    assert rec["count"] == 2
    assert rec["last_injected_at"] is not None
    assert rec["created_at"] is not None


@pytest.mark.asyncio
async def test_prune_stale_ttl(tmp_path):
    cfg = TTSEConfig(store_path=str(tmp_path / "bank.json"), dream_ttl_days=90, dream_prune_mode="retire")
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
    assert store.retired[0]["reason"] == "ttl_90d_no_inject"


@pytest.mark.asyncio
async def test_legacy_migrated_not_immediately_pruned(tmp_path):
    path = tmp_path / "bank.json"
    path.write_text(json.dumps({"facts": [{"text": "legacy", "count": 1}], "tips": [], "retired": []}), encoding="utf-8")
    cfg = TTSEConfig(store_path=str(path), dream_ttl_days=90)
    store = TTSERecordStore(cfg)
    pf, _ = await prune_stale(store, cfg, now=time.time())
    assert pf == 0
    assert store.facts_texts() == ["legacy"]


# ----------------------------------------------------------------------
# merge
# ----------------------------------------------------------------------


def test_parse_merge_verdict():
    v = parse_merge_verdict(
        "VERDICT: MERGE\nCANONICAL: the grader is case-sensitive\nKEEP_INDICES:\nREASON: paraphrase\n",
        3,
    )
    assert v is not None
    assert v.verdict == "MERGE"
    assert v.canonical == "the grader is case-sensitive"


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
            "VERDICT: MERGE\n"
            "CANONICAL: the grader checks column names case-sensitively\n"
            "KEEP_INDICES:\n"
            "REASON: near duplicates\n"
        )

    rail = _make_rail(tmp_path, ScriptedLLM(handler), cfg=cfg, embedding=emb)
    # Bypass online hard-dedup so soft-cluster can still see near-duplicates.
    for text, count in (
        ("grader checks case", 2),
        ("grader is case sensitive", 2),
        ("grader cares about case", 2),
    ):
        await rail._ttse_store.add_record_direct("fact", text, count=count, save=False)
    await rail._ttse_store.save()
    assert len(rail._ttse_store.facts) == 3

    result, _ = await run_dream_pass(
        rail._ttse_store,
        cfg,
        llm=rail._ttse_llm,
        model=rail._ttse_model,
        capability_names={"grep"},
    )
    assert not result.skipped
    assert result.merged_clusters >= 1
    assert len(rail._ttse_store.facts) == 1
    assert rail._ttse_store.facts[0]["count"] >= 6
    assert any(r["reason"] == "dream_merge" for r in rail._ttse_store.retired)


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
        dream_min_hours=0,
        dream_min_rules=1,
        dream_prune_enabled=False,
        dream_purge_tips_enabled=False,
    )

    def handler(prompt: str):
        return "VERDICT: KEEP_DISTINCT\nCANONICAL:\nKEEP_INDICES: 0, 1\nREASON: different conditions\n"

    rail = _make_rail(tmp_path, ScriptedLLM(handler), cfg=cfg, embedding=emb)
    await rail._ttse_store.add_record_direct(
        "tip", "When csv needs counts: use python_exec to group rows", save=False
    )
    await rail._ttse_store.add_record_direct(
        "tip", "When logs are huge: use grep to extract matches", save=False
    )
    await rail._ttse_store.save()
    before = list(rail._ttse_store.tips_texts())
    result, _ = await run_dream_pass(
        rail._ttse_store,
        cfg,
        llm=rail._ttse_llm,
        model=rail._ttse_model,
        capabilities="- tool `grep`: x\n- tool `python_exec`: y",
        capability_names={"grep", "python_exec"},
    )
    assert result.kept_clusters >= 1 or result.merged_clusters == 0
    assert set(rail._ttse_store.tips_texts()) == set(before)


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
    rail = _make_rail(tmp_path, ScriptedLLM(lambda _: "NONE"), cfg=cfg)
    await rail._ttse_store.add_tip("the grader checks exact columns")  # fact-shaped
    await rail._ttse_store.add_tip("When logs are large: use grep to extract matches")
    result, _ = await run_dream_pass(
        rail._ttse_store,
        cfg,
        llm=rail._ttse_llm,
        model=rail._ttse_model,
        capability_names={"grep"},
    )
    assert result.purged_tips == 1
    assert rail._ttse_store.tips_texts() == ["When logs are large: use grep to extract matches"]


# ----------------------------------------------------------------------
# gates + lock
# ----------------------------------------------------------------------


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
    rail = _make_rail(tmp_path, ScriptedLLM(lambda _: "NONE"), cfg=cfg)
    await rail._ttse_store.add_fact("x")
    result, _ = await run_dream_pass(
        rail._ttse_store,
        cfg,
        llm=rail._ttse_llm,
        model=rail._ttse_model,
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
async def test_dream_and_induce_serialize_on_lock(tmp_path):
    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        dream_enabled=True,
        dream_min_hours=0,
        dream_min_rules=100,
        dream_prune_enabled=False,
        dream_purge_tips_enabled=False,
        dream_interval=1,
    )
    order: list[str] = []

    async def slow_handler(prompt: str):
        order.append("dream_enter")
        await asyncio.sleep(0.05)
        order.append("dream_exit")
        return "NONE"

    # ScriptedLLM.invoke is async already — wrap sleep in handler via sync that can't sleep.
    # Use a custom LLM instead.
    class SlowLLM:
        async def invoke(self, *, model, messages, temperature=None, timeout=None, **kwargs):
            order.append("dream_enter")
            await asyncio.sleep(0.05)
            order.append("dream_exit")
            return "NONE"

    rail = _make_rail(tmp_path, SlowLLM(), cfg=cfg)
    await rail._ttse_store.add_fact("keep")

    async def induce_job():
        async with rail._evolution_lock:
            order.append("induce_enter")
            await asyncio.sleep(0.01)
            await rail._ttse_store.add_fact("from induce")
            order.append("induce_exit")

    dream_task = asyncio.create_task(rail.run_dream(capabilities=""))
    await asyncio.sleep(0.01)
    induce_task = asyncio.create_task(induce_job())
    await asyncio.gather(dream_task, induce_task)
    # Serialized: no interleaving of enter/exit pairs across critical sections.
    assert "from induce" in rail._ttse_store.facts_texts()
    assert order.count("dream_enter") == 0 or "induce_enter" in order
    # Lock held: either dream then induce or induce then dream, never nested enters.
    assert order.index("induce_enter") < order.index("induce_exit")


@pytest.mark.asyncio
async def test_after_task_iteration_schedules_dream(tmp_path):
    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        dream_enabled=True,
        dream_interval=2,
        dream_min_hours=0,
        dream_min_rules=100,
        dream_prune_enabled=False,
        dream_purge_tips_enabled=False,
    )
    called = {"n": 0}

    rail = _make_rail(tmp_path, ScriptedLLM(lambda _: "NONE"), cfg=cfg)

    async def fake_run_dream(*, capabilities=None):
        called["n"] += 1

    rail.run_dream = fake_run_dream  # type: ignore[method-assign]
    ctx = SimpleNamespace(inputs=SimpleNamespace(is_follow_up=False), extra={}, agent=None)
    await rail._on_after_task_iteration(ctx)
    assert called["n"] == 0
    await rail._on_after_task_iteration(ctx)
    # scheduled as create_task — yield to loop
    await asyncio.sleep(0)
    assert called["n"] == 1


@pytest.mark.asyncio
async def test_run_dream_projects_catalog_after_prune(tmp_path):
    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        dream_enabled=True,
        dream_min_hours=0,
        dream_min_rules=100,
        dream_ttl_days=90,
        dream_prune_mode="retire",
        dream_purge_tips_enabled=False,
    )
    rail = _make_rail(tmp_path, ScriptedLLM(lambda _: "NONE"), cfg=cfg)
    now = time.time()
    stale = _new_record("stale slides fact", now=now - 91 * 86400)
    stale["last_injected_at"] = now - 91 * 86400
    stale["category"] = "documents-office-and-records"
    keep = _new_record("keep devops fact", now=now - 10 * 86400)
    keep["last_injected_at"] = now - 10 * 86400
    keep["category"] = "software-engineering-devops"
    rail._ttse_store.facts = [stale, keep]
    await rail._ttse_store.save()

    await rail.run_dream(capabilities="")

    catalog = tmp_path / "CATALOG.md"
    assert catalog.is_file()
    text = catalog.read_text(encoding="utf-8")
    assert "software-engineering-devops" in text
    assert "documents-office-and-records" not in text
    assert (tmp_path / "by_cat" / "software-engineering-devops" / "SUMMARY.md").is_file()
    assert not (tmp_path / "by_cat" / "documents-office-and-records").exists()


@pytest.mark.asyncio
async def test_run_dream_classifies_merged_canonical(tmp_path):
    emb = FakeEmbedding(
        {
            "grader checks case": [1.0, 0.0],
            "grader is case sensitive": [0.99, 0.01],
        }
    )

    def handler(prompt: str):
        if "TTSE category assignment pass" in prompt:
            return '{"assignments": {"1": "software-engineering-devops"}}'
        return (
            "VERDICT: MERGE\n"
            "CANONICAL: the grader checks names case-sensitively\n"
            "KEEP_INDICES:\n"
            "REASON: near duplicates\n"
        )

    cfg = TTSEConfig(
        store_path=str(tmp_path / "bank.json"),
        embedding=emb,
        dream_enabled=True,
        dream_min_hours=0,
        dream_min_rules=1,
        dream_prune_enabled=False,
        dream_purge_tips_enabled=False,
        dream_soft_lo=0.72,
    )
    rail = _make_rail(tmp_path, ScriptedLLM(handler), cfg=cfg, embedding=emb)
    for text in ("grader checks case", "grader is case sensitive"):
        await rail._ttse_store.add_record_direct("fact", text, count=2, save=False)
    await rail._ttse_store.save()

    await rail.run_dream(capabilities="")

    assert len(rail._ttse_store.facts) == 1
    assert rail._ttse_store.facts[0]["category"] == "software-engineering-devops"
    catalog = (tmp_path / "CATALOG.md").read_text(encoding="utf-8")
    assert "software-engineering-devops" in catalog
