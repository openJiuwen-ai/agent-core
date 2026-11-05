# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared helpers for extension unit tests."""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from openjiuwen.core.events.bus import ExtensionEventBus
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, Event
from openjiuwen.core.extension.registry import Registry
from openjiuwen.core.extension.schema.manifest import ExtensionDecl, Manifest, PackageDependency
from openjiuwen.core.extension.schema.records import ActivePackageRecord

BUSINESS_EVENT = "tool.call"


def make_manifest(
    package_id: str,
    version: str = "1.0.0",
    requires: tuple[PackageDependency, ...] = (),
    host: str | None = None,
    extensions: tuple[ExtensionDecl, ...] = (),
    display_name: str | None = None,
) -> Manifest:
    return Manifest(
        schema_version=1,
        package_id=package_id,
        version=version,
        display_name=display_name,
        description=None,
        requires_host=host,
        requires_packages=requires,
        extensions=extensions,
    )


def make_active(
    package_id: str,
    version: str = "1.0.0",
    requires: tuple[PackageDependency, ...] = (),
) -> ActivePackageRecord:
    return ActivePackageRecord(
        package_id=package_id,
        version=version,
        root=Path(package_id),
        generation=1,
        requires_packages=requires,
    )


def write_package(
    root: Path,
    package_id: str,
    version: str = "1.0.0",
    module: str = "extensions/ext/extension.py",
    *,
    requires: tuple[PackageDependency, ...] = (),
    value: str = "1",
    body: str | None = None,
) -> None:
    """Write a one-extension package that registers a USER handler.

    ``body`` replaces the generated entry file, for tests that need a package
    that fails to load or registers more than one handler.
    """
    extension = root / module
    extension.parent.mkdir(parents=True, exist_ok=True)
    extension.write_text(
        body
        if body is not None
        else (
            f"VALUE = {value}\n\n"
            "def create_extension():\n"
            "    return Extension()\n\n"
            "class Extension:\n"
            "    def register(self, registrar):\n"
            "        def handler(event, ops, next_handler):\n"
            "            return VALUE\n"
            "        registrar.register_event_handler("
            f"'{BUSINESS_EVENT}', handler, {{'tool': 'Bash'}}, 'user')\n"
        ),
        encoding="utf-8",
    )
    manifest: dict[str, object] = {
        "package_type": "extension",
        "schemaVersion": 1,
        "package": {"id": package_id, "version": version, "displayName": "Demo"},
        "extensions": [{"id": "ext", "runtime": "backend", "module": module}],
    }
    if requires:
        manifest["requires"] = {
            "packages": [{"id": item.package_id, "version": item.version} for item in requires]
        }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def match_event_input(event_input: object, match_pattern: Mapping[str, object]) -> bool:
    """Match a business event the way a tool event would."""
    if not match_pattern:
        return True
    return isinstance(event_input, Mapping) and all(
        event_input.get(key) == value for key, value in match_pattern.items()
    )


async def echo_core_handler(event: Event, ops: object, event_next: object) -> object:
    """Default handler of the business event: proves no extension handled it."""
    del ops, event_next
    return ("core", event.event_input)


async def build_bus(business_events: Sequence[str] = (BUSINESS_EVENT,)) -> ExtensionEventBus:
    """Return a bus whose ``ops.build`` has already succeeded.

    Every other trigger is rejected until ``ops.build`` runs, and a package can
    only publish handlers for event types the bus already knows.
    """
    bus = ExtensionEventBus()
    for event_type in business_events:
        bus.register_event_type(event_type, match_event_input, echo_core_handler)
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    return bus


async def build_registry(
    install_dir: Path,
    *,
    host_version: str | None = None,
    business_events: Sequence[str] = (BUSINESS_EVENT,),
) -> tuple[Registry, ExtensionEventBus]:
    """Return a ready Registry and its bus."""
    bus = ExtensionEventBus()
    for event_type in business_events:
        bus.register_event_type(event_type, match_event_input, echo_core_handler)
    registry = Registry()
    await registry.init(bus)
    await registry.open_install_dir(install_dir, report_path=install_dir.parent / (install_dir.name + "-report.yaml"), host_version=host_version)
    return registry, bus


async def call_business_event(
    bus: ExtensionEventBus,
    payload: Mapping[str, object] | None = None,
) -> object:
    """Trigger the business event and return what the chain produced."""
    return await bus.trigger(Event(BUSINESS_EVENT, dict(payload or {"tool": "Bash"})))
