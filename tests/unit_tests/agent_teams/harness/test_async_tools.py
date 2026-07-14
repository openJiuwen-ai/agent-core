# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""AsyncToolRuntime + render_result_text: the NativeHarness async-tool core.

No real harness: a fake ``inject`` callback captures the completion text so the
launch → run → inject path is verified deterministically. Covers success and
failure injection, registry state transitions, ``has_running``, ``cancel_all``,
the ``get`` / ``list_all`` / ``cancel`` / ``wait`` control surface, oversized-
result spill to disk, and full (non-truncated) result rendering.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

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


def test_runtime_get_and_list_all():
    """get returns the record by id (None if unknown); list_all returns all."""

    async def _inject(text: str) -> None:
        pass

    async def _scenario() -> AsyncToolRuntime:
        runtime = AsyncToolRuntime(inject=_inject)

        async def _work() -> str:
            return "ok"

        runtime.launch("a", _work, tool_name="demo", description="da")
        runtime.launch("b", _work, tool_name="demo", description="db")
        while any(r.status == "running" for r in runtime.list_all()):
            await asyncio.sleep(0.005)
        return runtime

    runtime = asyncio.run(_scenario())
    assert runtime.get("a") is not None
    assert runtime.get("missing") is None
    assert {r.task_id for r in runtime.list_all()} == {"a", "b"}


def test_runtime_cancel_marks_record_cancelled():
    """cancel stops a running task and marks its record error/cancelled."""

    async def _inject(text: str) -> None:
        pass

    async def _scenario() -> tuple[AsyncToolRuntime, bool, bool]:
        runtime = AsyncToolRuntime(inject=_inject)
        started = asyncio.Event()

        async def _slow() -> None:
            started.set()
            await asyncio.sleep(10)

        runtime.launch("c", _slow, tool_name="demo", description="d")
        await started.wait()
        ok = await runtime.cancel("c")
        missing = await runtime.cancel("nope")
        await asyncio.sleep(0.01)
        return runtime, ok, missing

    runtime, ok, missing = asyncio.run(_scenario())
    assert ok is True
    assert missing is False
    assert runtime.get("c").status == "error"
    assert runtime.get("c").error == "cancelled"


def test_runtime_wait_returns_on_completion_and_timeout():
    """wait returns terminal record on completion, running record on timeout."""

    async def _inject(text: str) -> None:
        pass

    async def _scenario() -> tuple:
        runtime = AsyncToolRuntime(inject=_inject)
        gate = asyncio.Event()

        async def _gated() -> str:
            await gate.wait()
            return "done"

        runtime.launch("w", _gated, tool_name="demo", description="d")
        # Still running → wait times out; snapshot the status now. The record is
        # mutated in place when it completes, so holding the reference across the
        # second wait would read the later state.
        timed_out_status = (await runtime.wait("w", 0.02)).status
        gate.set()
        # Now completes → wait returns the terminal record.
        finished = await runtime.wait("w", 1.0)
        unknown = await runtime.wait("missing", 0.01)
        return timed_out_status, finished.status, finished.result, unknown

    timed_out_status, finished_status, result, unknown = asyncio.run(_scenario())
    assert timed_out_status == "running"
    assert finished_status == "completed"
    assert result == "done"
    assert unknown is None


def test_runtime_small_result_inlines(tmp_path):
    """A result at or under the threshold injects in full, no spill file."""

    injected: list[str] = []

    async def _inject(text: str) -> None:
        injected.append(text)

    async def _scenario() -> AsyncToolRuntime:
        runtime = AsyncToolRuntime(
            inject=_inject,
            output_dir_resolver=lambda: tmp_path,
            spill_threshold=1000,
        )

        async def _small() -> str:
            return "x" * 500

        runtime.launch("s1", _small, tool_name="demo", description="d")
        while runtime.registry["s1"].status == "running":
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.005)
        return runtime

    runtime = asyncio.run(_scenario())
    assert runtime.get("s1").output_file is None
    assert "x" * 500 in injected[0]
    assert not list(tmp_path.iterdir())


def test_runtime_large_result_spills_to_disk(tmp_path):
    """A result over the threshold spills to disk; injection carries the path."""
    set_language("cn")
    injected: list[str] = []

    async def _inject(text: str) -> None:
        injected.append(text)

    async def _scenario() -> AsyncToolRuntime:
        runtime = AsyncToolRuntime(
            inject=_inject,
            output_dir_resolver=lambda: tmp_path,
            spill_threshold=1000,
        )

        async def _big() -> str:
            return "y" * 5000

        runtime.launch("s2", _big, tool_name="demo", description="d")
        while runtime.registry["s2"].status == "running":
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.005)
        return runtime

    runtime = asyncio.run(_scenario())
    record = runtime.get("s2")
    assert record.output_file is not None
    spill = Path(record.output_file)
    assert spill.exists()
    assert spill.read_text(encoding="utf-8") == "y" * 5000
    # Injection is a summary + pointer, not the full 5000-char payload.
    assert record.output_file in injected[0]
    assert len(injected[0]) < 5000


def test_runtime_spill_disabled_without_resolver():
    """With no resolver a large result still inlines in full (back-compat)."""
    injected: list[str] = []

    async def _inject(text: str) -> None:
        injected.append(text)

    async def _scenario() -> AsyncToolRuntime:
        runtime = AsyncToolRuntime(inject=_inject, spill_threshold=1000)

        async def _big() -> str:
            return "z" * 5000

        runtime.launch("s3", _big, tool_name="demo", description="d")
        while runtime.registry["s3"].status == "running":
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.005)
        return runtime

    runtime = asyncio.run(_scenario())
    assert runtime.get("s3").output_file is None
    assert "z" * 5000 in injected[0]


def test_runtime_uses_custom_completion_formatters():
    """Record format callbacks override generic async_tool.* injection text."""
    injected: list[str] = []

    async def _inject(text: str) -> None:
        injected.append(text)

    async def _scenario() -> None:
        runtime = AsyncToolRuntime(inject=_inject)

        async def _work() -> str:
            return "payload"

        runtime.launch(
            "fmt1",
            _work,
            tool_name="swarmflow",
            description="d",
            format_completed=lambda result: f"custom-ok:{result}",
            format_failed=lambda err: f"custom-fail:{err}",
        )
        while runtime.registry["fmt1"].status == "running":
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.005)

    asyncio.run(_scenario())
    assert injected == ["custom-ok:payload"]


def test_runtime_ignores_run_id_without_custom_i18n_keys():
    """Without format callbacks the generic completion text has no run_id."""
    injected: list[str] = []

    async def _inject(text: str) -> None:
        injected.append(text)

    async def _scenario() -> None:
        runtime = AsyncToolRuntime(inject=_inject)

        async def _work() -> str:
            return "payload"

        runtime.launch("fmt2", _work, tool_name="demo", description="d")
        while runtime.registry["fmt2"].status == "running":
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.005)

    asyncio.run(_scenario())
    assert "run_id" not in injected[0]


def test_swarmflow_completion_formatters_include_run_id():
    """SwarmflowTool terminal formatters embed run_id in completion and failure text."""
    from openjiuwen.agent_teams.workflow.tool_swarmflow import SwarmflowTool

    class _Harness:
        model = None

    tool = SwarmflowTool(
        parent_agent=_Harness(),
        messager=None,
        team_name="t",
        model_resolver=None,
        concurrency_governor=None,
        language="en",
    )
    run_id = "wf_abc123def456"
    completed = tool.format_completed_injection("payload", run_id=run_id)
    failed = tool.format_failed_injection("boom", run_id=run_id)
    assert run_id in completed
    assert "payload" in completed
    assert run_id in failed
    assert "boom" in failed
