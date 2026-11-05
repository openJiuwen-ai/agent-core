# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""EX-03: Registry assembly and the install / uninstall / reload entry points."""

from pathlib import Path

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.events.bus import ExtensionEventBus
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, Event
from openjiuwen.core.extension.errors import ExtensionFailure
from openjiuwen.core.extension.management import MANAGEMENT_EVENTS, QueryRequest, QueryResult
from openjiuwen.core.extension.registry import Registry
from tests.unit_tests.core.extension.conftest import (
    BUSINESS_EVENT,
    build_registry,
    call_business_event,
    echo_core_handler,
    match_event_input,
    write_package,
)


@pytest.mark.asyncio
async def test_init_assembles_events_without_installing_anything(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    write_package(source, "demo")
    store = tmp_path / "store"
    registry, bus = await build_registry(store)

    assert registry.is_ready
    assert registry.runtime.active_packages() == ()
    assert not store.exists()

    queried = await bus.trigger(Event("package.query", QueryRequest(bus=bus)))
    assert isinstance(queried, QueryResult)
    assert queried.active == ()
    assert queried.installed == ()


@pytest.mark.asyncio
async def test_init_triggers_ops_build_so_the_first_install_can_run(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    write_package(source, "demo")
    bus = ExtensionEventBus()
    bus.register_event_type(BUSINESS_EVENT, match_event_input, echo_core_handler)
    registry = Registry()
    await registry.init(bus)
    await registry.open_install_dir(tmp_path / "store", report_path=tmp_path / "report.yaml")

    reports = await registry.install([source])

    assert reports[0].status == "SUCCESS"
    with pytest.raises(ValidationError) as again:
        await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    assert again.value.status is StatusCode.EVENTS_STATE_INVALID


@pytest.mark.asyncio
async def test_init_registers_the_nine_management_events_once(tmp_path: Path) -> None:
    registry, bus = await build_registry(tmp_path / "store")

    assert registry.is_ready
    for event_type in MANAGEMENT_EVENTS:
        with pytest.raises(ValidationError) as duplicate:
            bus.register_event_type(event_type, match_event_input, echo_core_handler)
        assert duplicate.value.status is StatusCode.EVENTS_REGISTRATION_INVALID


@pytest.mark.asyncio
async def test_a_second_init_is_refused_and_the_first_keeps_working(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    write_package(source, "demo")
    registry, bus = await build_registry(tmp_path / "store")

    with pytest.raises(ExtensionFailure) as again:
        await registry.init(bus)
    assert again.value.status == StatusCode.EXTENSION_LOAD_FAILED

    other = Registry()
    with pytest.raises(ExtensionFailure) as taken:
        await other.init(bus)
    assert taken.value.status == StatusCode.EXTENSION_LOAD_FAILED
    assert not other.is_ready

    assert (await registry.install([source]))[0].status == "SUCCESS"


@pytest.mark.asyncio
async def test_methods_of_an_uninitialized_registry_are_refused(tmp_path: Path) -> None:
    registry = Registry()

    assert not registry.is_ready
    with pytest.raises(ExtensionFailure) as caught:
        await registry.install([tmp_path])
    assert caught.value.status == StatusCode.EXTENSION_LOAD_FAILED


@pytest.mark.asyncio
async def test_init_runs_the_host_callback_and_unbinds_when_it_fails(tmp_path: Path) -> None:
    seen: list[object] = []

    def host_register(bus: object) -> None:
        seen.append(bus)

    def failing_register(bus: object) -> None:
        del bus
        raise RuntimeError("host registration failed")

    registry, bus = await build_registry(tmp_path / "store")
    assert seen == []

    broken = Registry()
    with pytest.raises(RuntimeError):
        await broken.init(ExtensionEventBus(), host_handlers=failing_register)
    assert not broken.is_ready

    reused = Registry()
    fresh = ExtensionEventBus()
    await reused.init(fresh, host_event_types=host_register)
    assert seen == [fresh]
    assert reused.is_ready
    assert registry.is_ready


@pytest.mark.asyncio
async def test_install_uninstall_and_reload_report_each_package(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    write_package(source, "demo")
    store = tmp_path / "store"
    registry, bus = await build_registry(store)

    installed = await registry.install(source)
    report = installed[0]
    assert report.package_id == "demo"
    assert report.status == "SUCCESS"
    assert report.current_generation == 1
    assert await call_business_event(bus) == 1
    assert (store / "demo" / "1.0.0" / "manifest.yaml").is_file()

    entry = store / "demo" / "1.0.0" / "extensions" / "ext" / "extension.py"
    entry.write_text(entry.read_text(encoding="utf-8").replace("VALUE = 1", "VALUE = 5"), encoding="utf-8")
    reloaded = await registry.reload("demo")
    assert reloaded.status == "SUCCESS"
    assert reloaded.current_generation == 2
    assert await call_business_event(bus) == 5

    removed = await registry.uninstall("demo")
    assert removed.status == "SUCCESS"
    assert removed.operation == "uninstall"
    assert registry.runtime.active_packages() == ()
    assert not (store / "demo").exists()
    assert await call_business_event(bus) == ("core", {"tool": "Bash"})


@pytest.mark.asyncio
async def test_install_reads_the_directories_of_the_install_store(tmp_path: Path) -> None:
    store = tmp_path / "store"
    package_root = store / "demo" / "1.0.0"
    package_root.mkdir(parents=True)
    write_package(package_root, "demo")
    registry, bus = await build_registry(store)

    assert registry.package_manager.list_installed() == [package_root.resolve()]
    installed = await registry.install(registry.package_manager.list_installed())

    assert installed[0].status == "SUCCESS"
    assert await call_business_event(bus) == 1
    assert package_root.is_dir()
