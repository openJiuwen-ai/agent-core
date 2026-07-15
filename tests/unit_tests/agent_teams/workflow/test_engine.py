# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Engine-port tests: run the ported dw/wf engine offline with MockBackend.

These exercise the business-agnostic core (no agent_teams coupling): the DSL
primitives, structural call-path resume, parallel/pipeline semantics, the
concurrency cap, and the structured progress-event stream added on top of the
port.
"""
from __future__ import annotations

import asyncio

from openjiuwen.agent_teams.workflow.engine import (
    MockBackend,
    ProgressKind,
    WorkflowProgressEvent,
    run_workflow,
)
from openjiuwen.agent_teams.workflow.engine.backends.base import AgentBackend, AgentResult

import pytest

_FANOUT_SCRIPT = '''
from swarmflow import agent, parallel, phase, log

META = {"name": "fanout", "description": "fan out + structured", "phases": [{"title": "Greet"}]}

SCHEMA = {"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]}

async def run(args):
    phase("Greet")
    log("starting")
    a = await agent("say hi", label="hi", schema=SCHEMA)
    b = await parallel([
        lambda: agent("x", label="x"),
        lambda: agent("y", label="y"),
    ])
    return {"a": a, "b": b}
'''

_PIPELINE_SCRIPT = '''
from swarmflow import agent, pipeline

META = {"name": "pipe", "description": "pipeline", "phases": []}

async def run(args):
    return await pipeline(
        ["t1", "t2", "t3"],
        lambda item, orig, i: agent(f"stage0 {item}", label=f"s0-{i}"),
        lambda prev, orig, i: agent(f"stage1 {prev}", label=f"s1-{i}"),
    )
'''

_NESTED_SCRIPT = '''
from swarmflow import agent, parallel

META = {"name": "nested", "description": "nested parallel", "phases": []}

async def run(args):
    return await parallel([
        lambda: parallel([lambda: agent("a", label="a"), lambda: agent("b", label="b")]),
        lambda: parallel([lambda: agent("c", label="c"), lambda: agent("d", label="d")]),
    ])
'''


_LAZY_IMPORT_SCRIPT = '''
META = {"name": "lazy", "description": "import inside run body", "phases": []}

async def run(args):
    from swarmflow import agent  # lazy: not a top-level import
    return await agent("hi", label="lazy")
'''

_CRASH_SCRIPT = '''
META = {"name": "crash", "description": "raises inside run", "phases": []}

async def run(args):
    raise RuntimeError("boom")
'''


def _write(tmp_path, name: str, src: str) -> str:
    path = tmp_path / name
    path.write_text(src, encoding="utf-8")
    return str(path)


def test_run_with_mock_backend_and_progress_events(tmp_path):
    """A workflow runs offline; structured progress events bracket every call."""
    script = _write(tmp_path, "fanout.py", _FANOUT_SCRIPT)
    events: list[WorkflowProgressEvent] = []

    result = asyncio.run(
        run_workflow(str(script), backend=MockBackend(), progress_sink=events.append)
    )

    # Structured-output agent returns a schema-conforming dict; parallel preserves order.
    assert isinstance(result["a"], dict) and "msg" in result["a"]
    assert isinstance(result["b"], list) and len(result["b"]) == 2

    kinds = [e.kind for e in events]
    assert kinds[0] == ProgressKind.WORKFLOW_STARTED
    assert kinds[-1] == ProgressKind.WORKFLOW_COMPLETED
    # 3 agent calls (hi, x, y) -> 3 started + 3 completed.
    assert kinds.count(ProgressKind.AGENT_STARTED) == 3
    assert kinds.count(ProgressKind.AGENT_COMPLETED) == 3
    assert ProgressKind.PHASE in kinds
    # The structured agent's start event carries its prompt and phase.
    started = [e for e in events if e.kind == ProgressKind.AGENT_STARTED and e.label == "hi"]
    assert started and started[0].phase == "Greet" and started[0].prompt == "say hi"


def test_resume_replays_from_journal_without_backend(tmp_path):
    """A second run with --resume is a pure cache replay: zero backend calls."""

    class _CountingBackend(AgentBackend):
        def __init__(self) -> None:
            self.calls = 0

        async def run(self, prompt: str, opts: dict, schema_json: dict | None) -> AgentResult:
            self.calls += 1
            if schema_json is not None:
                return AgentResult(structured={"msg": f"r{self.calls}"})
            return AgentResult(text=f"r{self.calls}")

    script = _write(tmp_path, "fanout.py", _FANOUT_SCRIPT)
    journal = str(tmp_path / "run.jsonl")

    first = _CountingBackend()
    asyncio.run(run_workflow(script, backend=first, journal_path=journal))
    assert first.calls == 3  # hi, x, y

    second = _CountingBackend()
    replay_events: list[WorkflowProgressEvent] = []
    asyncio.run(
        run_workflow(
            script,
            backend=second,
            resume=journal,
            progress_sink=replay_events.append,
        )
    )
    # Pure replay: no backend calls, yet completion events still fire (cache hits).
    assert second.calls == 0
    completed = [e for e in replay_events if e.kind == ProgressKind.AGENT_COMPLETED]
    assert len(completed) == 3


def test_pipeline_runs_each_item_through_all_stages(tmp_path):
    """Pipeline threads each item through every stage independently."""
    script = _write(tmp_path, "pipe.py", _PIPELINE_SCRIPT)
    result = asyncio.run(run_workflow(script, backend=MockBackend()))
    assert isinstance(result, list) and len(result) == 3
    assert all(isinstance(x, str) for x in result)


def test_nested_parallel_does_not_deadlock_under_cap_one(tmp_path):
    """Nested parallel(parallel(...)) completes even with the cap pinned to 1.

    Orchestration coroutines hold no semaphore permit (only ``agent()`` does),
    so a cap of 1 cannot deadlock the fan-out tree.
    """
    script = _write(tmp_path, "nested.py", _NESTED_SCRIPT)
    result = asyncio.run(run_workflow(script, backend=MockBackend(), cap=1))
    assert len(result) == 2
    assert all(len(inner) == 2 for inner in result)


_CONCURRENT_WF_CHILD = '''
from swarmflow import agent

META = {"name": "wf-child", "description": "leaf sub-workflow", "phases": []}

async def run(args):
    return await agent(f"echo:{args}", label="leaf")
'''


def _concurrent_parent_src(child_path: str) -> str:
    """Parent source that fans out three sub-workflows concurrently via parallel."""
    return (
        "from swarmflow import parallel, workflow\n"
        'META = {"name": "wf-parent", "description": "concurrent subs", "phases": []}\n'
        f"_CHILD = {child_path!r}\n"
        "async def run(args):\n"
        "    return await parallel([(lambda i=i: workflow(_CHILD, i)) for i in range(3)])\n"
    )


def test_workflow_runs_concurrently_via_parallel(tmp_path):
    """parallel([workflow(...), ...]) runs every sub-workflow — none are skipped.

    Regression guard: nesting depth is tracked per execution path (a ContextVar
    copied per Task), not as a single shared Runtime counter. So concurrent
    sibling ``workflow()`` calls each keep their own depth budget and all run,
    instead of all-but-one tripping the nesting guard and returning None.
    """
    child = _write(tmp_path, "child.py", _CONCURRENT_WF_CHILD)
    parent = _write(tmp_path, "parent.py", _concurrent_parent_src(child))

    result = asyncio.run(run_workflow(parent, backend=MockBackend()))

    assert isinstance(result, list) and len(result) == 3
    assert all(r is not None for r in result), result


_NESTING_DEEP_CHILD = '''
from swarmflow import workflow

META = {"name": "wf-deep-child", "description": "tries to nest one level deeper", "phases": []}

async def run(args):
    # Already at depth 1 on this path; nesting another workflow() must be skipped.
    return await workflow(args, None)
'''


def test_workflow_nesting_depth_still_capped(tmp_path):
    """Genuine recursion (a sub-workflow calling workflow() again) is still capped."""
    leaf = _write(tmp_path, "leaf.py", _CONCURRENT_WF_CHILD)
    deep = _write(tmp_path, "deep.py", _NESTING_DEEP_CHILD)
    top_src = (
        "from swarmflow import workflow\n"
        'META = {"name": "wf-top", "description": "top", "phases": []}\n'
        f"_DEEP = {deep!r}\n"
        f"_LEAF = {leaf!r}\n"
        "async def run(args):\n"
        "    return await workflow(_DEEP, _LEAF)\n"
    )
    top = _write(tmp_path, "top.py", top_src)

    result = asyncio.run(run_workflow(top, backend=MockBackend()))

    # top -> deep (depth 1) -> deep's workflow(leaf) is depth 2 -> skipped -> None.
    assert result is None


def test_lazy_import_inside_run_resolves(tmp_path):
    """`from swarmflow import ...` resolves in run's body, not only at module top.

    The facade alias is kept installed for the whole run, so a primitive imported
    lazily inside ``run`` works the same as a top-level import.
    """
    script = _write(tmp_path, "lazy.py", _LAZY_IMPORT_SCRIPT)
    result = asyncio.run(run_workflow(script, backend=MockBackend()))
    assert isinstance(result, str) and "lazy" in result


def test_workflow_failed_emitted_on_crash(tmp_path):
    """A workflow that raises emits WORKFLOW_FAILED, not WORKFLOW_COMPLETED."""
    script = _write(tmp_path, "crash.py", _CRASH_SCRIPT)
    events: list[WorkflowProgressEvent] = []

    with pytest.raises(RuntimeError):
        asyncio.run(run_workflow(str(script), backend=MockBackend(), progress_sink=events.append))

    kinds = [e.kind for e in events]
    assert kinds[0] == ProgressKind.WORKFLOW_STARTED
    assert ProgressKind.WORKFLOW_FAILED in kinds
    assert ProgressKind.WORKFLOW_COMPLETED not in kinds
    failed = [e for e in events if e.kind == ProgressKind.WORKFLOW_FAILED]
    assert "boom" in failed[0].message


def test_load_workflow_meta_reads_name_without_importing(tmp_path):
    """load_workflow_meta extracts META via AST only — it does not import."""
    import sys

    from openjiuwen.agent_teams.workflow.engine.loader import load_workflow_meta

    src = 'META = {"name": "metaonly", "description": "d"}\nasync def run(args):\n    return 1\n'
    script = _write(tmp_path, "meta_only.py", src)
    before = {k for k in sys.modules if k.startswith("wf_flow__")}

    meta = load_workflow_meta(str(script))

    after = {k for k in sys.modules if k.startswith("wf_flow__")}
    assert meta["name"] == "metaonly"
    assert before == after  # no importlib import happened


def test_load_workflow_meta_rejects_non_literal_meta(tmp_path):
    """A non-pure-literal META is rejected with MetaError."""
    from openjiuwen.agent_teams.workflow.engine.errors import MetaError
    from openjiuwen.agent_teams.workflow.engine.loader import load_workflow_meta

    src = 'NAME = "x"\nMETA = {"name": NAME}\n'
    script = _write(tmp_path, "bad_meta.py", src)
    with pytest.raises(MetaError):
        load_workflow_meta(str(script))


# ── isolation / agent_type pass-through + per-call fan-out cap ──────────────
_ISOLATION_SCRIPT = '''
from swarmflow import agent

META = {"name": "iso", "description": "isolation + agent_type pass-through", "phases": []}

async def run(args):
    return await agent("hi", label="iso", options={"isolation": "worktree", "agent_type": "Explore"})
'''

_OVERLIMIT_PARALLEL_SCRIPT = '''
from swarmflow import agent, parallel

META = {"name": "over-par", "description": "parallel beyond the fan-out cap", "phases": []}

async def run(args):
    return await parallel([(lambda i=i: agent(f"x{i}", label=str(i))) for i in range(4097)])
'''

_OVERLIMIT_PIPELINE_SCRIPT = '''
from swarmflow import agent, pipeline

META = {"name": "over-pipe", "description": "pipeline beyond the fan-out cap", "phases": []}

async def run(args):
    return await pipeline(list(range(4097)), lambda prev, item, i: agent(f"x{item}", label=str(i)))
'''


def test_agent_accepts_isolation_and_agent_type(tmp_path):
    """isolation / agent_type are accepted on the surface (a no-op in the reference engine)."""
    script = _write(tmp_path, "iso.py", _ISOLATION_SCRIPT)
    result = asyncio.run(run_workflow(script, backend=MockBackend()))
    assert result is not None


def test_parallel_rejects_fan_out_beyond_cap(tmp_path):
    """A single parallel() above the per-call cap is an explicit error, not silent truncation."""
    from openjiuwen.agent_teams.workflow.engine.errors import EngineError

    script = _write(tmp_path, "over_par.py", _OVERLIMIT_PARALLEL_SCRIPT)
    with pytest.raises(EngineError):
        asyncio.run(run_workflow(script, backend=MockBackend()))


def test_pipeline_rejects_fan_out_beyond_cap(tmp_path):
    """A single pipeline() above the per-call cap is an explicit error too."""
    from openjiuwen.agent_teams.workflow.engine.errors import EngineError

    script = _write(tmp_path, "over_pipe.py", _OVERLIMIT_PIPELINE_SCRIPT)
    with pytest.raises(EngineError):
        asyncio.run(run_workflow(script, backend=MockBackend()))
