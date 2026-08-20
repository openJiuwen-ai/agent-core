# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compatibility shim. Prefer ``openjiuwen.harness.security.permission_engine.core``."""

from openjiuwen.harness.security.permission_engine.core import (
    build_permission_interrupt_rail,
)

__all__ = ["build_permission_interrupt_rail"]
