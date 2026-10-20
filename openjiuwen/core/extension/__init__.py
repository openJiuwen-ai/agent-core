# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Package loading that does not call the EventBus.

Registry, management events, and publishing handlers are waiting on the
event implementation. The pieces here read manifests, order packages, copy
files into the install directory, load extension code, and remember which
packages are active.
"""

from openjiuwen.core.extension.errors import ExtensionFailure, OperationError, operation_error
from openjiuwen.core.extension.orchestrator import Orchestrator
from openjiuwen.core.extension.ordering import order_key
from openjiuwen.core.extension.package_manager import PackageManager
from openjiuwen.core.extension.runtime import (
    ActivationCandidate,
    ExtensionRegistrar,
    HandlerDeclaration,
    PrepareResult,
    Runtime,
)
from openjiuwen.core.extension.schema import (
    ActivationReport,
    ActivePackageRecord,
    ExtensionPlan,
    Manifest,
)
from openjiuwen.core.extension.selection import ResolveInstallResult, resolve_install_set

__all__ = [
    "ActivationCandidate",
    "ActivationReport",
    "ActivePackageRecord",
    "ExtensionFailure",
    "ExtensionPlan",
    "ExtensionRegistrar",
    "HandlerDeclaration",
    "Manifest",
    "OperationError",
    "Orchestrator",
    "PackageManager",
    "PrepareResult",
    "ResolveInstallResult",
    "Runtime",
    "operation_error",
    "order_key",
    "resolve_install_set",
]
