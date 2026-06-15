# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from pathlib import Path
from typing import Any

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.context.session_memory_manager import SessionMemoryManager
from openjiuwen.core.context_engine.qa_artifact.schema import QA_MEMORY_STATE_KEY, QAArtifactState


def session_memory_dir(workspace_root: str, session_id: str) -> Path:
    workspace = type("Workspace", (), {"root_path": workspace_root})()
    return SessionMemoryManager.notes_path_for(workspace, session_id, unit=None).parent


def qa_artifact_paths(workspace_root: str, session_id: str, qa_id: str) -> dict[str, str]:
    workspace = type("Workspace", (), {"root_path": workspace_root})()
    overview_path = SessionMemoryManager.notes_path_for(workspace, session_id, unit=qa_id)
    base = overview_path.parent
    return {
        "overview_path": str(overview_path),
        "catalog_path": str(base / f"{qa_id}.catalog.json"),
        "pending_path": str(base / f"{qa_id}.pending"),
        "pending_catalog_path": str(base / f"{qa_id}.pending.catalog"),
    }


def resolve_sys_operation(ctx: Any) -> Any:
    """Resolve sys_operation from processor ctx or nested ModelContext."""
    sys_operation = getattr(ctx, "sys_operation", None)
    if sys_operation is not None:
        return sys_operation
    context = getattr(ctx, "context", None)
    if context is not None:
        return getattr(context, "_sys_operation", None)
    return None


class QAArtifactStore:
    def __init__(self, session: Any, workspace_root: str, sys_operation: Any | None = None):
        self._session = session
        self._workspace_root = workspace_root
        self._sys_operation = sys_operation
        self._session_id = session.get_session_id() if hasattr(session, "get_session_id") else ""

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def workspace_root(self) -> str:
        return self._workspace_root

    def _state_table(self) -> dict[str, dict]:
        if not hasattr(self._session, "get_state"):
            return {}
        raw = self._session.get_state(QA_MEMORY_STATE_KEY) or {}
        return dict(raw) if isinstance(raw, dict) else {}

    def load(self, qa_id: str) -> QAArtifactState | None:
        data = self._state_table().get(qa_id)
        if not data:
            return None
        return QAArtifactState.model_validate(data)

    def get_or_init(self, qa_id: str) -> QAArtifactState:
        existing = self.load(qa_id)
        if existing is not None:
            return existing
        paths = qa_artifact_paths(self._workspace_root, self._session_id, qa_id)
        state = QAArtifactState(
            overview_path=paths["overview_path"],
            catalog_path=paths["catalog_path"],
            pending_path=paths["pending_path"],
        )
        self.save(qa_id, state)
        return state

    def save(self, qa_id: str, state: QAArtifactState) -> None:
        if not hasattr(self._session, "update_state"):
            return
        table = self._state_table()
        table[qa_id] = state.model_dump(mode="json")
        self._session.update_state({QA_MEMORY_STATE_KEY: table})
        logger.info(
            "[QAArtifactStore] save session_id=%s qa_id=%s state=%s products_ready=%s",
            self._session_id,
            qa_id,
            state.state,
            state.products_ready,
        )

    async def read_text(self, path: str) -> str:
        if not path:
            return ""
        if self._sys_operation is not None:
            result = await self._sys_operation.fs().read_file(path)
            if getattr(result, "code", 0) == 0 and getattr(result, "data", None):
                return result.data.content or ""
            return ""
        target = Path(path)
        if target.is_file():
            return target.read_text(encoding="utf-8")
        return ""

    async def write_atomic(self, active_path: str, content: str, pending_path: str) -> None:
        parent = Path(active_path).parent
        parent.mkdir(parents=True, exist_ok=True)
        if self._sys_operation is not None:
            await self._sys_operation.fs().write_file(pending_path, content)
            await self._sys_operation.fs().write_file(active_path, content)
            return
        Path(pending_path).write_text(content, encoding="utf-8")
        Path(pending_path).replace(active_path)
