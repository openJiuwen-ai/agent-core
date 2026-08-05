# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Nested workflow child phase — ``workflow()`` emits child PHASE declaration with display name."""

import pytest
from openjiuwen.agent_teams.workflow.engine import primitives
from openjiuwen.agent_teams.workflow.engine.runtime import Runtime
from openjiuwen.agent_teams.workflow.engine.budget import BudgetLedger
from openjiuwen.agent_teams.workflow.engine.progress import ProgressKind


class _Sink:
    def __init__(self):
        self.events = []

    def __call__(self, ev):
        self.events.append(ev)


def _set_path(path):
    primitives._path.set(path)


@pytest.mark.asyncio
async def test_workflow_emits_child_phase_declared_with_display_name(monkeypatch):
    # workflow() under a parallel branch i=1 -> display name "▸ intro #1"
    _set_path((("par", 5, 1),))
    sink = _Sink()
    rt = Runtime(backend=None, journal=None, budget=BudgetLedger())
    rt.progress_sink = sink

    # stub load_workflow_source + _invoke_loaded to avoid real load
    async def fake_invoke(loaded, args):
        return None

    class _Loaded:
        meta = {"name": "intro"}

    monkeypatch.setattr(primitives, "load_workflow_source", lambda n: _Loaded(), raising=False)
    # load_workflow_source is imported lazily inside workflow(); patch via loader module
    from openjiuwen.agent_teams.workflow.engine import loader
    monkeypatch.setattr(loader, "load_workflow_source", lambda n: _Loaded())
    monkeypatch.setattr(primitives, "_invoke_loaded", fake_invoke)
    primitives._rt.set(rt)

    await primitives.workflow("intro")

    phase_events = [e for e in sink.events if e.kind == ProgressKind.PHASE]
    assert len(phase_events) == 1
    ev = phase_events[0]
    assert ev.phase == "intro"
    assert ev.nested_phase == "▸ intro #1"
    assert ev.phase_type == "child"


@pytest.mark.asyncio
async def test_workflow_no_branch_display_name_has_no_hash(monkeypatch):
    _set_path(())  # top-level, no branch
    sink = _Sink()
    rt = Runtime(backend=None, journal=None, budget=BudgetLedger())
    rt.progress_sink = sink

    class _Loaded:
        meta = {"name": "intro"}

    from openjiuwen.agent_teams.workflow.engine import loader
    monkeypatch.setattr(loader, "load_workflow_source", lambda n: _Loaded())

    async def fake_invoke(loaded, args):
        return None

    monkeypatch.setattr(primitives, "_invoke_loaded", fake_invoke)
    primitives._rt.set(rt)

    await primitives.workflow("intro")
    phase_events = [e for e in sink.events if e.kind == ProgressKind.PHASE]
    assert phase_events[0].phase == "intro"  # original name
    assert phase_events[0].nested_phase == "▸ intro"  # display name


@pytest.mark.asyncio
async def test_depth_cap_emits_log_progress(monkeypatch):
    # force depth over cap
    primitives._wf_depth.set(99)
    sink = _Sink()
    rt = Runtime(backend=None, journal=None, budget=BudgetLedger())
    rt.progress_sink = sink
    primitives._rt.set(rt)

    await primitives.workflow("intro")  # should skip
    log_events = [e for e in sink.events if e.kind == ProgressKind.LOG]
    assert len(log_events) == 1
    assert "depth" in log_events[0].message and "skipping" in log_events[0].message


@pytest.mark.asyncio
async def test_phase_sets_current_phase_and_emits():
    """phase() sets per-task _current_phase and emits PHASE."""
    sink = _Sink()
    rt = Runtime(backend=None, journal=None, budget=BudgetLedger())
    rt.progress_sink = sink
    primitives._rt.set(rt)

    primitives.phase("撰写开场致辞稿")

    assert primitives._current_phase.get() == "撰写开场致辞稿"
    phase_events = [e for e in sink.events if e.kind == ProgressKind.PHASE]
    assert len(phase_events) == 1
    assert phase_events[0].phase == "撰写开场致辞稿"
