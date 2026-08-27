# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compatibility shim. Prefer ``openjiuwen.harness.security.permission_engine.models``."""

from openjiuwen.harness.security.permission_engine.models import *  # noqa: F403
from openjiuwen.harness.security.permission_engine.models import (
    ApprovalOverrideEntry,
    FileGuardDefaults,
    FileGuardPathEntry,
    FileGuardSection,
    NetGuardSection,
    ShellGuardSection,
    PermissionConfirmResponse,
    PermissionLevel,
    PermissionResult,
    PermissionsSection,
)

__all__ = [
    "ApprovalOverrideEntry",
    "FileGuardDefaults",
    "FileGuardPathEntry",
    "FileGuardSection",
    "NetGuardSection",
    "ShellGuardSection",
    "PermissionConfirmResponse",
    "PermissionLevel",
    "PermissionResult",
    "PermissionsSection",
]
