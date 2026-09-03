"""Pure projection functions: internal task/tree state -> the public wire
shapes (EngineState/EngineReport/TreeResponse) the Provider surface
returns. See docs/paper_tree_orchestrator_design.md "Provider surface".
No I/O here — callers pass in already-loaded state/nodes/artifacts.
"""

from __future__ import annotations

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider.schemas import (
    ArtifactRef,
    EngineReport,
    EngineState,
    PaperTaskState,
    RsiTreeNode,
    RsiUsage,
    TreeResponse,
)


def tree_depth(nodes: list[RsiTreeNode]) -> int:
    """Root has depth 0; every other node is its parent's depth + 1.
    Assumes parents always appear before or alongside children in `nodes`
    (true by construction — a node's parent always exists before the node
    itself is created)."""
    depth_by_id: dict[str, int] = {}
    for node in nodes:
        if node.parent_id is None:
            depth_by_id[node.node_id] = 0
            continue
        depth_by_id[node.node_id] = depth_by_id.get(node.parent_id, 0) + 1
    return max(depth_by_id.values(), default=0)


def project_tree_response(task: PaperTaskState, nodes: list[RsiTreeNode]) -> TreeResponse:
    return TreeResponse(nodes=nodes, depth=tree_depth(nodes), iteration=task.node_count)


def project_engine_state(task: PaperTaskState, usage: RsiUsage | None = None) -> EngineState:
    return EngineState(
        task_id=task.task_id,
        status=task.status,
        iteration=task.node_count,
        total_iterations=task.max_iterations,
        best_node_id=task.best_node_id,
        score=None,
        baseline=None,
        usage=usage,
        updated_at=task.updated_at,
        error_code=task.error_code,
        error_message=task.error_message,
    )


def project_engine_report(
    task: PaperTaskState,
    artifact_index: list[ArtifactRef],
    usage: RsiUsage | None = None,
    summary: str | None = None,
) -> EngineReport:
    return EngineReport(
        task_id=task.task_id,
        status=task.status,
        best_node_id=task.best_node_id,
        usage=usage,
        artifact_index=artifact_index,
        summary=summary,
    )
