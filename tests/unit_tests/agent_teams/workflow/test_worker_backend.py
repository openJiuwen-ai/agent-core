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
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

import pytest

from openjiuwen.agent_teams.rails.team_context import inject_team_handles
from openjiuwen.agent_teams.schema.build_context import BuildContext
from openjiuwen.agent_teams.workflow.backends.team_worker_backend import TeamWorkerBackend
from openjiuwen.agent_teams.workflow.engine import run_workflow
from openjiuwen.agent_teams.workflow.engine.backends.base import AgentBackend, AgentResult
from openjiuwen.agent_teams.workflow.engine.errors import BackendError
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
        budget_rail: Any = None,
        agent_id: str | None = None,
        phase: str | None = None,
        label: str | None = None,
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
    backend = _FakeWorkerBackend(model=None)
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
    # Schema path with empty invoke output falls back to JSON preview as outcome.
    assert do.agents[0].outcome and '"answer"' in do.agents[0].outcome


def test_schema_path_prefers_natural_text_over_json(tmp_path):
    """When schema capture succeeds and invoke also returns narration, prefer narration."""

    class _NarratingWorker(TeamWorkerBackend):
        async def _execute_worker(self, prompt, tools, *, member_name, has_schema, model, budget_rail=None, agent_id=None, phase=None, label=None):
            if has_schema and tools:
                tools[0].captured = {"answer": f"done::{member_name}"}
                tools[0].called = True
                return f"narration::{member_name}"
            return f"freetext::{member_name}"

    script = _write(tmp_path, _SCRIPT)
    events: list = []
    result = asyncio.run(
        run_workflow(str(script), backend=_NarratingWorker(model=None), progress_sink=events.append)
    )
    assert result["a"]["answer"].startswith("done::")
    do = next(p for p in build_workflow_run_from_events(events).phases if p.title == "Do")
    assert do.agents[0].outcome.startswith("narration::")


def test_missing_submit_makes_agent_return_none(tmp_path):
    """A worker that never calls structured_output -> backend raises -> agent()=None.

    The engine retries on the backend error and, after exhaustion, yields
    ``None`` for that call (a value dw control-flow already tolerates).
    """

    class _SilentWorker(TeamWorkerBackend):
        async def _execute_worker(self, prompt, tools, *, member_name, has_schema, model, budget_rail=None, agent_id=None, phase=None, label=None):
            return ""  # never fills structured_output

    script = _write(tmp_path, _SCRIPT)
    backend = _SilentWorker(model=None)
    result = asyncio.run(run_workflow(str(script), backend=backend))
    assert result["a"] is None  # structured call gave up after retries
    assert result["b"] == ""  # free-text call returns the empty final message


def test_per_call_model_hint_routes_through_resolver(tmp_path):
    """agent(model=X): known name -> resolved config; no hint -> None.

    The resolver returns a worker ``TeamModelConfig`` (not a built model); a
    missing hint yields ``None`` so the worker inherits its base spec's model.
    An unresolvable hint is now a pool miss: non-retryable, ``agent()`` -> None.
    """
    script = '''
from swarmflow import agent

META = {"name": "route", "description": "model routing", "phases": []}

async def run(args):
    a = await agent("task a", label="a", options={"model": "fast"})
    b = await agent("task b", label="b", options={"model": "unknown"})
    c = await agent("task c", label="c")
    return [a, b, c]
'''
    seen: list = []

    class _RecordingBackend(TeamWorkerBackend):
        async def _execute_worker(self, prompt, tools, *, member_name, has_schema, model, budget_rail=None, agent_id=None, phase=None, label=None):
            seen.append(model)
            return f"ran::{model}"

    backend = _RecordingBackend(
        model="leader-model",
        model_resolver=lambda name: "fast-cfg" if name == "fast" else None,
    )
    result = asyncio.run(run_workflow(_write(tmp_path, script), backend=backend))

    # "fast" resolves to its config; "unknown" is a pool miss -> non-retryable
    # error -> agent()=None; no-hint -> None (inherits the base spec's model).
    assert result == ["ran::fast-cfg", None, "ran::None"]
    assert seen == ["fast-cfg", None]


def test_agent_isolation_option_is_forwarded_to_backend(tmp_path):
    """agent(isolation='worktree') is part of the backend call options."""
    script = '''
from swarmflow import agent

META = {"name": "iso", "description": "isolation routing", "phases": []}

async def run(args):
    return await agent("task", label="w", options={"isolation": "worktree"})
'''
    seen: list[dict] = []

    class _Backend(AgentBackend):
        async def run(self, prompt, opts, schema_json, *, call_key=None):
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
    return await agent("task", label="w", options={"isolation": "container"})
'''
    import pytest

    from openjiuwen.agent_teams.workflow.engine.errors import EngineError

    with pytest.raises(EngineError, match="only supports 'worktree'"):
        asyncio.run(run_workflow(_write(tmp_path, script), backend=_FakeWorkerBackend(model=None)))


def test_worktree_isolation_requires_host_worktree_manager():
    """Worktree isolation must use the host-provided manager, not a fallback."""
    import pytest

    from openjiuwen.agent_teams.workflow.engine.errors import BackendError

    backend = TeamWorkerBackend(model=None)

    with pytest.raises(BackendError, match="requires worktree isolation"):
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


def test_preprocess_accepts_agent_template(tmp_path):
    """The offline preview must not reject an option the real backend accepts."""
    script = _write(tmp_path, '''
from swarmflow import agent

META = {"name": "exp", "description": "expert preview", "phases": []}

async def run(args):
    return await agent("shoot", label="clip", options={"agent_template": "clip"})
''')
    run = asyncio.run(preprocess_swarmflow(script))
    assert run.name == "exp"


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

    backend = TeamWorkerBackend(model=None, worker_base_spec=base)
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
    from openjiuwen.agent_teams.tools.structured_output_tool import StructuredOutputTool

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

        async def create_owner_worktree(self, slug, source_dir=None):
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

        def add_rail(self, rail):
            return None

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
        team_name="code-team",
        worker_base_spec=DeepAgentSpec(tools=[]),
        build_context=_build_context_with_worktree_manager(manager),
    )
    result = asyncio.run(backend.run("write code", {"label": "sum", "isolation": "worktree"}, None))

    assert result.text == "ok"
    assert manager.created and manager.created[0].startswith("agent-code-team-wf-sum-0-")
    # Isolation moves the worker's cwd into the worktree; its workspace stays
    # the stable per-worker directory under the team home.
    assert captured["spec"].cwd == str(worktree_path)
    assert captured["spec"].workspace.root_path != str(worktree_path)
    assert captured["spec"].workspace.stable_base is True
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
    return await agent("write code", label="sum", options={"isolation": "worktree"})
'''
    worktree_path = tmp_path / "worker-wt"
    worktree_path.mkdir()

    class _Manager:
        def __init__(self):
            self.removed: list[tuple[str, str]] = []

        async def create_owner_worktree(self, slug, source_dir=None):
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

        def add_rail(self, rail):
            return None

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
        team_name="code-team",
        worker_base_spec=DeepAgentSpec(tools=[]),
        build_context=_build_context_with_worktree_manager(manager),
    )
    result = asyncio.run(run_workflow(_write(tmp_path, script), backend=backend))

    parsed = json.loads(result)
    assert parsed == {"status": "done"}
    assert manager.removed == []


def test_member_names_use_run_id_prefix():
    """Concurrent runs prefix worker member names with the slugified run_id."""
    backend = TeamWorkerBackend(model=None, team_name="t", run_id="wf_abc123def456")
    name_a = backend._next_member_name({"label": "compute"})
    name_b = backend._next_member_name({"label": "compute"})
    assert name_a == "wf-abc123def456-compute-0"
    assert name_b == "wf-abc123def456-compute-1"

    fallback = TeamWorkerBackend(model=None, team_name="t")
    assert fallback._next_member_name({"label": "compute"}) == "wf-compute-0"


def test_concurrent_runs_member_names_do_not_collide():
    """SDD §7: different run_ids with the same label mint disjoint member names."""
    label = "compute"
    run_a = TeamWorkerBackend(model=None, team_name="t", run_id="wf_runaaaaaaa")
    run_b = TeamWorkerBackend(model=None, team_name="t", run_id="wf_runbbbbbbb")
    name_a = run_a._next_member_name({"label": label})
    name_b = run_b._next_member_name({"label": label})
    assert name_a == "wf-runaaaaaaa-compute-0"
    assert name_b == "wf-runbbbbbbb-compute-0"
    assert name_a != name_b


def test_long_run_id_slug_is_not_truncated_in_member_names():
    """SDD §7: slugified run_id prefix keeps the full id (no truncation)."""
    long_run_id = "wf_" + "a" * 48
    backend = TeamWorkerBackend(model=None, team_name="t", run_id=long_run_id)
    name = backend._next_member_name({"label": "w"})
    assert name.startswith("wf-" + "a" * 48 + "-")
    assert len(name) > 50


def test_member_name_is_stable_across_backend_instances_with_same_call_key():
    """Same call key + run id → same member name, regardless of instance.

    A resume builds a fresh TeamWorkerBackend whose counter restarts at zero;
    hashing the engine's call-path key instead keeps the name (and thus the
    worktree slug) identical so the create fast-recovery can find the
    paused run's kept worktree.
    """
    run_id = "wf_abc123def456"
    backend_a = TeamWorkerBackend(model=None, team_name="t", run_id=run_id)
    backend_b = TeamWorkerBackend(model=None, team_name="t", run_id=run_id)
    name_a = backend_a._next_member_name({"label": "compute"}, "call-0")
    name_b = backend_b._next_member_name({"label": "compute"}, "call-0")
    assert name_a == name_b
    assert name_a == "wf-abc123def456-compute-" + hashlib.sha256(b"call-0").hexdigest()[:12]

    # Different call sites in the same run stay distinct.
    name_other = backend_a._next_member_name({"label": "compute"}, "par-0-1")
    assert name_other != name_a

    # Without a call key the legacy counter path still works (direct callers).
    assert backend_a._next_member_name({"label": "compute"}) == "wf-abc123def456-compute-0"


def test_execute_worker_surfaces_task_loop_model_error():
    """Task-loop failures return {"error": ...}; worker must raise, not empty output."""
    from openjiuwen.agent_teams.workflow.backends.team_worker_backend import (
        _text_from_invoke_result,
    )
    from openjiuwen.agent_teams.workflow.engine.errors import BackendError

    err = "[181001] model call failed, reason: openAI API async stream error: ReadError"
    with pytest.raises(BackendError, match="ReadError"):
        _text_from_invoke_result({"error": err}, member_name="wf-w-5")

    assert _text_from_invoke_result({"output": ""}, member_name="wf-w-5") == ""
    assert _text_from_invoke_result({"output": "ok"}, member_name="wf-w-5") == "ok"


def test_agent_emits_failed_when_worker_backend_raises(tmp_path):
    """BackendError from a model failure surfaces as agent_failed, not empty completed."""
    from openjiuwen.agent_teams.workflow.engine.errors import BackendError
    from openjiuwen.agent_teams.workflow.engine.progress import ProgressKind

    class _FailingWorkerBackend(_FakeWorkerBackend):
        async def _execute_worker(self, prompt, tools, *, member_name, has_schema, model, budget_rail=None, agent_id=None, phase=None, label=None):
            raise BackendError(
                "worker 'wf-w-0' failed: [181001] model call failed, reason: ReadError"
            )

    script = _write(
        tmp_path,
        '''
from swarmflow import agent, phase

META = {"name": "wk", "description": "worker flow", "phases": [{"title": "Do"}]}

async def run(args):
    phase("Do")
    return await agent("fail me", label="broken")
''',
    )
    backend = _FailingWorkerBackend(model=None)
    events: list = []
    asyncio.run(run_workflow(str(script), backend=backend, progress_sink=events.append))

    kinds = [ev.kind for ev in events]
    assert ProgressKind.AGENT_FAILED in kinds
    failed = next(ev for ev in events if ev.kind == ProgressKind.AGENT_FAILED)
    assert "ReadError" in (failed.message or "")
    assert ProgressKind.AGENT_COMPLETED not in kinds


# ---------------------------------------------------------------------------
# SwarmflowWorkerWorktrees orphan reconcile
# ---------------------------------------------------------------------------
def _stub_manager(removed: list[str]):
    class _StubManager:
        async def create_owner_worktree(self, slug, *, source_dir=None):
            raise AssertionError("stub reconcile manager should not create")

        async def remove_worktree(self, worktree_path, repo_root):
            removed.append(worktree_path)
            return True

    return _StubManager()


def _make_repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    for cmd in (
        # no `-b main`: git < 2.28 (older CI) rejects it and the branch name is never asserted
        ["git", "init", str(repo)],
        ["git", "-C", str(repo), "config", "user.email", "st@example.com"],
        ["git", "-C", str(repo), "config", "user.name", "st"],
        ["git", "-C", str(repo), "add", "-A"],
        ["git", "-C", str(repo), "commit", "-m", "init", "--allow-empty"],
    ):
        subprocess.run(cmd, check=True, capture_output=True)
    return repo


def _seed_orphan(tmp_path: Path, repo: Path, slug: str, *, dirty: bool) -> Path:
    wt = tmp_path / slug
    subprocess.run(["git", "-C", str(repo), "worktree", "add", str(wt)], check=True, capture_output=True)
    if dirty:
        (wt / "note.txt").write_text("wip", encoding="utf-8")
    return wt


def test_reconcile_removes_clean_orphan_and_keeps_dirty(tmp_path, monkeypatch):
    """First ensure() sweeps prior-run residue: clean removed, dirty kept."""
    from openjiuwen.agent_teams import paths as team_paths
    from openjiuwen.agent_teams.paths import team_session_worktrees_dir
    from openjiuwen.agent_teams.workflow.worktree import SwarmflowWorkerWorktrees

    team_paths.configure_openjiuwen_home(tmp_path)

    repo = _make_repo(tmp_path, "proj")
    root = team_session_worktrees_dir("wt-team", "sess-1")
    root.mkdir(parents=True)
    clean = _seed_orphan(root, repo, "agent-t-clean1234-deadbeef00", dirty=False)
    dirty = _seed_orphan(root, repo, "agent-t-dirty1234-cafebabed0", dirty=True)
    removed: list[str] = []

    wts = SwarmflowWorkerWorktrees(
        team_name="wt-team",
        build_context=_build_context_with_worktree_manager(_stub_manager(removed)),
        session_id="sess-1",
    )
    # Drive the reconcile directly for determinism; ensure() only calls it
    # before the first worktree-creating agent().
    asyncio.run(wts._reconcile_orphans_once())

    assert str(clean) in removed
    assert str(dirty) not in removed
    # Idempotent within a run: second call is a no-op.
    asyncio.run(wts._reconcile_orphans_once())
    assert removed.count(str(clean)) == 1

    team_paths.reset_openjiuwen_home()


def test_activity_rail_emits_paired_tool_call_and_result():
    """before/after_tool_call pair on one tool_call_id; a raised call previews the
    exception text; non-tool inputs and blank names are ignored on both hooks."""
    from openjiuwen.agent_teams.workflow.backends.team_worker_backend import SwarmflowActivityRail
    from openjiuwen.core.single_agent.rail.base import ToolCallInputs

    emitted: list[tuple] = []
    rail = SwarmflowActivityRail(emit=lambda *a: emitted.append(a))

    def _ctx(tool_name="write_file", tool_args=None, exception=None):
        return SimpleNamespace(
            inputs=ToolCallInputs(tool_call=SimpleNamespace(id="call-7"), tool_name=tool_name,
                                  tool_args=tool_args or {"path": "a.txt", "content": "hi"}),
            exception=exception,
        )

    asyncio.run(rail.before_tool_call(_ctx()))
    ctx = _ctx()
    ctx.inputs.tool_result = "wrote a.txt"
    asyncio.run(rail.after_tool_call(ctx))
    asyncio.run(rail.after_tool_call(_ctx("bash", {"command": "ls"}, RuntimeError("tool boom"))))
    assert emitted == [
        ("tool_call", "call-7", "write_file", '{"path": "a.txt", "content": "hi"}'),
        ("tool_result", "call-7", "write_file", "wrote a.txt"),
        ("tool_result", "call-7", "bash", "tool boom"),
    ]

    asyncio.run(rail.before_tool_call(SimpleNamespace(inputs=None)))
    blank = _ctx(tool_name="")
    asyncio.run(rail.before_tool_call(blank))
    asyncio.run(rail.after_tool_call(blank))
    assert len(emitted) == 3


def test_activity_emitter_builds_agent_activity_event():
    """The backend emitter forwards structured tool activity as agent_activity events."""
    from openjiuwen.agent_teams.workflow.backends.team_worker_backend import TeamWorkerBackend
    from openjiuwen.agent_teams.workflow.engine.progress import ProgressKind

    events: list = []
    backend = TeamWorkerBackend(model=None)
    backend.bind_progress_sink(events.append)
    emit = backend._make_activity_emitter("main/call:7", "review", "coder")
    emit("tool_result", "call-9", "write_file", "wrote a.txt")

    assert len(events) == 1
    ev = events[0]
    assert ev.kind == ProgressKind.AGENT_ACTIVITY
    assert ev.agent_id == "main/call:7"
    assert ev.phase == "review"
    assert ev.label == "coder"
    assert ev.activity_type == "tool_result"
    assert ev.tool_call_id == "call-9"
    assert ev.tool_name == "write_file"
    assert ev.content == "wrote a.txt"
    assert ev.message == "tool: write_file"  # legacy narration kept

    # No sink bound -> emitter is a silent no-op.
    events.clear()
    backend.bind_progress_sink(None)
    emit = backend._make_activity_emitter("main/call:8", "review", "coder")
    emit("tool_call", "c-1", "bash", "ls")
    assert events == []


def test_activity_emitter_truncates_content_at_the_guard_bound():
    """Content passes through in full; only the pathological-payload guard cuts (with a marker)."""
    from openjiuwen.agent_teams.workflow.backends.team_worker_backend import TeamWorkerBackend

    events: list = []
    backend = TeamWorkerBackend(model=None)
    backend.bind_progress_sink(events.append)
    emit = backend._make_activity_emitter("main/call:7", "review", "coder")

    from openjiuwen.agent_teams.workflow.backends.activity_emitter import (
        ACTIVITY_CONTENT_LIMIT,
        truncate_activity_content,
    )

    # A realistic argument body passes through in full.
    realistic = "write_file with a full argument body " + "y" * 2048
    emit("tool_call", "call-9", "write_file", realistic)
    assert events[0].content == realistic

    # Only a payload beyond the pathological guard is cut, marker included.
    emit("tool_call", "call-10", "bash", "x" * (ACTIVITY_CONTENT_LIMIT + 10))
    assert events[1].content == "x" * ACTIVITY_CONTENT_LIMIT + "…(truncated)"
    assert truncate_activity_content(None) is None


# ---------------------------------------------------------------------------
# agent_type routing (third-party workers)
# ---------------------------------------------------------------------------


def _pool(*names: str) -> SimpleNamespace:
    """Mirror test_external_model.py's fake team backend: get_model_pool only."""
    return SimpleNamespace(
        get_model_pool=lambda: [
            SimpleNamespace(
                model_name=name,
                api_key="k1",
                api_base_url="https://api.jiuwen.example/v1",
                api_provider="deepseek",
            )
            for name in names
        ],
    )


def _external_backend(declared: tuple[str, ...] = ("codex",), *, team_backend: Any = None) -> "_FakeExternalBackend":
    from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec

    return _FakeExternalBackend(
        model=None,
        team_name="t",
        run_id="r1",
        external_cli_agents=tuple(ExternalCliAgentSpec(cli_agent=name) for name in declared),
        team_backend=team_backend,
    )


class _FakeExternalBackend(TeamWorkerBackend):
    """Records the external route instead of launching a CLI."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.external_calls: list[dict] = []
        self.native_calls: int = 0

    async def _execute_worker(self, prompt, tools, *, member_name, has_schema, model, budget_rail=None, **_kw):
        self.native_calls += 1
        return "native"

    async def _execute_external_worker(self, prompt, *, member_name, entry, agent_type, opts, schema_json, budget_rail, model_config=None):
        from openjiuwen.agent_teams.workflow.backends.external_turns import ExternalTurnOutcome

        self.external_calls.append(
            {"member": member_name, "agent_type": agent_type, "schema": schema_json, "model_config": model_config}
        )
        budget_rail.bill(42)
        return ExternalTurnOutcome(text="ext", structured={"ok": True} if schema_json else None, tokens=42)


def test_agent_type_routes_to_external_worker_and_bills_tokens():
    backend = _external_backend()
    result = asyncio.run(backend.run("p", {"label": "x", "agent_type": "codex"}, {"type": "object"}, call_key="k"))
    assert result.structured == {"ok": True}
    assert result.text == "ext"
    assert result.tokens == 42
    assert backend.native_calls == 0
    assert backend.external_calls[0]["agent_type"] == "codex"
    assert backend.external_calls[0]["member"].startswith("r1-x-")


def test_agent_type_unknown_backend_names_both_lists():
    backend = _external_backend(declared=("codex",))
    with pytest.raises(BackendError) as exc:
        asyncio.run(backend.run("p", {"label": "x", "agent_type": "gemini2"}, None, call_key="k"))
    message = str(exc.value)
    assert "gemini2" in message
    assert "claude" in message and "codex" in message  # registry list
    assert "declared" in message
    assert "SDK backends:" in message
    assert "external_cli_agents:" in message
    assert backend.external_calls == []


def test_agent_type_not_declared_by_team_is_rejected():
    backend = _external_backend(declared=("claude",))
    with pytest.raises(BackendError, match="codex"):
        asyncio.run(backend.run("p", {"label": "x", "agent_type": "codex"}, None, call_key="k"))


def test_describe_model_uses_the_plan():
    from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec, ExternalCliBuiltinModel

    builtin = ExternalCliBuiltinModel(name="gpt-5-codex", efforts=["medium"], default_effort="medium")
    declared = ExternalCliAgentSpec(cli_agent="codex", builtin_models=[builtin])
    backend = _FakeExternalBackend(
        model=None, team_name="t", external_cli_agents=(declared,), team_backend=_pool("deepseek-v4-flash")
    )
    # Native opts / bare third-party opts: no display.
    assert backend.describe_model({"label": "x"}) is None
    assert backend.describe_model({"label": "x", "agent_type": "codex"}) is None
    # Builtin pick -> the catalog name (endpointless).
    assert backend.describe_model({"label": "x", "agent_type": "codex", "builtin_model": "gpt-5-codex"}) == "gpt-5-codex"
    # Pool hit -> the pool name.
    assert backend.describe_model({"label": "x", "agent_type": "codex", "model": "deepseek-v4-flash"}) == "deepseek-v4-flash"
    # An invalid combo never raises pre-launch: best-known hint wins (model first).
    assert backend.describe_model({"agent_type": "codex", "model": "X"}) == "X"
    assert backend.describe_model({"agent_type": "codex", "model": "X", "builtin_model": "gpt-5-codex"}) == "X"


def test_external_builtin_model_reaches_worker_as_endpointless_config():
    from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec, ExternalCliBuiltinModel

    builtin = ExternalCliBuiltinModel(name="gpt-5-codex", efforts=["medium"], default_effort="medium")
    entry = ExternalCliAgentSpec(cli_agent="codex", builtin_models=[builtin])
    backend = _FakeExternalBackend(model=None, team_name="t", run_id="r1", external_cli_agents=(entry,))
    result = asyncio.run(
        backend.run("p", {"label": "x", "agent_type": "codex", "builtin_model": "gpt-5-codex"}, None, call_key="k")
    )
    assert result.text == "ext" and backend.native_calls == 0
    cfg = backend.external_calls[0]["model_config"]
    assert cfg is not None
    assert cfg.model == "gpt-5-codex" and cfg.effort == "medium"
    assert cfg.provider is None and cfg.api_base is None and cfg.api_key is None


def test_external_pool_miss_raises_before_builder_or_worktree(monkeypatch):
    """A pool-miss model hint fails before any resource is touched."""
    backend = _external_backend(team_backend=_pool("deepseek-v4-flash"))
    ensure_calls: list[str] = []

    async def fake_ensure(name: str, opts: dict) -> None:
        ensure_calls.append(name)

    monkeypatch.setattr(backend._worktrees, "ensure", fake_ensure)
    with pytest.raises(BackendError) as exc:
        asyncio.run(backend.run("p", {"label": "x", "agent_type": "codex", "model": "X"}, None, call_key="k"))
    assert exc.value.retryable is False
    assert "deepseek-v4-flash" in str(exc.value)
    assert backend.external_calls == []
    assert ensure_calls == []


# ---------------------------------------------------------------------------
# C1 — external branch must go through worktree ensure / finalize
# ---------------------------------------------------------------------------


def test_external_worktree_finalize_called_even_on_error(monkeypatch):
    """finalize must run even when _execute_external_worker raises."""
    backend = _external_backend()
    finalize_calls: list[str] = []

    async def fake_ensure(name: str, opts: dict) -> None:
        pass

    async def fake_finalize(name: str) -> None:
        finalize_calls.append(name)

    async def failing_execute(prompt: str, **kwargs: Any) -> None:
        raise BackendError("external boom")

    monkeypatch.setattr(backend._worktrees, "ensure", fake_ensure)
    monkeypatch.setattr(backend._worktrees, "finalize", fake_finalize)
    monkeypatch.setattr(backend, "_execute_external_worker", failing_execute)

    with pytest.raises(BackendError, match="external boom"):
        asyncio.run(backend.run("p", {"label": "x", "agent_type": "codex"}, None, call_key="k"))

    assert len(finalize_calls) == 1


# ---------------------------------------------------------------------------
# External turn outcomes: billing, reported model, runtime build failures
# ---------------------------------------------------------------------------


def _external_backend_with_turns(monkeypatch, *, run_turn, reported_model=None, started_with=None, runtime=None):
    """A codex-declared backend whose ExternalMemberTurns is a fake driving ``run_turn``."""
    import openjiuwen.agent_teams.workflow.backends.team_worker_backend as be_mod
    from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec

    class _Turns:
        def __init__(self, runtime: Any, *, member_name: str, auth_hint_models: Any = None) -> None:
            pass

        @property
        def reported_model(self):
            return reported_model

        async def start(self, team_session: Any) -> None:
            if started_with is not None:
                started_with.append(team_session)

        async def run_turn(self, prompt: str, *, schema_json: Any = None) -> Any:
            return await run_turn()

        async def stop(self) -> None:
            pass

    async def _fake_build(**kwargs: Any) -> object:
        return runtime if runtime is not None else object()

    monkeypatch.setattr(be_mod, "ExternalMemberTurns", _Turns)
    monkeypatch.setattr("openjiuwen.agent_teams.external.cli_agent.spawn.build_cli_workflow_runtime", _fake_build)
    return TeamWorkerBackend(
        model=None, team_name="t", run_id="r1", external_cli_agents=(ExternalCliAgentSpec(cli_agent="codex"),),
    )


def test_external_turn_bills_tokens_and_attaches_reported_model(monkeypatch):
    """Success and failure both bill the turn and name the CLI-reported model.

    A failed turn bills its tokens before re-raising and the BackendError
    carries the model; a successful one carries it on the AgentResult. The
    turn driver always starts with a real team session, never None.
    """
    from openjiuwen.agent_teams.workflow.backends.external_turns import ExternalTurnOutcome

    started_with: list[Any] = []

    async def _fail():
        raise BackendError("boom", tokens=9)

    backend = _external_backend_with_turns(monkeypatch, run_turn=_fail, reported_model="gpt-5.5",
                                           started_with=started_with)
    with pytest.raises(BackendError, match="boom") as exc:
        asyncio.run(backend.run("p", {"label": "x", "agent_type": "codex"}, None, call_key="k"))
    assert backend.budget.spent == 9
    assert exc.value.model == "gpt-5.5"
    assert started_with and started_with[0] is not None

    async def _ok():
        return ExternalTurnOutcome(text="ext", structured=None, tokens=42)

    backend = _external_backend_with_turns(monkeypatch, run_turn=_ok, reported_model="gpt-5.5")
    result = asyncio.run(backend.run("p", {"label": "x", "agent_type": "codex"}, None, call_key="k"))
    assert result.text == "ext"
    assert result.model == "gpt-5.5"
    assert backend.budget.spent == 42


def test_build_cli_failure_raises_backend_error(monkeypatch):
    """A runtime build failure wraps as BackendError(…build failed…)."""
    from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec

    async def _exploding_build(**kwargs: Any) -> None:
        raise RuntimeError("no cli")

    monkeypatch.setattr(
        "openjiuwen.agent_teams.external.cli_agent.spawn.build_cli_workflow_runtime",
        _exploding_build,
    )

    backend = TeamWorkerBackend(
        model=None,
        team_name="t",
        run_id="r1",
        external_cli_agents=(ExternalCliAgentSpec(cli_agent="codex"),),
    )

    with pytest.raises(BackendError, match="build failed"):
        asyncio.run(backend.run("p", {"label": "x", "agent_type": "codex"}, None, call_key="k"))


def test_external_worker_binds_activity_sink_with_node_identity(monkeypatch):
    """A one-shot third-party worker with agent_id in opts binds the runtime's
    activity sink carrying (agent_id, phase, label) and no correlation id."""
    from openjiuwen.agent_teams.workflow.backends.external_turns import ExternalTurnOutcome

    binds: list[tuple] = []

    class _RecRuntime:
        def bind_activity_sink(self, sink, *, agent_id=None, phase=None, label=None, correlation_id=None):
            binds.append((sink, agent_id, phase, label, correlation_id))

    async def _ok():
        return ExternalTurnOutcome(text="ext", structured=None, tokens=0)

    backend = _external_backend_with_turns(monkeypatch, run_turn=_ok, runtime=_RecRuntime())
    sink = object()
    backend.bind_progress_sink(sink)
    result = asyncio.run(
        backend.run("p", {"label": "x", "agent_type": "codex", "agent_id": "n1", "phase": "run"}, None, call_key="k")
    )
    assert result.text == "ext"
    assert binds == [(sink, "n1", "run", "x", None)]


def test_execute_worker_runs_under_its_own_trajectory_subject(monkeypatch):
    """A native worker runs under a ``team_member`` subject shown by its call label."""
    from openjiuwen.agent_teams.harness import team_harness as th_mod
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
    from openjiuwen.harness.execution_subject import current_execution_subject

    run_subjects: list = []

    class _FakeHarness:
        async def run_once(self, content, **kw):
            run_subjects.append(current_execution_subject())
            return {"output": "ok", "result_type": "answer"}

        def add_rail(self, rail):
            return None

        async def dispose(self):
            return None

    monkeypatch.setattr(th_mod.TeamHarness, "build", lambda **kw: _FakeHarness())

    backend = TeamWorkerBackend(
        model=None,
        worker_base_spec=DeepAgentSpec(tools=[]),
        team_name="t",
        session_id="product-sess",
    )
    asyncio.run(
        backend._execute_worker(
            "t",
            [],
            member_name="wf-compute-0",
            has_schema=False,
            model=None,
            label="compute",
        )
    )

    subject = run_subjects[0]
    assert subject.subject_id == "team-member:product-sess:t:wf-compute-0"
    assert subject.display_name == "compute"
    assert subject.kind == "team_member"
    assert subject.session_id == "product-sess"
    assert current_execution_subject() is None


# --- agent_template: pick an AgentGroup member as the worker identity ---


def _expert(name="clip", persona="PERSONA", *, external=False, spec=None):
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
    from openjiuwen.agent_teams.workflow.backends.expert_specs import SwarmflowExpertEntry

    return SwarmflowExpertEntry(
        member_name=name,
        display_name=f"display-{name}",
        persona=persona,
        spec=None
        if external
        else (
            spec
            or DeepAgentSpec(
                tools=[], enable_task_loop=True, agent_template_spec={"agent_card": {"id": name, "name": name}}
            )
        ),
        external_cli=object() if external else None,
    )


class _RecordingBackend(TeamWorkerBackend):
    """Records what _execute_worker received instead of building a harness."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls: list[dict] = []

    async def _execute_worker(self, prompt, tools, *, member_name, has_schema, model, budget_rail=None, **kw):
        self.calls.append({"member": member_name, "has_schema": has_schema, "kw": kw})
        if has_schema and tools:
            tools[0].captured = {"x": "1"}
            tools[0].called = True
            return ""
        return "native"


def _expert_backend(**experts):
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec

    return _RecordingBackend(model=None, worker_base_spec=DeepAgentSpec(tools=[]), run_id="r1", expert_specs=experts)


def test_known_options_declare_agent_template():
    assert "agent_template" in TeamWorkerBackend.KNOWN_OPTIONS


def test_preview_backend_accepts_every_team_backend_option():
    """Offline preview must not reject an option the live backend accepts."""
    from openjiuwen.agent_teams.workflow.engine.backends.mock import MockBackend

    assert TeamWorkerBackend.KNOWN_OPTIONS <= MockBackend.KNOWN_OPTIONS


def test_agent_template_hit_routes_expert_entry_into_execute_worker():
    backend = _expert_backend(clip=_expert())
    result = asyncio.run(backend.run("p", {"label": "x", "agent_template": "clip"}, None, call_key="k"))
    assert result.text == "native"
    assert backend.calls[0]["kw"]["expert"].member_name == "clip"


def test_agent_template_absent_keeps_legacy_call_shape():
    backend = _expert_backend(clip=_expert())
    asyncio.run(backend.run("p", {"label": "x"}, None, call_key="k"))
    assert "expert" not in backend.calls[0]["kw"]  # byte-compatible path: no new kwarg


@pytest.mark.parametrize(
    ("opts", "needle"),
    [
        ({"label": "x", "agent_template": "designer/clip"}, "bare member name"),
        ({"label": "x", "agent_template": 123}, "bare member name"),
        ({"label": "x", "agent_template": "scene"}, "available members: ['clip']"),
        # the Leader is never in the expert table; say so instead of "not a member"
        ({"label": "x", "agent_template": "leader"}, "Team Leader"),
        ({"label": "x", "agent_template": "clip", "agent_type": "codex"}, "mutually exclusive"),
    ],
)
def test_agent_template_validation_rejects_before_launch(opts, needle):
    backend = _expert_backend(clip=_expert())
    with pytest.raises(BackendError) as exc:
        asyncio.run(backend.run("p", opts, None, call_key="k"))
    assert needle in str(exc.value)
    assert exc.value.retryable is False
    assert backend.calls == []


def test_agent_template_without_bound_group_explains_binding():
    backend = _expert_backend()
    with pytest.raises(BackendError, match="no AgentGroup is bound"):
        asyncio.run(backend.run("p", {"label": "x", "agent_template": "clip"}, None, call_key="k"))


def test_agent_template_external_member_is_not_supported_yet():
    backend = _expert_backend(writer=_expert("writer", external=True))
    with pytest.raises(BackendError, match="external runtime"):
        asyncio.run(backend.run("p", {"label": "x", "agent_template": "writer"}, None, call_key="k"))
    assert backend.calls == []


def test_describe_agent_template_returns_display_name_or_none():
    backend = _expert_backend(clip=_expert())
    assert backend.describe_agent_template({"label": "x", "agent_template": "clip"}) == "display-clip"
    assert backend.describe_agent_template({"label": "x"}) is None
    assert backend.describe_agent_template({"label": "x", "agent_template": "nope"}) is None


def test_execute_worker_prepends_persona_to_engine_worker_prompt(monkeypatch):
    """Expert path: system_prompt = persona + blank line + the unchanged engine text;
    tools come from the expert spec (+ structured_output), not the teammate base."""
    from openjiuwen.agent_teams.harness import team_harness as th_mod
    from openjiuwen.agent_teams.schema.deep_agent_spec import BuiltinToolSpec, DeepAgentSpec
    from openjiuwen.agent_teams.tools.structured_output_tool import StructuredOutputTool

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
        return _FakeHarness()

    monkeypatch.setattr(th_mod.TeamHarness, "build", _fake_build)

    base = DeepAgentSpec(tools=[BuiltinToolSpec(type="core.web_search")], enable_task_loop=True)
    backend = TeamWorkerBackend(model=None, worker_base_spec=base, language="cn")
    engine_free = backend._t("swarmflow_worker", key="free")
    engine_schema = backend._t("swarmflow_worker", key="schema")

    asyncio.run(backend._execute_worker("t", [], member_name="wf-w-0", has_schema=False, model=None, expert=_expert()))
    assert captured["spec"].system_prompt == f"PERSONA\n\n{engine_free}"
    assert captured["spec"].tools == []  # expert spec tools, not the teammate's web_search
    assert captured["spec"].agent_template_spec == {"agent_card": {"id": "clip", "name": "clip"}}

    tool = StructuredOutputTool({"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]})
    asyncio.run(
        backend._execute_worker("t", [tool], member_name="wf-w-1", has_schema=True, model=None, expert=_expert())
    )
    assert captured["spec"].system_prompt == f"PERSONA\n\n{engine_schema}"
    assert captured["spec"].tools == [tool]

    # No expert: the legacy prompt, byte for byte.
    asyncio.run(backend._execute_worker("t", [], member_name="wf-w-2", has_schema=False, model=None))
    assert captured["spec"].system_prompt == engine_free
