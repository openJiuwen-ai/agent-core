# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Artifact storage for stage-to-stage communication."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ArtifactStore:
    """A scoped artifact store with session and task namespaces."""

    _session: dict[str, Any] = field(
        default_factory=dict
    )
    _task: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )

    def get(
        self,
        name: str,
        *,
        task_id: str = "",
        default: Any = None,
    ) -> Any:
        if task_id:
            task_bucket = self._task.get(task_id, {})
            if name in task_bucket:
                return task_bucket[name]
        return self._session.get(name, default)

    def require(
        self,
        name: str,
        *,
        task_id: str = "",
    ) -> Any:
        marker = object()
        value = self.get(
            name,
            task_id=task_id,
            default=marker,
        )
        if value is marker:
            scope = f"task={task_id}" if task_id else "session"
            raise KeyError(
                f"Missing artifact '{name}' in {scope}"
            )
        return value

    def put(
        self,
        name: str,
        value: Any,
        *,
        task_id: str = "",
    ) -> None:
        if task_id:
            self._task.setdefault(task_id, {})[name] = value
            return
        self._session[name] = value

    def put_many(
        self,
        artifacts: dict[str, Any],
        *,
        task_id: str = "",
    ) -> None:
        for key, value in artifacts.items():
            self.put(key, value, task_id=task_id)

    def has(
        self,
        name: str,
        *,
        task_id: str = "",
    ) -> bool:
        marker = object()
        return self.get(
            name,
            task_id=task_id,
            default=marker,
        ) is not marker

    def reset_task(self, task_id: str) -> None:
        self._task.pop(task_id, None)
