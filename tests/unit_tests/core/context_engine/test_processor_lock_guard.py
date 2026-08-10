# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Guarded processor-lock acquisition on SessionModelContext.

The processor lock serialises add_messages / get_context_window /
compress_context on one context. A holder wedged on a never-completing await
used to hang every later acquirer invisibly; the guarded acquire now logs a
WARNING per interval while blocked, then proceeds normally once the holder
releases — behavior is unchanged, stalls become observable.
"""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.context import context as context_module
from openjiuwen.core.foundation.llm import UserMessage


async def _make_context():
    engine = ContextEngine(ContextEngineConfig(default_window_message_num=100))
    return await engine.create_context("lock_guard_ctx", None)


@pytest.mark.asyncio
async def test_guarded_acquire_warns_while_blocked_then_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blocked acquirer logs warnings per interval and succeeds once the lock frees."""
    monkeypatch.setattr(context_module, "_PROCESSOR_LOCK_WARN_INTERVAL_SECONDS", 0.05)
    warnings: list[str] = []
    original_warning = context_module.logger.warning

    def capture_warning(msg, *args, **kwargs):
        warnings.append(str(msg) % args if args else str(msg))
        original_warning(msg, *args, **kwargs)

    monkeypatch.setattr(context_module.logger, "warning", capture_warning)

    ctx = await _make_context()
    await ctx._processor_lock.acquire()

    async def release_later() -> None:
        await asyncio.sleep(0.12)
        ctx._processor_lock.release()

    releaser = asyncio.create_task(release_later())
    await asyncio.wait_for(ctx._acquire_processor_lock("test_caller"), timeout=2.0)
    await releaser

    assert ctx._processor_lock.locked()
    ctx._processor_lock.release()

    blocked_warnings = [w for w in warnings if "test_caller" in w and "blocked" in w]
    assert blocked_warnings, warnings
    recovered = [w for w in warnings if "test_caller" in w and "acquired" in w]
    assert recovered, warnings


@pytest.mark.asyncio
async def test_add_messages_and_get_context_window_roundtrip_under_guard() -> None:
    """The guarded lock keeps the normal add/get paths working unchanged."""
    ctx = await _make_context()
    await ctx.add_messages(UserMessage(content="hello"))
    window = await ctx.get_context_window(system_messages=[], tools=[])
    contents = [m.content for m in window.get_messages()]
    assert contents == ["hello"]
    assert not ctx._processor_lock.locked()


@pytest.mark.asyncio
async def test_concurrent_add_messages_serialise_without_deadlock() -> None:
    """Concurrent acquirers queue behind each other and all complete."""
    ctx = await _make_context()
    await asyncio.gather(*(ctx.add_messages(UserMessage(content=f"m{i}")) for i in range(5)))
    assert len(ctx.get_messages()) == 5
    assert not ctx._processor_lock.locked()


@pytest.mark.asyncio
async def test_compress_context_busy_path_unaffected() -> None:
    """compress_context still reports busy when the lock is already held."""
    ctx = await _make_context()
    await ctx._processor_lock.acquire()
    try:
        result = await ctx.compress_context()
    finally:
        ctx._processor_lock.release()
    assert result == "busy"
