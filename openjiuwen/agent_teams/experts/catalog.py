# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Expert locating protocol a host implements for the leader's expert tools.

Which expert packages are installed on this machine, and where, is host
knowledge (install state, directory layout). What a package contains is not:
manifests are parsed by agent-core (``read_agent_template_card`` /
``read_agent_group_card`` / the package loaders). So the protocol only
answers "which packages, at which directory" and never opens a manifest.

A host injects one catalog per process through
``TeamRuntimeManager.set_expert_catalog``; the leader's
``view_experts`` / ``view_expert_groups`` / ``spawn_expert`` tools are
registered only when a catalog is present.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ExpertRef:
    """Identity of one expert template.

    A single expert is identified by its package name; a member of an
    AgentGroup only by ``(group, member name)`` because member names are
    unique within their group alone.
    """

    name: str
    """Package name of a single expert, or member name inside ``group``."""
    group: str | None = None
    """AgentGroup name; ``None`` for a single expert."""

    def to_dict(self) -> dict[str, str | None]:
        """JSON form persisted on a member row."""
        return {"name": self.name, "group": self.group}

    def label(self) -> str:
        """``group/name`` for a group member, ``name`` for a single expert (logs, results)."""
        return f"{self.group}/{self.name}" if self.group else self.name


@dataclass(frozen=True)
class PackageLocation:
    """Where one installed package lives."""

    name: str
    """Package name (single expert) or group name (AgentGroup); equals the directory name."""
    path: Path
    """Absolute package root directory."""
    source: str
    """Host install source, e.g. ``builtin`` / ``local`` / ``hub``."""


@runtime_checkable
class ExpertCatalog(Protocol):
    """Locates installed expert packages; async and read-only.

    Implementations return only installed packages whose directory exists,
    and never read or validate manifest content — a broken package is
    reported by the core parser that reads it. File access must not block
    the event loop.
    """

    async def list_expert_packages(self) -> list[PackageLocation]:
        """Every installed single-expert (``agent_template``) package."""
        ...

    async def locate_expert(self, name: str) -> PackageLocation:
        """One installed single-expert package; ``ValueError`` when not installed."""
        ...

    async def list_group_packages(self) -> list[PackageLocation]:
        """Every installed AgentGroup package."""
        ...

    async def locate_group(self, name: str) -> PackageLocation:
        """One installed AgentGroup package; ``ValueError`` when not installed."""
        ...


_process_catalog: ExpertCatalog | None = None


def set_process_expert_catalog(catalog: ExpertCatalog | None) -> None:
    """Install (or clear with ``None``) this process's expert catalog.

    Called through ``TeamRuntimeManager.set_expert_catalog``. The catalog is
    process-scoped like the shared DB and messager: leaders are assembled in
    the host process while their spec is built, before any runtime manager
    hook could reach them.
    """
    global _process_catalog
    _process_catalog = catalog


def get_process_expert_catalog() -> ExpertCatalog | None:
    """This process's expert catalog, or ``None`` when the host injected none."""
    return _process_catalog


__all__ = [
    "ExpertCatalog",
    "ExpertRef",
    "PackageLocation",
    "get_process_expert_catalog",
    "set_process_expert_catalog",
]
