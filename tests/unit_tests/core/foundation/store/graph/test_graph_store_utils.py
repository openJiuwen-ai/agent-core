# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for graph store utils."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.foundation.store.graph import utils as graph_utils


class TestBatched:
    """Tests for batched()."""

    @staticmethod
    def test_yields_batches_of_size_n():
        """Yields batches of size n from iterable."""
        from openjiuwen.core.foundation.store.graph.utils import batched

        data = list(range(7))
        batches = list(batched(data, 3))
        assert batches == [(0, 1, 2), (3, 4, 5), (6,)]

    @staticmethod
    def test_n_less_than_one_raises():
        """n < 1 raises ValueError."""
        from openjiuwen.core.foundation.store.graph.utils import batched

        with pytest.raises(ValueError, match="n must be at least one"):
            list(batched([1, 2, 3], 0))
        with pytest.raises(ValueError, match="n must be at least one"):
            list(batched([1, 2, 3], -1))

    @staticmethod
    def test_strict_incomplete_batch_raises():
        """When strict=True and last batch is incomplete, raises ValueError (fallback impl)."""
        import sys

        from openjiuwen.core.foundation.store.graph.utils import batched

        data = list(range(5))  # 5 items, batch 3 -> (0,1,2), (3,4) incomplete
        if sys.version_info >= (3, 12):
            pytest.skip("itertools.batched (3.12+) does not support strict=")
        with pytest.raises(ValueError, match="incomplete batch"):
            list(batched(data, 3, strict=True))

    @staticmethod
    def test_strict_complete_batches_ok():
        """strict=True with complete batches does not raise (fallback impl)."""
        import sys

        from openjiuwen.core.foundation.store.graph.utils import batched

        if sys.version_info >= (3, 12):
            pytest.skip("itertools.batched (3.12+) does not support strict=")
        data = list(range(6))
        out = list(batched(data, 3, strict=True))
        assert out == [(0, 1, 2), (3, 4, 5)]


@pytest.mark.asyncio
class TestWithMetadata:
    """Tests for with_metadata."""

    @staticmethod
    async def test_returns_result_and_metadata():
        """Await coroutine and return (result, metadata)."""

        async def coro():
            return 42

        result, meta = await graph_utils.with_metadata(coro(), "meta")
        assert result == 42
        assert meta == "meta"


class TestGetUuid:
    """Tests for get_uuid."""

    @staticmethod
    def test_returns_32_char_hex():
        """Returns 32-char hex string."""
        uid = graph_utils.get_uuid()
        assert len(uid) == 32
        assert all(c in "0123456789abcdef" for c in uid)


class TestEnsureUniqueUuids:
    """Tests for ensure_unique_uuids."""

    @staticmethod
    def test_replaces_none_empty_with_new_uuid():
        """Replaces None/empty ids with new UUIDs."""
        backend = MagicMock()
        backend.is_empty.return_value = True
        ids = [None, "", "existing_id"]
        out = graph_utils.ensure_unique_uuids(backend, ids, "entities")
        assert out[0] != "" and out[0] is not None and len(out[0]) == 32
        assert out[1] != "" and out[1] is not None and len(out[1]) == 32
        assert out[2] == "existing_id"

    @staticmethod
    def test_skip_true_returns_after_uuid_fill():
        """When skip=True, returns list as-is after filling uuids (no de-dup)."""
        backend = MagicMock()
        ids = [None, "id1"]
        out = graph_utils.ensure_unique_uuids(backend, ids, "entities", skip=True)
        backend.is_empty.assert_not_called()
        assert len(out) == 2
        assert out[1] == "id1"

    @staticmethod
    def test_backend_empty_returns_unique_ids():
        """When backend is_empty(collection) True, returns unique_ids."""
        backend = MagicMock()
        backend.is_empty.return_value = True
        ids = ["a", "b"]
        out = graph_utils.ensure_unique_uuids(backend, ids, "entities")
        assert out == ["a", "b"]
        backend.query.assert_not_called()

    @staticmethod
    def test_backend_has_duplicates_replaces_until_unique():
        """When backend has duplicates, mock query returns existing uuids and de-dup replaces them."""
        backend = MagicMock()
        backend.is_empty.return_value = False
        call_count = [0]

        def query_side_effect(**kwargs):
            ids = kwargs.get("ids", [])
            # First call: report "id1" as duplicate; second call: no duplicates
            call_count[0] += 1
            if call_count[0] == 1:
                return [{"uuid": "id1"}] if "id1" in ids else []
            return []

        backend.query.side_effect = query_side_effect
        ids = ["id1", "id2"]
        out = graph_utils.ensure_unique_uuids(backend, ids, "entities")
        assert out[1] == "id2"
        assert out[0] != "id1" and len(out[0]) == 32
        assert backend.query.called


class TestFormatListOfMessages:
    """Tests for format_list_of_messages."""

    @staticmethod
    def test_formats_role_and_content():
        """Format list of dicts with role/content."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        out = graph_utils.format_list_of_messages(messages)
        assert "user" in out and "Hello" in out
        assert "assistant" in out and "Hi" in out

    @staticmethod
    def test_role_replace():
        """Optional role_replace maps roles."""
        messages = [{"role": "assistant", "content": "Hi"}]
        out = graph_utils.format_list_of_messages(messages, role_replace={"assistant": "Agent"})
        assert "Agent" in out

    @staticmethod
    def test_template():
        """Custom template is used."""
        messages = [{"role": "user", "content": "x"}]
        out = graph_utils.format_list_of_messages(messages, template="{role}|{content}\n")
        assert out == "user|x\n"


class TestSafeTimestamp:
    """Tests for safe_timestamp."""

    @staticmethod
    def test_pre_1970_returns_offset_seconds():
        """Pre-1970 datetime returns (datetime - 1970-01-01).total_seconds()."""
        # 1960-01-01 00:00:00 UTC
        dt = datetime(1960, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        ts = graph_utils.safe_timestamp(dt)
        expected = (dt - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds()
        assert ts == expected

    @staticmethod
    def test_post_1970_returns_normal_timestamp():
        """Post-1970 returns normal timestamp."""
        dt = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        ts = graph_utils.safe_timestamp(dt)
        assert ts == dt.timestamp()


class TestGetCurrentUtcTimestamp:
    """Tests for get_current_utc_timestamp."""

    @staticmethod
    def test_returns_int_roughly_current_time():
        """Returns int, roughly current time."""
        import time

        t = graph_utils.get_current_utc_timestamp()
        assert isinstance(t, int)
        assert abs(t - int(time.time())) <= 2


class TestFormatTimestamp:
    """Tests for format_timestamp."""

    @staticmethod
    def test_valid_t_returns_formatted_string():
        """Valid t returns formatted string."""
        t = 1735729200  # 2025-01-01 15:00 UTC
        out = graph_utils.format_timestamp(t, tz=timezone.utc)
        assert "2025" in out
        # Time part depends on UTC (e.g. 15:00) or local
        assert ":" in out

    @staticmethod
    def test_t_minus_one_returns_unknown_datetime():
        """t=-1 returns 'Unknown Datetime'."""
        assert graph_utils.format_timestamp(-1) == "Unknown Datetime"


class TestFormatTimestampIso:
    """Tests for format_timestamp_iso."""

    @staticmethod
    def test_valid_t_returns_iso_string():
        """Valid t returns ISO format string."""
        t = 1735729200
        out = graph_utils.format_timestamp_iso(t, tz=timezone.utc)
        assert "2025" in out and "01" in out

    @staticmethod
    def test_t_minus_one_returns_unknown_datetime():
        """t=-1 returns 'Unknown Datetime'."""
        assert graph_utils.format_timestamp_iso(-1) == "Unknown Datetime"


class TestIso2Timestamp:
    """Tests for iso2timestamp."""

    @staticmethod
    def test_valid_iso_returns_timestamp_and_offset():
        """Valid ISO string returns (timestamp, offset)."""
        ts, offset = graph_utils.iso2timestamp("2025-09-10T15:56:53+08:00")
        assert ts != -1
        assert isinstance(offset, int)

    @staticmethod
    def test_invalid_returns_minus_one_zero():
        """Invalid ISO returns (-1, 0)."""
        ts, offset = graph_utils.iso2timestamp("not-a-date")
        assert ts == -1
        assert offset == 0


class TestLoadStoredTimeFromDb:
    """Tests for load_stored_time_from_db."""

    @staticmethod
    def test_valid_timestamp_offset_returns_datetime():
        """Valid timestamp + offset return datetime."""
        t = 1735729200
        dt = graph_utils.load_stored_time_from_db(t, 0)
        assert dt is not None
        assert dt.year == 2025

    @staticmethod
    def test_timestamp_minus_one_returns_none():
        """timestamp=-1 returns None."""
        assert graph_utils.load_stored_time_from_db(-1, 0) is None


class TestTzOffsetRoundtrip:
    """Tests for _store_tz_offset / _load_tz_offset roundtrip."""

    @staticmethod
    def test_utc_plus_8_roundtrip():
        """Roundtrip offset e.g. UTC+8 -> int -> timezone."""
        from openjiuwen.core.foundation.store.graph.utils import (
            _load_tz_offset,
            _store_tz_offset,
        )

        # UTC+8: tz_str from datetime might be "UTC+08:00" or similar
        offset_int = _store_tz_offset("+08:00")
        tz = _load_tz_offset(offset_int)
        assert tz.utcoffset(None).total_seconds() == 8 * 3600

    @staticmethod
    def test_load_tz_offset_zero():
        """Offset 0 -> UTC."""
        from openjiuwen.core.foundation.store.graph.utils import _load_tz_offset

        tz = _load_tz_offset(0)
        assert tz.utcoffset(None).total_seconds() == 0
