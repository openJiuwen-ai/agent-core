# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compatibility shim. Prefer ``openjiuwen.harness.security.permission_engine.core``."""

from openjiuwen.harness.security.permission_engine.core import *  # noqa: F403
from openjiuwen.harness.security.permission_engine.core import (
    PermissionEngine,
    build_permission_interrupt_rail,
)

__all__ = [
    "PermissionEngine",
    "build_permission_interrupt_rail",
]
