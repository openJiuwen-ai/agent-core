"""Capability rail: the manager may submit a decision and nothing else."""

from __future__ import annotations

from typing import Any

from openjiuwen.harness.rails.base import DeepAgentRail

ALLOWED_TOOL_NAMES = frozenset({"submit_manager_decision"})
FORBIDDEN_TOOL_NAMES = frozenset(
    {
        "write_file",
        "edit_file",
        "read_file",
        "glob",
        "grep",
        "list_files",
        "bash",
        "powershell",
        "code",
        "free_search",
        "paid_search",
        "fetch_webpage",
        "download_survey_source",
    }
)


class ManagerCapabilityRail(DeepAgentRail):
    """Strip filesystem, network, and shell tools from the manager agent."""

    priority = 200

    def __init__(self) -> None:
        super().__init__()
        self.tools: list[Any] = []

    def init(self, agent) -> None:
        manager = getattr(agent, "ability_manager", None)
        if manager is None:
            return
        for name in list(FORBIDDEN_TOOL_NAMES):
            remover = getattr(manager, "remove_ability", None)
            if callable(remover):
                try:
                    remover(name)
                except Exception:  # noqa: BLE001, S110 - best-effort tool strip
                    pass
        remaining = set()
        abilities = getattr(manager, "abilities", None) or getattr(manager, "_abilities", None)
        if isinstance(abilities, dict):
            remaining = set(abilities)
        elif abilities is not None:
            try:
                remaining = {getattr(item, "name", str(item)) for item in abilities}
            except TypeError:
                remaining = set()
        leaked = remaining & FORBIDDEN_TOOL_NAMES
        if leaked:
            raise RuntimeError(f"forbidden manager tools leaked: {sorted(leaked)}")

    def uninit(self, agent) -> None:
        return


__all__ = ["ALLOWED_TOOL_NAMES", "FORBIDDEN_TOOL_NAMES", "ManagerCapabilityRail"]
