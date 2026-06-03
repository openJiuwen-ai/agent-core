# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TeamWorkerBackend + preprocessing tests (no real LLM).

The real worker DeepAgent turn lives in ``_execute_worker``; here a subclass
overrides it to simulate the worker calling ``submit_result`` (schema path) or
returning free text, so the backend's run/result/4-layer flow is exercised
deterministically. Preprocessing is verified against the offline MockBackend.
"""
from __future__ import annotations

import asyncio
from typing import Any, Sequence

from openjiuwen.agent_teams.workflow.backends.team_worker_backend import TeamWorkerBackend
from openjiuwen.agent_teams.workflow.engine import run_workflow
from openjiuwen.agent_teams.workflow.runner import preprocess_swarmflow
from openjiuwen.agent_teams.workflow.schema import build_workflow_run_from_events

_SCRIPT = '''
from swarmflow import agent, phase

META = {"name": "wk", "description": "worker flow", "phases": [{"title": "Do"}]}

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}

async def run(args):
    phase("Do")
    a = await agent("compute the answer", label="compute", schema=SCHEMA)
    b = await agent("free narration", label="free")
    return {"a": a, "b": b}
'''


class _FakeWorkerBackend(TeamWorkerBackend):
    """Simulates the worker DeepAgent turn without an LLM."""

    async def _execute_worker(
        self,
        prompt: str,
        tools: Sequence[Any],
        *,
        member_name: str,
        has_schema: bool,
        model: Any,
    ) -> str:
        if has_schema and tools:
            tools[0].captured = {"answer": f"done::{member_name}"}
            tools[0].called = True
            return ""
        return f"freetext::{member_name}"


def _write(tmp_path, src: str) -> str:
    path = tmp_path / "wk.py"
    path.write_text(src, encoding="utf-8")
    return str(path)


def test_schema_path_returns_structured_and_free_path_returns_text(tmp_path):
    """Schema agent() -> submit_result capture; no-schema agent() -> free text."""
    script = _write(tmp_path, _SCRIPT)
    backend = _FakeWorkerBackend(model=None, team_backend=None)
    events: list = []

    result = asyncio.run(run_workflow(str(script), backend=backend, progress_sink=events.append))

    # Structured result came through submit_result and validated against SCHEMA.
    assert isinstance(result["a"], dict) and result["a"]["answer"].startswith("done::wf-compute-")
    # Free-text result came through the worker's final message.
    assert isinstance(result["b"], str) and result["b"].startswith("freetext::wf-free-")

    # 4-layer structure: one phase "Do" with two completed agents.
    run4 = build_workflow_run_from_events(events)
    assert run4.status == "completed"
    do = next(p for p in run4.phases if p.title == "Do")
    assert [a.label for a in do.agents] == ["compute", "free"]
    assert all(a.status == "completed" for a in do.agents)
    assert do.agents[0].prompt == "compute the answer"


def test_missing_submit_makes_agent_return_none(tmp_path):
    """A worker that never calls submit_result -> backend raises -> agent()=None.

    The engine retries on the backend error and, after exhaustion, yields
    ``None`` for that call (a value dw control-flow already tolerates).
    """

    class _SilentWorker(TeamWorkerBackend):
        async def _execute_worker(self, prompt, tools, *, member_name, has_schema, model):
            return ""  # never fills submit_result

    script = _write(tmp_path, _SCRIPT)
    backend = _SilentWorker(model=None, team_backend=None)
    result = asyncio.run(run_workflow(str(script), backend=backend))
    assert result["a"] is None  # structured call gave up after retries
    assert result["b"] == ""  # free-text call returns the empty final message


def test_per_call_model_hint_routes_through_resolver(tmp_path):
    """agent(model=X): known name -> resolved model; unknown / no hint -> default."""
    script = '''
from swarmflow import agent

META = {"name": "route", "description": "model routing", "phases": []}

async def run(args):
    a = await agent("task a", label="a", model="fast")
    b = await agent("task b", label="b", model="unknown")
    c = await agent("task c", label="c")
    return [a, b, c]
'''
    seen: list = []

    class _RecordingBackend(TeamWorkerBackend):
        async def _execute_worker(self, prompt, tools, *, member_name, has_schema, model):
            seen.append(model)
            return f"ran::{model}"

    backend = _RecordingBackend(
        model="leader-model",
        team_backend=None,
        model_resolver=lambda name: "fast-model" if name == "fast" else None,
    )
    result = asyncio.run(run_workflow(_write(tmp_path, script), backend=backend))

    # "fast" resolves; "unknown" misses -> default; no hint -> default.
    assert result == ["ran::fast-model", "ran::leader-model", "ran::leader-model"]
    assert seen == ["fast-model", "leader-model", "leader-model"]


def test_preprocess_builds_four_layer_offline(tmp_path):
    """MockBackend dry-run yields the planned 4-layer WorkflowRun, zero network."""
    script = _write(tmp_path, _SCRIPT)
    run4 = asyncio.run(preprocess_swarmflow(script))
    assert run4.name == "wk"
    do = next(p for p in run4.phases if p.title == "Do")
    assert len(do.agents) == 2
    assert all(a.status == "completed" for a in do.agents)
    assert do.agents[0].prompt == "compute the answer"
