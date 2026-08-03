# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Checkpoint management for resumable optimization runs."""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from openjiuwen.rsi.orchestrator.context import OrchestratorContextStore
from openjiuwen.rsi.schema import OrchestratorRunContext


class CheckpointManager:
    """Create and load lightweight context snapshots."""

    def __init__(self, checkpoint_dir: str) -> None:
        self.checkpoint_dir = checkpoint_dir

    def save(self, context: OrchestratorRunContext, checkpoint_id: str | None = None) -> str:
        """Persist a lightweight context snapshot and return its directory."""
        checkpoint_root = Path(self.checkpoint_dir).expanduser().resolve()
        checkpoint_root.mkdir(parents=True, exist_ok=True)
        target_dir = checkpoint_root / (checkpoint_id or _next_checkpoint_id(checkpoint_root))
        target_dir.mkdir(parents=True, exist_ok=True)
        OrchestratorContextStore(str(target_dir / "orchestrator_context.yaml")).save(context)
        return str(target_dir)

    def snapshot_harness_refs(
        self,
        *,
        checkpoint_id: str,
        harness_refs_path: str,
        harness_refs: dict[str, str],
    ) -> tuple[str, dict[str, str]]:
        """Copy current member harness refs into an immutable checkpoint directory."""
        checkpoint_root = Path(self.checkpoint_dir).expanduser().resolve()
        target_dir = checkpoint_root / checkpoint_id
        target_dir.mkdir(parents=True, exist_ok=True)

        source_refs = _load_harness_refs(harness_refs_path)
        source_refs.update({str(role): str(path) for role, path in harness_refs.items() if str(role)})
        if not source_refs:
            return harness_refs_path, dict(harness_refs)

        snapshot_refs: dict[str, str] = {}
        harnesses_dir = target_dir / "harnesses"
        harnesses_dir.mkdir(parents=True, exist_ok=True)
        for role, source in source_refs.items():
            source_path = Path(source).expanduser().resolve()
            target_path = harnesses_dir / _safe_path_segment(role)
            if source_path.is_dir():
                if target_path.exists():
                    shutil.rmtree(target_path)
                shutil.copytree(source_path, target_path)
                snapshot_refs[role] = str(target_path)
            elif source_path.is_file():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, target_path)
                snapshot_refs[role] = str(target_path)
            else:
                snapshot_refs[role] = source

        snapshot_refs_path = target_dir / "harness_refs.yaml"
        snapshot_refs_path.write_text(
            yaml.safe_dump(
                {"harness_refs": snapshot_refs},
                allow_unicode=True,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return str(snapshot_refs_path), snapshot_refs

    def load(self, checkpoint_id: str) -> OrchestratorRunContext:
        """Load a checkpoint context snapshot."""
        checkpoint_path = Path(self.checkpoint_dir).expanduser().resolve() / checkpoint_id / "orchestrator_context.yaml"
        if not checkpoint_path.is_file():
            raise FileNotFoundError(f"checkpoint context not found: {checkpoint_path}")
        return OrchestratorContextStore(str(checkpoint_path)).load()

    def list(self) -> list[str]:
        """List available checkpoint identifiers in creation order."""
        checkpoint_root = Path(self.checkpoint_dir).expanduser().resolve()
        if not checkpoint_root.is_dir():
            return []
        return sorted(path.name for path in checkpoint_root.iterdir() if path.is_dir())


__all__ = [
    "CheckpointManager",
]


def _next_checkpoint_id(checkpoint_root: Path) -> str:
    index = 1
    while True:
        checkpoint_id = f"checkpoint_{index:03d}"
        if not (checkpoint_root / checkpoint_id).exists():
            return checkpoint_id
        index += 1


def _load_harness_refs(harness_refs_path: str) -> dict[str, str]:
    path = Path(harness_refs_path).expanduser()
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(data, dict):
        return {}
    raw_refs = data.get("harness_refs", data)
    if not isinstance(raw_refs, dict):
        return {}
    return {str(role): str(ref) for role, ref in raw_refs.items() if str(role) and str(ref)}


def _safe_path_segment(value: str) -> str:
    cleaned = [char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value.strip()]
    return "".join(cleaned).strip("._") or "role"
