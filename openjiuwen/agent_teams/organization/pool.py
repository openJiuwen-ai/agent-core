# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Process-local registry for organization managers."""

from __future__ import annotations

from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.organization.manager import TeamOrganizationManager
from openjiuwen.agent_teams.tools.database import TeamDatabase

_MANAGERS: dict[tuple[int, str, str | None], TeamOrganizationManager] = {}


def get_process_org_manager(
    *,
    organization_id: str,
    db: TeamDatabase,
    messager: Messager | None = None,
    session_id: str | None = None,
) -> TeamOrganizationManager:
    """Return the process-local manager for ``organization_id`` and DB handle."""

    key = (id(db), organization_id, session_id)
    manager = _MANAGERS.get(key)
    if manager is None:
        manager = TeamOrganizationManager(
            organization_id=organization_id,
            db=db,
            messager=messager,
            session_id=session_id,
        )
        _MANAGERS[key] = manager
    return manager


def clear_process_org_managers() -> None:
    _MANAGERS.clear()


def remove_process_org_manager(
    *,
    organization_id: str,
    db: TeamDatabase,
    session_id: str | None = None,
) -> None:
    """Forget one dissolved organization manager from the process registry."""

    _MANAGERS.pop((id(db), organization_id, session_id), None)


__all__ = ["clear_process_org_managers", "get_process_org_manager", "remove_process_org_manager"]
