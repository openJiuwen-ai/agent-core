# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TeamAgentSpec sources for the interactive CLI.

The CLI accepts specs from two parallel sources:

* YAML files on disk — typically maintained alongside ``examples/``
  configurations; loaded via :func:`load_spec_yaml` which mirrors the
  ``${ENV}`` expansion + ``runtime`` block stripping logic used by the
  ``examples/agent_teams/*_e2e.py`` scripts.
* Programmatic ``TeamAgentSpec`` objects — passed in by callers that
  embed the CLI inside a larger application and build specs in code.

:class:`SpecRegistry` merges both sources behind a single ``get(name)``
lookup, with in-memory specs taking precedence over YAML on name
collision (warns rather than raising).
"""

from __future__ import annotations

import os
import re
from dataclasses import (
    dataclass,
    field,
)
from pathlib import Path
from typing import (
    Any,
    Iterable,
)

import yaml

from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import team_logger

_ENV_VAR_RE = re.compile(r"\$\{(\w+)}")


def _expand_env_vars(value: Any) -> Any:
    """Recursively replace ``${VAR}`` placeholders with environment values."""
    if isinstance(value, str):
        return _ENV_VAR_RE.sub(
            lambda match: os.environ.get(match.group(1), match.group(0)),
            value,
        )
    if isinstance(value, dict):
        return {key: _expand_env_vars(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class SpecEntry:
    """Registry record for a single TeamAgentSpec.

    Attributes:
        spec: The validated TeamAgentSpec.
        source: Origin description — absolute YAML path or ``"in-memory"``.
        runtime_overrides: Optional ``runtime`` block extracted from the
            YAML (session_id / initial_query / extra hints). Empty dict
            for in-memory specs.
    """

    spec: TeamAgentSpec
    source: str
    runtime_overrides: dict[str, Any] = field(default_factory=dict)


def load_spec_yaml(path: str | Path) -> tuple[TeamAgentSpec, dict[str, Any]]:
    """Load a YAML team config and return the parsed spec plus runtime block.

    Args:
        path: Filesystem path to a YAML file shaped like
            ``examples/agent_teams/config.yaml`` (a ``TeamAgentSpec`` body
            plus an optional top-level ``runtime`` mapping with
            ``session_id`` / ``initial_query`` hints).

    Returns:
        Tuple of (validated ``TeamAgentSpec``, ``runtime`` dict). The
        ``runtime`` dict is empty when the YAML has no such block.

    Raises:
        AGENT_TEAM_CONFIG_INVALID: When the file is missing or malformed.
    """
    yaml_path = Path(path).expanduser().resolve()
    if not yaml_path.is_file():
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason=f"team spec yaml not found: {yaml_path}",
        )
    with open(yaml_path, "r", encoding="utf-8") as file:
        raw = yaml.safe_load(file)
    if not isinstance(raw, dict):
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason=f"team spec yaml must decode to a mapping: {yaml_path}",
        )
    expanded = _expand_env_vars(raw)
    runtime = expanded.pop("runtime", {}) or {}
    spec = TeamAgentSpec.model_validate(expanded)
    return spec, dict(runtime)


class SpecRegistry:
    """Name-keyed registry of TeamAgentSpec entries from YAML and in-memory sources."""

    def __init__(self) -> None:
        self._entries: dict[str, SpecEntry] = {}

    def add_yaml(self, path: str | Path) -> SpecEntry:
        """Load ``path`` and register the spec under its ``team_name``.

        Re-registering the same team_name from YAML overwrites the old
        entry with a warning. In-memory specs are not displaced.
        """
        spec, runtime = load_spec_yaml(path)
        entry = SpecEntry(
            spec=spec,
            source=str(Path(path).expanduser().resolve()),
            runtime_overrides=runtime,
        )
        existing = self._entries.get(spec.team_name)
        if existing is not None and existing.source == "in-memory":
            team_logger.warning(
                "[cli.spec_registry] in-memory spec for team={} shadows yaml source={}",
                spec.team_name,
                entry.source,
            )
            return existing
        if existing is not None:
            team_logger.warning(
                "[cli.spec_registry] yaml reload for team={} replaces source={} with {}",
                spec.team_name,
                existing.source,
                entry.source,
            )
        self._entries[spec.team_name] = entry
        return entry

    def add_inmemory(self, spec: TeamAgentSpec) -> SpecEntry:
        """Register a programmatic spec; takes precedence over YAML."""
        entry = SpecEntry(spec=spec, source="in-memory")
        existing = self._entries.get(spec.team_name)
        if existing is not None and existing.source != "in-memory":
            team_logger.warning(
                "[cli.spec_registry] in-memory spec for team={} replaces yaml source={}",
                spec.team_name,
                existing.source,
            )
        self._entries[spec.team_name] = entry
        return entry

    def get(self, team_name: str) -> SpecEntry | None:
        """Return the entry for ``team_name`` or ``None``."""
        return self._entries.get(team_name)

    def names(self) -> list[str]:
        """List registered team names in insertion order."""
        return list(self._entries.keys())

    def entries(self) -> list[SpecEntry]:
        """Snapshot of all entries in insertion order."""
        return list(self._entries.values())

    def bulk_load_yaml(self, paths: Iterable[str | Path]) -> None:
        """Register every YAML path, ignoring duplicates after warning."""
        for path in paths:
            self.add_yaml(path)

    def bulk_register(self, specs: dict[str, TeamAgentSpec]) -> None:
        """Register an iterable of ``(name, spec)`` pairs as in-memory entries.

        The dict key is informational only; the registered name comes
        from ``spec.team_name`` to keep the registry consistent with the
        runtime pool key. A mismatched dict key emits a warning.
        """
        for declared_name, spec in specs.items():
            if declared_name != spec.team_name:
                team_logger.warning(
                    "[cli.spec_registry] dict key={} does not match spec.team_name={}; "
                    "registering under spec.team_name",
                    declared_name,
                    spec.team_name,
                )
            self.add_inmemory(spec)


__all__ = [
    "SpecEntry",
    "SpecRegistry",
    "load_spec_yaml",
]
