# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Persistent Agent workspace access contracts."""

from .access import (
    WorkspaceAccessPort,
    WorkspaceReadScope,
    WorkspaceWriteAccessPort,
    WorkspaceWriteScope,
    validate_read_scope,
    validate_write_scope,
)
from .path_policy import (
    ResolvedWorkspacePath,
    WorkspacePathError,
    observe_version,
    resolve_read_path,
    resolve_write_path,
)

__all__ = [
    "ResolvedWorkspacePath",
    "WorkspaceAccessPort",
    "WorkspacePathError",
    "WorkspaceReadScope",
    "WorkspaceWriteAccessPort",
    "WorkspaceWriteScope",
    "observe_version",
    "resolve_read_path",
    "resolve_write_path",
    "validate_read_scope",
    "validate_write_scope",
]
