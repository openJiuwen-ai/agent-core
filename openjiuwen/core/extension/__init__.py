# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Extension package loading: control plane, management events, and publishing.

The control plane reads manifests, orders packages, copies files into the
install directory, loads extension code, and remembers which packages are
active. ``Registry`` is the entry point business code calls: it assembles the
nine package management events on an EventBus, then triggers install, reload,
and uninstall. ``Runtime`` writes a loaded package to the Bus in one pass.
"""

from openjiuwen.core.extension.errors import ExtensionFailure, OperationError, operation_error
from openjiuwen.core.extension.management import (
    ManagementContext,
    PackageOperationResult,
    dispatch_management_event,
    ensure_management_events,
)
from openjiuwen.core.extension.orchestrator import Orchestrator
from openjiuwen.core.extension.ordering import order_key
from openjiuwen.core.extension.package_manager import PackageManager
from openjiuwen.core.extension.registry import Registry
from openjiuwen.core.extension.runtime import (
    ActivationCandidate,
    ExtensionRegistrar,
    HandlerDeclaration,
    PrepareResult,
    PublishResult,
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
    "ManagementContext",
    "Manifest",
    "OperationError",
    "Orchestrator",
    "PackageManager",
    "PackageOperationResult",
    "PrepareResult",
    "PublishResult",
    "Registry",
    "ResolveInstallResult",
    "Runtime",
    "dispatch_management_event",
    "ensure_management_events",
    "operation_error",
    "order_key",
    "resolve_install_set",
]
