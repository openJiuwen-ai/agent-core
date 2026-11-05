# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Data carried between the package loader stages."""

from openjiuwen.core.extension.schema.manifest import ExtensionDecl, Manifest, PackageDependency
from openjiuwen.core.extension.schema.mcp import McpServerSpec
from openjiuwen.core.extension.schema.records import (
    ActivationReport,
    ActivePackageRecord,
    ExtensionPlan,
    ExtensionRef,
)

__all__ = [
    "ActivationReport",
    "ActivePackageRecord",
    "ExtensionDecl",
    "ExtensionPlan",
    "ExtensionRef",
    "Manifest",
    "McpServerSpec",
    "PackageDependency",
]
