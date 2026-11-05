# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""The management entry point business code calls.

``init`` receives an EventBus that already has the nine management events.
It registers only the host callbacks. The install directory and host version
are passed afterwards, through ``open_install_dir``, which does not install.
The convenience methods then trigger the matching root event.
"""

from collections.abc import Callable, Sequence
from pathlib import Path

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.events.protocol import ExtensionEventBusProtocol
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, Event
from openjiuwen.core.extension.data import JsonState, JsonStore, StateDependencies
from openjiuwen.core.extension.errors import ExtensionFailure
from openjiuwen.core.extension.management import (
    PACKAGE_INSTALL_EVENT,
    PACKAGE_RELOAD_EVENT,
    PACKAGE_UNINSTALL_EVENT,
    InstallRequest,
    ManagementContext,
    PackageOperationResult,
    ReloadRequest,
    UninstallRequest,
    bind_context,
    context_for,
    replace_context,
    unbind_context,
)
from openjiuwen.core.extension.orchestrator import Orchestrator
from openjiuwen.core.extension.package_manager import PackageManager
from openjiuwen.core.extension.runtime import PackageResourceCleanup, Runtime
from openjiuwen.core.extension.schema.records import ActivationReport


class Registry:
    """One EventBus, one install directory, one set of active packages."""

    def __init__(self) -> None:
        self._context: ManagementContext | None = None

    @property
    def is_ready(self) -> bool:
        """True once ``init`` has bound this Registry to a bus."""
        return self._context is not None

    @property
    def context(self) -> ManagementContext:
        """The components bound at ``init``.

        Raises:
            ExtensionFailure: ``init`` has not run yet.
        """
        return self._require_ready()

    @property
    def runtime(self) -> Runtime:
        """The Runtime that holds the active records of this Registry."""
        return self._require_ready().runtime

    @property
    def package_manager(self) -> PackageManager:
        """The install store of this Registry."""
        manager = self._require_store().package_manager
        if manager is None:
            raise ExtensionFailure(
                StatusCode.EXTENSION_LOAD_FAILED,
                reason="install directory has not been provided",
            )
        return manager

    async def init(
        self,
        event_bus: ExtensionEventBusProtocol,
        *,
        host_event_types: Callable[[ExtensionEventBusProtocol], None] | None = None,
        host_handlers: Callable[[ExtensionEventBusProtocol], None] | None = None,
        store: JsonStore | None = None,
        states: JsonState | None = None,
        state_dependencies: StateDependencies | None = None,
        cleanup_callbacks: Sequence[PackageResourceCleanup] = (),
    ) -> None:
        """Bind one EventBus and register the host's own events and handlers.

        The nine package-management events are already on the bus. This method
        does not register them again, read a manifest, or install a package.
        After the host callbacks return, it triggers ``ops.build`` once so
        later management events are accepted. The caller does not trigger it.
        ``store`` has an explicit host-selected directory. Resource cleanup
        callbacks run in order before Core deletes package store/state data.
        ``states`` and ``state_dependencies`` bind durable state and opaque
        build dependencies before capability construction.

        Raises:
            ExtensionFailure: This Registry is already initialized, or the bus
                already serves another Registry.
        """
        if self._context is not None:
            raise ExtensionFailure(
                StatusCode.EXTENSION_LOAD_FAILED,
                reason="registry is already initialized",
            )
        callbacks = tuple(cleanup_callbacks)
        dependencies = state_dependencies if state_dependencies is not None else StateDependencies()
        if states is not None:

            async def cleanup_state(package_id: str, generation: int, reason: str) -> None:
                del generation
                await states.cleanup_package(package_id, reason=reason)
                if reason == "uninstall":
                    dependencies.drop_package(package_id)

            callbacks = (*callbacks, cleanup_state)
        if store is not None:

            async def cleanup_store(package_id: str, generation: int, reason: str) -> None:
                del generation
                await store.cleanup_package(package_id, reason=reason)

            callbacks = (*callbacks, cleanup_store)
        runtime = Runtime(cleanup_callbacks=callbacks)
        context = ManagementContext(
            bus=event_bus,
            package_manager=None,
            orchestrator=Orchestrator(),
            runtime=runtime,
        )
        bind_context(context)
        store_bound = False
        state_bound = False
        # Imported lazily: ops_impl.store imports extension.data, and this module
        # is loaded while openjiuwen.core.extension.__init__ is still running.
        from openjiuwen.core.events.ops_impl.store import configure_store, unconfigure_store
        from openjiuwen.core.events.ops_impl.state import configure_state, unconfigure_state

        try:
            if states is not None:
                configure_state(
                    event_bus, states, dependencies=dependencies,
                    begin_calls=runtime.begin_calls, end_calls=runtime.end_calls,
                )
                state_bound = True
            if store is not None:
                configure_store(event_bus, store, begin_calls=runtime.begin_calls, end_calls=runtime.end_calls)
                store_bound = True
            if host_event_types is not None:
                host_event_types(event_bus)
            if host_handlers is not None:
                host_handlers(event_bus)
            await event_bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
        except BaseException:
            if state_bound:
                unconfigure_state(event_bus)
            if store_bound:
                unconfigure_store(event_bus)
            unbind_context(event_bus)
            raise
        self._context = context

    def open_install_dir(
        self,
        install_dir: Path,
        *,
        host_version: str | None = None,
    ) -> tuple[Path, ...]:
        """Remember the install directory and list the packages already in it.

        Call this after ``init``. It does not copy files or install packages.
        ``host_version`` is the version compared with ``requires.host``.

        Returns:
            Package directories ``<install_dir>/<package.id>/<version>`` that
            contain a manifest. The caller passes them to ``install``.

        Raises:
            ExtensionFailure: ``init`` has not run, or a directory is already set.
        """
        context = self._require_ready()
        if context.package_manager is not None:
            raise ExtensionFailure(
                StatusCode.EXTENSION_LOAD_FAILED,
                reason="install directory is already set",
            )
        manager = PackageManager(Path(install_dir))
        replace_context(
            ManagementContext(
                bus=context.bus,
                package_manager=manager,
                orchestrator=context.orchestrator,
                runtime=context.runtime,
                host_version=host_version,
            )
        )
        self._context = context_for(context.bus)
        return tuple(manager.list_installed())

    async def install(
        self,
        source: Path | str | Sequence[Path | str],
    ) -> tuple[ActivationReport, ...]:
        """Trigger ``package.install`` for one source or a batch of sources."""
        context = self._require_store()
        result = await self._trigger(
            PACKAGE_INSTALL_EVENT,
            InstallRequest(bus=context.bus, paths=_as_paths(source)),
        )
        return result.reports

    async def uninstall(self, package_id: str) -> ActivationReport:
        """Trigger ``package.uninstall`` for one active package."""
        context = self._require_store()
        result = await self._trigger(
            PACKAGE_UNINSTALL_EVENT,
            UninstallRequest(bus=context.bus, package_id=package_id),
        )
        return _one_report(result)

    async def reload(self, package_id: str) -> ActivationReport:
        """Trigger ``package.reload`` for one active package."""
        context = self._require_store()
        result = await self._trigger(
            PACKAGE_RELOAD_EVENT,
            ReloadRequest(bus=context.bus, package_id=package_id),
        )
        return _one_report(result)

    async def _trigger(self, event_type: str, request: object) -> PackageOperationResult:
        context = self._require_ready()
        result = await context.bus.trigger(Event(event_type, request))
        if not isinstance(result, PackageOperationResult):
            raise ExtensionFailure(
                StatusCode.EXTENSION_LOAD_FAILED,
                reason=f"{event_type} returned no package operation result",
            )
        return result

    def _require_ready(self) -> ManagementContext:
        if self._context is None:
            raise ExtensionFailure(
                StatusCode.EXTENSION_LOAD_FAILED,
                reason="registry is not initialized",
            )
        return self._context

    def _require_store(self) -> ManagementContext:
        context = self._require_ready()
        if context.package_manager is None:
            raise ExtensionFailure(
                StatusCode.EXTENSION_LOAD_FAILED,
                reason="install directory has not been provided",
            )
        return context


def _as_paths(source: Path | str | Sequence[Path | str]) -> tuple[Path, ...]:
    if isinstance(source, (str, Path)):
        return (Path(source),)
    return tuple(Path(item) for item in source)


def _one_report(result: PackageOperationResult) -> ActivationReport:
    if len(result.reports) != 1:
        raise ExtensionFailure(
            StatusCode.EXTENSION_LOAD_FAILED,
            reason="management event did not return one activation report",
        )
    return result.reports[0]
