# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""External managed roots let a host load bundle members without a store copy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen.core.events import Event
from openjiuwen.core.extension.bundle import bundle_version_dir
from openjiuwen.core.extension.management import PACKAGE_QUERY_EVENT, QueryRequest, QueryResult
from openjiuwen.core.extension.manifest import load_manifest
from openjiuwen.core.extension.package_manager import PackageManager
from tests.unit_tests.core.extension.conftest import build_registry, write_package


def _managed_member(store: Path, package_id: str = "demo.member") -> Path:
    root = bundle_version_dir(store, "demo.bundle", "1.0.0") / "plugins" / package_id
    root.mkdir(parents=True)
    write_package(root, package_id)
    return root


def test_managed_root_participates_in_the_store_view(tmp_path: Path) -> None:
    store = tmp_path / "store"
    root = _managed_member(store)
    manager = PackageManager(store)

    assert manager.register_managed_root("demo.member", "1.0.0", root) == root.resolve()
    assert manager.version_dir("demo.member", "1.0.0") == root.resolve()
    assert manager.is_managed_root(root)
    assert root.resolve() in manager.list_installed()
    assert manager.managed_versions("demo.member") == ("1.0.0",)


def test_managed_root_is_loaded_in_place_and_never_copied_or_deleted(tmp_path: Path) -> None:
    store = tmp_path / "store"
    root = _managed_member(store)
    manager = PackageManager(store)
    manager.register_managed_root("demo.member", "1.0.0", root)
    manifest = load_manifest(root)

    target = manager.copy_into_store(root, manifest)
    manager.set_enabled("demo.member", "1.0.0", True)
    manager.remove_installed("demo.member", "1.0.0")

    assert target == root.resolve()
    assert root.is_dir()
    document = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert document["enable"] is True


@pytest.mark.asyncio
async def test_registry_startup_loads_a_registered_managed_root(tmp_path: Path) -> None:
    store = tmp_path / "store"
    root = _managed_member(store)
    registry, _ = await build_registry(store)
    registry.package_manager.register_managed_root("demo.member", "1.0.0", root)

    reports = await registry.load_installed()

    assert [report.status for report in reports] == ["SUCCESS"]
    active = registry.runtime.active_packages()
    assert [(item.package_id, item.version) for item in active] == [("demo.member", "1.0.0")]
    assert active[0].root == root.resolve()


@pytest.mark.asyncio
async def test_query_finds_a_registered_managed_root(tmp_path: Path) -> None:
    store = tmp_path / "store"
    root = _managed_member(store)
    registry, bus = await build_registry(store)
    registry.package_manager.register_managed_root("demo.member", "1.0.0", root)

    result = await bus.trigger(
        Event(PACKAGE_QUERY_EVENT, QueryRequest(bus=bus, package_id="demo.member"))
    )

    assert isinstance(result, QueryResult)
    assert [item.resolve() for item in result.installed] == [root.resolve()]
