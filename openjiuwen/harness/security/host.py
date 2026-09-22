# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compatibility shim. Prefer ``openjiuwen.harness.security.permission_engine.host``."""

from openjiuwen.harness.security.permission_engine.host import *  # noqa: F403
from openjiuwen.harness.security.permission_engine.host import (
    PermissionConfirmationRequest,
    PermissionEvaluationRequest,
    PermissionEvaluatedHook,
    PermissionConfirmationResult,
    PermissionSceneHook,
    PermissionSceneHookInput,
    RequestPermissionConfirmationHook,
    ToolPermissionHost,
)

__all__ = [
    "PermissionEvaluationRequest",
    "PermissionEvaluatedHook",
    "PermissionConfirmationRequest",
    "PermissionConfirmationResult",
    "PermissionSceneHook",
    "PermissionSceneHookInput",
    "RequestPermissionConfirmationHook",
    "ToolPermissionHost",
]
