"""SysOperationRail that actually enforces bash_deny_patterns on Windows.

Upstream `SysOperationRail.init()` threads `bash_deny_patterns` into its
`BashTool`, but on Windows it also registers a separate `PowerShellTool`
(`os.name == "nt"`) with no deny patterns at all — a denied bash command is
still reachable via `powershell`. Subclassing (never monkeypatching) per
docs/openjiuwen_conventions.md: after the base class registers its tools
normally, re-register PowerShellTool with the same deny patterns so both
shells are actually covered.
"""

from __future__ import annotations

import os

from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
from openjiuwen.harness.tools import PowerShellTool


class GuardedSysOperationRail(SysOperationRail):
    def init(self, agent) -> None:
        super().init(agent)
        if os.name != "nt" or not self._bash_deny_patterns or not self.tools:
            return

        lang = agent.system_prompt_builder.language
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        guarded_powershell = PowerShellTool(
            self.sys_operation,
            lang,
            agent_id=agent_id,
            deny_patterns=self._bash_deny_patterns,
        )
        for i, tool in enumerate(self.tools):
            if isinstance(tool, PowerShellTool):
                # Same card name as the unguarded instance registered by
                # super().init() — add_ability rebinds it in place.
                agent.ability_manager.add_ability(guarded_powershell.card, guarded_powershell)
                self.tools[i] = guarded_powershell
                break


__all__ = ["GuardedSysOperationRail"]
