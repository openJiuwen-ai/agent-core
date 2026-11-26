# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Extension package loading: control plane, management events, and publishing.

The control plane reads manifests, orders packages, copies files into the
install directory, loads extension code, and remembers which packages are
active. ``Registry`` is the entry point business code calls: it assembles the
nine package management events on an EventBus, then triggers install, reload,
and uninstall. ``Runtime`` writes a loaded package to the Bus in one pass.
"""

from openjiuwen.core.extension.bundle import (
    BUNDLE_FLAG,
    MAX_BUNDLE_MEMBERS,
    BundleMember,
    BundleSpec,
    bundle_version_dir,
    dependency_levels,
    is_bundle_document,
    read_bundle_json,
    read_bundle_spec,
)
from openjiuwen.core.extension.install_report import PackageInstallReport, PackageInstallState
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
    PackageResourceCleanup,
    PrepareResult,
    PublishResult,
    Runtime,
)
from openjiuwen.core.extension.schema import (
    ActivationReport,
    ActivePackageRecord,
    ExtensionPlan,
    Manifest,
    McpServerSpec,
)
from openjiuwen.core.extension.selection import ResolveInstallResult, resolve_install_set

__all__ = [
    "ActivationCandidate",
    "ActivationReport",
    "ActivePackageRecord",
    "BUNDLE_FLAG",
    "BundleMember",
    "BundleSpec",
    "ExtensionFailure",
    "ExtensionPlan",
    "ExtensionRegistrar",
    "HandlerDeclaration",
    "MAX_BUNDLE_MEMBERS",
    "ManagementContext",
    "Manifest",
    "McpServerSpec",
    "OperationError",
    "Orchestrator",
    "PackageInstallReport",
    "PackageInstallState",
    "PackageManager",
    "PackageOperationResult",
    "PackageResourceCleanup",
    "PrepareResult",
    "PublishResult",
    "Registry",
    "ResolveInstallResult",
    "Runtime",
    "bundle_version_dir",
    "dependency_levels",
    "dispatch_management_event",
    "ensure_management_events",
    "is_bundle_document",
    "operation_error",
    "order_key",
    "read_bundle_json",
    "read_bundle_spec",
    "resolve_install_set",
]
