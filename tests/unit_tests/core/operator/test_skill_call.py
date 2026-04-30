# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for openjiuwen.core.operator.skill_call module."""

from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.operator.skill_call import SkillCallOperator


class TestSkillCallOperator:
    """Tests for SkillCallOperator batching helpers."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_flush_records_to_store_does_not_mutate_staged_queue():
        op = SkillCallOperator("skill-a")
        staged = ["later-record"]
        op._staged_records = list(staged)
        store = AsyncMock()

        result = await op.flush_records_to_store(store, ["approved-record"])

        store.append_record.assert_awaited_once_with("skill-a", "approved-record")
        assert result.flushed_count == 1
        assert result.remaining_records == []
        assert op.staged_records == staged

    @staticmethod
    @pytest.mark.asyncio
    async def test_flush_records_to_store_returns_remaining_tail_on_failure():
        op = SkillCallOperator("skill-a")
        store = AsyncMock()
        store.append_record = AsyncMock(side_effect=[None, OSError("disk full")])

        result = await op.flush_records_to_store(store, ["r1", "r2", "r3"])

        assert result.flushed_count == 1
        assert result.remaining_records == ["r2", "r3"]
        assert op.staged_records == []

    @staticmethod
    @pytest.mark.asyncio
    async def test_flush_to_store_preserves_records_staged_during_io():
        op = SkillCallOperator("skill-a")
        op._staged_records = ["r1"]

        async def _append_record(_skill_name, record):
            if record == "r1":
                op.set_parameter("experiences", "r2")

        store = AsyncMock()
        store.append_record = AsyncMock(side_effect=_append_record)

        flushed = await op.flush_to_store(store)

        assert flushed == 1
        assert op.staged_records == ["r2"]
