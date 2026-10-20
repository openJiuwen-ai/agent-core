# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared helpers for extension unit tests."""

from pathlib import Path

from openjiuwen.core.extension.schema.manifest import ExtensionDecl, Manifest, PackageDependency
from openjiuwen.core.extension.schema.records import ActivePackageRecord


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
) -> None:
    extension = root / module
    extension.parent.mkdir(parents=True, exist_ok=True)
    extension.write_text(
        "VALUE = 1\n\n"
        "def create_extension():\n"
        "    return Extension()\n\n"
        "class Extension:\n"
        "    def register(self, registrar):\n"
        "        def handler(event, ops, next_handler):\n"
        "            return VALUE\n"
        "        registrar.register_event_handler("
        "'tool.call', handler, {'tool': 'Bash'}, 'user')\n",
        encoding="utf-8",
    )
    (root / "manifest.yaml").write_text(
        "schemaVersion: 1\n"
        "package:\n"
        f"  id: {package_id}\n"
        f"  version: {version}\n"
        "  displayName: Demo\n"
        "extensions:\n"
        "  - id: ext\n"
        "    runtime: backend\n"
        f"    module: {module}\n",
        encoding="utf-8",
    )
