# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Build an ordered plan for one package.

Executing the plan is an ``extension.apply`` / ``extension.rollback`` event.
Those events need the EventBus, so this module only builds the plan.
"""

import uuid
from pathlib import Path
from typing import Literal

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.events.types import PackageMetadata
from openjiuwen.core.extension.errors import ExtensionFailure
from openjiuwen.core.extension.ordering import topological_order
from openjiuwen.core.extension.schema.manifest import Manifest
from openjiuwen.core.extension.schema.mcp import load_mcp_config
from openjiuwen.core.extension.schema.records import (
    ActivePackageRecord,
    ExtensionPlan,
    ExtensionRef,
)


class Orchestrator:
    """Turn one manifest into the ordered steps for a single package."""

    @staticmethod
    def build_plan(
        package_root: Path,
        manifest: Manifest,
        operation: Literal["install", "reload", "uninstall"],
        previous: ActivePackageRecord | None = None,
    ) -> ExtensionPlan:
        """Return a plan whose extensions are already ordered.

        Install and reload load dependencies first. Uninstall reverses that
        order. The generation counter advances on install and reload. Uninstall
        keeps the generation it is removing.

        Raises:
            ExtensionFailure: Extension dependencies cycle, or reload/uninstall
                has no active record to work from.
        """
        if operation not in ("install", "reload", "uninstall"):
            raise ExtensionFailure(
                StatusCode.EXTENSION_LOAD_FAILED,
                reason=f"unsupported plan operation: {operation!r}",
            )
        ordered, remaining = topological_order(
            manifest.extensions,
            identifier=lambda extension: extension.id,
            dependencies=lambda extension: extension.requires,
        )
        if remaining:
            names = ", ".join(remaining)
            raise ExtensionFailure(
                StatusCode.EXTENSION_DEPENDENCY_CYCLE,
                packages=names,
            )
        if operation == "uninstall":
            if previous is None:
                raise ExtensionFailure(
                    StatusCode.EXTENSION_PACKAGE_NOT_INSTALLED,
                    package_id=manifest.package_id,
                    reason="package is not active",
                )
            ordered = list(reversed(ordered))
            previous_generation: int | None = previous.generation
            # Spec: uninstall keeps the generation it removes (no increment).
            target_generation = previous.generation
        elif operation == "reload":
            if previous is None:
                raise ExtensionFailure(
                    StatusCode.EXTENSION_PACKAGE_NOT_INSTALLED,
                    package_id=manifest.package_id,
                    reason="package is not active; cannot reload",
                )
            previous_generation = previous.generation
            target_generation = previous.generation + 1
        else:
            # install never replaces an already-active package (PACKAGE_EXISTS).
            if previous is not None:
                raise ExtensionFailure(
                    StatusCode.EXTENSION_PACKAGE_EXISTS,
                    package_id=manifest.package_id,
                    display_name=manifest.display_name or manifest.package_id,
                )
            previous_generation = None
            target_generation = 1

        return ExtensionPlan(
            plan_id=uuid.uuid4().hex,
            operation=operation,
            package=PackageMetadata(
                package_id=manifest.package_id,
                package_version=manifest.version,
                root=package_root.resolve(),
            ),
            previous_generation=previous_generation,
            target_generation=target_generation,
            extensions=tuple(
                ExtensionRef(
                    id=extension.id,
                    runtime=extension.runtime,
                    module=extension.module,
                )
                for extension in ordered
            ),
            mcp_servers=(
                load_mcp_config(package_root, manifest.package_id)
                if operation != "uninstall"
                else ()
            ),
        )
