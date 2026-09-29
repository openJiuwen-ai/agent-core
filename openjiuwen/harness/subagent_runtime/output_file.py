# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Resolve and write subagent turn output files under the parent workspace."""

from __future__ import annotations

from pathlib import Path
from typing import Any

SUB_AGENTS_DIR = "sub_agents"
OUTPUTS_DIR = "outputs"


def resolve_parent_workspace_root(parent_agent: Any) -> Path:
    """Return the parent agent workspace root (absolute)."""
    deep_config = getattr(parent_agent, "deep_config", None)
    if deep_config is None:
        return Path(".").resolve()
    workspace = getattr(deep_config, "workspace", None)
    if workspace is None:
        return Path(".").resolve()
    if isinstance(workspace, str):
        text = workspace.strip()
        if not text:
            return Path(".").resolve()
        return Path(text).resolve()
    root_path = getattr(workspace, "root_path", None)
    if root_path is None or not str(root_path).strip():
        return Path(".").resolve()
    return Path(root_path).resolve()


def resolve_output_path(
    parent_workspace_root: Path,
    subagent_id: str,
    task_id: str,
) -> Path:
    """Return the absolute path for one subagent turn output file."""
    base = (parent_workspace_root / SUB_AGENTS_DIR / subagent_id / OUTPUTS_DIR).resolve()
    path = (base / f"{task_id}.md").resolve()
    if path != base and base not in path.parents:
        raise ValueError(f"unsafe subagent output path for {subagent_id!r}")
    return path


def write_output(path: Path, content: str) -> str:
    """Write turn content and return the absolute file path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path.resolve())


def write_turn_output(
    parent_workspace_root: Path,
    subagent_id: str,
    task_id: str,
    content: str,
) -> str:
    """Resolve the turn output path under the parent workspace and write content."""
    path = resolve_output_path(parent_workspace_root, subagent_id, task_id)
    return write_output(path, content)


__all__ = [
    "OUTPUTS_DIR",
    "SUB_AGENTS_DIR",
    "resolve_output_path",
    "resolve_parent_workspace_root",
    "write_output",
    "write_turn_output",
]
