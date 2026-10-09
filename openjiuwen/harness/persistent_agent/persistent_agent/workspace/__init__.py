# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Persistent Agent workspace access contracts."""

from .access import WorkspaceAccessPort, WorkspaceReadScope, validate_read_scope
from .path_policy import ResolvedWorkspacePath, WorkspacePathError, observe_version, resolve_read_path

__all__ = [
    "ResolvedWorkspacePath",
    "WorkspaceAccessPort",
    "WorkspacePathError",
    "WorkspaceReadScope",
    "observe_version",
    "resolve_read_path",
    "validate_read_scope",
]
