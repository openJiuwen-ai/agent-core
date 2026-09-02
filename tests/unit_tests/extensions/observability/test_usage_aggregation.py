# coding: utf-8

from openjiuwen.extensions.observability.usage_aggregation import UsageAccumulator


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
