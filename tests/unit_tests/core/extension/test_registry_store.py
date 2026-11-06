# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Store assembly and uninstall through the real Registry and event chains."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.events import Event, EventHandlerOps, ExtensionEventBus
from openjiuwen.core.events.ops_impl.mcp.runtime import set_resource_mgr_provider
from openjiuwen.core.extension import ExtensionFailure, Registry
from openjiuwen.core.extension.data import JsonStore
from openjiuwen.core.extension.schema.mcp import declarations
from tests.unit_tests.core.extension.conftest import BUSINESS_EVENT, echo_core_handler, match_event_input, write_package

_BODY = (
    "def create_extension():\n"
    "    return Extension()\n\n"
    "class Extension:\n"
    "    def register(self, registrar):\n"
    "        async def handler(event, ops, next_handler):\n"
    "            return await event.event_input['action'](ops)\n"
    f"        registrar.register_event_handler('{BUSINESS_EVENT}', handler, {{}}, 'user')\n"
)


async def _registry(tmp_path: Path, *, callbacks=(), mcp_servers=None):
    service = JsonStore(tmp_path / "data")
    bus = ExtensionEventBus()
    bus.register_event_type(BUSINESS_EVENT, match_event_input, echo_core_handler)
    registry = Registry()
    await registry.init(bus, store=service, cleanup_callbacks=callbacks)
    await registry.open_install_dir(tmp_path / "installed", report_path=tmp_path / "report.yaml")
    source = tmp_path / "source"
    write_package(source, "demo", body=_BODY)
    if mcp_servers is not None:
        (source / "mcp.json").write_text(json.dumps(mcp_servers), encoding="utf-8")
    (report,) = await registry.install(source)
    assert report.status == "SUCCESS"
    return registry, bus, service, source


async def _set(ops):
    return await ops.store.set("config", {"enabled": True})


@pytest.mark.asyncio
async def test_registry_injects_store_and_uninstall_cleans_in_order(tmp_path: Path) -> None:
    calls = []

    async def cleanup(package_id, generation, reason):
        calls.append((package_id, generation, reason))
        assert registry.runtime.get_active("demo") is not None
        assert registry.runtime.published_handlers("demo") == ()
        assert (service.directory / "demo.json").exists()
        assert registry.package_manager.version_dir("demo", "1.0.0").exists()

    registry, bus, service, source = await _registry(tmp_path, callbacks=(cleanup,))
    assert not service.directory.exists()
    assert await bus.trigger(Event(BUSINESS_EVENT, {"action": _set})) is True
    assert registry.runtime.inflight_count("demo") == 0
    assert await service.set("other", "key", 2)
    result = await registry.uninstall("demo")
    assert result.status == "SUCCESS"
    assert calls == [("demo", 1, "uninstall")]
    assert registry.runtime.get_active("demo") is None
    assert not (service.directory / "demo.json").exists()
    assert await service.get("other", "key") == 2
    assert not registry.package_manager.version_dir("demo", "1.0.0").exists()
    assert source.is_dir()


@pytest.mark.asyncio
async def test_reload_and_shutdown_cleanup_keep_store(tmp_path: Path) -> None:
    registry, bus, service, _ = await _registry(tmp_path)
    assert await bus.trigger(Event(BUSINESS_EVENT, {"action": _set})) is True
    assert (await registry.reload("demo")).status == "SUCCESS"
    assert await service.get("demo", "config") == {"enabled": True}
    for reason in ("reload", "shutdown"):
        await registry.runtime.cleanup_package_resources("demo", 1, reason=reason)
        assert await service.get("demo", "config") == {"enabled": True}


@pytest.mark.asyncio
async def test_busy_runtime_counts_real_handlers_and_store_calls(tmp_path: Path, monkeypatch) -> None:
    registry, bus, service, _ = await _registry(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()
    original = service.set

    async def delayed_set(*args):
        entered.set()
        await release.wait()
        return await original(*args)

    monkeypatch.setattr(service, "set", delayed_set)
    caller = asyncio.create_task(bus.trigger(Event(BUSINESS_EVENT, {"action": _set})))
    await entered.wait()
    assert registry.runtime.inflight_count("demo") == 2
    try:
        result = await registry.uninstall("demo")
        assert result.status == "FAILED"
        assert result.error.status is StatusCode.EXTENSION_PACKAGE_BUSY
        assert registry.runtime.published_handlers("demo")
    finally:
        release.set()
    assert await caller is True
    assert registry.runtime.inflight_count("demo") == 0
    assert (await registry.uninstall("demo")).status == "SUCCESS"


@pytest.mark.asyncio
async def test_detached_store_call_blocks_uninstall_until_it_finishes(tmp_path: Path, monkeypatch) -> None:
    registry, bus, service, _ = await _registry(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()
    original = service.set

    async def delayed_set(*args):
        entered.set()
        await release.wait()
        return await original(*args)

    monkeypatch.setattr(service, "set", delayed_set)

    async def start(ops):
        task = asyncio.create_task(ops.store.set("key", 1))
        await entered.wait()
        return task

    detached = await bus.trigger(Event(BUSINESS_EVENT, {"action": start}))
    assert registry.runtime.inflight_count("demo") == 1
    try:
        assert (await registry.uninstall("demo")).error.status is StatusCode.EXTENSION_PACKAGE_BUSY
    finally:
        release.set()
    assert await detached is True
    assert (await registry.uninstall("demo")).status == "SUCCESS"
    assert not (service.directory / "demo.json").exists()


@pytest.mark.asyncio
async def test_callback_failure_keeps_record_and_can_be_retried(tmp_path: Path) -> None:
    calls = []

    async def cleanup(package_id, generation, reason):
        calls.append(reason)
        if len(calls) == 1:
            raise OSError("host cleanup failed")

    registry, bus, service, _ = await _registry(tmp_path, callbacks=(cleanup,))
    assert await bus.trigger(Event(BUSINESS_EVENT, {"action": _set})) is True
    assert (await registry.uninstall("demo")).status == "FAILED"
    assert registry.runtime.get_active("demo") is not None
    assert (service.directory / "demo.json").exists()
    assert registry.package_manager.version_dir("demo", "1.0.0").exists()
    assert (await registry.reload("demo")).error.status is StatusCode.EXTENSION_PACKAGE_BUSY
    with pytest.raises(ExtensionFailure):
        registry.runtime.begin_calls(("demo",))
    assert (await registry.uninstall("demo")).status == "SUCCESS"
    assert calls == ["uninstall", "uninstall"]


@pytest.mark.asyncio
async def test_failed_unregistration_keeps_handler_id_and_retries(tmp_path: Path, monkeypatch) -> None:
    registry, bus, service, _ = await _registry(tmp_path)
    assert await bus.trigger(Event(BUSINESS_EVENT, {"action": _set})) is True
    handler_ids = registry.runtime.published_handlers("demo")
    original = bus.unregister_event_handler

    def fail(handler_id):
        raise OSError("unregister failed")

    monkeypatch.setattr(bus, "unregister_event_handler", fail)
    assert (await registry.uninstall("demo")).status == "FAILED"
    assert registry.runtime.published_handlers("demo") == handler_ids
    assert registry.runtime.get_active("demo") is not None
    assert (service.directory / "demo.json").exists()
    monkeypatch.setattr(bus, "unregister_event_handler", original)
    assert (await registry.uninstall("demo")).status == "SUCCESS"


@pytest.mark.asyncio
async def test_store_delete_failure_is_reported_and_retried(tmp_path: Path, monkeypatch) -> None:
    registry, bus, service, _ = await _registry(tmp_path)
    assert await bus.trigger(Event(BUSINESS_EVENT, {"action": _set})) is True
    original = service.cleanup_package

    async def fail(*args, **kwargs):
        raise build_error(StatusCode.EXTENSION_DATA_WRITE_FAILED, reason="unlink refused")

    monkeypatch.setattr(service, "cleanup_package", fail)
    result = await registry.uninstall("demo")
    assert result.status == "FAILED"
    assert result.error.status is StatusCode.EXTENSION_DATA_WRITE_FAILED
    assert registry.runtime.get_active("demo") is not None
    assert (service.directory / "demo.json").exists()
    monkeypatch.setattr(service, "cleanup_package", original)
    assert (await registry.uninstall("demo")).status == "SUCCESS"


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [True, False])
async def test_partial_install_copy_deletion_can_retry_without_manifest(
    tmp_path: Path, monkeypatch, active: bool
) -> None:
    registry, bus, service, _ = await _registry(tmp_path)
    assert await bus.trigger(Event(BUSINESS_EVENT, {"action": _set})) is True
    if not active:
        registry.runtime.unpublish("demo", bus)
    original = registry.package_manager.remove_installed
    calls = []

    def partial_remove(package_id, version, *, missing_ok=False):
        calls.append(version)
        if len(calls) == 1:
            (registry.package_manager.version_dir(package_id, version) / "manifest.json").unlink()
            raise ExtensionFailure(StatusCode.EXTENSION_INSTALL_COPY_FAILED, reason="partial deletion")
        original(package_id, version, missing_ok=missing_ok)

    monkeypatch.setattr(registry.package_manager, "remove_installed", partial_remove)
    assert (await registry.uninstall("demo")).status == "FAILED"
    assert not (service.directory / "demo.json").exists()
    assert registry.runtime.uninstall_versions("demo") == ("1.0.0",)
    assert (await registry.uninstall("demo")).status == "SUCCESS"
    assert calls == ["1.0.0", "1.0.0"]
    assert registry.runtime.uninstall_versions("demo") == ()


@pytest.mark.asyncio
async def test_uninstall_removes_all_verified_versions_including_inactive_copy(tmp_path: Path) -> None:
    registry, bus, service, source = await _registry(tmp_path)
    assert await bus.trigger(Event(BUSINESS_EVENT, {"action": _set})) is True
    write_package(registry.package_manager.version_dir("demo", "0.9.0"), "demo", version="0.9.0")
    assert (await registry.uninstall("demo")).status == "SUCCESS"
    assert registry.package_manager.managed_versions("demo") == ()
    assert not (service.directory / "demo.json").exists()
    assert source.exists()


@pytest.mark.asyncio
async def test_expired_ops_cannot_recreate_data_after_uninstall(tmp_path: Path) -> None:
    registry, bus, service, _ = await _registry(tmp_path)

    async def save_ops(ops):
        assert await ops.store.set("key", 1)
        return ops

    saved = await bus.trigger(Event(BUSINESS_EVENT, {"action": save_ops}))
    assert isinstance(saved, EventHandlerOps)
    assert (await registry.uninstall("demo")).status == "SUCCESS"
    with pytest.raises(BaseError) as caught:
        await saved.store.set("key", 2)
    assert caught.value.status is StatusCode.EVENTS_OPS_INVALID
    assert not (service.directory / "demo.json").exists()


@pytest.mark.asyncio
async def test_active_package_with_damaged_manifest_can_still_be_uninstalled(tmp_path: Path) -> None:
    registry, bus, service, source = await _registry(tmp_path)
    assert await bus.trigger(Event(BUSINESS_EVENT, {"action": _set})) is True
    (registry.package_manager.version_dir("demo", "1.0.0") / "manifest.json").write_text("invalid", encoding="utf-8")
    assert (await registry.uninstall("demo")).status == "SUCCESS"
    assert not (service.directory / "demo.json").exists()
    assert source.exists()


@pytest.mark.asyncio
async def test_unverified_inactive_manifest_cannot_authorize_cleanup(tmp_path: Path) -> None:
    registry, bus, service, _ = await _registry(tmp_path)
    assert await bus.trigger(Event(BUSINESS_EVENT, {"action": _set})) is True
    registry.runtime.unpublish("demo", bus)
    (registry.package_manager.version_dir("demo", "1.0.0") / "manifest.json").write_text("invalid", encoding="utf-8")
    assert (await registry.uninstall("demo")).status == "FAILED"
    assert await service.get("demo", "config") == {"enabled": True}


@pytest.mark.asyncio
async def test_cancelled_cleanup_keeps_record_and_management_lock_can_retry(tmp_path: Path) -> None:
    entered = asyncio.Event()
    attempts = []

    async def cleanup(package_id, generation, reason):
        attempts.append(reason)
        if len(attempts) == 1:
            entered.set()
            await asyncio.Event().wait()

    registry, bus, service, _ = await _registry(tmp_path, callbacks=(cleanup,))
    assert await bus.trigger(Event(BUSINESS_EVENT, {"action": _set})) is True
    task = asyncio.create_task(registry.uninstall("demo"))
    await entered.wait()
    assert (await registry.uninstall("demo")).error.status is StatusCode.EXTENSION_PACKAGE_BUSY
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert registry.runtime.get_active("demo") is not None
    assert (service.directory / "demo.json").exists()
    assert (await registry.uninstall("demo")).status == "SUCCESS"


@pytest.mark.asyncio
async def test_mcp_cleanup_failure_preserves_store_and_retries_before_host_cleanup(tmp_path: Path) -> None:
    calls = []

    async def cleanup(package_id, generation, reason):
        calls.append("host")
        assert (service.directory / "demo.json").exists()

    async def remove_mcp_server(server_id=None, **kwargs):
        del server_id, kwargs
        calls.append("mcp")
        assert registry.runtime.published_handlers("demo") == ()
        assert (service.directory / "demo.json").exists()
        if len(calls) == 1:
            raise RuntimeError("connection cleanup failed")
        return []

    manager = SimpleNamespace(remove_mcp_server=remove_mcp_server)
    previous = set_resource_mgr_provider(lambda: manager)
    try:
        registry, bus, service, _ = await _registry(
            tmp_path,
            callbacks=(cleanup,),
            mcp_servers={"mcpServers": {"probe": {"client_type": "stdio", "params": {"command": "probe-mcp"}}}},
        )
        assert await bus.trigger(Event(BUSINESS_EVENT, {"action": _set})) is True
        assert declarations.resolve("demo", "probe") is not None
        assert (await registry.uninstall("demo")).status == "FAILED"
        assert calls == ["mcp"]
        assert registry.runtime.get_active("demo") is not None
        assert declarations.resolve("demo", "probe") is not None
        assert await service.get("demo", "config") == {"enabled": True}
        assert (await registry.uninstall("demo")).status == "SUCCESS"
        assert calls == ["mcp", "mcp", "host"]
        assert declarations.resolve("demo", "probe") is None
        assert not (service.directory / "demo.json").exists()
    finally:
        declarations.withdraw("demo")
        set_resource_mgr_provider(previous)
