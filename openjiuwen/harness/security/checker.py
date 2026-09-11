# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compatibility shim. Prefer ``openjiuwen.harness.security.file_guard.ExternalDirectoryChecker``."""

from openjiuwen.harness.security.permission_engine.fileguard.file_guard import (
    ExternalDirectoryChecker,
)

__all__ = ["ExternalDirectoryChecker"]
