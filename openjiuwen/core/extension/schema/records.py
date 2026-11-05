# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Plan, report, and active-package records for package loading."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from openjiuwen.core.events.types import PackageMetadata
from openjiuwen.core.extension.errors import OperationError
from openjiuwen.core.extension.schema.manifest import PackageDependency
from openjiuwen.core.extension.schema.mcp import McpServerSpec


@dataclass(frozen=True, slots=True)
class ExtensionRef:
    """One extension the plan will load, already ordered."""

    id: str
    runtime: str
    module: str


@dataclass(frozen=True, slots=True)
class ExtensionPlan:
    """Inputs fixed for one install, reload, or uninstall of one package.

    ``package`` is Events ``PackageMetadata`` (id, version, and package root).
    """

    plan_id: str
    operation: Literal["install", "reload", "uninstall"]
    package: PackageMetadata
    previous_generation: int | None
    target_generation: int
    extensions: tuple[ExtensionRef, ...]
    mcp_servers: tuple[McpServerSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class ActivationReport:
    """Success or failure for one package. Event errors can still be raised.

    There is no SKIPPED status. A package that is not loaded because a
    dependency failed is reported as FAILED with ``DEPENDENCY_NOT_MET``.
    """

    plan_id: str
    package_id: str
    operation: str
    status: Literal["SUCCESS", "FAILED"]
    previous_generation: int | None
    current_generation: int | None
    error: OperationError | None = None


@dataclass(frozen=True, slots=True)
class ActivePackageRecord:
    """Facts later installs consult. This is not the EventBus registry."""

    package_id: str
    version: str
    root: Path
    generation: int
    requires_packages: tuple[PackageDependency, ...]
