"""Covers the friendly-reason display transform added for Provider
consumers (frontend EventNode stream + TreeResponse polling): a rejected
node's raw `reason`/`failure_class` get a plain-language prefix at the
orchestrator/projection boundary, while the value stored to disk and fed
into the next round's seed prompt (`PaperTaskState.last_reason`) stays raw.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import set_project_root
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.schemas import TerminalReport
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider.orchestrator import (
    PaperTreeOrchestrator,
    _display_node,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider.projection import (
    project_tree_response,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider.schemas import (
    EventNode,
    RsiTreeNode,
    friendly_failure_reason,
)


def _node(*, reason: str | None, failure_class: str | None, adopted: bool = False) -> RsiTreeNode:
    return RsiTreeNode(
        node_id="n1",
        iteration=1,
        parent_id="ROOT",
        type="reporting",
        adopted=adopted,
        reason=reason,
        failure_class=failure_class,
    )


# -- schemas.py::friendly_failure_reason -------------------------------------


def test_known_failure_class_gets_a_friendly_prefix_and_keeps_the_detail():
    node = _node(reason="manager_blocked", failure_class="pipeline_blocked")

    friendly = friendly_failure_reason(node)

    assert friendly is not None
    assert "manager_blocked" in friendly
    assert "提前终止" in friendly


def test_unknown_failure_class_falls_back_to_raw_reason():
    node = _node(reason="some future failure mode", failure_class="something_new")

    assert friendly_failure_reason(node) == "some future failure mode"


def test_adopted_node_with_no_failure_class_falls_back_to_raw_reason():
    node = _node(reason="score 2 > parent score 1. fake", failure_class=None, adopted=True)

    assert friendly_failure_reason(node) == "score 2 > parent score 1. fake"


def test_no_reason_at_all_returns_none():
    node = _node(reason=None, failure_class="pipeline_blocked")

    assert friendly_failure_reason(node) is None


# -- orchestrator.py::_display_node -------------------------------------


def test_display_node_swaps_reason_without_mutating_the_original():
    node = _node(reason="manager_blocked", failure_class="pipeline_blocked")

    displayed = _display_node(node)

    assert displayed is not node
    assert displayed.reason != node.reason
    assert node.reason == "manager_blocked"  # original untouched
    assert "manager_blocked" in (displayed.reason or "")


def test_display_node_returns_same_node_when_nothing_to_translate():
    node = _node(reason=None, failure_class=None, adopted=True)

    assert _display_node(node) is node


# -- projection.py::project_tree_response -------------------------------------


def test_project_tree_response_translates_rejected_nodes_only():
    root = _node(reason=None, failure_class=None, adopted=True)
    root = root.model_copy(update={"node_id": "ROOT", "parent_id": None})
    rejected = _node(reason="manager_blocked", failure_class="pipeline_blocked")

    class _FakeTask:
        node_count = 1

    response = project_tree_response(_FakeTask(), [root, rejected])  # type: ignore[arg-type]

    by_id = {node.node_id: node for node in response.nodes}
    assert by_id["ROOT"].reason is None
    assert by_id["n1"].reason is not None
    assert "manager_blocked" in by_id["n1"].reason
    assert "提前终止" in by_id["n1"].reason


# -- end-to-end: stored/last_reason and emitted EventNode stay friendly ------


@pytest.fixture(autouse=True)
def _project_root(tmp_path: Path):
    set_project_root(tmp_path)
    yield


@pytest.mark.asyncio
async def test_emitted_node_is_friendly_but_stored_state_stays_raw(tmp_path: Path, monkeypatch):
    async def _run_manager(self, seed):
        return TerminalReport(
            status="blocked",
            run_id=seed.run_id,
            abort_reason="manager_blocked",
            summary="model recommended stopping",
        )

    monkeypatch.setattr(PaperTreeOrchestrator, "_run_manager", _run_manager)

    emitted: list[EventNode] = []

    async def on_event(event) -> None:
        if isinstance(event, EventNode):
            emitted.append(event)

    orchestrator = PaperTreeOrchestrator(
        task_id="task-display",
        run_dir=str(tmp_path / "task_run_dir"),
        max_iterations=1,
        optimization_instruction="improve section 3",
        artifact_path=None,
        on_event=on_event,
    )
    await orchestrator.start()
    await orchestrator._task

    round_one_events = [e for e in emitted if e.node.node_id != "artifact:task-display:root"]
    assert round_one_events
    emitted_node = round_one_events[-1].node
    assert emitted_node.reason is not None
    assert "提前终止" in emitted_node.reason
    assert "当前方案效果未达到要求，已剪枝。" in emitted_node.reason
    assert "manager_blocked" not in emitted_node.reason

    stored_node = next(
        n for n in orchestrator.storage.load_tree() if n.node_id == emitted_node.node_id
    )
    assert stored_node.reason == "当前方案效果未达到要求，已剪枝。"

    state = orchestrator.storage.load_task_state()
    assert state.last_reason == "当前方案效果未达到要求，已剪枝。"
