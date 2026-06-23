# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""E2E: swarmflow pause/resume via BackgroundTaskController (deterministic, no LLM).

``TeamWorkerBackend._execute_worker`` is patched to a gated stub so a pause lands
at a known agent. Drives the real control path end to end: ``SwarmflowTool``
launches a run on a native stub's ``AsyncToolRuntime``;
``BackgroundTaskController.pause()`` sets the engine abort_event, aborts sessions,
and cancels the task (the WAL keeps the completed prefix); ``resume()`` relaunches
and the journal replays that prefix so only the interrupted call reruns live.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from openjiuwen.agent_teams import paths
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.harness.async_tools import AsyncToolRuntime
from openjiuwen.agent_teams.paths import configure_openjiuwen_home, reset_openjiuwen_home
from openjiuwen.agent_teams.runtime.background_task_controller import BackgroundTaskController
from openjiuwen.agent_teams.workflow.backends.team_worker_backend import TeamWorkerBackend
from openjiuwen.agent_teams.workflow.tool_swarmflow import SwarmflowTool

_WF_NAME = "pause-resume-e2e"
_SCRIPT = '''
from swarmflow import agent

META = {"name": "pause-resume-e2e", "description": "three sequential agents", "phases": []}

async def run(args):
    a = await agent("A", label="A")
    b = await agent("B", label="B")
    c = await agent("C", label="C")
    return [a, b, c]
'''

# Module-level gated-worker state (the patched _execute_worker reads it).
_GATES: dict[str, asyncio.Event] = {}
_STARTED: dict[str, asyncio.Event] = {}
_EXECUTED: list[str] = []


async def _gated_execute_worker(self, prompt, tools, *, member_name, has_schema, model):
    """Patched worker: record the call, signal start, block on a gate if set."""
    _EXECUTED.append(prompt)
    started = _STARTED.get(prompt)
    if started is not None:
        started.set()
    gate = _GATES.get(prompt)
    if gate is not None:
        await gate.wait()
    return f"{prompt}-done"


class _NativeStub:
    """Minimal NativeHarness surface SwarmflowTool needs to launch + inject."""

    def __init__(self, controller: BackgroundTaskController) -> None:
        self.model = None
        self.build_context = None
        self.background_task_controller = controller
        self.injected: list[str] = []
        self.async_tool_runtime = AsyncToolRuntime(inject=self._inject)

    async def _inject(self, text: str) -> None:
        self.injected.append(text)

    def launch_async_tool(self, task_id, coro_factory, *, tool_name, description) -> None:
        self.async_tool_runtime.launch(task_id, coro_factory, tool_name=tool_name, description=description)


def _wal_labels(team: str, session: str) -> list[str]:
    jp = paths.workflow_journal_path(team, session, _WF_NAME)
    wal = Path(f"{jp}.wal")
    if not wal.exists():
        return []
    labels: list[str] = []
    for line in wal.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        labels.append(json.loads(line).get("label"))
    return labels


def test_swarmflow_pause_resume_e2e(tmp_path, monkeypatch):
    """Pause stops the in-flight run (WAL keeps A); resume replays A and finishes."""
    configure_openjiuwen_home(str(tmp_path))
    monkeypatch.setattr(TeamWorkerBackend, "_execute_worker", _gated_execute_worker)
    _EXECUTED.clear()
    _GATES.clear()
    _STARTED.clear()
    _GATES["B"] = asyncio.Event()
    _STARTED["B"] = asyncio.Event()

    ctl = BackgroundTaskController()
    stub = _NativeStub(ctl)
    tool = SwarmflowTool(
        parent_agent=stub,
        messager=None,
        team_backend=None,
        team_name="t1",
        model_resolver=lambda name: None,
        language="cn",
    )
    script = tmp_path / "flow.py"
    script.write_text(_SCRIPT, encoding="utf-8")

    async def _scenario() -> None:
        token = set_session_id("sess-pr")
        try:
            out = await tool.invoke({"script_path": str(script)})
            assert out.data["status"] == "launched"
            task_id = out.data["task_id"]

            # A completes + journals; B is in flight (blocked on its gate).
            await asyncio.wait_for(_STARTED["B"].wait(), timeout=5.0)

            # === PAUSE ===
            assert await ctl.pause() is True
            assert ctl.is_paused() is True
            # The top-level task was cancelled; its record reflects it.
            rec = stub.async_tool_runtime.get(task_id)
            assert rec is not None and rec.status == "error"
            # WAL holds only the completed prefix (A); B never journaled.
            assert _wal_labels("t1", "sess-pr") == ["A"]

            # === RESUME ===
            _GATES["B"].set()  # let B's worker return on the resumed run
            assert await ctl.resume() is True
            # Wait for the resumed run to complete and inject its result.
            for _ in range(300):
                if stub.injected:
                    break
                await asyncio.sleep(0.02)
            assert stub.injected, "resumed run did not inject a completion"
        finally:
            reset_session_id(token)

    try:
        asyncio.run(_scenario())
    finally:
        reset_openjiuwen_home()

    # A ran once (pre-pause); resume re-ran only B and C (A was a cache hit).
    assert _EXECUTED[0] == "A"
    assert _EXECUTED.count("A") == 1
    assert "B" in _EXECUTED[1:] and "C" in _EXECUTED[1:]
