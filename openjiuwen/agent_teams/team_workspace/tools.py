# coding: utf-8

"""Workspace metadata tool for lock management and version history.

File I/O goes through standard read_file/write_file/glob via the .team/
mount point. This tool ONLY handles lock management and version history
queries that have no filesystem equivalent.
"""

from __future__ import annotations

from typing import Any, Dict, TYPE_CHECKING

from openjiuwen.agent_teams.tools.locales import Translator
from openjiuwen.agent_teams.tools.team_tools import TeamTool
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

if TYPE_CHECKING:
    from openjiuwen.agent_teams.team_workspace.manager import TeamWorkspaceManager


class WorkspaceMetaTool(TeamTool):
    """Workspace metadata operations (lock management and version history).

    File I/O goes through standard read_file/write_file/glob via the .team/
    mount point. This tool ONLY handles lock management and version history
    queries that have no filesystem equivalent.
    """

    def __init__(self, workspace: TeamWorkspaceManager, t: Translator):
        super().__init__(
            ToolCard(
                id="team.workspace_meta",
                name="workspace_meta",
                description=t("workspace_meta"),
            )
        )
        self._ws = workspace
        self.card.input_params = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["lock", "unlock", "locks", "history"],
                    "description": t("workspace_meta", "action"),
                },
                "path": {
                    "type": "string",
                    "description": t("workspace_meta", "path"),
                },
            },
            "required": ["action"],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        """Execute a workspace metadata action.

        Args:
            inputs: Tool inputs with required "action" and optional "path".
            **kwargs: May contain "member_id" and "member_name" from caller context.

        Returns:
            ToolOutput with action-specific result data.
        """
        action = inputs.get("action")
        path = inputs.get("path", "")
        member_id = kwargs.get("member_id", "unknown")
        member_name = kwargs.get("member_name", member_id)

        if action == "lock":
            if not path:
                return ToolOutput(success=False, error="'path' is required for lock action")
            acquired = await self._ws.acquire_lock(path, member_id, member_name)
            if not acquired:
                lock = self._ws.get_lock(path)
                return ToolOutput(
                    success=False,
                    error=f"Locked by {lock.holder_name}" if lock else "Lock failed",
                )
            return ToolOutput(success=True, data={"locked": path})

        if action == "unlock":
            if not path:
                return ToolOutput(success=False, error="'path' is required for unlock action")
            released = await self._ws.release_lock(path, member_id)
            return ToolOutput(success=True, data={"released": released})

        if action == "locks":
            locks = await self._ws.list_locks()
            return ToolOutput(
                success=True,
                data={"locks": [lock.model_dump() for lock in locks]},
            )

        if action == "history":
            if not path:
                return ToolOutput(success=False, error="'path' is required for history action")
            history = await self._ws.get_history(path)
            return ToolOutput(success=True, data={"history": history})

        return ToolOutput(success=False, error=f"Unknown action '{action}'")
