# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""AsyncToolRuntime + render_result_text: the NativeHarness async-tool core.

No real harness: a fake ``inject`` callback captures the completion text so the
launch → run → inject path is verified deterministically. Covers success and
failure injection, registry state transitions, ``has_running``, ``cancel_all``,
and full (non-truncated) result rendering.
"""
from __future__ import annotations

import asyncio

from openjiuwen.agent_teams.harness.async_tools import AsyncToolRuntime, render_result_text
from openjiuwen.agent_teams.i18n import set_language


def test_render_result_text_variants_are_full():
    """None → empty, str passthrough, dict → JSON, large object not truncated."""
    assert render_result_text(None) == ""
    assert render_result_text("hello") == "hello"
    rendered = render_result_text({"k": 1})
    assert '"k": 1' in rendered
    big = {"data": list(range(1000))}
    assert "999" in render_result_text(big)


def test_runtime_injects_full_result_on_success():
    """A completed task injects the rendered result via the inject callback."""
    set_language("cn")
    injected: list[str] = []

    async def _inject(text: str) -> None:
        injected.append(text)

    async def _scenario() -> AsyncToolRuntime:
        runtime = AsyncToolRuntime(inject=_inject)

        async def _work() -> dict:
            return {"answer": 42}

        runtime.launch("t1", _work, tool_name="demo", description="d")
        while runtime.registry["t1"].status == "running":
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.005)
        return runtime

    runtime = asyncio.run(_scenario())
    assert runtime.registry["t1"].status == "completed"
    assert len(injected) == 1
    assert "42" in injected[0]
    assert "demo" in injected[0]


def test_runtime_injects_error_on_failure():
    """A failing task marks the record error and injects the failure text."""
    set_language("cn")
    injected: list[str] = []

    async def _inject(text: str) -> None:
        injected.append(text)

    async def _scenario() -> AsyncToolRuntime:
        runtime = AsyncToolRuntime(inject=_inject)

        async def _boom() -> None:
            raise ValueError("nope")

        runtime.launch("t2", _boom, tool_name="demo", description="d")
        while runtime.registry["t2"].status == "running":
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.005)
        return runtime

    runtime = asyncio.run(_scenario())
    assert runtime.registry["t2"].status == "error"
    assert "nope" in runtime.registry["t2"].error
    assert len(injected) == 1
    assert "nope" in injected[0]


def test_runtime_has_running_and_cancel_all():
    """has_running reflects in-flight tasks; cancel_all stops them without inject."""
    injected: list[str] = []

    async def _inject(text: str) -> None:
        injected.append(text)

    async def _scenario() -> AsyncToolRuntime:
        runtime = AsyncToolRuntime(inject=_inject)
        started = asyncio.Event()

        async def _slow() -> None:
            started.set()
            await asyncio.sleep(10)

        runtime.launch("t3", _slow, tool_name="demo", description="d")
        await started.wait()
        assert runtime.has_running("demo") is True
        assert runtime.has_running("other") is False
        runtime.cancel_all()
        await asyncio.sleep(0.01)
        return runtime

    runtime = asyncio.run(_scenario())
    assert runtime.registry["t3"].status == "error"
    assert injected == []
