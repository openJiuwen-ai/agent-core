# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.memory.config.config import MemoryEngineConfig
from openjiuwen.core.memory.manage.expiration.memory_expiration_service import (
    MemoryExpirationService,
    _EXPIRABLE_MEM_TYPES,
)
from openjiuwen.core.memory.manage.mem_model.memory_unit import MemoryType


def _make_service(**overrides):
    defaults = {
        "kv_store": MagicMock(),
        "config": MemoryEngineConfig(enable_memory_expiration=True, memory_expiration_seconds=60),
        "scope_user_mapping_manager": MagicMock(),
        "write_manager": MagicMock(),
        "search_manager": MagicMock(),
        "semantic_store_factory": AsyncMock(),
    }
    defaults.update(overrides)
    return MemoryExpirationService(**defaults)


@asynccontextmanager
async def _mock_lock():
    with patch("openjiuwen.core.memory.manage.expiration.memory_expiration_service.DistributedLock") as MockLock:
        mock_lock_instance = MagicMock()
        mock_lock_instance.__aenter__ = AsyncMock(return_value=None)
        mock_lock_instance.__aexit__ = AsyncMock(return_value=None)
        MockLock.return_value = mock_lock_instance
        yield


# ---------- MemoryEngineConfig defaults ----------

class TestMemoryEngineConfigDefaults:
    def test_default_values(self):
        config = MemoryEngineConfig()
        assert config.enable_memory_expiration is False
        assert config.memory_expiration_seconds == 7 * 24 * 60 * 60

    def test_custom_values(self):
        config = MemoryEngineConfig(
            enable_memory_expiration=True,
            memory_expiration_seconds=3600,
        )
        assert config.enable_memory_expiration is True
        assert config.memory_expiration_seconds == 3600

    def test_retention_seconds_must_be_positive(self):
        with pytest.raises(Exception):
            MemoryEngineConfig(memory_expiration_seconds=0)

    def test_retention_seconds_negative(self):
        with pytest.raises(Exception):
            MemoryEngineConfig(memory_expiration_seconds=-1)


# ---------- _is_expired ----------

class TestIsExpired:
    def test_expired_timestamp(self):
        cutoff = datetime.now(timezone.utc).astimezone()
        old_ts = (cutoff - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S")
        assert MemoryExpirationService._is_expired(old_ts, cutoff) is True

    def test_not_expired_timestamp(self):
        cutoff = datetime.now(timezone.utc).astimezone()
        recent_ts = (cutoff + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        assert MemoryExpirationService._is_expired(recent_ts, cutoff) is False

    def test_empty_timestamp(self):
        cutoff = datetime.now(timezone.utc).astimezone()
        assert MemoryExpirationService._is_expired("", cutoff) is False

    def test_invalid_timestamp_format(self):
        cutoff = datetime.now(timezone.utc).astimezone()
        assert MemoryExpirationService._is_expired("not-a-date", cutoff) is False

    def test_none_timestamp(self):
        cutoff = datetime.now(timezone.utc).astimezone()
        assert MemoryExpirationService._is_expired(None, cutoff) is False

    def test_exact_cutoff_boundary(self):
        now = datetime.now(timezone.utc).astimezone().replace(microsecond=0)
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        assert MemoryExpirationService._is_expired(ts, now) is False

    def test_one_second_before_cutoff(self):
        now = datetime.now(timezone.utc).astimezone()
        one_sec_before = (now - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
        assert MemoryExpirationService._is_expired(one_sec_before, now) is True


# ---------- _cleanup_user ----------

class TestCleanupUser:
    @pytest.mark.asyncio
    async def test_cleanup_deletes_expired_only(self):
        cutoff = datetime.now(timezone.utc).astimezone()
        old_ts = (cutoff - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S")
        recent_ts = (cutoff + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

        search_manager = MagicMock()

        async def mock_get_all(user_id, scope_id, mem_type):
            if mem_type == MemoryType.USER_PROFILE.value:
                return [
                    {"id": "expired_1", "timestamp": old_ts, "mem": "old memory"},
                    {"id": "valid_1", "timestamp": recent_ts, "mem": "recent memory"},
                ]
            return None

        search_manager.get_all = mock_get_all
        search_manager.get_mem_by_id = AsyncMock(
            return_value={"id": "expired_1", "timestamp": old_ts, "mem": "old memory"}
        )

        write_manager = MagicMock()
        write_manager.delete_mem_by_id = AsyncMock()
        semantic_store_factory = AsyncMock(return_value=MagicMock())

        service = _make_service(
            write_manager=write_manager,
            search_manager=search_manager,
            semantic_store_factory=semantic_store_factory,
        )

        async with _mock_lock():
            deleted = await service._cleanup_user("user1", "scope1", cutoff)

        assert deleted == 1
        write_manager.delete_mem_by_id.assert_called_once()
        call_kwargs = write_manager.delete_mem_by_id.call_args.kwargs
        assert call_kwargs["mem_id"] == "expired_1"

    @pytest.mark.asyncio
    async def test_cleanup_no_expired(self):
        cutoff = datetime.now(timezone.utc).astimezone()
        recent_ts = (cutoff + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

        search_manager = MagicMock()
        search_manager.get_all = AsyncMock(return_value=[
            {"id": "valid_1", "timestamp": recent_ts, "mem": "recent memory"},
        ])

        write_manager = MagicMock()

        service = _make_service(write_manager=write_manager, search_manager=search_manager)

        async with _mock_lock():
            deleted = await service._cleanup_user("user1", "scope1", cutoff)

        assert deleted == 0
        write_manager.delete_mem_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_variable_type_skipped(self):
        cutoff = datetime.now(timezone.utc).astimezone()
        search_manager = MagicMock()

        async def mock_get_all(user_id, scope_id, mem_type):
            if mem_type == MemoryType.VARIABLE.value:
                return [{"id": "var_1", "timestamp": "2020-01-01 00:00:00"}]
            return None

        search_manager.get_all = mock_get_all

        write_manager = MagicMock()

        service = _make_service(write_manager=write_manager, search_manager=search_manager)

        async with _mock_lock():
            deleted = await service._cleanup_user("user1", "scope1", cutoff)

        assert deleted == 0

    @pytest.mark.asyncio
    async def test_cleanup_multiple_expirable_types(self):
        cutoff = datetime.now(timezone.utc).astimezone()
        old_ts = (cutoff - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S")
        recent_ts = (cutoff + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

        search_manager = MagicMock()

        async def mock_get_all(user_id, scope_id, mem_type):
            data = {
                MemoryType.USER_PROFILE.value: [
                    {"id": "exp_up", "timestamp": old_ts, "mem": "old profile"},
                ],
                MemoryType.SEMANTIC_MEMORY.value: [
                    {"id": "exp_sm", "timestamp": old_ts, "mem": "old semantic"},
                    {"id": "valid_sm", "timestamp": recent_ts, "mem": "recent semantic"},
                ],
                MemoryType.EPISODIC_MEMORY.value: [
                    {"id": "exp_em", "timestamp": old_ts, "mem": "old episodic"},
                ],
                MemoryType.SUMMARY.value: [
                    {"id": "exp_su", "timestamp": old_ts, "mem": "old summary"},
                ],
                MemoryType.VARIABLE.value: [
                    {"id": "var_1", "timestamp": old_ts, "mem": "should be skipped"},
                ],
            }
            return data.get(mem_type)

        async def mock_get_mem_by_id(user_id, scope_id, mem_id):
            return {"id": mem_id, "timestamp": old_ts}

        search_manager.get_all = mock_get_all
        search_manager.get_mem_by_id = mock_get_mem_by_id

        write_manager = MagicMock()
        write_manager.delete_mem_by_id = AsyncMock()
        semantic_store_factory = AsyncMock(return_value=MagicMock())

        service = _make_service(
            write_manager=write_manager,
            search_manager=search_manager,
            semantic_store_factory=semantic_store_factory,
        )

        async with _mock_lock():
            deleted = await service._cleanup_user("user1", "scope1", cutoff)

        assert deleted == 4
        deleted_ids = {call.kwargs["mem_id"] for call in write_manager.delete_mem_by_id.call_args_list}
        assert deleted_ids == {"exp_up", "exp_sm", "exp_em", "exp_su"}

    @pytest.mark.asyncio
    async def test_cleanup_all_types_return_none(self):
        cutoff = datetime.now(timezone.utc).astimezone()
        search_manager = MagicMock()
        search_manager.get_all = AsyncMock(return_value=None)

        write_manager = MagicMock()

        service = _make_service(write_manager=write_manager, search_manager=search_manager)

        async with _mock_lock():
            deleted = await service._cleanup_user("user1", "scope1", cutoff)

        assert deleted == 0
        write_manager.delete_mem_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_memory_without_id_skipped(self):
        cutoff = datetime.now(timezone.utc).astimezone()
        old_ts = (cutoff - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S")

        search_manager = MagicMock()

        async def mock_get_all(user_id, scope_id, mem_type):
            if mem_type == MemoryType.USER_PROFILE.value:
                return [
                    {"id": "expired_1", "timestamp": old_ts, "mem": "has id"},
                    {"timestamp": old_ts, "mem": "no id field"},
                    {"id": "", "timestamp": old_ts, "mem": "empty id"},
                ]
            return None

        search_manager.get_all = mock_get_all
        search_manager.get_mem_by_id = AsyncMock(
            return_value={"id": "expired_1", "timestamp": old_ts, "mem": "has id"}
        )

        write_manager = MagicMock()
        write_manager.delete_mem_by_id = AsyncMock()
        semantic_store_factory = AsyncMock(return_value=MagicMock())

        service = _make_service(
            write_manager=write_manager,
            search_manager=search_manager,
            semantic_store_factory=semantic_store_factory,
        )

        async with _mock_lock():
            deleted = await service._cleanup_user("user1", "scope1", cutoff)

        assert deleted == 1
        call_kwargs = write_manager.delete_mem_by_id.call_args.kwargs
        assert call_kwargs["mem_id"] == "expired_1"

    @pytest.mark.asyncio
    async def test_cleanup_skips_second_lock_when_no_candidates(self):
        """When no expired memories are found, phase 2 should be skipped entirely."""
        cutoff = datetime.now(timezone.utc).astimezone()
        recent_ts = (cutoff + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

        search_manager = MagicMock()
        call_count = [0]

        async def mock_get_all(user_id, scope_id, mem_type):
            call_count[0] += 1
            if mem_type == MemoryType.USER_PROFILE.value:
                return [{"id": "valid_1", "timestamp": recent_ts}]
            return None

        search_manager.get_all = mock_get_all

        write_manager = MagicMock()

        service = _make_service(write_manager=write_manager, search_manager=search_manager)

        async with _mock_lock():
            deleted = await service._cleanup_user("user1", "scope1", cutoff)

        assert deleted == 0
        assert call_count[0] == len(_EXPIRABLE_MEM_TYPES)
        search_manager.get_mem_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_skips_updated_candidate(self):
        """When a candidate memory was updated between phases, skip its deletion."""
        cutoff = datetime.now(timezone.utc).astimezone()
        old_ts = (cutoff - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S")
        recent_ts = (cutoff + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

        search_manager = MagicMock()

        async def mock_get_all(user_id, scope_id, mem_type):
            if mem_type == MemoryType.USER_PROFILE.value:
                return [{"id": "expired_1", "timestamp": old_ts}]
            return None

        search_manager.get_all = mock_get_all
        search_manager.get_mem_by_id = AsyncMock(
            return_value={"id": "expired_1", "timestamp": recent_ts}
        )

        write_manager = MagicMock()
        write_manager.delete_mem_by_id = AsyncMock()
        semantic_store_factory = AsyncMock(return_value=MagicMock())

        service = _make_service(
            write_manager=write_manager,
            search_manager=search_manager,
            semantic_store_factory=semantic_store_factory,
        )

        async with _mock_lock():
            deleted = await service._cleanup_user("user1", "scope1", cutoff)

        assert deleted == 0
        write_manager.delete_mem_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_partial_update_only_deletes_still_expired(self):
        """When some candidates are updated, only delete still-expired ones."""
        cutoff = datetime.now(timezone.utc).astimezone()
        old_ts = (cutoff - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S")
        recent_ts = (cutoff + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

        search_manager = MagicMock()

        async def mock_get_all(user_id, scope_id, mem_type):
            if mem_type == MemoryType.USER_PROFILE.value:
                return [
                    {"id": "expired_1", "timestamp": old_ts},
                    {"id": "expired_2", "timestamp": old_ts},
                ]
            return None

        async def mock_get_mem_by_id(user_id, scope_id, mem_id):
            if mem_id == "expired_1":
                return {"id": "expired_1", "timestamp": recent_ts}
            return {"id": mem_id, "timestamp": old_ts}

        search_manager.get_all = mock_get_all
        search_manager.get_mem_by_id = mock_get_mem_by_id

        write_manager = MagicMock()
        write_manager.delete_mem_by_id = AsyncMock()
        semantic_store_factory = AsyncMock(return_value=MagicMock())

        service = _make_service(
            write_manager=write_manager,
            search_manager=search_manager,
            semantic_store_factory=semantic_store_factory,
        )

        async with _mock_lock():
            deleted = await service._cleanup_user("user1", "scope1", cutoff)

        assert deleted == 1
        call_kwargs = write_manager.delete_mem_by_id.call_args.kwargs
        assert call_kwargs["mem_id"] == "expired_2"

    @pytest.mark.asyncio
    async def test_cleanup_skips_deleted_candidate(self):
        """When a candidate memory was deleted between phases, skip it."""
        cutoff = datetime.now(timezone.utc).astimezone()
        old_ts = (cutoff - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S")

        search_manager = MagicMock()

        async def mock_get_all(user_id, scope_id, mem_type):
            if mem_type == MemoryType.USER_PROFILE.value:
                return [{"id": "expired_1", "timestamp": old_ts}]
            return None

        search_manager.get_all = mock_get_all
        search_manager.get_mem_by_id = AsyncMock(return_value=None)

        write_manager = MagicMock()
        write_manager.delete_mem_by_id = AsyncMock()
        semantic_store_factory = AsyncMock(return_value=MagicMock())

        service = _make_service(
            write_manager=write_manager,
            search_manager=search_manager,
            semantic_store_factory=semantic_store_factory,
        )

        async with _mock_lock():
            deleted = await service._cleanup_user("user1", "scope1", cutoff)

        assert deleted == 0
        write_manager.delete_mem_by_id.assert_not_called()


# ---------- cleanup_all_users ----------

class TestCleanupAllUsers:
    @pytest.mark.asyncio
    async def test_cleanup_all_users_iterates_mappings(self):
        scope_mapping_mgr = MagicMock()
        scope_mapping_mgr.get_all_mappings = AsyncMock(return_value=[
            {"user_id": "u1", "scope_id": "s1"},
            {"user_id": "u2", "scope_id": "s2"},
        ])

        service = _make_service(scope_user_mapping_manager=scope_mapping_mgr)
        service._cleanup_user = AsyncMock(return_value=3)

        cutoff = datetime.now(timezone.utc).astimezone()
        await service.cleanup_all_users(cutoff_time=cutoff)

        assert service._cleanup_user.call_count == 2

    @pytest.mark.asyncio
    async def test_cleanup_all_users_no_mappings(self):
        scope_mapping_mgr = MagicMock()
        scope_mapping_mgr.get_all_mappings = AsyncMock(return_value=None)

        service = _make_service(scope_user_mapping_manager=scope_mapping_mgr)
        service._cleanup_user = AsyncMock(return_value=0)

        await service.cleanup_all_users()

        service._cleanup_user.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_all_users_auto_cutoff(self):
        scope_mapping_mgr = MagicMock()
        scope_mapping_mgr.get_all_mappings = AsyncMock(return_value=[])
        service = _make_service(scope_user_mapping_manager=scope_mapping_mgr)
        service._cleanup_user = AsyncMock(return_value=0)

        await service.cleanup_all_users(cutoff_time=None)

        service._cleanup_user.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_all_users_skips_empty_user_or_scope(self):
        scope_mapping_mgr = MagicMock()
        scope_mapping_mgr.get_all_mappings = AsyncMock(return_value=[
            {"user_id": "", "scope_id": "s1"},
            {"user_id": "u2", "scope_id": ""},
            {"user_id": "u3", "scope_id": "s3"},
        ])
        service = _make_service(scope_user_mapping_manager=scope_mapping_mgr)
        service._cleanup_user = AsyncMock(return_value=0)

        await service.cleanup_all_users()

        service._cleanup_user.assert_called_once_with("u3", "s3", ANY)

    @pytest.mark.asyncio
    async def test_cleanup_all_users_exception_does_not_stop_others(self):
        scope_mapping_mgr = MagicMock()
        scope_mapping_mgr.get_all_mappings = AsyncMock(return_value=[
            {"user_id": "u1", "scope_id": "s1"},
            {"user_id": "u2", "scope_id": "s2"},
            {"user_id": "u3", "scope_id": "s3"},
        ])
        service = _make_service(scope_user_mapping_manager=scope_mapping_mgr)

        call_count = 0

        async def side_effect(user_id, scope_id, cutoff_time):
            nonlocal call_count
            call_count += 1
            if user_id == "u2":
                raise RuntimeError("simulated failure")

        service._cleanup_user = AsyncMock(side_effect=side_effect)

        await service.cleanup_all_users()

        assert call_count == 3


# ---------- Service lifecycle ----------

class TestServiceLifecycle:
    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        service = _make_service()

        await service.start()
        assert service._running is True
        assert service._task is not None

        task_before = service._task
        await service.start()
        assert service._task == task_before

        await service.stop()

    @pytest.mark.asyncio
    async def test_stop_idempotent(self):
        service = _make_service()
        await service.stop()
        assert service._running is False

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        service = _make_service()

        await service.start()
        assert service._running is True

        await service.stop()
        assert service._running is False
        assert service._task is None


# ---------- _run_periodically ----------

class TestRunPeriodically:
    @pytest.mark.asyncio
    async def test_periodic_cleanup_calls_cleanup_all_users(self):
        service = _make_service(
            config=MemoryEngineConfig(
                enable_memory_expiration=True,
                memory_expiration_seconds=60,
            ),
        )
        service._running = True
        call_count = 0

        async def fake_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                service._running = False

        with patch("openjiuwen.core.memory.manage.expiration.memory_expiration_service.asyncio.sleep",
                   side_effect=fake_sleep):
            service.cleanup_all_users = AsyncMock()
            await service._run_periodically()

        assert service.cleanup_all_users.call_count == 2

    @pytest.mark.asyncio
    async def test_periodic_exception_does_not_stop_loop(self):
        service = _make_service(
            config=MemoryEngineConfig(
                enable_memory_expiration=True,
                memory_expiration_seconds=60,
            ),
        )
        service._running = True
        call_count = 0

        async def fake_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                service._running = False

        async def failing_cleanup(**kwargs):
            if call_count == 1:
                raise RuntimeError("transient failure")

        with patch("openjiuwen.core.memory.manage.expiration.memory_expiration_service.asyncio.sleep",
                   side_effect=fake_sleep):
            service.cleanup_all_users = AsyncMock(side_effect=failing_cleanup)
            await service._run_periodically()

        assert service.cleanup_all_users.call_count == 3

    @pytest.mark.asyncio
    async def test_cancelled_error_stops_loop(self):
        service = _make_service()

        async def fake_sleep(seconds):
            raise asyncio.CancelledError()

        with patch("openjiuwen.core.memory.manage.expiration.memory_expiration_service.asyncio.sleep",
                   side_effect=fake_sleep):
            service.cleanup_all_users = AsyncMock()
            await service._run_periodically()

        service.cleanup_all_users.assert_not_called()
