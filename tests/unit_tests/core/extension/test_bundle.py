# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Bundle manifest rules, member limit and dependency ordering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.extension import ExtensionFailure
from openjiuwen.core.extension.bundle import (
    MAX_BUNDLE_MEMBERS,
    dependency_levels,
    read_bundle_spec,
)


def _write_member(root: Path, package_id: str, *, domain: str = "agentserver", requires=None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "package_type": "extension",
        "schemaVersion": 1,
        "package": {"id": package_id, "version": "1.0.0", "domain": domain},
        "extensions": [{"id": "main", "runtime": "backend", "module": "extension.py"}],
    }
    if requires:
        manifest["requires"] = {
            "packages": [{"id": dep, "version": spec} for dep, spec in requires],
        }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "extension.py").write_text("def create_extension():\n    return None\n", encoding="utf-8")


def _write_bundle(root: Path, members, *, extra=None, domain=None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    package = {"id": "demo.bundle", "version": "2.0.0"}
    if domain is not None:
        package["domain"] = domain
    manifest = {
        "package_type": "extension",
        "schemaVersion": 1,
        "is_bundle": True,
        "package": package,
        "members": [{"path": path} for path in members],
    }
    if extra:
        manifest.update(extra)
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_bundle_reads_members_and_orders_dependencies(tmp_path: Path) -> None:
    root = _write_bundle(tmp_path / "bundle", ["plugins/base", "plugins/mid", "plugins/top"])
    _write_member(root / "plugins/base", "demo.base", domain="agentserver")
    _write_member(root / "plugins/mid", "demo.mid", domain="gateway", requires=[("demo.base", ">=1.0.0")])
    _write_member(
        root / "plugins/top", "demo.top", domain="gateway",
        requires=[("demo.mid", ">=1.0.0"), ("demo.base", ">=1.0.0")],
    )

    spec = read_bundle_spec(root)
    levels = dependency_levels(spec)

    assert [member.package_id for member in spec.members] == ["demo.base", "demo.mid", "demo.top"]
    assert [[member.package_id for member in level] for level in levels] == [
        ["demo.base"], ["demo.mid"], ["demo.top"],
    ]


def test_bundle_rejects_too_many_members(tmp_path: Path) -> None:
    paths = [f"plugins/m{index}" for index in range(MAX_BUNDLE_MEMBERS + 1)]
    root = _write_bundle(tmp_path / "bundle", paths)
    for index, path in enumerate(paths):
        _write_member(root / path, f"demo.m{index}")

    with pytest.raises(ExtensionFailure, match="limit"):
        read_bundle_spec(root)


def test_bundle_rejects_a_dependency_cycle(tmp_path: Path) -> None:
    root = _write_bundle(tmp_path / "bundle", ["plugins/a", "plugins/b"])
    _write_member(root / "plugins/a", "demo.a", requires=[("demo.b", ">=1.0.0")])
    _write_member(root / "plugins/b", "demo.b", requires=[("demo.a", ">=1.0.0")])

    spec = read_bundle_spec(root)
    with pytest.raises(ExtensionFailure) as error:
        dependency_levels(spec)
    assert error.value.status is StatusCode.EXTENSION_DEPENDENCY_CYCLE


def test_bundle_rejects_a_self_dependency(tmp_path: Path) -> None:
    root = _write_bundle(tmp_path / "bundle", ["plugins/a"])
    _write_member(root / "plugins/a", "demo.a", requires=[("demo.a", ">=1.0.0")])

    spec = read_bundle_spec(root)
    with pytest.raises(ExtensionFailure) as error:
        dependency_levels(spec)
    assert error.value.status is StatusCode.EXTENSION_DEPENDENCY_CYCLE


def test_bundle_rejects_a_version_mismatch_between_members(tmp_path: Path) -> None:
    root = _write_bundle(tmp_path / "bundle", ["plugins/a", "plugins/b"])
    _write_member(root / "plugins/a", "demo.a")
    _write_member(root / "plugins/b", "demo.b", requires=[("demo.a", ">=2.0.0")])

    with pytest.raises(ExtensionFailure) as error:
        read_bundle_spec(root)
    assert error.value.status is StatusCode.EXTENSION_DEPENDENCY_NOT_MET


def test_bundle_rejects_duplicate_member_ids(tmp_path: Path) -> None:
    root = _write_bundle(tmp_path / "bundle", ["plugins/a", "plugins/b"])
    _write_member(root / "plugins/a", "demo.same")
    _write_member(root / "plugins/b", "demo.same")

    with pytest.raises(ExtensionFailure) as error:
        read_bundle_spec(root)
    assert error.value.status is StatusCode.EXTENSION_DUPLICATE_PACKAGE_ID


def test_bundle_rejects_a_member_reusing_the_bundle_id(tmp_path: Path) -> None:
    root = _write_bundle(tmp_path / "bundle", ["plugins/a"])
    _write_member(root / "plugins/a", "demo.bundle")

    with pytest.raises(ExtensionFailure, match="bundle id"):
        read_bundle_spec(root)


def test_bundle_rejects_an_escaping_member_path(tmp_path: Path) -> None:
    root = _write_bundle(tmp_path / "bundle", ["../outside"])

    with pytest.raises(ExtensionFailure, match="relative"):
        read_bundle_spec(root)


def test_bundle_rejects_a_root_domain(tmp_path: Path) -> None:
    root = _write_bundle(tmp_path / "bundle", ["plugins/a"], domain="gateway")
    _write_member(root / "plugins/a", "demo.a")

    with pytest.raises(ExtensionFailure, match="package.domain"):
        read_bundle_spec(root)


def test_bundle_rejects_a_member_without_a_domain(tmp_path: Path) -> None:
    root = _write_bundle(tmp_path / "bundle", ["plugins/a"])
    (root / "plugins/a").mkdir(parents=True)
    manifest = {
        "package_type": "extension",
        "schemaVersion": 1,
        "package": {"id": "demo.a", "version": "1.0.0"},
        "extensions": [],
    }
    (root / "plugins/a/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ExtensionFailure):
        read_bundle_spec(root)


def test_bundle_root_uses_the_store_layout_and_is_never_enabled(tmp_path: Path) -> None:
    from openjiuwen.core.extension.bundle import bundle_version_dir
    from openjiuwen.core.extension.manifest import load_manifest
    from openjiuwen.core.extension.package_manager import PackageManager

    store = tmp_path / "store"
    root = store / "demo.bundle" / "2.0.0"
    _write_bundle(root, ["plugins/base"])
    _write_member(root / "plugins/base", "demo.base")

    manager = PackageManager(store, allowed_domains=("gateway", "agentserver"))
    manager.normalize_legacy_enable()

    assert root.resolve() in manager.list_installed()
    assert bundle_version_dir(store, "demo.bundle", "2.0.0") == root.resolve()
    manifest = load_manifest(root, require_domain=True)
    assert manifest.is_bundle is True
    assert manifest.enable is False
    assert manifest.domain is None
    manager.validate_manifest(manifest)
    assert "enable" not in json.loads((root / "manifest.json").read_text(encoding="utf-8"))


def test_bundle_rejects_a_symlinked_member(tmp_path: Path) -> None:
    root = _write_bundle(tmp_path / "bundle", ["plugins/base"])
    real = root / "plugins" / "real"
    _write_member(real, "demo.real")
    try:
        (root / "plugins" / "base").symlink_to(real, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")

    # The target is a valid member inside the root; only the symlink guard rejects it.
    with pytest.raises(ExtensionFailure) as error:
        read_bundle_spec(root)

    assert error.value.status is StatusCode.EXTENSION_MANIFEST_INVALID


def test_bundle_root_rejects_declared_extensions(tmp_path: Path) -> None:
    root = _write_bundle(
        tmp_path / "bundle", ["plugins/a"],
        extra={"extensions": [{"id": "main", "runtime": "backend", "module": "extension.py"}]},
    )
    _write_member(root / "plugins/a", "demo.a")

    with pytest.raises(ExtensionFailure, match="extensions"):
        read_bundle_spec(root)


def test_bundle_root_requires_members(tmp_path: Path) -> None:
    root = _write_bundle(tmp_path / "bundle", [])

    with pytest.raises(ExtensionFailure, match="members"):
        read_bundle_spec(root)


def test_core_never_deletes_a_bundle_root(tmp_path: Path) -> None:
    from openjiuwen.core.extension.package_manager import PackageManager

    store = tmp_path / "store"
    root = store / "demo.bundle" / "2.0.0"
    _write_bundle(root, ["plugins/base"])
    _write_member(root / "plugins/base", "demo.base")
    manager = PackageManager(store)

    manager.remove_installed("demo.bundle", "2.0.0")
    manager.remove_package_dir("demo.bundle")
    with pytest.raises(ExtensionFailure):
        manager.set_enabled("demo.bundle", "2.0.0", True)

    assert (root / "plugins" / "base" / "manifest.json").is_file()


@pytest.mark.asyncio
async def test_registry_refuses_to_uninstall_a_bundle_root(tmp_path: Path) -> None:
    from openjiuwen.core.events import Event
    from openjiuwen.core.extension.management import PACKAGE_UNINSTALL_EVENT, UninstallRequest
    from tests.unit_tests.core.extension.conftest import build_registry

    store = tmp_path / "store"
    root = store / "demo.bundle" / "2.0.0"
    _write_bundle(root, ["plugins/base"])
    _write_member(root / "plugins/base", "demo.base")
    _, bus = await build_registry(store)

    result = await bus.trigger(
        Event(PACKAGE_UNINSTALL_EVENT, UninstallRequest(bus=bus, package_id="demo.bundle"))
    )

    assert result.reports[0].status == "FAILED"
    assert (root / "plugins" / "base" / "manifest.json").is_file()
