# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for StreamingToolExecutor.

Coverage:
- Concurrency rule a/b/c (no-running / all-safe-parallel / otherwise-wait)
- FIFO ordering (later safe tools cannot jump ahead of a blocked earlier tool)
- Result order matches add() order regardless of completion order
- Duplicate add() de-duplication
- cancel_all on EXECUTING and on QUEUED
- executor_fn exception is captured as value
- is_concurrency_safe default predicate (always True)
- Custom concurrency_check injection to verify scheduling logic

并发安全性判定通过 ``concurrency_check`` 参数注入自定义 predicate，
以构造 safe / unsafe 场景来验证调度逻辑，不再依赖硬编码白名单。
"""
from __future__ import annotations

import asyncio
from typing import List

import pytest

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.operator.streaming_tool_executor import (
    StreamingToolExecutor,
    is_concurrency_safe,
)


def _tc(name: str, tid: str, index: int = 0) -> ToolCall:
    return ToolCall(id=tid, type="function", name=name, arguments="{}", index=index)


# ---------------------------------------------------------------------------
# Test predicates — injected via StreamingToolExecutor(concurrency_check=...)
# to construct safe/unsafe scenarios without relying on a hardcoded whitelist.
# ---------------------------------------------------------------------------

# 只读类工具视为 safe，其他视为 unsafe（模拟未来引入分级判断的场景）
_READ_ONLY_NAMES = frozenset({"read_file", "grep", "glob", "list_dir"})


def _read_only_safe(tc: ToolCall) -> bool:
    """Only read-only-style tool names are considered concurrency-safe."""
    return tc.name in _READ_ONLY_NAMES


def _all_unsafe(_tc: ToolCall) -> bool:
    """Every tool is treated as non-concurrency-safe."""
    return False


# ---------------------------------------------------------------------------
# Default predicate
# ---------------------------------------------------------------------------

class TestIsConcurrencySafe:
    """Verify the default ``is_concurrency_safe`` always returns True."""

    def test_returns_true_for_any_tool(self):
        for name in ("read_file", "grep", "write_file", "bash", "run_command"):
            assert is_concurrency_safe(_tc(name, "x")) is True


# ---------------------------------------------------------------------------
# Rule A: No tools running → start immediately
# ---------------------------------------------------------------------------

class TestRuleA_NoRunningStartImmediately:
    @pytest.mark.asyncio
    async def test_single_safe_starts(self):
        started: List[str] = []
        gate = asyncio.Event()

        async def fn(tc):
            started.append(tc.id)
            await gate.wait()
            return ("ok", None, None)

        ex = StreamingToolExecutor(fn, concurrency_check=_read_only_safe)
        ex.add(_tc("read_file", "1"))
        await asyncio.sleep(0)
        assert started == ["1"]
        gate.set()
        results = await ex.wait_all()
        assert [r[0].id for r in results] == ["1"]

    @pytest.mark.asyncio
    async def test_single_non_safe_starts(self):
        """Even an unsafe tool starts immediately when nothing else is running."""
        started: List[str] = []
        gate = asyncio.Event()

        async def fn(tc):
            started.append(tc.id)
            await gate.wait()
            return ("ok", None, None)

        ex = StreamingToolExecutor(fn, concurrency_check=_read_only_safe)
        ex.add(_tc("write_file", "1"))
        await asyncio.sleep(0)
        assert started == ["1"]
        gate.set()
        await ex.wait_all()


# ---------------------------------------------------------------------------
# Rule B: All executing tools are safe AND new tool is safe → run in parallel
# ---------------------------------------------------------------------------

class TestRuleB_AllSafeRunParallel:
    @pytest.mark.asyncio
    async def test_multiple_safe_run_in_parallel(self):
        started: List[str] = []
        gate = asyncio.Event()

        async def fn(tc):
            started.append(tc.id)
            await gate.wait()
            return (tc.id, None, None)

        ex = StreamingToolExecutor(fn, concurrency_check=_read_only_safe)
        ex.add(_tc("read_file", "1"))
        ex.add(_tc("grep", "2"))
        ex.add(_tc("glob", "3"))
        await asyncio.sleep(0)
        assert set(started) == {"1", "2", "3"}, started
        gate.set()
        results = await ex.wait_all()
        assert [r[0].id for r in results] == ["1", "2", "3"]

    @pytest.mark.asyncio
    async def test_all_unsafe_runs_serially(self):
        """When every tool is unsafe they must execute one at a time."""
        started: List[str] = []
        finished: List[str] = []
        gates = {f"id{i}": asyncio.Event() for i in range(3)}

        async def fn(tc):
            started.append(tc.id)
            await gates[tc.id].wait()
            finished.append(tc.id)
            return (tc.id, None, None)

        ex = StreamingToolExecutor(fn, concurrency_check=_all_unsafe)
        ex.add(_tc("tool_a", "id0"))
        ex.add(_tc("tool_b", "id1"))
        ex.add(_tc("tool_c", "id2"))

        await asyncio.sleep(0)
        # Only the first should have started.
        assert started == ["id0"]

        # Release the first → second starts.
        gates["id0"].set()
        # Multiple yields needed: id0's _run finishes → finally → _process_queue
        # → creates id1's task → id1's _run starts and appends to `started`.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert started == ["id0", "id1"]
        assert finished == ["id0"]

        gates["id1"].set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert started == ["id0", "id1", "id2"]

        gates["id2"].set()
        results = await ex.wait_all()
        assert [r[0].id for r in results] == ["id0", "id1", "id2"]


# ---------------------------------------------------------------------------
# Rule C: Non-safe tools get exclusive access
# ---------------------------------------------------------------------------

class TestRuleC_NonSafeIsExclusive:
    @pytest.mark.asyncio
    async def test_non_safe_blocks_subsequent_safe(self):
        started: List[str] = []
        gate1 = asyncio.Event()

        async def fn(tc):
            started.append(tc.id)
            if tc.id == "bash":
                await gate1.wait()
            return (tc.id, None, None)

        ex = StreamingToolExecutor(fn, concurrency_check=_read_only_safe)
        ex.add(_tc("write_file", "bash"))  # non-safe
        ex.add(_tc("read_file", "safe1"))
        ex.add(_tc("grep", "safe2"))

        await asyncio.sleep(0)
        # Only the non-safe one should be running.
        assert started == ["bash"]

        gate1.set()
        results = await ex.wait_all()
        assert [r[0].id for r in results] == ["bash", "safe1", "safe2"]
        # All scheduled eventually.
        assert set(started) == {"bash", "safe1", "safe2"}

    @pytest.mark.asyncio
    async def test_safe_running_blocks_non_safe_from_starting(self):
        started: List[str] = []
        gate = asyncio.Event()

        async def fn(tc):
            started.append(tc.id)
            if tc.id == "safe":
                await gate.wait()
            return (tc.id, None, None)

        ex = StreamingToolExecutor(fn, concurrency_check=_read_only_safe)
        ex.add(_tc("read_file", "safe"))
        ex.add(_tc("write_file", "nonsafe"))
        await asyncio.sleep(0)
        # Safe is running but non-safe must wait (because non-safe is exclusive).
        assert started == ["safe"]
        gate.set()
        await ex.wait_all()
        assert set(started) == {"safe", "nonsafe"}


# ---------------------------------------------------------------------------
# Strict FIFO: later safe tools cannot jump ahead of a blocked earlier tool
# ---------------------------------------------------------------------------

class TestFifoStrict:
    @pytest.mark.asyncio
    async def test_later_safe_does_not_jump_ahead_of_blocked_non_safe(self):
        """[safe1, non_safe, safe2]: when safe1 finishes, non_safe runs alone;
        safe2 must wait for non_safe even though it's safe — strict FIFO."""
        started: List[str] = []
        order_lock = asyncio.Lock()
        gate_safe1 = asyncio.Event()
        gate_nonsafe = asyncio.Event()

        async def fn(tc):
            async with order_lock:
                started.append(tc.id)
            if tc.id == "safe1":
                await gate_safe1.wait()
            elif tc.id == "nonsafe":
                await gate_nonsafe.wait()
            return (tc.id, None, None)

        ex = StreamingToolExecutor(fn, concurrency_check=_read_only_safe)
        ex.add(_tc("read_file", "safe1"))
        ex.add(_tc("write_file", "nonsafe"))
        ex.add(_tc("grep", "safe2"))

        # Initially only safe1 is running.
        await asyncio.sleep(0)
        assert started == ["safe1"]

        # Release safe1 -> nonsafe should start (alone).
        gate_safe1.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert started == ["safe1", "nonsafe"]
        # safe2 must NOT have started yet.
        assert "safe2" not in started

        # Release nonsafe -> safe2 starts.
        gate_nonsafe.set()
        results = await ex.wait_all()
        assert [r[0].id for r in results] == ["safe1", "nonsafe", "safe2"]


# ---------------------------------------------------------------------------
# Result ordering: wait_all() returns results in add() order
# ---------------------------------------------------------------------------

class TestResultOrdering:
    @pytest.mark.asyncio
    async def test_later_completion_does_not_reorder(self):
        """Three safe tools; the third finishes first, but results stay in add() order."""
        gates = {f"id{i}": asyncio.Event() for i in range(3)}

        async def fn(tc):
            await gates[tc.id].wait()
            return (f"r-{tc.id}", None, None)

        ex = StreamingToolExecutor(fn, concurrency_check=_read_only_safe)
        ex.add(_tc("read_file", "id0"))
        ex.add(_tc("grep", "id1"))
        ex.add(_tc("glob", "id2"))

        # Finish in reverse order.
        gates["id2"].set()
        await asyncio.sleep(0)
        gates["id1"].set()
        await asyncio.sleep(0)
        gates["id0"].set()
        results = await ex.wait_all()
        assert [r[0].id for r in results] == ["id0", "id1", "id2"]
        assert [r[1][0] for r in results] == ["r-id0", "r-id1", "r-id2"]


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestAddDeduplication:
    @pytest.mark.asyncio
    async def test_same_id_index_added_once(self):
        calls: List[str] = []

        async def fn(tc):
            calls.append(tc.id)
            return ("ok", None, None)

        ex = StreamingToolExecutor(fn)
        tc = _tc("read_file", "dup", index=0)
        ex.add(tc)
        ex.add(tc)
        ex.add(_tc("read_file", "dup", index=0))
        await ex.wait_all()
        assert calls == ["dup"]

    @pytest.mark.asyncio
    async def test_same_id_different_index_added(self):
        calls: List[str] = []

        async def fn(tc):
            calls.append(f"{tc.id}:{tc.index}")
            return ("ok", None, None)

        ex = StreamingToolExecutor(fn)
        ex.add(_tc("read_file", "x", index=0))
        ex.add(_tc("read_file", "x", index=1))
        await ex.wait_all()
        assert sorted(calls) == ["x:0", "x:1"]


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

class TestCancellation:
    @pytest.mark.asyncio
    async def test_cancel_executing_returns_cancelled_error(self):
        async def fn(tc):
            await asyncio.sleep(60)
            return ("never", None, None)

        ex = StreamingToolExecutor(fn)
        ex.add(_tc("read_file", "1"))
        await asyncio.sleep(0)
        ex.cancel_all()
        results = await ex.wait_all()
        assert len(results) == 1
        _, value = results[0]
        assert isinstance(value, asyncio.CancelledError)

    @pytest.mark.asyncio
    async def test_cancel_queued_returns_cancelled_error(self):
        gate = asyncio.Event()

        async def fn(tc):
            if tc.id == "blocker":
                await gate.wait()
            return ("ok", None, None)

        ex = StreamingToolExecutor(fn, concurrency_check=_read_only_safe)
        ex.add(_tc("write_file", "blocker"))
        ex.add(_tc("read_file", "queued"))
        await asyncio.sleep(0)
        ex.cancel_all()
        gate.set()
        results = await ex.wait_all()
        assert len(results) == 2
        # blocker may have been cancelled or completed depending on timing.
        # queued was definitely still in QUEUED at cancel time.
        queued_value = next(v for tc, v in results if tc.id == "queued")
        assert isinstance(queued_value, asyncio.CancelledError) or \
               isinstance(queued_value, BaseException)

    @pytest.mark.asyncio
    async def test_add_after_cancel_is_noop(self):
        async def fn(tc):
            return ("ok", None, None)

        ex = StreamingToolExecutor(fn)
        ex.cancel_all()
        ex.add(_tc("read_file", "1"))
        results = await ex.wait_all()
        assert results == []


# ---------------------------------------------------------------------------
# Exception capture
# ---------------------------------------------------------------------------

class TestExceptionCapture:
    @pytest.mark.asyncio
    async def test_executor_fn_exception_returned_as_value(self):
        async def fn(tc):
            raise RuntimeError("boom")

        ex = StreamingToolExecutor(fn)
        ex.add(_tc("read_file", "1"))
        results = await ex.wait_all()
        assert len(results) == 1
        _, value = results[0]
        assert isinstance(value, RuntimeError)
        assert str(value) == "boom"

    @pytest.mark.asyncio
    async def test_exception_does_not_block_subsequent_tools(self):
        async def fn(tc):
            if tc.id == "bad":
                raise RuntimeError("boom")
            return (tc.id, None, None)

        ex = StreamingToolExecutor(fn, concurrency_check=_read_only_safe)
        ex.add(_tc("read_file", "bad"))
        ex.add(_tc("grep", "good"))
        results = await ex.wait_all()
        values = {r[0].id: r[1] for r in results}
        assert isinstance(values["bad"], RuntimeError)
        assert values["good"] == ("good", None, None)


# ---------------------------------------------------------------------------
# is_added
# ---------------------------------------------------------------------------

class TestIsAdded:
    @pytest.mark.asyncio
    async def test_is_added_reflects_state(self):
        async def fn(tc):
            return ("ok", None, None)

        ex = StreamingToolExecutor(fn)
        tc = _tc("read_file", "1", index=0)
        assert ex.is_added(tc) is False
        ex.add(tc)
        assert ex.is_added(tc) is True
        assert ex.is_added(_tc("read_file", "2", index=0)) is False
        await ex.wait_all()
