"""Covers PaperTreeOrchestrator._emit folding a NodeStageEvent's live
`note` (from pipeline/stage_activity.py's trace tailer) into the same
`node.summary` string the frontend already renders, with no new field
required on the frontend side. See tree_provider/orchestrator.py::_emit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import set_project_root
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider.orchestrator import PaperTreeOrchestrator
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider.schemas import NodeStageEvent, RsiTreeNode


@pytest.fixture(autouse=True)
def _project_root(tmp_path: Path):
    set_project_root(tmp_path)
    yield


def _orchestrator(tmp_path: Path) -> PaperTreeOrchestrator:
    orchestrator = PaperTreeOrchestrator(
        task_id="task-note",
        run_dir=str(tmp_path / "task_run_dir"),
        max_iterations=1,
        optimization_instruction="improve section 3",
        artifact_path=None,
    )
    orchestrator.storage.append_node(
        RsiTreeNode(node_id="n1", iteration=1, parent_id="ROOT", type="reporting", adopted=False)
    )
    return orchestrator


@pytest.mark.asyncio
async def test_stage_event_without_note_keeps_plain_label(tmp_path: Path):
    orchestrator = _orchestrator(tmp_path)

    await orchestrator._emit(
        NodeStageEvent(node_ref="n1", stage={"id": "code_implementation", "name": "正在实现代码"})
    )

    node = next(n for n in orchestrator.storage.load_tree() if n.node_id == "n1")
    assert node.summary == "正在实现代码"
    assert node.extra["stage"]["note"] is None


@pytest.mark.asyncio
async def test_stage_event_with_note_appends_it_to_summary(tmp_path: Path):
    orchestrator = _orchestrator(tmp_path)

    await orchestrator._emit(
        NodeStageEvent(
            node_ref="n1",
            stage={"id": "code_implementation", "name": "正在实现代码"},
            note="最近动作：执行命令 `pytest tests/`",
        )
    )

    node = next(n for n in orchestrator.storage.load_tree() if n.node_id == "n1")
    assert node.summary == "正在实现代码 · 最近动作：执行命令 `pytest tests/`"
    assert node.extra["stage"] == {
        "id": "code_implementation",
        "name": "正在实现代码",
        "note": "最近动作：执行命令 `pytest tests/`",
    }


@pytest.mark.asyncio
async def test_later_stage_event_without_note_clears_the_previous_one(tmp_path: Path):
    orchestrator = _orchestrator(tmp_path)

    await orchestrator._emit(
        NodeStageEvent(
            node_ref="n1",
            stage={"id": "code_implementation", "name": "正在实现代码"},
            note="最近动作：执行命令 `ls`",
        )
    )
    await orchestrator._emit(
        NodeStageEvent(node_ref="n1", stage={"id": "reporting", "name": "正在撰写论文"})
    )

    node = next(n for n in orchestrator.storage.load_tree() if n.node_id == "n1")
    assert node.summary == "正在撰写论文"
