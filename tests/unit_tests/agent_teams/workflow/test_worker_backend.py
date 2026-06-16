# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TeamWorkerBackend + preprocessing tests (no real LLM).

The real worker harness execution lives in ``_execute_worker``; here a subclass
overrides it to simulate the worker calling ``structured_output`` (schema path)
or returning free text, so the backend's run/result/4-layer flow is exercised
deterministically. Preprocessing is verified against the offline MockBackend.
A separate test exercises the real ``_execute_worker`` spec-derivation path with
``TeamHarness.build`` patched, to assert worker = "teammate without team tools".
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Sequence

from openjiuwen.agent_teams.rails.team_context import inject_team_handles
from openjiuwen.agent_teams.schema.build_context import BuildContext
from openjiuwen.agent_teams.workflow.backends.team_worker_backend import TeamWorkerBackend
from openjiuwen.agent_teams.workflow.engine.backends.base import AgentBackend, AgentResult
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


def _build_context_with_worktree_manager(manager: Any) -> BuildContext:
    context = BuildContext()
    inject_team_handles(context.extras, worktree_manager=manager)
    return context


def test_schema_path_returns_structured_and_free_path_returns_text(tmp_path):
    """Schema agent() -> structured_output capture; no-schema agent() -> free text."""
    script = _write(tmp_path, _SCRIPT)
    backend = _FakeWorkerBackend(model=None, team_backend=None)
    events: list = []

    result = asyncio.run(run_workflow(str(script), backend=backend, progress_sink=events.append))

    # Structured result came through structured_output and validated against SCHEMA.
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
    """A worker that never calls structured_output -> backend raises -> agent()=None.

    The engine retries on the backend error and, after exhaustion, yields
    ``None`` for that call (a value dw control-flow already tolerates).
    """

    class _SilentWorker(TeamWorkerBackend):
        async def _execute_worker(self, prompt, tools, *, member_name, has_schema, model):
            return ""  # never fills structured_output

    script = _write(tmp_path, _SCRIPT)
    backend = _SilentWorker(model=None, team_backend=None)
    result = asyncio.run(run_workflow(str(script), backend=backend))
    assert result["a"] is None  # structured call gave up after retries
    assert result["b"] == ""  # free-text call returns the empty final message


def test_per_call_model_hint_routes_through_resolver(tmp_path):
    """agent(model=X): known name -> resolved config; unknown / no hint -> None.

    The resolver returns a worker ``TeamModelConfig`` (not a built model); a miss
    or absent hint yields ``None`` so the worker inherits its base spec's model.
    """
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
        model_resolver=lambda name: "fast-cfg" if name == "fast" else None,
    )
    result = asyncio.run(run_workflow(_write(tmp_path, script), backend=backend))

    # "fast" resolves to its config; "unknown" and no-hint miss -> None (the
    # worker then inherits its base spec's model, not exercised by this stub).
    assert result == ["ran::fast-cfg", "ran::None", "ran::None"]
    assert seen == ["fast-cfg", None, None]


def test_agent_isolation_option_is_forwarded_to_backend(tmp_path):
    """agent(isolation='worktree') is part of the backend call options."""
    script = '''
from swarmflow import agent

META = {"name": "iso", "description": "isolation routing", "phases": []}

async def run(args):
    return await agent("task", label="w", isolation="worktree")
'''
    seen: list[dict] = []

    class _Backend(AgentBackend):
        async def run(self, prompt, opts, schema_json):
            seen.append(dict(opts))
            return AgentResult(text="ok")

    result = asyncio.run(run_workflow(_write(tmp_path, script), backend=_Backend()))

    assert result == "ok"
    assert seen[0]["isolation"] == "worktree"


def test_agent_rejects_unknown_isolation(tmp_path):
    """Only worktree isolation is supported by the swarmflow DSL."""
    script = '''
from swarmflow import agent

META = {"name": "bad-iso", "description": "bad isolation", "phases": []}

async def run(args):
    return await agent("task", label="w", isolation="container")
'''
    import pytest
    from openjiuwen.agent_teams.workflow.engine.errors import WorkflowError

    with pytest.raises(WorkflowError, match="only supports 'worktree'"):
        asyncio.run(run_workflow(_write(tmp_path, script), backend=_FakeWorkerBackend(model=None)))


def test_worktree_isolation_requires_host_worktree_manager():
    """Worktree isolation must use the host-provided manager, not a fallback."""
    import pytest
    from openjiuwen.agent_teams.workflow.engine.errors import BackendError

    backend = TeamWorkerBackend(model=None, team_backend=None)

    with pytest.raises(BackendError, match="host-provided worktree manager"):
        asyncio.run(backend.run("write code", {"label": "sum", "isolation": "worktree"}, None))


def test_preprocess_builds_four_layer_offline(tmp_path):
    """MockBackend dry-run yields the planned 4-layer WorkflowRun, zero network."""
    script = _write(tmp_path, _SCRIPT)
    run4 = asyncio.run(preprocess_swarmflow(script))
    assert run4.name == "wk"
    do = next(p for p in run4.phases if p.title == "Do")
    assert len(do.agents) == 2
    assert all(a.status == "completed" for a in do.agents)
    assert do.agents[0].prompt == "compute the answer"


def test_execute_worker_derives_teammate_spec_without_team_tools(tmp_path, monkeypatch):
    """_execute_worker derives a WORKER spec from the base spec: a teammate
    without team tools — todo planning preserved, structured_output appended,
    role=WORKER, swarmflow worker system prompt."""
    from openjiuwen.agent_teams.harness import team_harness as th_mod
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
    from openjiuwen.agent_teams.schema.team import TeamRole

    base = DeepAgentSpec(enable_task_loop=True, enable_task_planning=True, tools=[])
    captured: dict = {}

    class _FakeHarness:
        async def run_once(self, content, **kw):
            return {"output": "ok", "result_type": "answer"}

        def add_rail(self, rail):
            return None

        async def dispose(self):
            return None

    def _fake_build(*, agent_spec, role, member_name, build_context=None, **kw):
        captured["spec"] = agent_spec
        captured["role"] = role
        captured["member_name"] = member_name
        captured["build_context"] = build_context
        return _FakeHarness()

    monkeypatch.setattr(th_mod.TeamHarness, "build", _fake_build)

    backend = TeamWorkerBackend(model=None, team_backend=None, worker_base_spec=base)
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}

    # Free path: no structured_output tool, base capabilities preserved.
    asyncio.run(backend._execute_worker("t", [], member_name="wf-w-0", has_schema=False, model=None))
    spec = captured["spec"]
    assert captured["role"] == TeamRole.WORKER
    assert captured["build_context"] is None
    assert spec.enable_task_loop is True  # todo planning / task loop preserved
    assert spec.enable_task_planning is True
    assert spec.card.name == "wf-w-0"
    assert "swarmflow" in (spec.system_prompt or "")
    assert spec.tools == []  # no team tools

    # Schema path: the structured_output tool instance is appended to spec.tools.
    from openjiuwen.agent_teams.workflow.backends.structured_output_tool import StructuredOutputTool

    tool = StructuredOutputTool(schema)
    asyncio.run(
        backend._execute_worker("t", [tool], member_name="wf-w-1", has_schema=True, model=None)
    )
    assert captured["spec"].tools == [tool]
    assert captured["spec"].tools[0].card.name == "structured_output"


def test_execute_worker_derives_build_context_from_leader_base(monkeypatch):
    """_execute_worker derives a per-worker BuildContext from the leader base."""
    from openjiuwen.agent_teams.harness import team_harness as th_mod
    from openjiuwen.agent_teams.schema.build_context import BuildContext
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
    from openjiuwen.agent_teams.schema.team import TeamRole

    base = DeepAgentSpec(enable_task_loop=True, tools=[])
    leader_ctx = BuildContext(
        language="cn",
        member_name="leader",
        role="leader",
        extras={"team_key": "leader-only"},
    )
    captured: dict = {}

    class _FakeHarness:
        async def run_once(self, content, **kw):
            return {"output": "ok", "result_type": "answer"}

        def add_rail(self, rail):
            return None

        async def dispose(self):
            return None

    def _fake_build(*, agent_spec, role, member_name, build_context=None, **kw):
        captured["build_context"] = build_context
        return _FakeHarness()

    monkeypatch.setattr(th_mod.TeamHarness, "build", _fake_build)

    backend = TeamWorkerBackend(
        model=None,
        team_backend=None,
        worker_base_spec=base,
        build_context=leader_ctx,
        language="en",
        team_name="myteam",
    )
    asyncio.run(backend._execute_worker("t", [], member_name="wf-w-0", has_schema=False, model=None))

    ctx = captured["build_context"]
    assert ctx is not None
    assert ctx.member_name == "wf-w-0"
    assert ctx.role == TeamRole.WORKER.value
    assert ctx.member_card_id == "myteam_wf-w-0"
    assert ctx.language == "en"
    assert ctx.extras == {"team_key": "leader-only"}
    assert ctx is not leader_ctx


def test_worktree_isolation_sets_worker_workspace_and_removes_clean_worktree(tmp_path, monkeypatch):
    """agent(isolation='worktree') creates an owner worktree for the worker cwd."""
    from openjiuwen.agent_teams.harness import team_harness as th_mod
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
    from openjiuwen.harness.tools.worktree.models import (
        WorktreeChangeSummary,
        WorktreeCreateResult,
    )

    worktree_path = tmp_path / "worker-wt"
    worktree_path.mkdir()
    captured: dict = {}

    class _Manager:
        def __init__(self):
            self.created: list[str] = []
            self.removed: list[tuple[str, str]] = []

        async def create_owner_worktree(self, slug):
            self.created.append(slug)
            return WorktreeCreateResult(
                worktree_path=str(worktree_path),
                worktree_branch=f"worktree-{slug}",
                head_commit="base",
            )

        async def count_changes(self, session):
            captured["session"] = session
            return WorktreeChangeSummary(changed_files=0, commits=0)

        async def remove_worktree(self, path, repo_root):
            self.removed.append((path, repo_root))
            return True

    class _FakeHarness:
        async def run_once(self, content, **kw):
            return "ok"

        async def dispose(self):
            return None

    def _fake_build(*, agent_spec, role, member_name, build_context=None, **kw):
        captured["spec"] = agent_spec
        captured["member_name"] = member_name
        return _FakeHarness()

    async def _fake_root(path):
        return str(tmp_path)

    manager = _Manager()
    monkeypatch.setattr(th_mod.TeamHarness, "build", _fake_build)
    monkeypatch.setattr("openjiuwen.harness.tools.worktree.git.find_canonical_git_root", _fake_root)

    backend = TeamWorkerBackend(
        model=None,
        team_backend=None,
        team_name="code-team",
        worker_base_spec=DeepAgentSpec(tools=[]),
        build_context=_build_context_with_worktree_manager(manager),
    )
    result = asyncio.run(backend.run("write code", {"label": "sum", "isolation": "worktree"}, None))

    assert result.text == "ok"
    assert manager.created and manager.created[0].startswith("agent-code-team-wf-sum-0-")
    assert captured["spec"].workspace.root_path == str(worktree_path)
    assert captured["spec"].workspace.stable_base is False
    assert manager.removed == [(str(worktree_path), str(tmp_path))]
    assert captured["session"].worktree_path == str(worktree_path)


def test_worktree_isolation_keeps_changed_worktree_without_return_metadata(tmp_path, monkeypatch):
    """Changed isolated worktrees are kept, but not injected into agent results."""
    from openjiuwen.agent_teams.harness import team_harness as th_mod
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
    from openjiuwen.harness.tools.worktree.models import (
        WorktreeChangeSummary,
        WorktreeCreateResult,
    )

    script = '''
from swarmflow import agent

META = {"name": "iso-meta", "description": "worktree isolation", "phases": []}

async def run(args):
    return await agent("write code", label="sum", isolation="worktree")
'''
    worktree_path = tmp_path / "worker-wt"
    worktree_path.mkdir()

    class _Manager:
        def __init__(self):
            self.removed: list[tuple[str, str]] = []

        async def create_owner_worktree(self, slug):
            return WorktreeCreateResult(
                worktree_path=str(worktree_path),
                worktree_branch=f"worktree-{slug}",
                head_commit="base",
            )

        async def count_changes(self, session):
            return WorktreeChangeSummary(changed_files=1, commits=0)

        async def remove_worktree(self, path, repo_root):
            self.removed.append((path, repo_root))
            return True

    class _FakeHarness:
        async def run_once(self, content, **kw):
            return '{"status": "done"}'

        async def dispose(self):
            return None

    def _fake_build(*, agent_spec, role, member_name, build_context=None, **kw):
        return _FakeHarness()

    async def _fake_root(path):
        return str(tmp_path)

    manager = _Manager()
    monkeypatch.setattr(th_mod.TeamHarness, "build", _fake_build)
    monkeypatch.setattr("openjiuwen.harness.tools.worktree.git.find_canonical_git_root", _fake_root)

    backend = TeamWorkerBackend(
        model=None,
        team_backend=None,
        team_name="code-team",
        worker_base_spec=DeepAgentSpec(tools=[]),
        build_context=_build_context_with_worktree_manager(manager),
    )
    result = asyncio.run(run_workflow(_write(tmp_path, script), backend=backend))

    parsed = json.loads(result)
    assert parsed == {"status": "done"}
    assert manager.removed == []
