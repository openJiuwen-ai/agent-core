# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""EX-01: manifest, install store, selection, and ordering."""

import hashlib
import json
import stat
import zipfile
from pathlib import Path

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.extension import package_manager as package_manager_module
from openjiuwen.core.extension.errors import ExtensionFailure, operation_error
from openjiuwen.core.extension.manifest import load_manifest, parse_manifest_mapping
from openjiuwen.core.extension.ordering import order_key, topological_order
from openjiuwen.core.extension.package_manager import PackageManager
from openjiuwen.core.extension.schema.manifest import PackageDependency
from openjiuwen.core.extension.selection import evaluate_reload, resolve_install_set
from openjiuwen.core.extension.versions import parse_version, specifier_matches
from tests.unit_tests.core.extension.conftest import make_active, make_manifest, write_package


def test_order_key_is_stable_sha256() -> None:
    assert order_key("a") == order_key("a")
    assert order_key("a") != order_key("b")
    assert order_key("pkg") == hashlib.sha256(b"pkg").hexdigest()


def test_packages_without_dependencies_follow_sha256_order() -> None:
    manifests = [make_manifest("pkg-b"), make_manifest("pkg-a"), make_manifest("pkg-c")]
    result = resolve_install_set(manifests, active=(), host_version="1.0.0")

    assert result.fatal is None
    assert result.rejected == ()
    assert [item.package_id for item in result.ordered] == sorted(
        ("pkg-a", "pkg-b", "pkg-c"),
        key=order_key,
    )


def test_dependency_is_ordered_before_its_dependent() -> None:
    manifests = [
        make_manifest("child", requires=(PackageDependency("parent", ">=1.0.0 <2.0.0"),)),
        make_manifest("parent"),
        make_manifest("other"),
    ]
    result = resolve_install_set(manifests, active=(), host_version=None)
    ids = [item.package_id for item in result.ordered]

    assert ids.index("parent") < ids.index("child")
    assert "other" in ids
    assert result.rejected == ()


def test_cycle_rejects_the_cycle_and_its_dependent_only() -> None:
    manifests = [
        make_manifest("a", requires=(PackageDependency("b", ">=1.0.0"),)),
        make_manifest("b", requires=(PackageDependency("a", ">=1.0.0"),)),
        make_manifest("c", requires=(PackageDependency("a", ">=1.0.0"),)),
        make_manifest("d"),
    ]
    result = resolve_install_set(manifests, active=(), host_version=None)
    rejected = {item.package_id: item.error.status for item in result.rejected}

    assert [item.package_id for item in result.ordered] == ["d"]
    assert rejected["a"] == StatusCode.EXTENSION_DEPENDENCY_CYCLE
    assert rejected["b"] == StatusCode.EXTENSION_DEPENDENCY_CYCLE
    assert rejected["c"] == StatusCode.EXTENSION_DEPENDENCY_NOT_MET


def test_rejected_dependency_blocks_dependents_only() -> None:
    manifests = [
        make_manifest("needs-host", host=">=9.0.0"),
        make_manifest(
            "child",
            requires=(PackageDependency("needs-host", ">=1.0.0"),),
        ),
        make_manifest("ok"),
    ]
    result = resolve_install_set(manifests, active=(), host_version="1.0.0")
    codes = {item.package_id: item.error.status for item in result.rejected}

    assert [item.package_id for item in result.ordered] == ["ok"]
    assert codes["needs-host"] == StatusCode.EXTENSION_HOST_VERSION_TOO_LOW
    assert codes["child"] == StatusCode.EXTENSION_DEPENDENCY_NOT_MET


def test_duplicate_id_in_one_batch_installs_nothing() -> None:
    result = resolve_install_set(
        [make_manifest("same"), make_manifest("same", version="2.0.0"), make_manifest("other")],
        active=(),
        host_version=None,
    )

    assert result.ordered == ()
    assert result.fatal is not None
    assert result.fatal.status == StatusCode.EXTENSION_DUPLICATE_PACKAGE_ID
    assert result.fatal.code == StatusCode.EXTENSION_DUPLICATE_PACKAGE_ID.name


def test_already_active_id_is_rejected() -> None:
    active = (make_active("same", version="1.0.0"),)
    blocked = resolve_install_set(
        [make_manifest("same", version="1.1.0", display_name="Demo"), make_manifest("fresh")],
        active,
        None,
    )

    assert [item.package_id for item in blocked.ordered] == ["fresh"]
    assert blocked.rejected[0].package_id == "same"
    assert blocked.rejected[0].error.status == StatusCode.EXTENSION_PACKAGE_EXISTS
    assert "Demo" in blocked.rejected[0].error.message


def test_host_version_and_missing_dependency_do_not_block_unrelated_packages() -> None:
    manifests = [
        make_manifest("needs-host", host=">=9.0.0"),
        make_manifest("needs-missing", requires=(PackageDependency("absent", ">=1.0.0"),)),
        make_manifest("ok"),
    ]
    result = resolve_install_set(manifests, active=(), host_version="1.2.0")
    codes = {item.package_id: item.error.status for item in result.rejected}

    assert [item.package_id for item in result.ordered] == ["ok"]
    assert codes["needs-host"] == StatusCode.EXTENSION_HOST_VERSION_TOO_LOW
    assert codes["needs-missing"] == StatusCode.EXTENSION_DEPENDENCY_NOT_MET


def test_missing_host_version_is_a_distinct_error() -> None:
    result = resolve_install_set(
        [make_manifest("needs-host", host=">=1.0.0"), make_manifest("ok")],
        active=(),
        host_version=None,
    )
    codes = {item.package_id: item.error.status for item in result.rejected}

    assert [item.package_id for item in result.ordered] == ["ok"]
    assert codes["needs-host"] == StatusCode.EXTENSION_HOST_VERSION_MISSING


def test_active_dependency_version_mismatch_is_rejected() -> None:
    active = (make_active("base", "1.0.0"),)
    result = resolve_install_set(
        [make_manifest("child", requires=(PackageDependency("base", ">=2.0.0"),))],
        active,
        None,
    )

    assert result.ordered == ()
    assert result.rejected[0].error.status == StatusCode.EXTENSION_DEPENDENCY_NOT_MET


def test_batch_dependency_version_mismatch_is_rejected() -> None:
    result = resolve_install_set(
        [
            make_manifest("base", "1.0.0"),
            make_manifest("child", requires=(PackageDependency("base", ">=2.0.0"),)),
        ],
        active=(),
        host_version=None,
    )

    assert [item.package_id for item in result.ordered] == ["base"]
    assert result.rejected[0].package_id == "child"
    assert result.rejected[0].error.status == StatusCode.EXTENSION_DEPENDENCY_NOT_MET


def test_reload_allows_a_new_version_dependents_still_accept() -> None:
    active = (
        make_active("base", "1.0.0"),
        make_active("user", "1.0.0", requires=(PackageDependency("base", ">=1.0.0 <2.0.0"),)),
    )
    assert evaluate_reload(active, "base", make_manifest("base", "1.4.0")) is None


def test_reload_rejects_a_version_dependents_cannot_use() -> None:
    active = (
        make_active("base", "1.0.0"),
        make_active("user", "1.0.0", requires=(PackageDependency("base", ">=1.0.0 <2.0.0"),)),
    )
    error = evaluate_reload(active, "base", make_manifest("base", "2.0.0"))

    assert error is not None
    assert error.status == StatusCode.EXTENSION_DEPENDENT_INCOMPATIBLE


def test_reload_rejects_inactive_or_wrong_package_id() -> None:
    missing = evaluate_reload((), "base", make_manifest("base"))
    assert missing is not None
    assert missing.status == StatusCode.EXTENSION_PACKAGE_NOT_INSTALLED

    mismatched = evaluate_reload((make_active("base"),), "base", make_manifest("other"))
    assert mismatched is not None
    assert mismatched.status == StatusCode.EXTENSION_MANIFEST_INVALID


def test_manifest_rejects_entry_outside_the_package(tmp_path: Path) -> None:
    write_package(tmp_path, "demo")
    text = (tmp_path / "manifest.json").read_text(encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        text.replace("extensions/ext/extension.py", "../outside.py"),
        encoding="utf-8",
    )

    with pytest.raises(ExtensionFailure) as caught:
        load_manifest(tmp_path)

    assert caught.value.status == StatusCode.EXTENSION_MANIFEST_INVALID


@pytest.mark.parametrize("package_id", ["CON", "nul.data", "demo.", "a..b", "../escape"])
def test_manifest_rejects_package_ids_unsafe_for_store(tmp_path: Path, package_id: str) -> None:
    write_package(tmp_path, "demo")
    with pytest.raises(ExtensionFailure) as caught:
        parse_manifest_mapping(
            {
                "package_type": "extension",
                "schemaVersion": 1,
                "package": {"id": package_id, "version": "1.0.0"},
                "extensions": [],
            },
            tmp_path,
        )
    assert caught.value.status == StatusCode.EXTENSION_MANIFEST_INVALID


def test_manifest_rejects_bad_schema_and_runtime(tmp_path: Path) -> None:
    write_package(tmp_path, "demo")
    with pytest.raises(ExtensionFailure) as caught:
        parse_manifest_mapping(
            {
                "package_type": "extension",
                "schemaVersion": 2,
                "package": {"id": "demo", "version": "1.0.0"},
                "extensions": [],
            },
            tmp_path,
        )
    assert caught.value.status == StatusCode.EXTENSION_MANIFEST_INVALID

    extension = tmp_path / "extensions" / "ext" / "extension.py"
    with pytest.raises(ExtensionFailure) as caught_runtime:
        parse_manifest_mapping(
            {
                "package_type": "extension",
                "schemaVersion": 1,
                "package": {"id": "demo", "version": "1.0.0"},
                "extensions": [
                    {
                        "id": "ext",
                        "runtime": "frontend",
                        "module": "extensions/ext/extension.py",
                    }
                ],
            },
            tmp_path,
        )
    assert caught_runtime.value.status == StatusCode.EXTENSION_MANIFEST_INVALID
    assert extension.is_file()


def test_manifest_reads_display_block(tmp_path: Path) -> None:
    write_package(tmp_path, "demo")
    manifest = parse_manifest_mapping(
        {
            "package_type": "extension",
            "schemaVersion": 1,
            "package": {"id": "demo", "version": "1.0.0"},
            "display": {
                "name": {"zh": "演示包", "en": "Demo Package"},
                "description": {"zh": "演示描述", "en": "Demo description"},
            },
            "extensions": [
                {"id": "ext", "runtime": "backend", "module": "extensions/ext/extension.py"}
            ],
        },
        tmp_path,
    )

    assert manifest.display_name == "演示包"
    assert manifest.description == "演示描述"


def test_manifest_enable_defaults_to_true_and_rejects_non_boolean(tmp_path: Path) -> None:
    write_package(tmp_path, "demo")
    document = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))

    assert parse_manifest_mapping(document, tmp_path).enable is True

    document["enable"] = "true"
    with pytest.raises(ExtensionFailure) as caught:
        parse_manifest_mapping(document, tmp_path)
    assert caught.value.status == StatusCode.EXTENSION_MANIFEST_INVALID

def test_manifest_rejects_unknown_extension_dependency(tmp_path: Path) -> None:
    write_package(tmp_path, "demo")
    with pytest.raises(ExtensionFailure) as caught:
        parse_manifest_mapping(
            {
                "package_type": "extension",
                "schemaVersion": 1,
                "package": {"id": "demo", "version": "1.0.0"},
                "extensions": [
                    {
                        "id": "ext",
                        "runtime": "backend",
                        "module": "extensions/ext/extension.py",
                        "requires": [{"id": "missing"}],
                    }
                ],
            },
            tmp_path,
        )
    assert caught.value.status == StatusCode.EXTENSION_MANIFEST_INVALID


def test_manifest_rejects_invalid_package_id(tmp_path: Path) -> None:
    write_package(tmp_path, "demo")
    with pytest.raises(ExtensionFailure) as caught:
        parse_manifest_mapping(
            {
                "package_type": "extension",
                "schemaVersion": 1,
                "package": {"id": "../bad", "version": "1.0.0"},
                "extensions": [],
            },
            tmp_path,
        )
    assert caught.value.status == StatusCode.EXTENSION_MANIFEST_INVALID


def test_zip_copy_never_deletes_the_callers_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_package(source, "demo", "1.2.0")
    marker = source / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    archive_path = tmp_path / "demo.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for path in source.rglob("*"):
            archive.write(path, path.relative_to(source).as_posix())

    manager = PackageManager(tmp_path / "store")
    with manager.open_source(archive_path) as opened:
        assert opened.manifest.package_id == "demo"
        installed = manager.copy_into_store(opened.root, opened.manifest)

    assert archive_path.is_file()
    assert marker.read_text(encoding="utf-8") == "keep"
    assert (installed / "manifest.json").is_file()
    assert manager.list_installed() == [installed]

    with manager.open_source(source) as opened_dir:
        assert opened_dir.root == source.resolve()
    assert marker.is_file()


def test_copy_into_store_is_idempotent_for_install_copy(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_package(source, "demo")
    manager = PackageManager(tmp_path / "store")
    manifest = load_manifest(source)
    first = manager.copy_into_store(source, manifest)
    second = manager.copy_into_store(first, manifest)

    assert first == second
    assert (source / "manifest.json").is_file()


def test_copy_into_store_adds_default_enable_without_changing_the_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_package(source, "demo")
    manager = PackageManager(tmp_path / "store")
    manifest = load_manifest(source)

    installed = manager.copy_into_store(source, manifest)

    source_document = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    installed_document = json.loads((installed / "manifest.json").read_text(encoding="utf-8"))
    assert "enable" not in source_document
    assert installed_document["enable"] is True


def test_copy_into_store_refuses_to_overwrite_existing_version(tmp_path: Path) -> None:
    source = tmp_path / "source"
    other = tmp_path / "other"
    write_package(source, "demo")
    write_package(other, "demo")
    manager = PackageManager(tmp_path / "store")
    manifest = load_manifest(source)
    manager.copy_into_store(source, manifest)

    with pytest.raises(ExtensionFailure) as caught:
        manager.copy_into_store(other, manifest)
    assert caught.value.status == StatusCode.EXTENSION_INSTALL_COPY_FAILED


def test_zip_member_escape_is_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../outside.txt", "nope")
    manager = PackageManager(tmp_path / "store")

    with pytest.raises(ExtensionFailure) as caught:
        manager.open_source(archive_path)

    assert caught.value.status == StatusCode.EXTENSION_SOURCE_INVALID
    assert archive_path.is_file()
    assert not (tmp_path / "outside.txt").exists()


def test_zip_absolute_member_path_is_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "abs.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("/etc/passwd", "nope")
    with pytest.raises(ExtensionFailure) as caught:
        PackageManager(tmp_path / "store").open_source(archive_path)
    assert caught.value.status == StatusCode.EXTENSION_SOURCE_INVALID


def test_zip_symlink_member_is_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "link.zip"
    info = zipfile.ZipInfo("link-name")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(info, b"target")
    with pytest.raises(ExtensionFailure) as caught:
        PackageManager(tmp_path / "store").open_source(archive_path)
    assert caught.value.status == StatusCode.EXTENSION_SOURCE_INVALID


def test_zip_file_count_limit_is_enforced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(package_manager_module, "_MAX_ZIP_FILE_COUNT", 2)
    archive_path = tmp_path / "many.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("a.txt", "1")
        archive.writestr("b.txt", "2")
        archive.writestr("c.txt", "3")
    with pytest.raises(ExtensionFailure) as caught:
        PackageManager(tmp_path / "store").open_source(archive_path)
    assert caught.value.status == StatusCode.EXTENSION_SOURCE_INVALID


def test_bad_zip_file_is_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "not-a-zip.zip"
    archive_path.write_text("not zip", encoding="utf-8")
    with pytest.raises(ExtensionFailure) as caught:
        PackageManager(tmp_path / "store").open_source(archive_path)
    assert caught.value.status == StatusCode.EXTENSION_SOURCE_INVALID


def test_directory_symlink_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_package(source, "demo")
    target = tmp_path / "outside"
    target.mkdir()
    link = source / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks is not permitted in this environment")

    with pytest.raises(ExtensionFailure) as caught:
        PackageManager(tmp_path / "store").open_source(source)
    assert caught.value.status == StatusCode.EXTENSION_SOURCE_INVALID


def test_open_source_rejects_plain_file(tmp_path: Path) -> None:
    plain = tmp_path / "notes.txt"
    plain.write_text("x", encoding="utf-8")
    with pytest.raises(ExtensionFailure) as caught:
        PackageManager(tmp_path / "store").open_source(plain)
    assert caught.value.status == StatusCode.EXTENSION_SOURCE_INVALID


def test_remove_installed_only_deletes_the_store_copy(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_package(source, "demo")
    manager = PackageManager(tmp_path / "store")
    manifest = load_manifest(source)
    installed = manager.copy_into_store(source, manifest)

    manager.remove_installed("demo", "1.0.0")

    assert not installed.exists()
    assert (source / "manifest.json").is_file()


def test_remove_installed_missing_version_fails(tmp_path: Path) -> None:
    manager = PackageManager(tmp_path / "store")
    with pytest.raises(ExtensionFailure) as caught:
        manager.remove_installed("demo", "1.0.0")
    assert caught.value.status == StatusCode.EXTENSION_PACKAGE_NOT_INSTALLED


def test_version_helpers() -> None:
    assert parse_version("1.2.3").major == 1
    assert specifier_matches("1.4.0", ">=1.0.0 <2.0.0")
    assert not specifier_matches("2.0.0", ">=1.0.0 <2.0.0")
    assert specifier_matches("1.0.0", "")
    with pytest.raises(ValueError):
        parse_version("1.2")
    with pytest.raises(ValueError):
        specifier_matches("1.0.0", "~~1.0.0")


def test_topological_order_ignores_external_dependencies() -> None:
    nodes = ["b", "a"]
    ordered, cycle = topological_order(
        nodes,
        identifier=lambda item: item,
        dependencies=lambda item: ("external",) if item == "a" else (),
    )
    assert cycle == []
    assert ordered == sorted(nodes, key=lambda item: (order_key(item), item))


def test_operation_error_code_is_status_name() -> None:
    error = operation_error(
        StatusCode.EXTENSION_PACKAGE_EXISTS,
        package_id="demo",
        display_name="Demo",
    )
    assert error.code == "EXTENSION_PACKAGE_EXISTS"
    assert error.status.code == 201000
    assert "demo" in error.message


def test_list_installed_skips_hidden_and_missing_manifest(tmp_path: Path) -> None:
    store = tmp_path / "store"
    good = store / "demo" / "1.0.0"
    good.mkdir(parents=True)
    (good / "manifest.json").write_text(
        '{"package_type": "extension", "schemaVersion": 1,'
        ' "package": {"id": "demo", "version": "1.0.0"}, "extensions": []}',
        encoding="utf-8",
    )
    hidden = store / ".cache" / "1.0.0"
    hidden.mkdir(parents=True)
    (hidden / "manifest.json").write_text("x", encoding="utf-8")
    bare = store / "other" / "1.0.0"
    bare.mkdir(parents=True)

    assert PackageManager(store).list_installed() == [good.resolve()]


def test_open_source_directory_rejects_when_entry_missing(tmp_path: Path) -> None:
    write_package(tmp_path, "demo")
    (tmp_path / "extensions" / "ext" / "extension.py").unlink()
    with pytest.raises(ExtensionFailure) as caught:
        load_manifest(tmp_path)
    assert caught.value.status == StatusCode.EXTENSION_MANIFEST_INVALID


def test_zip_member_path_rejects_backslash_and_deep_paths(tmp_path: Path) -> None:
    # Windows ZipFile normalizes backslashes on write; exercise the path guard directly.
    with pytest.raises(ExtensionFailure) as slash:
        package_manager_module._member_path(tmp_path, r"dir\file.txt")
    assert slash.value.status == StatusCode.EXTENSION_SOURCE_INVALID

    deep = "/".join(["d"] * 40) + "/f.txt"
    with pytest.raises(ExtensionFailure) as depth:
        package_manager_module._member_path(tmp_path, deep)
    assert depth.value.status == StatusCode.EXTENSION_SOURCE_INVALID
