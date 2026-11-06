# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Parsed package manifest (manifest.json). Field names follow the package file, not Python style."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PackageDependency:
    """One required package and the version specifier it must satisfy."""

    package_id: str
    version: str


@dataclass(frozen=True, slots=True)
class ExtensionDecl:
    """One loadable extension inside a package."""

    id: str
    runtime: str
    module: str
    requires: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Manifest:
    """One package declaration."""

    schema_version: int
    package_id: str
    version: str
    display_name: str | None
    description: str | None
    requires_host: str | None
    requires_packages: tuple[PackageDependency, ...]
    extensions: tuple[ExtensionDecl, ...]
    domain: str | None = None
