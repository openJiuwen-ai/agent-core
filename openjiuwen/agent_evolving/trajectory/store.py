# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""TrajectoryStore: persistence interface for trajectories.

Provides protocol and implementations for saving/loading/querying
trajectory data with optional version isolation.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from openjiuwen.agent_evolving.trajectory.types import Trajectory


class TrajectoryStore(Protocol):
    """Trajectory persistence protocol."""

    def save(
        self,
        trajectory: Trajectory,
        version: Optional[str] = None,
    ) -> None:
        """Save trajectory. Version is used for experiment isolation.

        Args:
            trajectory: Trajectory to save
            version: Optional version identifier
        """
        ...

    def load(
        self,
        execution_id: str,
        version: Optional[str] = None,
    ) -> Optional[Trajectory]:
        """Load a specific trajectory.

        Args:
            execution_id: Execution ID of the trajectory
            version: Optional version identifier

        Returns:
            Loaded Trajectory or None if not found
        """
        ...

    def query(
        self,
        version: Optional[str] = None,
        **filters,
    ) -> List[Trajectory]:
        """Query trajectory list.

        Args:
            version: Optional version identifier
            **filters: Filters like session_id, case_id, source, etc.

        Returns:
            List of matching Trajectories
        """
        ...


class InMemoryTrajectoryStore:
    """In-memory store for testing and development."""

    def __init__(self) -> None:
        """Initialize empty store."""
        self._data: Dict[str, Dict[str, Trajectory]] = {}

    def save(
        self,
        trajectory: Trajectory,
        version: Optional[str] = None,
    ) -> None:
        """Save trajectory to memory."""
        ver = version or "default"
        if ver not in self._data:
            self._data[ver] = {}
        self._data[ver][trajectory.execution_id] = trajectory

    def load(
        self,
        execution_id: str,
        version: Optional[str] = None,
    ) -> Optional[Trajectory]:
        """Load trajectory from memory."""
        ver = version or "default"
        return self._data.get(ver, {}).get(execution_id)

    def query(
        self,
        version: Optional[str] = None,
        **filters,
    ) -> List[Trajectory]:
        """Query trajectories from memory."""
        ver = version or "default"
        trajectories = list(self._data.get(ver, {}).values())

        # Apply filters
        for key, value in filters.items():
            trajectories = [
                t for t in trajectories
                if getattr(t, key, None) == value
            ]

        return trajectories


class FileTrajectoryStore:
    """File-based store (JSONL) for jiuwenclaw personal assistant."""

    def __init__(self, base_dir: Path) -> None:
        """Initialize with base directory.

        Args:
            base_dir: Directory to store trajectory files
        """
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _get_file_path(self, version: Optional[str]) -> Path:
        """Get file path for version."""
        filename = f"trajectories_{version or 'default'}.jsonl"
        return self._base_dir / filename

    def save(
        self,
        trajectory: Trajectory,
        version: Optional[str] = None,
    ) -> None:
        """Append trajectory to JSONL file."""
        file_path = self._get_file_path(version)

        # Convert to JSON-serializable dict
        data = self._trajectory_to_dict(trajectory)

        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

    def load(
        self,
        execution_id: str,
        version: Optional[str] = None,
    ) -> Optional[Trajectory]:
        """Load trajectory by execution_id."""
        file_path = self._get_file_path(version)

        if not file_path.exists():
            return None

        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("execution_id") == execution_id:
                        return self._dict_to_trajectory(data)
                except json.JSONDecodeError:
                    continue

        return None

    def query(
        self,
        version: Optional[str] = None,
        **filters,
    ) -> List[Trajectory]:
        """Query trajectories matching filters."""
        file_path = self._get_file_path(version)
        results: List[Trajectory] = []

        if not file_path.exists():
            return results

        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    # Apply filters
                    if all(
                        data.get(key) == value
                        for key, value in filters.items()
                    ):
                        traj = self._dict_to_trajectory(data)
                        if traj:
                            results.append(traj)
                except (json.JSONDecodeError, KeyError):
                    continue

        return results

    @staticmethod
    def _trajectory_to_dict(trajectory: Trajectory) -> dict:
        """Convert Trajectory to dict."""
        return FileTrajectoryStore._to_json_compatible(trajectory)

    @staticmethod
    def _to_json_compatible(obj: Any) -> Any:
        """Recursively convert values to JSON-compatible data."""
        if hasattr(obj, "model_dump") and callable(obj.model_dump):
            return FileTrajectoryStore._to_json_compatible(obj.model_dump())

        if hasattr(obj, "__dataclass_fields__"):
            return FileTrajectoryStore._to_json_compatible(asdict(obj))

        if isinstance(obj, (list, tuple)):
            return [FileTrajectoryStore._to_json_compatible(item) for item in obj]

        if isinstance(obj, dict):
            return {
                str(key): FileTrajectoryStore._to_json_compatible(value)
                for key, value in obj.items()
            }

        if isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj

        return str(obj)

    @staticmethod
    def _dict_to_trajectory(data: dict) -> Optional[Trajectory]:
        """Convert dict to Trajectory."""
        from openjiuwen.agent_evolving.trajectory.types import (
            LLMCallDetail,
            StepDetail,
            ToolCallDetail,
            TrajectoryStep,
        )

        try:
            # Convert steps
            steps_data = data.get("steps", [])
            steps = []
            for step_data in steps_data:
                # Extract and convert detail
                detail_data = step_data.pop("detail", None)
                detail: Optional[StepDetail] = None

                if detail_data:
                    # Determine detail type by presence of key fields
                    if "messages" in detail_data:
                        detail = LLMCallDetail(**detail_data)
                    elif "tool_name" in detail_data:
                        detail = ToolCallDetail(**detail_data)

                # Build step with converted detail
                step_data["detail"] = detail
                steps.append(TrajectoryStep(**step_data))

            data["steps"] = steps
            return Trajectory(**data)

        except (KeyError, TypeError, ValueError):
            return None
