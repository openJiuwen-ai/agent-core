# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_experience_store — ExperienceStore 单元测试。"""

from __future__ import annotations

import tempfile
import time
from unittest import IsolatedAsyncioTestCase

from openjiuwen.auto_harness.experience.experience_store import (
    ExperienceStore,
    _count_hits,
    _recency_score,
    _tokenize,
)
from openjiuwen.auto_harness.schema import (
    Experience,
    ExperienceType,
)


class TestExperienceStoreRecord(IsolatedAsyncioTestCase):
    async def test_record_and_get(self):
        with tempfile.TemporaryDirectory() as d:
            store = ExperienceStore(d)
            exp = Experience(
                type=ExperienceType.OPTIMIZATION,
                topic="fix timeout",
                summary="increased limit",
            )
            exp_id = await store.record(exp)
            assert exp_id == exp.id

            got = await store.get(exp_id)
            assert got is not None
            assert got.topic == "fix timeout"

    async def test_dedup_within_24h(self):
        with tempfile.TemporaryDirectory() as d:
            store = ExperienceStore(d)
            e1 = Experience(
                type=ExperienceType.FAILURE,
                topic="same topic",
            )
            e2 = Experience(
                type=ExperienceType.FAILURE,
                topic="same topic",
            )
            r1 = await store.record(e1)
            r2 = await store.record(e2)
            assert r1 != ""
            assert r2 == ""

    async def test_different_type_not_dedup(self):
        with tempfile.TemporaryDirectory() as d:
            store = ExperienceStore(d)
            e1 = Experience(
                type=ExperienceType.FAILURE,
                topic="topic",
            )
            e2 = Experience(
                type=ExperienceType.OPTIMIZATION,
                topic="topic",
            )
            r1 = await store.record(e1)
            r2 = await store.record(e2)
            assert r1 != ""
            assert r2 != ""


class TestExperienceStoreSearch(IsolatedAsyncioTestCase):
    async def test_keyword_search(self):
        with tempfile.TemporaryDirectory() as d:
            store = ExperienceStore(d)
            await store.record(Experience(
                type=ExperienceType.OPTIMIZATION,
                topic="fix timeout bug",
                summary="increased limit to 300s",
            ))
            await store.record(Experience(
                type=ExperienceType.INSIGHT,
                topic="refactor logging",
                summary="switched to structlog",
            ))
            results = await store.search("timeout")
            assert len(results) == 1
            assert results[0].topic == "fix timeout bug"

    async def test_empty_query(self):
        with tempfile.TemporaryDirectory() as d:
            store = ExperienceStore(d)
            await store.record(Experience(topic="x"))
            results = await store.search("")
            assert results == []

    async def test_top_k(self):
        with tempfile.TemporaryDirectory() as d:
            store = ExperienceStore(d)
            for i in range(5):
                await store.record(Experience(
                    type=ExperienceType.OPTIMIZATION,
                    topic=f"fix bug {i}",
                    summary=f"bug fix {i}",
                    id=f"id-{i}",
                ))
            results = await store.search("fix", top_k=2)
            assert len(results) == 2


class TestExperienceStoreListRecent(IsolatedAsyncioTestCase):
    async def test_list_recent(self):
        with tempfile.TemporaryDirectory() as d:
            store = ExperienceStore(d)
            await store.record(Experience(
                topic="old",
                id="old-1",
                timestamp=time.time() - 1000,
            ))
            await store.record(Experience(
                topic="new",
                id="new-1",
                timestamp=time.time(),
            ))
            recent = await store.list_recent(limit=1)
            assert len(recent) == 1
            assert recent[0].id == "new-1"

    async def test_get_nonexistent(self):
        with tempfile.TemporaryDirectory() as d:
            store = ExperienceStore(d)
            assert await store.get("nope") is None


class TestScoringHelpers(IsolatedAsyncioTestCase):
    def test_tokenize(self):
        tokens = _tokenize("Fix the BUG now")
        assert "fix" in tokens
        assert "the" in tokens
        assert "bug" in tokens

    def test_tokenize_drops_short(self):
        tokens = _tokenize("a bb ccc")
        assert "a" not in tokens
        assert "bb" in tokens

    def test_count_hits(self):
        exp = Experience(
            topic="fix timeout",
            summary="increased limit",
            details="was 60s",
        )
        assert _count_hits(["fix", "timeout"], exp) == 2
        assert _count_hits(["missing"], exp) == 0

    def test_recency_score_recent(self):
        now = time.time()
        score = _recency_score(now - 60, now)
        assert score > 0.99

    def test_recency_score_old(self):
        now = time.time()
        score = _recency_score(now - 31 * 86400, now)
        assert score == 0.0
