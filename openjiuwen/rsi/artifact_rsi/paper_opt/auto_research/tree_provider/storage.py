"""Atomic on-disk persistence for one paper-tree task — tree.json /
state.json / report.json / artifacts.json under the task's own run_dir
(the caller-assigned ``ArtifactEngineRequest.run_dir``, distinct from any
node's own ``experiments/<node_run_id>/`` — see
docs/paper_tree_orchestrator_design.md "Storage layout"). Same
tmp-file-then-replace atomic-write idiom
``modules/manager/artifacts.py::_atomic_write_text`` already uses.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.tree_provider.schemas import (
    ArtifactRef,
    PaperTaskState,
    RsiTreeNode,
)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class TaskStorage:
    """Owns tree.json/state.json/report.json/artifacts.json for one task_id."""

    def __init__(self, run_dir: str | Path) -> None:
        self.run_dir = Path(run_dir)

    @property
    def tree_path(self) -> Path:
        return self.run_dir / "tree.json"

    @property
    def state_path(self) -> Path:
        return self.run_dir / "state.json"

    @property
    def report_path(self) -> Path:
        return self.run_dir / "report.json"

    @property
    def artifacts_path(self) -> Path:
        return self.run_dir / "artifacts.json"

    # -- task state -------------------------------------------------------
    def load_task_state(self) -> PaperTaskState | None:
        if not self.state_path.exists():
            return None
        return PaperTaskState.model_validate_json(self.state_path.read_text(encoding="utf-8"))

    def save_task_state(self, state: PaperTaskState) -> None:
        state.updated_at = _now()
        _atomic_write_text(self.state_path, state.model_dump_json(indent=2))

    # -- tree ---------------------------------------------------------------
    def load_tree(self) -> list[RsiTreeNode]:
        if not self.tree_path.exists():
            return []
        raw = json.loads(self.tree_path.read_text(encoding="utf-8"))
        return [RsiTreeNode.model_validate(item) for item in raw]

    def save_tree(self, nodes: list[RsiTreeNode]) -> None:
        payload = json.dumps([node.model_dump(mode="json") for node in nodes], indent=2)
        _atomic_write_text(self.tree_path, payload)

    def append_node(self, node: RsiTreeNode) -> list[RsiTreeNode]:
        """Append a new node, or replace the existing node with the same
        ``node_id`` in place. Upsert semantics let a node be persisted as an
        in-flight placeholder (so a ``NodeStageEvent``'s ``node_ref`` always
        resolves to something in ``tree.json``) and later overwritten with
        its final result under the same ``node_id``.
        """
        nodes = self.load_tree()
        for index, existing in enumerate(nodes):
            if existing.node_id == node.node_id:
                nodes[index] = node
                break
        else:
            nodes.append(node)
        self.save_tree(nodes)
        return nodes

    # -- artifacts ------------------------------------------------------------
    def load_artifacts(self) -> dict[str, ArtifactRef]:
        if not self.artifacts_path.exists():
            return {}
        raw = json.loads(self.artifacts_path.read_text(encoding="utf-8"))
        return {key: ArtifactRef.model_validate(value) for key, value in raw.items()}

    def save_artifacts(self, index: dict[str, ArtifactRef]) -> None:
        payload = json.dumps(
            {key: ref.model_dump(mode="json") for key, ref in index.items()}, indent=2
        )
        _atomic_write_text(self.artifacts_path, payload)

    def register_artifact(self, ref: ArtifactRef) -> None:
        index = self.load_artifacts()
        index[ref.artifact_id] = ref
        self.save_artifacts(index)
