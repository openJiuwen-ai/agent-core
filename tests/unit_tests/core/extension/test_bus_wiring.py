# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""EX-04: publishing a candidate to the EventBus, hot switch, and removal."""

import sys
from pathlib import Path

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.events.bus import ExtensionEventBus
from openjiuwen.core.extension.manifest import load_manifest
from openjiuwen.core.extension.orchestrator import Orchestrator
from openjiuwen.core.extension.runtime import ActivationCandidate, Runtime
from openjiuwen.core.extension.schema.manifest import Manifest
from openjiuwen.core.extension.schema.records import ExtensionPlan
from tests.unit_tests.core.extension.conftest import (
    build_bus,
    call_business_event,
    write_package,
)

_METADATA_BODY = (
    "def create_extension():\n"
    "    return Extension()\n\n"
    "class Extension:\n"
    "    def register(self, registrar):\n"
    "        def handler(event, ops, next_handler):\n"
    "            package = ops.package_metadata\n"
    "            return (package.package_id, package.package_version, str(package.root))\n"
    "        registrar.register_event_handler('tool.call', handler, {}, 'user')\n"
)

_HALF_SET_BODY = (
    "def create_extension():\n"
    "    return Extension()\n\n"
    "class Extension:\n"
    "    def register(self, registrar):\n"
    "        def handler(event, ops, next_handler):\n"
    "            return 'published'\n"
    "        registrar.register_event_handler('tool.call', handler, {}, 'user')\n"
    "        registrar.register_event_handler('tool.unknown', handler, {}, 'user')\n"
)

_ASYNC_BODY = (
    "def create_extension():\n"
    "    return Extension()\n\n"
    "class Extension:\n"
    "    def register(self, registrar):\n"
    "        async def handler(event, ops, next_handler):\n"
    "            return 'async-handler'\n"
    "        registrar.register_event_handler('tool.call', handler, {}, 'user')\n"
)


def _prepare(
    runtime: Runtime,
    root: Path,
    operation: str = "install",
) -> tuple[ActivationCandidate, ExtensionPlan, Manifest]:
    """Build a plan for the package at ``root`` and load its candidate."""
    manifest = load_manifest(root)
    previous = runtime.get_active(manifest.package_id)
    plan = Orchestrator().build_plan(root, manifest, operation, previous)
    prepared = runtime.prepare(plan)
    assert prepared.error is None
    assert prepared.candidate is not None
    return prepared.candidate, plan, manifest


@pytest.mark.asyncio
async def test_publish_makes_the_whole_package_visible_and_records_it(tmp_path: Path) -> None:
    write_package(tmp_path, "demo")
    bus = await build_bus()
    runtime = Runtime()
    candidate, plan, manifest = _prepare(runtime, tmp_path)

    assert await call_business_event(bus) == ("core", {"tool": "Bash"})

    published = runtime.publish(candidate, plan, bus, manifest=manifest)

    assert published.error is None
    assert len(published.handler_ids) == 1
    assert runtime.published_handlers("demo") == published.handler_ids
    assert await call_business_event(bus) == 1

    record = runtime.get_active("demo")
    assert record is not None
    assert record.generation == 1
    assert record.version == "1.0.0"


@pytest.mark.asyncio
async def test_publish_failure_leaves_no_handler_and_drops_the_candidate(tmp_path: Path) -> None:
    write_package(tmp_path, "demo", body=_HALF_SET_BODY)
    bus = await build_bus()
    runtime = Runtime()
    candidate, plan, manifest = _prepare(runtime, tmp_path)
    assert len(candidate.declarations) == 2

    published = runtime.publish(candidate, plan, bus, manifest=manifest)

    assert published.handler_ids == ()
    assert published.error is not None
    assert published.error.status == StatusCode.EXTENSION_LOAD_FAILED
    assert runtime.published_handlers("demo") == ()
    assert runtime.get_active("demo") is None
    assert await call_business_event(bus) == ("core", {"tool": "Bash"})
    assert all(name not in sys.modules for name in candidate.module_names)


@pytest.mark.asyncio
async def test_publish_passes_the_verified_package_metadata(tmp_path: Path) -> None:
    write_package(tmp_path, "demo", version="2.1.0", body=_METADATA_BODY)
    bus = await build_bus()
    runtime = Runtime()
    candidate, plan, manifest = _prepare(runtime, tmp_path)
    expected_root = str(plan.package.root)

    assert runtime.publish(candidate, plan, bus, manifest=manifest).error is None

    assert await call_business_event(bus) == ("demo", "2.1.0", expected_root)


@pytest.mark.asyncio
async def test_publish_wraps_a_synchronous_handler_and_keeps_an_async_one(tmp_path: Path) -> None:
    write_package(tmp_path, "demo", body=_ASYNC_BODY)
    bus = await build_bus()
    runtime = Runtime()
    candidate, plan, manifest = _prepare(runtime, tmp_path)

    assert runtime.publish(candidate, plan, bus, manifest=manifest).error is None

    assert await call_business_event(bus) == "async-handler"


@pytest.mark.asyncio
async def test_replace_published_hot_switches_and_keeps_the_old_callable(tmp_path: Path) -> None:
    write_package(tmp_path, "demo")
    bus = await build_bus()
    runtime = Runtime()
    first, install_plan, manifest = _prepare(runtime, tmp_path)
    assert runtime.publish(first, install_plan, bus, manifest=manifest).error is None
    old_ids = runtime.published_handlers("demo")
    old_handler = first.declarations[0].handler
    assert await call_business_event(bus) == 1

    entry = tmp_path / "extensions" / "ext" / "extension.py"
    entry.write_text(entry.read_text(encoding="utf-8").replace("VALUE = 1", "VALUE = 2"), encoding="utf-8")
    second, reload_plan, reloaded = _prepare(runtime, tmp_path, "reload")
    published = runtime.replace_published(second, reload_plan, bus, manifest=reloaded)

    assert published.error is None
    assert runtime.published_handlers("demo") == published.handler_ids
    assert published.handler_ids != old_ids
    assert await call_business_event(bus) == 2
    assert old_handler(None, None, None) == 1

    record = runtime.get_active("demo")
    assert record is not None
    assert record.generation == 2


@pytest.mark.asyncio
async def test_replace_published_failure_keeps_the_current_handlers(tmp_path: Path) -> None:
    write_package(tmp_path, "demo")
    bus = await build_bus()
    runtime = Runtime()
    first, install_plan, manifest = _prepare(runtime, tmp_path)
    assert runtime.publish(first, install_plan, bus, manifest=manifest).error is None
    old_ids = runtime.published_handlers("demo")

    entry = tmp_path / "extensions" / "ext" / "extension.py"
    entry.write_text(_HALF_SET_BODY, encoding="utf-8")
    second, reload_plan, reloaded = _prepare(runtime, tmp_path, "reload")
    published = runtime.replace_published(second, reload_plan, bus, manifest=reloaded)

    assert published.error is not None
    assert runtime.published_handlers("demo") == old_ids
    assert await call_business_event(bus) == 1
    record = runtime.get_active("demo")
    assert record is not None
    assert record.generation == 1


@pytest.mark.asyncio
async def test_unpublish_removes_the_handlers_and_the_active_record(tmp_path: Path) -> None:
    write_package(tmp_path, "demo")
    bus = await build_bus()
    runtime = Runtime()
    candidate, plan, manifest = _prepare(runtime, tmp_path)
    assert runtime.publish(candidate, plan, bus, manifest=manifest).error is None

    runtime.unpublish("demo", bus)

    assert runtime.published_handlers("demo") == ()
    assert runtime.get_active("demo") is None
    assert await call_business_event(bus) == ("core", {"tool": "Bash"})


@pytest.mark.asyncio
async def test_abort_only_drops_the_candidate_that_was_never_published(tmp_path: Path) -> None:
    published_root = tmp_path / "published"
    pending_root = tmp_path / "pending"
    published_root.mkdir()
    pending_root.mkdir()
    write_package(published_root, "demo")
    write_package(pending_root, "other", value="7")
    bus = await build_bus()
    runtime = Runtime()
    candidate, plan, manifest = _prepare(runtime, published_root)
    assert runtime.publish(candidate, plan, bus, manifest=manifest).error is None
    pending, _, _ = _prepare(runtime, pending_root)

    runtime.abort(pending)

    assert all(name not in sys.modules for name in pending.module_names)
    assert runtime.get_active("other") is None
    assert runtime.published_handlers("other") == ()
    assert runtime.published_handlers("demo") != ()
    assert await call_business_event(bus) == 1


@pytest.mark.asyncio
async def test_each_bus_keeps_its_own_registry(tmp_path: Path) -> None:
    write_package(tmp_path, "demo")
    first: ExtensionEventBus = await build_bus()
    second: ExtensionEventBus = await build_bus()
    runtime = Runtime()
    candidate, plan, manifest = _prepare(runtime, tmp_path)

    assert runtime.publish(candidate, plan, first, manifest=manifest).error is None

    assert await call_business_event(first) == 1
    assert await call_business_event(second) == ("core", {"tool": "Bash"})
