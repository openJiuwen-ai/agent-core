# coding: utf-8

"""Tests for MtimeSectionCache.

These tests use plain in-memory mocks; the cache is intentionally
agnostic of any database or rail concern.
"""

from __future__ import annotations

from typing import Optional

import pytest

from openjiuwen.agent_teams.agent.team_section_cache import MtimeSectionCache
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from tests.test_logger import logger


def _make_section(text: str) -> PromptSection:
    return PromptSection(name="probe-test", content={"cn": text}, priority=50)


class _Counter:
    """Tracks how many times the probe and the fetch fired."""

    def __init__(self, mtime: int = 0, section: Optional[PromptSection] = None):
        self._mtime = mtime
        self._section = section
        self.probe_calls = 0
        self.fetch_calls = 0

    async def probe(self) -> int:
        self.probe_calls += 1
        return self._mtime

    async def fetch(self) -> Optional[PromptSection]:
        self.fetch_calls += 1
        return self._section

    def set_mtime(self, mtime: int) -> None:
        self._mtime = mtime

    def set_section(self, section: Optional[PromptSection]) -> None:
        self._section = section


class TestFirstCallIsMiss:
    @pytest.mark.asyncio
    async def test_zero_mtime_still_loads(self):
        """First refresh always fetches even when probe returns 0."""
        counter = _Counter(mtime=0, section=_make_section("hello"))
        cache = MtimeSectionCache(probe=counter.probe, fetch_and_build=counter.fetch)

        section = await cache.refresh()
        assert section is not None
        assert section.render("cn") == "hello"
        assert counter.probe_calls == 1
        assert counter.fetch_calls == 1
        logger.info("First refresh triggered fetch")

    @pytest.mark.asyncio
    async def test_first_call_caches_none_section(self):
        """A None result is still considered initialized for cache hits."""
        counter = _Counter(mtime=42, section=None)
        cache = MtimeSectionCache(probe=counter.probe, fetch_and_build=counter.fetch)

        result = await cache.refresh()
        assert result is None
        assert counter.fetch_calls == 1


class TestCacheHit:
    @pytest.mark.asyncio
    async def test_same_mtime_skips_fetch(self):
        counter = _Counter(mtime=100, section=_make_section("v1"))
        cache = MtimeSectionCache(probe=counter.probe, fetch_and_build=counter.fetch)

        first = await cache.refresh()
        second = await cache.refresh()
        third = await cache.refresh()

        assert first is second is third
        assert counter.probe_calls == 3  # probe runs every call
        assert counter.fetch_calls == 1  # but fetch only once
        logger.info("Cache hit: 3 probes, 1 fetch")

    @pytest.mark.asyncio
    async def test_cache_hit_when_fetch_returned_none(self):
        """Even when fetch returns None, repeated probes don't refire it."""
        counter = _Counter(mtime=7, section=None)
        cache = MtimeSectionCache(probe=counter.probe, fetch_and_build=counter.fetch)

        await cache.refresh()
        await cache.refresh()
        await cache.refresh()

        assert counter.fetch_calls == 1


class TestCacheMiss:
    @pytest.mark.asyncio
    async def test_mtime_change_triggers_refetch(self):
        counter = _Counter(mtime=10, section=_make_section("v1"))
        cache = MtimeSectionCache(probe=counter.probe, fetch_and_build=counter.fetch)

        first = await cache.refresh()
        assert first.render("cn") == "v1"

        counter.set_mtime(11)
        counter.set_section(_make_section("v2"))
        second = await cache.refresh()
        assert second.render("cn") == "v2"
        assert counter.fetch_calls == 2
        logger.info("mtime bump triggered refetch")

    @pytest.mark.asyncio
    async def test_each_change_triggers_one_refetch(self):
        counter = _Counter(mtime=1, section=_make_section("a"))
        cache = MtimeSectionCache(probe=counter.probe, fetch_and_build=counter.fetch)

        await cache.refresh()
        for i in range(2, 6):
            counter.set_mtime(i)
            counter.set_section(_make_section(chr(ord("a") + i - 1)))
            await cache.refresh()

        assert counter.fetch_calls == 5

    @pytest.mark.asyncio
    async def test_cache_hit_after_miss(self):
        """A miss-then-hit-then-hit pattern only fetches twice (initial + bump)."""
        counter = _Counter(mtime=1, section=_make_section("init"))
        cache = MtimeSectionCache(probe=counter.probe, fetch_and_build=counter.fetch)

        await cache.refresh()
        await cache.refresh()

        counter.set_mtime(2)
        counter.set_section(_make_section("bumped"))
        await cache.refresh()
        await cache.refresh()
        await cache.refresh()

        assert counter.fetch_calls == 2


class TestInvalidate:
    @pytest.mark.asyncio
    async def test_invalidate_forces_refetch(self):
        counter = _Counter(mtime=1, section=_make_section("v1"))
        cache = MtimeSectionCache(probe=counter.probe, fetch_and_build=counter.fetch)

        await cache.refresh()
        cache.invalidate()
        await cache.refresh()

        assert counter.fetch_calls == 2

    @pytest.mark.asyncio
    async def test_invalidate_resets_to_uninitialized(self):
        counter = _Counter(mtime=99, section=_make_section("payload"))
        cache = MtimeSectionCache(probe=counter.probe, fetch_and_build=counter.fetch)

        await cache.refresh()
        cache.invalidate()
        # mtime stays at 99 in the probe; without invalidate this would
        # be a hit, but invalidate cleared the initialized flag.
        await cache.refresh()
        assert counter.fetch_calls == 2
