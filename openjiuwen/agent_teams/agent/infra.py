# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TeamAgent infrastructure layer.

The third quadrant of the four-quadrant TeamAgent decomposition: per-process
infra resources that are reachable from every member running in this
process. Since leader and teammates run in different processes, "shared"
here is a per-process scope, not a cross-instance singleton.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
)

if TYPE_CHECKING:
    from openjiuwen.agent_teams.messager import Messager
    from openjiuwen.agent_teams.team_workspace.manager import TeamWorkspaceManager
    from openjiuwen.agent_teams.tools.team import TeamBackend


@dataclass
class TeamInfra:
    """Per-process team infrastructure.

    Holds the message bus, the team backend (DB + task/message managers),
    and the team workspace manager. Each process running a TeamAgent
    builds its own TeamInfra; references are not shared across process
    boundaries.

    ``task_manager`` and ``message_manager`` are conceptually derived from
    ``team_backend``, but kept as explicit fields so callers can inject
    test doubles without monkey-patching the backend.
    """

    messager: Optional["Messager"] = None
    team_backend: Optional["TeamBackend"] = None
    workspace_manager: Optional["TeamWorkspaceManager"] = None
    workspace_initialized: bool = False
    task_manager: Any = None
    message_manager: Any = None


__all__ = ["TeamInfra"]
