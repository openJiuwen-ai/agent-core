# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for RedisCheckpointerProvider.
"""

import pytest

from openjiuwen.extensions.checkpointer.redis.checkpointer import (
    RedisCheckpointer,
    RedisCheckpointerProvider,
)


@pytest.mark.asyncio
async def test_provider_create_with_redis_client(redis_client):
    """Test creating checkpointer with redis_client."""
    provider = RedisCheckpointerProvider()
    conf = {"connection": {"redis_client": redis_client}}
    checkpointer = await provider.create(conf)

    assert isinstance(checkpointer, RedisCheckpointer)
    assert checkpointer._redis_store.redis is redis_client


@pytest.mark.asyncio
async def test_provider_create_with_redis_url():
    """Test creating checkpointer with redis_url."""
    provider = RedisCheckpointerProvider()
    conf = {"connection": {"url": "redis://127.0.0.1:6379"}}
    checkpointer = await provider.create(conf)

    assert isinstance(checkpointer, RedisCheckpointer)
    assert checkpointer._redis_store is not None
    assert checkpointer._redis_store.redis is not None
    await checkpointer._redis_store.redis.aclose()


@pytest.mark.asyncio
async def test_provider_create_with_ttl(redis_client):
    """Test creating checkpointer with TTL configuration."""
    provider = RedisCheckpointerProvider()
    ttl = {
        "default_ttl": 5,
        "refresh_on_read": True,
    }
    conf = {
        "connection": {"redis_client": redis_client},
        "ttl": ttl
    }
    checkpointer = await provider.create(conf)

    assert isinstance(checkpointer, RedisCheckpointer)
    assert checkpointer._agent_storage._ttl_seconds == 5 * 60
    assert checkpointer._agent_storage._refresh_on_read is True


@pytest.mark.asyncio
async def test_provider_create_without_redis_client_or_url():
    """Test creating checkpointer without redis_client or url raises error."""
    provider = RedisCheckpointerProvider()
    conf = {}

    with pytest.raises(ValueError, match="connection"):
        await provider.create(conf)
