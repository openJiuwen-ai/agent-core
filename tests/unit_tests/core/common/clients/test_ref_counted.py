# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest

from openjiuwen.core.common.clients.ref_counted import (
    BaseRefResourceMgr,
    RefCountedResource,
)


class FakeResource(RefCountedResource):
    """Minimal concrete resource that counts close calls."""

    def __init__(self):
        super().__init__()
        self.close_calls = 0

    async def _do_close(self, **kwargs):
        self.close_calls += 1


class FakeResourceMgr(BaseRefResourceMgr[FakeResource]):
    """Minimal concrete manager keyed by a plain string config."""

    def __init__(self):
        super().__init__()
        self.created = 0

    def _get_resource_key(self, config) -> str:
        return str(config)

    async def _create_resource(self, config) -> FakeResource:
        self.created += 1
        return FakeResource()


class TestRelease:
    @pytest.mark.asyncio
    async def test_release_unknown_key_returns_silently(self):
        """Releasing a config that was never acquired must not raise KeyError."""
        mgr = FakeResourceMgr()

        await mgr.release("never-acquired")

    @pytest.mark.asyncio
    async def test_release_after_close_all_returns_silently(self):
        """Release after close_all (key removed from registry) must not raise."""
        mgr = FakeResourceMgr()

        resource, is_new = await mgr.acquire("cfg-a")
        assert is_new
        await mgr.close_all()

        await mgr.release("cfg-a")

    @pytest.mark.asyncio
    async def test_release_closed_resource_returns_silently(self):
        """Release of a resource already closed must be a no-op, not an error."""
        mgr = FakeResourceMgr()

        resource, _ = await mgr.acquire("cfg-a")
        await resource.close()

        await mgr.release("cfg-a")

    @pytest.mark.asyncio
    async def test_release_closes_resource_when_last_ref_dropped(self):
        """Last release must close the resource and remove it from the registry."""
        mgr = FakeResourceMgr()

        resource, _ = await mgr.acquire("cfg-a")
        await mgr.release("cfg-a")

        assert resource.close_calls == 1
        assert resource.closed
        stats = await mgr.get_stats()
        assert stats["total_resources"] == 0

    @pytest.mark.asyncio
    async def test_release_keeps_resource_when_refs_remain(self):
        """Shared references must keep the resource alive after one release."""
        mgr = FakeResourceMgr()

        resource1, _ = await mgr.acquire("cfg-a")
        resource2, _ = await mgr.acquire("cfg-a")
        await mgr.release("cfg-a")

        assert not resource1.closed
        assert resource1.ref_count == 1
        stats = await mgr.get_stats()
        assert stats["total_resources"] == 1
        # Same shared instance
        assert resource1 is resource2

    @pytest.mark.asyncio
    async def test_double_release_last_ref_is_silent_noop(self):
        """A second release of the same config must not raise KeyError."""
        mgr = FakeResourceMgr()

        resource, _ = await mgr.acquire("cfg-a")
        await mgr.release("cfg-a")
        await mgr.release("cfg-a")

        assert resource.close_calls == 1
