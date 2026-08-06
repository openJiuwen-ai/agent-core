# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Short runtime path layout for member optimization.

Audit artifacts stay under the configured member_optimizations directory.
Runtime directories that recursively copy ExpertHarness packages live under a
short workspace-level root to avoid Windows path length failures.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_OPTIMIZATION_ID_RE = re.compile(r"member_optimization_(\d+)$")


@dataclass(frozen=True, slots=True)
class MemberOptimizerPathLayout:
    """Allocate short runtime paths while preserving readable audit paths."""

    output_root: Path
    runtime_root: Path

    @classmethod
    def from_output_root(cls, output_root: str | Path) -> "MemberOptimizerPathLayout":
        resolved_output_root = Path(output_root).expanduser().resolve()
        return cls(
            output_root=resolved_output_root,
            runtime_root=_infer_runtime_root(resolved_output_root),
        )

    @property
    def roles_map_path(self) -> Path:
        return self.runtime_root / "roles.yaml"

    @staticmethod
    def optimization_key(optimization_id: str) -> str:
        match = _OPTIMIZATION_ID_RE.match(str(optimization_id))
        if match:
            return f"m{int(match.group(1)):03d}"
        return f"m{_hash_token(optimization_id)}"

    @staticmethod
    def role_key(role: str) -> str:
        return f"r{_hash_token(role)}"

    def run_runtime_dir(self, optimization_id: str) -> Path:
        return self.runtime_root / "runs" / self.optimization_key(optimization_id)

    def worktrees_dir(self, optimization_id: str) -> Path:
        return self.run_runtime_dir(optimization_id) / "wt"

    def publish_tmp_dir(self, optimization_id: str, role: str) -> Path:
        return self.run_runtime_dir(optimization_id) / "tmp" / self.role_key(role)

    def current_harness_dir(self, role: str) -> Path:
        return self.runtime_root / "current" / self.role_key(role)

    def candidate_harness_dir(self, optimization_id: str, role: str) -> Path:
        """Return an immutable run-scoped candidate directory."""
        return self.run_runtime_dir(optimization_id) / "candidate" / self.role_key(role)

    def initial_harness_dir(self, role: str) -> Path:
        return self.runtime_root / "initial" / self.role_key(role)

    def write_role_mapping(self, role: str) -> None:
        """Persist the readable role -> short key mapping for auditability."""
        role_key = self.role_key(role)
        payload: dict[str, Any] = {"roles": {}}
        if self.roles_map_path.is_file():
            loaded = yaml.safe_load(self.roles_map_path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                payload = loaded
        roles = payload.setdefault("roles", {})
        if not isinstance(roles, dict):
            roles = {}
            payload["roles"] = roles
        roles[role] = {
            "role_key": role_key,
            "current_harness_dir": str(self.current_harness_dir(role)),
        }
        self.roles_map_path.parent.mkdir(parents=True, exist_ok=True)
        self.roles_map_path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=True),
            encoding="utf-8",
        )


def _infer_runtime_root(output_root: Path) -> Path:
    """Infer a short workspace-level runtime root from member_optimizations."""
    if output_root.name == "member_optimizations":
        team_root = output_root.parent
        workspace_root = team_root.parent
        if workspace_root.name == "workspace" or _looks_like_orchestrator_team_root(team_root):
            return workspace_root / "mh"
    return output_root / "mh"


def _looks_like_orchestrator_team_root(team_root: Path) -> bool:
    expected = {"evaluations", "team_skills", "datasets", "checkpoints"}
    try:
        existing = {child.name for child in team_root.iterdir()}
    except OSError:
        return False
    return bool(existing & expected)


def _hash_token(value: str) -> str:
    raw = str(value or "default").encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:8]


__all__ = ["MemberOptimizerPathLayout"]
