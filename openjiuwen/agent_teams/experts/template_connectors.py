# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Mount an expert template's host-managed connectors onto its Team member.

An AgentTemplate manifest may bind host-managed connectors
(``"mcps": [{"connector": "amap"}]``). The native loader keeps only their
names (``AgentTemplateSpec.connectors``), so the capability snapshot stored on
the member row never carries server configs or credentials. When the member is
built, the host resolver registered here turns those names into the
connectors' resources, which are attached to that one member:

* MCP servers (``McpServerConfig``, credentials resolved) are appended to
  ``DeepAgentSpec.mcps``;
* bundled Skill directories (one directory per Skill — CLI and skill-only
  connectors carry nothing else) are appended to the snapshot's ``skills`` so
  the template hot-mount binds them, and their names join
  ``DeepAgentSpec.skills``. Connector Skills count as the expert's own Skills:
  they seed the member's visibility allow-list exactly like template Skills.

Without a registered resolver nothing changes. A resolver failure only drops
the connectors (logged); it never fails the member build.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any, Callable

from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.schema.deep_agent_spec import DeepAgentSpec


@dataclass(frozen=True)
class ResolvedConnectors:
    """What the host resolved for one member's connectors."""

    mcps: list[McpServerConfig] = field(default_factory=list)
    """MCP servers to mount, credentials already resolved."""
    skill_dirs: list[str] = field(default_factory=list)
    """Absolute Skill directories, one per Skill (the directory name is the Skill name)."""


TemplateConnectorResolver = Callable[[list[str], str, str], ResolvedConnectors]
"""``(connector_names, team_name, member_name) -> resolved resources``."""

_resolver: TemplateConnectorResolver | None = None


def register_template_connector_resolver(resolver: TemplateConnectorResolver | None) -> None:
    """Install (or clear with ``None``) the process-wide host connector resolver."""
    global _resolver
    _resolver = resolver


def template_connector_names(snapshot: dict[str, Any] | None) -> list[str]:
    """Connector names of a capability snapshot, in order, without duplicates."""
    names: list[str] = []
    for raw in (snapshot or {}).get("connectors") or []:
        name = raw.strip() if isinstance(raw, str) else ""
        if name and name not in names:
            names.append(name)
    return names


def attach_template_connectors(agent_spec: DeepAgentSpec, *, team_name: str, member_name: str) -> DeepAgentSpec:
    """Return ``agent_spec`` with its template connectors' MCPs and Skills attached.

    Servers already present (same ``server_name``) and Skills already declared
    (same directory or name) are kept and not duplicated.
    """
    snapshot = agent_spec.agent_template_spec
    names = template_connector_names(snapshot)
    if not names:
        return agent_spec
    if _resolver is None:
        team_logger.warning(
            "[expert] member {!r} declares connectors {} but no connector resolver is registered",
            member_name,
            names,
        )
        return agent_spec
    try:
        resolved = _resolver(names, team_name, member_name)
    except Exception as exc:  # noqa: BLE001 — a bad connector must not fail the member build
        team_logger.warning("[expert] member {!r} connector resolution failed for {}: {}", member_name, names, exc)
        return agent_spec
    if not isinstance(resolved, ResolvedConnectors):
        team_logger.warning("[expert] member {!r} connector resolver returned {!r}", member_name, resolved)
        return agent_spec

    # The resolver is host code: what it returns is validated item by item, so a
    # malformed entry is skipped (logged) rather than failing the member build.
    existing_mcps = list(agent_spec.mcps or [])
    added_mcps = _new_mcps(existing_mcps, resolved.mcps, member_name)
    snapshot_skills = list((snapshot or {}).get("skills") or [])
    skill_names = list(agent_spec.skills or [])
    added_skills = _append_skill_dirs(snapshot_skills, skill_names, resolved.skill_dirs, member_name)

    team_logger.info(
        "[expert] member {!r} connectors={} mounted_mcps={} mounted_skills={}",
        member_name,
        names,
        [cfg.server_name for cfg in added_mcps],
        len(added_skills),
    )
    update: dict[str, Any] = {}
    if added_mcps:
        update["mcps"] = [*existing_mcps, *added_mcps]
    if added_skills:
        update["agent_template_spec"] = {**(snapshot or {}), "skills": snapshot_skills}
        update["skills"] = skill_names
    if not update:
        return agent_spec
    return agent_spec.model_copy(update=update)


def _new_mcps(existing: list[McpServerConfig], candidates: list[Any], member_name: str) -> list[McpServerConfig]:
    """Valid resolved servers not already present, deduplicated by ``server_name``."""
    seen = {cfg.server_name for cfg in existing}
    added: list[McpServerConfig] = []
    for cfg in candidates or []:
        if not isinstance(cfg, McpServerConfig):
            team_logger.warning("[expert] member {!r} skipped invalid connector MCP: {!r}", member_name, cfg)
            continue
        if cfg.server_name not in seen:
            seen.add(cfg.server_name)
            added.append(cfg)
    return added


def _append_skill_dirs(
    snapshot_skills: list[Any],
    skill_names: list[str],
    candidates: list[Any],
    member_name: str,
) -> list[str]:
    """Append valid, new Skill dirs to ``snapshot_skills`` / ``skill_names`` in place.

    Returns:
        The names of the Skills that were appended.
    """
    seen_dirs = {item.get("dir") for item in snapshot_skills if isinstance(item, dict)}
    added: list[str] = []
    for directory in candidates or []:
        if not isinstance(directory, str) or not PurePath(directory).is_absolute():
            team_logger.warning("[expert] member {!r} skipped invalid skill dir: {!r}", member_name, directory)
            continue
        name = PurePath(directory).name
        if not name or directory in seen_dirs or name in skill_names:
            continue
        snapshot_skills.append({"dir": directory, "mode": "all", "enabled_skills": None})
        seen_dirs.add(directory)
        skill_names.append(name)
        added.append(name)
    return added


__all__ = [
    "ResolvedConnectors",
    "TemplateConnectorResolver",
    "attach_template_connectors",
    "register_template_connector_resolver",
    "template_connector_names",
]
