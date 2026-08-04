# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Resource manager facade for optimization modules."""

from __future__ import annotations


class ResourceManager:
    """Resolve reusable resources without coupling modules to file layout details."""

    def __init__(self, resource_root: str) -> None:
        self.resource_root = resource_root

    def read_text(self, resource_type: str, name: str) -> str:
        """TODO: validate and load a text resource by type and name."""
        raise NotImplementedError("TODO: load text resource")

    def resolve_path(self, resource_type: str, name: str) -> str:
        """TODO: resolve a resource path without reading its content."""
        raise NotImplementedError("TODO: resolve resource path")


__all__ = [
    "ResourceManager",
]
