# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DeepAgent + worktree tools integration tests.

These tests drive a ``DeepAgent`` with deterministic mock-LLM responses
so we can assert the full ``enter_worktree`` -> tool work -> ``exit_worktree``
loop end-to-end against a real on-disk git repository. The cases are
documented in ``case_design.md`` in this directory.

The mock LLM always emits a fixed sequence of tool calls so the tests
exercise the tool wiring (tool registration, ``WorktreeManager``,
``cwd`` ContextVar restoration, ``ToolOutput`` propagation) without
depending on real model availability.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
    ToolCallInputs,
)
from openjiuwen.core.sys_operation.cwd import get_cwd
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.worktree import (
    WorktreeCreatedEvent,
    WorktreeRemovedEvent,
    get_current_session,
)
from tests.system_tests.harness.tools.worktree.conftest import WorktreeBed
from tests.test_logger import logger
from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)


# --- Mock LLM plumbing -------------------------------------------------------


class _MockRuntimeModel:
    """Adapt ``MockLLMModel`` to DeepAgent's ``Model`` contract.

    DeepAgent reads ``model_client_config`` / ``model_config`` off the model
    plus calls ``invoke`` / ``stream``, so we only have to forward those.
    """

    def __init__(self, client: MockLLMModel) -> None:
        self.client = client
        self.model_client_config = client.model_client_config
        self.model_config = client.model_config

    async def invoke(self, *args: Any, **kwargs: Any) -> Any:
        """Forward invoke to the underlying mock client."""
        return await self.client.invoke(*args, **kwargs)

    async def stream(self, *args: Any, **kwargs: Any):
        """Forward stream to the underlying mock client."""
        async for chunk in self.client.stream(*args, **kwargs):
            yield chunk


def _build_runtime_model(mock_llm: MockLLMModel) -> Model:
    return cast(Model, _MockRuntimeModel(mock_llm))


# --- Test helpers ------------------------------------------------------------


class _ToolTraceRail(AgentRail):
    """Capture every tool call's name, arguments, and ``ToolOutput`` result.

    Tests assert against ``calls`` instead of parsing LLM text output, since
    the mock LLM's text only matters to verify the agent reached the final
    answer turn.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, Any, Any]] = []

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Append (tool_name, tool_args, tool_result) for each completed call."""
        if not isinstance(ctx.inputs, ToolCallInputs):
            return
        self.calls.append((ctx.inputs.tool_name, ctx.inputs.tool_args, ctx.inputs.tool_result))


class _NoteWriteTool(Tool):
    """Minimal write tool used to dirty a worktree.

    The tool intentionally writes relative to ``get_cwd()`` rather than to
    a SysOperation backend, so it picks up the cwd switch performed by
    ``EnterWorktreeTool`` without needing the full filesystem rail.
    """

    def __init__(self) -> None:
        card = ToolCard(
            id=f"note_write_{uuid.uuid4().hex}",
            name="note_write",
            description="Write a UTF-8 text file at <cwd>/<path>.",
            input_params={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        )
        super().__init__(card)

    async def invoke(self, inputs: dict, **kwargs: Any) -> ToolOutput:
        """Write the given content to ``<get_cwd()>/<path>``."""
        rel_path = inputs.get("path") or ""
        content = inputs.get("content") or ""
        if not rel_path:
            return ToolOutput(success=False, error="path is required")
        target = Path(get_cwd()) / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolOutput(success=True, data={"path": str(target), "size": len(content)})

    async def stream(self, inputs: dict, **kwargs: Any):
        """Streaming is not used in tests; raise to surface accidental use."""
        raise NotImplementedError("note_write does not stream")


def _enter(name: str, call_id: str) -> Any:
    return create_tool_call_response("enter_worktree", json.dumps({"name": name}), tool_call_id=call_id)


def _exit(action: str, call_id: str, *, discard_changes: bool | None = None) -> Any:
    args: dict[str, Any] = {"action": action}
    if discard_changes is not None:
        args["discard_changes"] = discard_changes
    return create_tool_call_response("exit_worktree", json.dumps(args), tool_call_id=call_id)


def _note(path: str, content: str, call_id: str) -> Any:
    return create_tool_call_response(
        "note_write",
        json.dumps({"path": path, "content": content}),
        tool_call_id=call_id,
    )


def _assertion_text(text: str = "done") -> Any:
    return create_text_response(text)


def _outputs_by_tool(rail: _ToolTraceRail) -> dict[str, list[ToolOutput]]:
    """Group captured ``ToolOutput`` per tool name preserving call order."""
    grouped: dict[str, list[ToolOutput]] = {}
    for name, _args, result in rail.calls:
        grouped.setdefault(name, []).append(result)
    return grouped


# --- Fixtures ----------------------------------------------------------------


@pytest_asyncio.fixture
async def runner_lifecycle():
    """Start/stop the global Runner around each test for resource isolation."""
    await Runner.start()
    yield
    await Runner.stop()


def _build_agent(
    bed: WorktreeBed,
    mock_llm: MockLLMModel,
    trace_rail: _ToolTraceRail,
    *,
    note_tool: _NoteWriteTool,
):
    """Common DeepAgent factory wired with worktree tools + a writer tool.

    ``restrict_to_work_dir`` is left at the default to keep the SysOperation
    sandbox aligned with the workspace; the worktree directories live under
    ``<workspace>/.worktrees`` so they remain inside that sandbox.
    """
    return create_deep_agent(
        model=_build_runtime_model(mock_llm),
        system_prompt="execute tools in order",
        tools=[bed.enter_tool, bed.exit_tool, note_tool],
        rails=[trace_rail],
        enable_task_loop=False,
        max_iterations=12,
        workspace=str(bed.workspace_root),
    )


# --- TC-01 -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enter_write_keep(worktree_bed: WorktreeBed, runner_lifecycle) -> None:
    """TC-01: enter -> note_write -> exit(keep) keeps the worktree on disk."""
    bed = worktree_bed
    mock_llm = MockLLMModel()
    mock_llm.set_responses(
        [
            _enter("wt-happy", "call_enter"),
            _note("notes.md", "first draft\n", "call_write"),
            _exit("keep", "call_exit"),
            _assertion_text("kept"),
        ]
    )
    trace = _ToolTraceRail()
    agent = _build_agent(bed, mock_llm, trace, note_tool=_NoteWriteTool())

    result = await Runner.run_agent(agent, {"query": "create wt-happy and keep it"})

    assert isinstance(result, dict)
    assert result.get("result_type") == "answer"

    grouped = _outputs_by_tool(trace)
    assert len(grouped["enter_worktree"]) == 1
    assert grouped["enter_worktree"][0].success, grouped["enter_worktree"][0].error
    assert grouped["note_write"][0].success
    assert len(grouped["exit_worktree"]) == 1
    assert grouped["exit_worktree"][0].success
    assert grouped["exit_worktree"][0].data["action"] == "keep"

    worktree_path = Path(grouped["enter_worktree"][0].data["worktree_path"])
    assert worktree_path.is_dir(), "kept worktree must remain on disk"
    assert (worktree_path / "notes.md").read_text(encoding="utf-8") == "first draft\n"
    # Main repo must not see the file written into the worktree.
    assert not (bed.repo_root / "notes.md").exists()
    # Single-agent flows do not maintain a workspace-side .worktree
    # symlink view; that mirror lives in TeamWorkspaceManager and is
    # exercised by team-side tests.
    assert not (bed.workspace_root / ".worktree").exists()
    # Session is cleared but the cwd was restored to original.
    assert get_current_session() is None
    assert get_cwd() == str(bed.workspace_root.resolve())
    # WorktreeManager should have emitted exactly one CreatedEvent.
    assert len(bed.events) == 1
    assert isinstance(bed.events[0], WorktreeCreatedEvent)
    assert bed.events[0].worktree_name == "wt-happy"
    logger.info("TC-01 ok: kept worktree at %s", worktree_path)


# --- TC-02 -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enter_write_remove_discard(worktree_bed: WorktreeBed, runner_lifecycle) -> None:
    """TC-02: exit(remove, discard_changes=True) deletes the dirtied worktree."""
    bed = worktree_bed
    mock_llm = MockLLMModel()
    mock_llm.set_responses(
        [
            _enter("wt-discard", "call_enter"),
            _note("scratch.txt", "throwaway\n", "call_write"),
            _exit("remove", "call_exit", discard_changes=True),
            _assertion_text("removed"),
        ]
    )
    trace = _ToolTraceRail()
    agent = _build_agent(bed, mock_llm, trace, note_tool=_NoteWriteTool())

    result = await Runner.run_agent(agent, {"query": "create wt-discard and discard it"})
    assert result.get("result_type") == "answer"

    grouped = _outputs_by_tool(trace)
    enter_out = grouped["enter_worktree"][0]
    exit_out = grouped["exit_worktree"][0]
    assert enter_out.success
    assert exit_out.success, exit_out.error
    assert exit_out.data["action"] == "remove"
    # Manager counted at least one discarded file (scratch.txt).
    assert exit_out.data.get("discarded_files", 0) >= 1

    worktree_path = Path(enter_out.data["worktree_path"])
    assert not worktree_path.exists(), "removed worktree must be gone from disk"
    # Single-agent flows never install the workspace-side mirror.
    assert not (bed.workspace_root / ".worktree").exists()
    # Both Created and Removed events should fire in order.
    event_types = [type(e).__name__ for e in bed.events]
    assert event_types == [WorktreeCreatedEvent.__name__, WorktreeRemovedEvent.__name__]
    logger.info("TC-02 ok: removed worktree (discarded %s files)", exit_out.data.get("discarded_files"))


# --- TC-03 -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_two_phase_confirmation(worktree_bed: WorktreeBed, runner_lifecycle) -> None:
    """TC-03: remove without discard refuses, retry with discard succeeds."""
    bed = worktree_bed
    mock_llm = MockLLMModel()
    mock_llm.set_responses(
        [
            _enter("wt-twophase", "call_enter"),
            _note("dirty.txt", "uncommitted\n", "call_write"),
            _exit("remove", "call_remove_first"),
            _exit("remove", "call_remove_second", discard_changes=True),
            _assertion_text("removed after confirmation"),
        ]
    )
    trace = _ToolTraceRail()
    agent = _build_agent(bed, mock_llm, trace, note_tool=_NoteWriteTool())

    await Runner.run_agent(agent, {"query": "two-phase confirmation"})

    grouped = _outputs_by_tool(trace)
    assert len(grouped["exit_worktree"]) == 2
    first, second = grouped["exit_worktree"]
    # First attempt rejected without discard_changes.
    assert first.success is False
    assert "uncommitted" in (first.error or "").lower()
    # Worktree directory is still intact between the two exit attempts.
    worktree_path = Path(grouped["enter_worktree"][0].data["worktree_path"])
    # By the time we inspect, second exit removed it; verify second succeeded
    # and event log only fired one Removed event (not two).
    assert second.success, second.error
    assert second.data["action"] == "remove"
    assert not worktree_path.exists()
    removed_events = [e for e in bed.events if isinstance(e, WorktreeRemovedEvent)]
    assert len(removed_events) == 1, "rejected first attempt must not emit RemovedEvent"
    logger.info("TC-03 ok: two-phase confirmation enforced")


# --- TC-04 -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_double_enter_rejected(worktree_bed: WorktreeBed, runner_lifecycle) -> None:
    """TC-04: a second enter while a session is active is refused."""
    bed = worktree_bed
    mock_llm = MockLLMModel()
    mock_llm.set_responses(
        [
            _enter("wt-first", "call_enter_first"),
            _enter("wt-second", "call_enter_second"),
            _exit("remove", "call_exit", discard_changes=True),
            _assertion_text("only first kept"),
        ]
    )
    trace = _ToolTraceRail()
    agent = _build_agent(bed, mock_llm, trace, note_tool=_NoteWriteTool())

    await Runner.run_agent(agent, {"query": "should refuse second enter"})

    grouped = _outputs_by_tool(trace)
    assert len(grouped["enter_worktree"]) == 2
    first_enter, second_enter = grouped["enter_worktree"]
    assert first_enter.success
    assert second_enter.success is False
    assert "Already in worktree" in (second_enter.error or "")
    assert "wt-first" in (second_enter.error or "")
    # Second enter must not have created a wt-second directory.
    second_path = bed.workspace_root / ".worktrees" / "wt-second"
    assert not second_path.exists()
    # First worktree was reachable until the final exit removed it.
    assert grouped["exit_worktree"][0].success
    assert not Path(first_enter.data["worktree_path"]).exists()
    # Only one Created event fired (the rejected enter never reached the manager).
    created_events = [e for e in bed.events if isinstance(e, WorktreeCreatedEvent)]
    assert len(created_events) == 1
    assert created_events[0].worktree_name == "wt-first"
    logger.info("TC-04 ok: double enter rejected without leaking wt-second")


# --- TC-05 -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_without_session_recovers(worktree_bed: WorktreeBed, runner_lifecycle) -> None:
    """TC-05: a stray exit before any enter is rejected and does not corrupt state."""
    bed = worktree_bed
    mock_llm = MockLLMModel()
    mock_llm.set_responses(
        [
            _exit("keep", "call_exit_first"),
            _enter("wt-recover", "call_enter"),
            _exit("keep", "call_exit_second"),
            _assertion_text("recovered"),
        ]
    )
    trace = _ToolTraceRail()
    agent = _build_agent(bed, mock_llm, trace, note_tool=_NoteWriteTool())

    cwd_before_invoke = str(bed.workspace_root.resolve())

    await Runner.run_agent(agent, {"query": "stray exit"})

    grouped = _outputs_by_tool(trace)
    assert len(grouped["exit_worktree"]) == 2
    stray, real = grouped["exit_worktree"]
    assert stray.success is False
    assert "No active worktree session" in (stray.error or "")
    # The stray exit must not have advanced any state.
    assert grouped["enter_worktree"][0].success
    assert real.success
    assert real.data["action"] == "keep"
    # cwd should now be back at the pre-invoke value (workspace root).
    assert get_cwd() == cwd_before_invoke
    assert get_current_session() is None
    logger.info("TC-05 ok: stray exit recovered cleanly")


# --- TC-06 -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_slug_then_recover(worktree_bed: WorktreeBed, runner_lifecycle) -> None:
    """TC-06: an invalid slug is rejected; a sanitized retry succeeds."""
    bed = worktree_bed
    mock_llm = MockLLMModel()
    mock_llm.set_responses(
        [
            _enter("../escape", "call_enter_bad"),
            _enter("safe-name", "call_enter_good"),
            _exit("remove", "call_exit", discard_changes=True),
            _assertion_text("recovered"),
        ]
    )
    trace = _ToolTraceRail()
    agent = _build_agent(bed, mock_llm, trace, note_tool=_NoteWriteTool())

    await Runner.run_agent(agent, {"query": "fix slug"})

    grouped = _outputs_by_tool(trace)
    assert len(grouped["enter_worktree"]) == 2
    bad, good = grouped["enter_worktree"]
    assert bad.success is False
    assert "Invalid worktree name" in (bad.error or "")
    # No directory should have been created for the rejected slug.
    worktrees_dir = bed.workspace_root / ".worktrees"
    if worktrees_dir.exists():
        siblings = {p.name for p in worktrees_dir.iterdir()}
    else:
        siblings = set()
    assert "escape" not in siblings and ".." not in siblings

    assert good.success
    good_path = Path(good.data["worktree_path"])
    assert good_path.name == "safe-name"
    assert grouped["exit_worktree"][0].success
    assert not good_path.exists()
    logger.info("TC-06 ok: invalid slug rejected then recovered")
