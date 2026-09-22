# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host-invoked security hook before committing prepared skill content.

Installers call this after staging and before replacing/registering a skill.
The hook deliberately has no dependency on an Agent instance or vendor API.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from openjiuwen.core.common.logging import logger
from openjiuwen.harness.security.permission_engine.models import PermissionLevel, PermissionResult


@dataclass(frozen=True)
class SkillInstallContext:
    operation_id: str
    skill_name: str
    source: str
    staged_path: Path
    destination: Path
    metadata: dict[str, Any] = field(default_factory=dict)


BeforeSkillInstallHook = Callable[[SkillInstallContext], Awaitable[PermissionResult]]


async def before_skill_install(
    context: SkillInstallContext, hook: BeforeSkillInstallHook | None = None,
) -> PermissionResult:
    """Evaluate a prepared installation; caller must enforce ASK/DENY before commit.

    No hook preserves legacy installers. A configured hook failing or returning an
    unresolved/invalid decision requires local confirmation, never implicit allow.
    Cancellation is propagated to the installer.
    """
    if hook is None:
        return PermissionResult(PermissionLevel.ALLOW, "skill_install:no_hook")
    try:
        result = await hook(context)
        if isinstance(result, PermissionResult) and result.permission in (
            PermissionLevel.ALLOW, PermissionLevel.DENY, PermissionLevel.ASK,
        ):
            return result
    except Exception:
        logger.warning("[SkillInstall] before_install hook failed", exc_info=True)
    return PermissionResult(PermissionLevel.ASK, "skill_install:unavailable", "Installation requires approval")


__all__ = ["BeforeSkillInstallHook", "SkillInstallContext", "before_skill_install"]
