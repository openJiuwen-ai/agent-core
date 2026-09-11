# coding: utf-8
from __future__ import annotations
import asyncio
import pytest
from openjiuwen.agent_teams.workflow.backends.avatar_session_backend import AvatarSessionManager


def _make_manager():
    return AvatarSessionManager(team_name="t", run_id="wf_1")


def test_reply_buffered_when_no_future():
    mgr = _make_manager()
    # 无挂起的 future，回复应被缓存而非丢弃
    ok = mgr.submit_human_reply("corr_1", "迟到的回复")
    assert ok is True
    assert mgr._pending_reply_buffer.get("corr_1") == "迟到的回复"


def test_reply_consumed_before_new_future():
    mgr = _make_manager()
    mgr.submit_human_reply("corr_1", "缓存的回复")
    # 模拟 resume 后 _await_human_reply 重挂 future 前查 buffer
    # 验证 buffer 命中即清（pop）
    buffered = mgr._pending_reply_buffer.pop("corr_1", None)
    assert buffered == "缓存的回复"
    assert "corr_1" not in mgr._pending_reply_buffer


def test_reply_resolves_live_future():
    mgr = _make_manager()
    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    mgr._pending_human["corr_2"] = fut
    ok = mgr.submit_human_reply("corr_2", "即时回复")
    assert ok is True and fut.result() == "即时回复"
    loop.close()


def test_abort_clears_buffer():
    mgr = _make_manager()
    mgr._pending_reply_buffer["corr_3"] = "滞留"
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(mgr.abort_all())
    assert mgr._pending_reply_buffer == {}
    loop.close()


def test_await_human_reply_consumes_buffered_reply_end_to_end():
    # Drive _await_human_reply through its buffer-consume branch: a buffered
    # reply must be returned immediately, no new future registered, no prompt
    # re-pushed, and _on_human_replied fired exactly once.
    replied_calls = []
    mgr = AvatarSessionManager(
        team_name="t",
        run_id="wf_1",
        on_human_replied=lambda member, corr, reply: replied_calls.append((member, corr, reply)),
    )
    mgr._pending_reply_buffer["corr_1"] = "缓存的回复"

    class _FakeState:
        member_name = "wf-human-test"

    loop = asyncio.new_event_loop()
    try:
        raw = loop.run_until_complete(
            mgr._await_human_reply(
                state=_FakeState(), prompt="问", opts={}, correlation_id="corr_1"
            )
        )
    finally:
        loop.close()

    assert raw == "缓存的回复"
    assert "corr_1" not in mgr._pending_reply_buffer
    assert "corr_1" not in mgr._pending_human
    assert replied_calls == [("wf-human-test", "corr_1", "缓存的回复")]
