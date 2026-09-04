# coding: utf-8

import pytest

from openjiuwen.extensions.observability import usage_aggregation as usage_mod
from openjiuwen.extensions.observability.usage_aggregation import UsageAccumulator


@pytest.fixture(autouse=True)
def _reset_accumulator():
    saved = usage_mod._ACCUMULATOR
    usage_mod._ACCUMULATOR = None
    yield
    usage_mod._ACCUMULATOR = saved


def test_accumulate_and_snapshot():
    acc = UsageAccumulator()
    tid = 12345
    acc.accumulate_llm(tid, prompt=10, completion=20, cost=0.5)
    acc.accumulate_llm(tid, prompt=5, completion=3, cost=0.1)
    acc.accumulate_tool(tid, is_error=False)
    acc.accumulate_tool(tid, is_error=True)
    snap = acc.snapshot(tid)
    assert snap["prompt_tokens"] == 15
    assert snap["completion_tokens"] == 23
    assert snap["tool_calls"] == 2
    assert snap["tool_errors"] == 1
    assert abs(snap["cost"] - 0.6) < 1e-9
    acc.clear(tid)
    assert acc.snapshot(tid) == {}


def test_snapshot_unknown_trace_is_empty():
    assert UsageAccumulator().snapshot(999) == {}


def test_drain_rollup_snapshots_and_clears():
    tid = 12345
    usage_mod.get_accumulator().accumulate_llm(tid, prompt=10, completion=20, cost=0.5)
    snap = usage_mod.drain_rollup(tid)
    assert snap["prompt_tokens"] == 10
    assert snap["completion_tokens"] == 20
    assert usage_mod.get_accumulator().snapshot(tid) == {}


def test_drain_rollup_unknown_trace_returns_empty():
    assert usage_mod.drain_rollup(999) == {}
    assert usage_mod.drain_rollup(None) == {}

