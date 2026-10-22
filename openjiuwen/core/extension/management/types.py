# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Event names, inputs, and outputs of the nine package management events.

Every request carries the bus the operation runs on. A Core Handler uses it to
find the Registry context bound at ``init`` and to trigger the sub-events the
architecture describes. A request object is the only input these events
accept, so a caller cannot hand in a plan the Runtime never produced.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from openjiuwen.core.events.protocol import ExtensionEventBusProtocol
from openjiuwen.core.events.types import HandlerId
from openjiuwen.core.extension.errors import OperationError
from openjiuwen.core.extension.package_manager import OpenedSource
from openjiuwen.core.extension.runtime import ActivationCandidate
from openjiuwen.core.extension.schema.manifest import Manifest
from openjiuwen.core.extension.schema.records import (
    ActivationReport,
    ActivePackageRecord,
    ExtensionPlan,
)
from openjiuwen.core.extension.selection import RejectedPackage

PACKAGE_PARSE_EVENT = "package.parse"
PACKAGE_RESOLVE_EVENT = "package.resolve"
PACKAGE_QUERY_EVENT = "package.query"
PACKAGE_INSTALL_EVENT = "package.install"
PACKAGE_UNINSTALL_EVENT = "package.uninstall"
PACKAGE_RELOAD_EVENT = "package.reload"
EXTENSION_PLAN_EVENT = "extension.plan"
EXTENSION_APPLY_EVENT = "extension.apply"
EXTENSION_ROLLBACK_EVENT = "extension.rollback"

MANAGEMENT_EVENTS = (
    PACKAGE_PARSE_EVENT,
    PACKAGE_RESOLVE_EVENT,
    PACKAGE_QUERY_EVENT,
    PACKAGE_INSTALL_EVENT,
    PACKAGE_UNINSTALL_EVENT,
    PACKAGE_RELOAD_EVENT,
    EXTENSION_PLAN_EVENT,
    EXTENSION_APPLY_EVENT,
    EXTENSION_ROLLBACK_EVENT,
)

PlanOperation = Literal["install", "reload", "uninstall"]


@dataclass(frozen=True, slots=True)
class ParsedPackage:
    """A package whose source and manifest were read, but not yet resolved.

    ``opened`` keeps the temporary directory of a zip source alive until the
    install handler has copied the files into the install store.
    """

    manifest: Manifest
    root: Path
    source: Path
    opened: OpenedSource | None = None


@dataclass(frozen=True, slots=True)
class ParseRequest:
    """Input of ``package.parse``: one local directory or zip."""

    bus: ExtensionEventBusProtocol
    source: Path


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Output of ``package.parse``: a candidate package or why it was refused."""

    source: Path
    package: ParsedPackage | None = None
    error: OperationError | None = None


@dataclass(frozen=True, slots=True)
class ResolveRequest:
    """Input of ``package.resolve`` for an install batch or one uninstall."""

    bus: ExtensionEventBusProtocol
    operation: Literal["install", "uninstall"]
    candidates: tuple[Manifest, ...] = ()
    package_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResolveResult:
    """Output of ``package.resolve``.

    Install fills ``ordered`` / ``rejected`` and sets ``fatal`` when the whole
    batch stops. Uninstall sets ``error`` when the package may not be removed.
    """

    operation: str
    ordered: tuple[Manifest, ...] = ()
    rejected: tuple[RejectedPackage, ...] = ()
    fatal: OperationError | None = None
    error: OperationError | None = None


@dataclass(frozen=True, slots=True)
class PlanRequest:
    """Input of ``extension.plan``: one resolved package and the operation."""

    bus: ExtensionEventBusProtocol
    operation: PlanOperation
    manifest: Manifest
    package_root: Path


@dataclass(frozen=True, slots=True)
class PlanResult:
    """Output of ``extension.plan``: an ordered plan or why it was refused."""

    plan: ExtensionPlan | None = None
    error: OperationError | None = None


@dataclass(frozen=True, slots=True)
class ApplyRequest:
    """Input of ``extension.apply``: a plan the Orchestrator produced.

    ``store_copy_created`` marks an install copy this operation created. Only
    such a copy may be cleaned up when the package never reaches the Bus.
    """

    bus: ExtensionEventBusProtocol
    plan: ExtensionPlan
    manifest: Manifest
    store_copy_created: bool = False


@dataclass(frozen=True, slots=True)
class RollbackRequest:
    """Input of ``extension.rollback``: the candidate of a failed install."""

    bus: ExtensionEventBusProtocol
    plan: ExtensionPlan
    candidate: ActivationCandidate | None = None
    error: OperationError | None = None
    store_copy_created: bool = False


@dataclass(frozen=True, slots=True)
class RollbackResult:
    """Output of ``extension.rollback``: what was cleaned up and what remains."""

    plan_id: str
    package_id: str
    released_modules: tuple[str, ...] = ()
    removed_store_copy: bool = False
    error: OperationError | None = None
    residue: OperationError | None = None


@dataclass(frozen=True, slots=True)
class QueryRequest:
    """Input of ``package.query``: every package, or one package id."""

    bus: ExtensionEventBusProtocol
    package_id: str | None = None


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Output of ``package.query``: facts the Runtime and store can confirm."""

    active: tuple[ActivePackageRecord, ...] = ()
    installed: tuple[Path, ...] = ()
    inflight: dict[str, int] = field(default_factory=dict)
    published: dict[str, tuple[HandlerId, ...]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InstallRequest:
    """Input of ``package.install``: the local sources to install."""

    bus: ExtensionEventBusProtocol
    paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class UninstallRequest:
    """Input of ``package.uninstall``: the active package to remove."""

    bus: ExtensionEventBusProtocol
    package_id: str


@dataclass(frozen=True, slots=True)
class ReloadRequest:
    """Input of ``package.reload``: the active package to hot-switch."""

    bus: ExtensionEventBusProtocol
    package_id: str


@dataclass(frozen=True, slots=True)
class PackageOperationResult:
    """One ``ActivationReport`` per package a root management event touched."""

    operation: str
    reports: tuple[ActivationReport, ...] = ()

    @property
    def succeeded(self) -> bool:
        """True when every report of this operation is SUCCESS."""
        return bool(self.reports) and all(item.status == "SUCCESS" for item in self.reports)

    def report_for(self, package_id: str) -> ActivationReport | None:
        """Return the report of one package, or None when it was not touched."""
        for report in self.reports:
            if report.package_id == package_id:
                return report
        return None
