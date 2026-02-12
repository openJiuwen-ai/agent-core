# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for InMemoryKVStore."""

import asyncio
import time

import pytest

from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore


class TestInMemoryKVStoreBasicOperations:
    """Tests for basic key-value operations."""

    @pytest.mark.asyncio
    async def test_set_and_get(self):
        """Test setting and getting a value."""
        store = InMemoryKVStore()
        await store.set("key1", "value1")

        result = await store.get("key1")
        assert result == "value1"

    @pytest.mark.asyncio
    async def test_set_overwrites_existing_value(self):
        """Test that set overwrites an existing value."""
        store = InMemoryKVStore()
        await store.set("key1", "value1")
        await store.set("key1", "value2")

        result = await store.get("key1")
        assert result == "value2"

    @pytest.mark.asyncio
    async def test_get_nonexistent_key_returns_none(self):
        """Test getting a non-existent key returns None."""
        store = InMemoryKVStore()

        result = await store.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_and_get_bytes(self):
        """Test setting and getting bytes values."""
        store = InMemoryKVStore()
        await store.set("key1", b"bytes_value")

        result = await store.get("key1")
        assert result == b"bytes_value"

    @pytest.mark.asyncio
    async def test_delete_existing_key(self):
        """Test deleting an existing key."""
        store = InMemoryKVStore()
        await store.set("key1", "value1")
        await store.delete("key1")

        result = await store.get("key1")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_key_no_error(self):
        """Test deleting a non-existent key doesn't raise an error."""
        store = InMemoryKVStore()
        await store.delete("nonexistent")  # Should not raise

    @pytest.mark.asyncio
    async def test_exists_true(self):
        """Test exists returns True for existing key."""
        store = InMemoryKVStore()
        await store.set("key1", "value1")

        result = await store.exists("key1")
        assert result is True

    @pytest.mark.asyncio
    async def test_exists_false(self):
        """Test exists returns False for non-existent key."""
        store = InMemoryKVStore()

        result = await store.exists("nonexistent")
        assert result is False


class TestInMemoryKVStoreExclusiveSet:
    """Tests for exclusive_set operation."""

    @pytest.mark.asyncio
    async def test_exclusive_set_new_key(self):
        """Test exclusive_set on a new key succeeds."""
        store = InMemoryKVStore()
        result = await store.exclusive_set("key1", "value1")

        assert result is True
        assert await store.get("key1") == "value1"

    @pytest.mark.asyncio
    async def test_exclusive_set_existing_key_fails(self):
        """Test exclusive_set on an existing key fails."""
        store = InMemoryKVStore()
        await store.set("key1", "value1")

        result = await store.exclusive_set("key1", "value2")

        assert result is False
        assert await store.get("key1") == "value1"

    @pytest.mark.asyncio
    async def test_exclusive_set_with_expiry(self):
        """Test exclusive_set with expiry parameter."""
        store = InMemoryKVStore()
        await store.exclusive_set("key1", "value1", expiry=1)

        assert await store.exists("key1") is True
        await asyncio.sleep(1.1)
        assert await store.exists("key1") is False

    @pytest.mark.asyncio
    async def test_exclusive_set_allows_setting_after_expiry(self):
        """Test that we can exclusive_set on a key after it expires."""
        store = InMemoryKVStore()
        result1 = await store.exclusive_set("key1", "value1", expiry=1)
        assert result1 is True

        # Wait for expiry
        await asyncio.sleep(1.1)

        # Should be able to set again
        result2 = await store.exclusive_set("key1", "value2", expiry=1)
        assert result2 is True


class TestInMemoryKVStorePrefixOperations:
    """Tests for prefix-based operations."""

    @pytest.mark.asyncio
    async def test_get_by_prefix(self):
        """Test getting values by prefix."""
        store = InMemoryKVStore()
        await store.set("user:1:name", "Alice")
        await store.set("user:1:email", "alice@example.com")
        await store.set("user:2:name", "Bob")
        await store.set("admin:settings", "value")

        result = await store.get_by_prefix("user:1")

        assert result == {"user:1:name": "Alice", "user:1:email": "alice@example.com"}

    @pytest.mark.asyncio
    async def test_get_by_prefix_empty_result(self):
        """Test get_by_prefix returns empty dict when no matches."""
        store = InMemoryKVStore()
        await store.set("key1", "value1")

        result = await store.get_by_prefix("nonexistent:")

        assert result == {}

    @pytest.mark.asyncio
    async def test_delete_by_prefix(self):
        """Test deleting keys by prefix."""
        store = InMemoryKVStore()
        await store.set("user:1:name", "Alice")
        await store.set("user:1:email", "alice@example.com")
        await store.set("user:2:name", "Bob")
        await store.set("admin:settings", "value")

        await store.delete_by_prefix("user:1")

        assert await store.exists("user:1:name") is False
        assert await store.exists("user:1:email") is False
        assert await store.exists("user:2:name") is True
        assert await store.exists("admin:settings") is True

    @pytest.mark.asyncio
    async def test_delete_by_prefix_with_batch_size(self):
        """Test delete_by_prefix with batch size."""
        store = InMemoryKVStore()
        for i in range(10):
            await store.set(f"prefix:key{i}", f"value{i}")

        await store.delete_by_prefix("prefix:", batch_size=3)

        result = await store.get_by_prefix("prefix:")
        assert result == {}

    @pytest.mark.asyncio
    async def test_delete_by_prefix_no_matches(self):
        """Test delete_by_prefix when no keys match."""
        store = InMemoryKVStore()
        await store.set("key1", "value1")

        await store.delete_by_prefix("nonexistent:")  # Should not raise

        assert await store.exists("key1") is True


class TestInMemoryKVStoreBatchOperations:
    """Tests for batch operations."""

    @pytest.mark.asyncio
    async def test_mget(self):
        """Test getting multiple keys at once."""
        store = InMemoryKVStore()
        await store.set("key1", "value1")
        await store.set("key2", "value2")
        await store.set("key3", "value3")

        result = await store.mget(["key1", "key2", "key4"])

        assert result == ["value1", "value2", None]

    @pytest.mark.asyncio
    async def test_mget_empty_list(self):
        """Test mget with empty list."""
        store = InMemoryKVStore()

        result = await store.mget([])

        assert result == []

    @pytest.mark.asyncio
    async def test_batch_delete(self):
        """Test deleting multiple keys at once."""
        store = InMemoryKVStore()
        await store.set("key1", "value1")
        await store.set("key2", "value2")
        await store.set("key3", "value3")

        deleted = await store.batch_delete(["key1", "key2", "key4"])

        assert deleted == 2
        assert await store.exists("key1") is False
        assert await store.exists("key2") is False
        assert await store.exists("key3") is True

    @pytest.mark.asyncio
    async def test_batch_delete_empty_list(self):
        """Test batch_delete with empty list."""
        store = InMemoryKVStore()

        deleted = await store.batch_delete([])

        assert deleted == 0

    @pytest.mark.asyncio
    async def test_batch_delete_with_batch_size(self):
        """Test batch_delete with custom batch size."""
        store = InMemoryKVStore()
        for i in range(10):
            await store.set(f"key{i}", f"value{i}")

        deleted = await store.batch_delete([f"key{i}" for i in range(10)], batch_size=3)

        assert deleted == 10


class TestInMemoryKVStorePipeline:
    """Tests for pipeline operations."""

    @pytest.mark.asyncio
    async def test_pipeline_set_and_get(self):
        """Test pipeline with set and get operations."""
        store = InMemoryKVStore()
        pipeline = store.pipeline()

        await pipeline.set("key1", "value1")
        await pipeline.set("key2", "value2")
        await pipeline.get("key1")
        await pipeline.get("key2")

        results = await pipeline.execute()

        assert results == [None, None, "value1", "value2"]

    @pytest.mark.asyncio
    async def test_pipeline_set_get_exists(self):
        """Test pipeline with set, get, and exists operations."""
        store = InMemoryKVStore()
        pipeline = store.pipeline()

        await pipeline.set("key1", "value1")
        await pipeline.get("key1")
        await pipeline.exists("key1")
        await pipeline.get("nonexistent")
        await pipeline.exists("nonexistent")

        results = await pipeline.execute()

        assert results == [None, "value1", True, None, False]

    @pytest.mark.asyncio
    async def test_pipeline_multiple_executes(self):
        """Test that pipeline can be executed multiple times."""
        store = InMemoryKVStore()
        pipeline = store.pipeline()

        await pipeline.set("key1", "value1")
        await pipeline.get("key1")
        results1 = await pipeline.execute()

        await pipeline.set("key2", "value2")
        await pipeline.get("key2")
        results2 = await pipeline.execute()

        assert results1 == [None, "value1"]
        assert results2 == [None, "value2"]

    @pytest.mark.asyncio
    async def test_pipeline_empty_operations(self):
        """Test executing an empty pipeline."""
        store = InMemoryKVStore()
        pipeline = store.pipeline()

        results = await pipeline.execute()

        assert results == []

    @pytest.mark.asyncio
    async def test_pipeline_with_bytes(self):
        """Test pipeline with bytes values."""
        store = InMemoryKVStore()
        pipeline = store.pipeline()

        await pipeline.set("key1", b"bytes_value")
        await pipeline.get("key1")

        results = await pipeline.execute()

        assert results == [None, b"bytes_value"]


class TestInMemoryKVStoreExpiry:
    """Tests for key expiry functionality."""

    @pytest.mark.asyncio
    async def test_key_expiry(self):
        """Test that keys expire after the specified time."""
        store = InMemoryKVStore()
        await store.exclusive_set("key1", "value1", expiry=1)

        assert await store.exists("key1") is True
        await asyncio.sleep(1.1)
        assert await store.exists("key1") is False

    @pytest.mark.asyncio
    async def test_expired_key_returns_none(self):
        """Test that getting an expired key returns None."""
        store = InMemoryKVStore()
        await store.exclusive_set("key1", "value1", expiry=1)

        await asyncio.sleep(1.1)
        result = await store.get("key1")

        assert result is None

    @pytest.mark.asyncio
    async def test_expired_key_in_get_by_prefix_returns_none(self):
        """Test that expired keys are returned by get_by_prefix with None values."""
        store = InMemoryKVStore()
        await store.set("prefix:key1", "value1")
        await store.exclusive_set("prefix:key2", "value2", expiry=1)

        await asyncio.sleep(1.1)
        result = await store.get_by_prefix("prefix:")

        # Expired keys are still in the store but return None
        assert result == {"prefix:key1": "value1", "prefix:key2": None}

    @pytest.mark.asyncio
    async def test_can_set_after_key_expires(self):
        """Test that we can set a key after it expires."""
        store = InMemoryKVStore()
        await store.exclusive_set("key1", "value1", expiry=1)

        await asyncio.sleep(1.1)
        await store.set("key1", "value2")

        assert await store.get("key1") == "value2"


class TestInMemoryKVStoreConcurrency:
    """Tests for concurrent operations."""

    @pytest.mark.asyncio
    async def test_concurrent_sets(self):
        """Test that concurrent sets are handled correctly."""
        store = InMemoryKVStore()

        async def set_value(i):
            await store.set(f"key{i}", f"value{i}")

        tasks = [set_value(i) for i in range(100)]
        await asyncio.gather(*tasks)

        for i in range(100):
            assert await store.get(f"key{i}") == f"value{i}"

    @pytest.mark.asyncio
    async def test_consecutive_pipeline_operations(self):
        """Test multiple pipeline operations executed sequentially."""
        store = InMemoryKVStore()

        for i in range(10):
            pipeline = store.pipeline()
            await pipeline.set(f"key{i}", f"value{i}")
            await pipeline.get(f"key{i}")
            results = await pipeline.execute()
            assert results == [None, f"value{i}"]


class TestInMemoryKVStoreEdgeCases:
    """Tests for edge cases."""

    @pytest.mark.asyncio
    async def test_empty_string_key(self):
        """Test handling of empty string as key."""
        store = InMemoryKVStore()
        await store.set("", "value")

        assert await store.get("") == "value"

    @pytest.mark.asyncio
    async def test_empty_string_value(self):
        """Test handling of empty string as value."""
        store = InMemoryKVStore()
        await store.set("key1", "")

        assert await store.get("key1") == ""

    @pytest.mark.asyncio
    async def test_special_characters_in_key(self):
        """Test handling of special characters in keys."""
        store = InMemoryKVStore()
        special_keys = [
            "key:with:colons",
            "key/with/slashes",
            "key-with-dashes",
            "key_with_underscores",
            "key.with.dots",
            "key with spaces",
        ]

        for key in special_keys:
            await store.set(key, f"value_{key}")

        for key in special_keys:
            assert await store.get(key) == f"value_{key}"

    @pytest.mark.asyncio
    async def test_large_value(self):
        """Test handling of large values."""
        store = InMemoryKVStore()
        large_value = "x" * 100000  # 100KB

        await store.set("key1", large_value)
        result = await store.get("key1")

        assert result == large_value

    @pytest.mark.asyncio
    async def test_get_by_prefix_empty_prefix(self):
        """Test get_by_prefix with empty prefix returns all keys."""
        store = InMemoryKVStore()
        await store.set("key1", "value1")
        await store.set("key2", "value2")

        result = await store.get_by_prefix("")

        assert result == {"key1": "value1", "key2": "value2"}

    @pytest.mark.asyncio
    async def test_delete_by_prefix_empty_prefix(self):
        """Test delete_by_prefix with empty prefix deletes all keys."""
        store = InMemoryKVStore()
        await store.set("key1", "value1")
        await store.set("key2", "value2")

        await store.delete_by_prefix("")

        assert await store.exists("key1") is False
        assert await store.exists("key2") is False
