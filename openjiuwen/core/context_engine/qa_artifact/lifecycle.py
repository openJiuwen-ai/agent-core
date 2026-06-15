# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import weakref
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openjiuwen.core.context_engine.qa_artifact.manager import QAArtifactManager

_MANAGERS: weakref.WeakSet[QAArtifactManager] = weakref.WeakSet()


def register_qa_artifact_manager(mgr: QAArtifactManager) -> None:
    _MANAGERS.add(mgr)


async def cancel_qa_artifact_tasks_for_session(session_id: str | None) -> None:
    """Cancel in-flight produce/overview tasks when a session context is cleared."""
    if session_id is None:
        for mgr in list(_MANAGERS):
            await mgr.cancel_all_session_tasks()
        return
    for mgr in list(_MANAGERS):
        await mgr.cancel_session_tasks(session_id)


__all__ = [
    "cancel_qa_artifact_tasks_for_session",
    "register_qa_artifact_manager",
]
