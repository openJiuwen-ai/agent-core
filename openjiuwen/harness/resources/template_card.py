# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Display card of an AgentTemplate package, read from its manifest alone.

``load_agent_template_package`` produces the runtime ``AgentTemplateSpec`` and
keeps only what the agent needs to run; the display fields of a manifest
(``display_name``, ``category``, ``tags``, ``version`` ...) do not belong on
that spec. The card carries them instead, mirroring the Card / Config split:
listing or showing a package reads only ``manifest.json`` and never loads the
persona, tools or skills.

The card is lenient on purpose: it only requires the right ``package_type``.
Whether a package can actually run is decided by the full loader.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any

LocalizedText = str | dict[str, str]
"""A manifest display value: a plain string or a ``{language: text}`` map."""

# Team / harness language codes and the keys manifests use for them.
_LANGUAGE_KEYS: dict[str, tuple[str, ...]] = {
    "cn": ("cn", "zh"),
    "zh": ("zh", "cn"),
    "en": ("en",),
}


@dataclass(frozen=True)
class AgentTemplateCard:
    """Display information of one AgentTemplate package."""

    name: str
    """``manifest.name``; empty when the manifest has none."""
    description: str
    """``manifest.description``; empty when the manifest has none."""
    display_name: LocalizedText = ""
    display_description: LocalizedText = ""
    category: str = ""
    tags: tuple[LocalizedText, ...] = ()
    """Manifest tags as written (plain strings or localized maps)."""
    version: str = ""
    avatar: str = ""
    """Package-relative avatar path as written in the manifest."""
    runtime: str = "native"
    """``"external"`` when the manifest declares a ``runtime``, else ``"native"``."""
    runtime_provider: str | None = None
    """``manifest.runtime.provider_name`` for an external runtime."""
    runtime_config: dict[str, Any] = field(default_factory=dict)
    """``manifest.runtime.config`` as written: what a host hands to the provider for this
    expert, without loading (and validating) the whole package."""
    skills: tuple[str, ...] = ()
    """Skill directory names, in manifest order."""
    connectors: tuple[str, ...] = ()
    """Host-managed MCP connector names (``{"connector": name}`` entries)."""

    def display_label(self, language: str) -> str:
        """Display name in ``language``, falling back to ``name``."""
        return localized_text(self.display_name, language) or self.name

    def display_text(self, language: str) -> str:
        """Display description in ``language``, falling back to ``description``."""
        return localized_text(self.display_description, language) or self.description

    def tag_labels(self, language: str) -> tuple[str, ...]:
        """Tags rendered in ``language``; empty tags are dropped."""
        labels = (localized_text(tag, language) for tag in self.tags)
        return tuple(label for label in labels if label)


def localized_text(value: LocalizedText, language: str) -> str:
    """Pick ``value`` in ``language`` (``cn`` also reads ``zh``), then English, then any text."""
    if isinstance(value, str):
        return value.strip()
    for key in (*_LANGUAGE_KEYS.get(language, (language,)), "en", "zh", "cn"):
        text = value.get(key, "").strip()
        if text:
            return text
    return next((text.strip() for text in value.values() if text.strip()), "")


def parse_agent_template_card(manifest: Mapping[str, Any]) -> AgentTemplateCard:
    """Build a card from a parsed ``manifest.json`` (pure).

    Raises:
        ValueError: The manifest is not an ``agent_template`` package.
    """
    if not isinstance(manifest, Mapping):
        raise ValueError("AgentTemplate manifest must contain a mapping")
    package_type = manifest.get("package_type")
    if package_type != "agent_template":
        raise ValueError(f"manifest declares package_type={package_type!r}, expected 'agent_template'")
    runtime = manifest.get("runtime")
    provider = runtime.get("provider_name") if isinstance(runtime, Mapping) else None
    return AgentTemplateCard(
        name=manifest_text(manifest.get("name")),
        description=manifest_text(manifest.get("description")),
        display_name=normalize_localized(manifest.get("display_name")),
        display_description=normalize_localized(manifest.get("display_description")),
        category=manifest.get("category") if isinstance(manifest.get("category"), str) else "",
        tags=tuple(
            normalize_localized(tag) for tag in _as_list(manifest.get("tags")) if isinstance(tag, (str, Mapping))
        ),
        version=manifest_text(manifest.get("version")),
        avatar=manifest.get("avatar") if isinstance(manifest.get("avatar"), str) else "",
        runtime="external" if runtime else "native",
        runtime_provider=provider.strip() if isinstance(provider, str) and provider.strip() else None,
        runtime_config=dict(runtime.get("config") or {}) if isinstance(runtime, Mapping) else {},
        skills=_skill_names(manifest.get("skills")),
        connectors=_connector_names(manifest.get("mcps")),
    )


async def read_agent_template_card(manifest_path: str | Path) -> AgentTemplateCard:
    """Async ``load_agent_template_card``, run in a worker thread."""
    return await asyncio.to_thread(load_agent_template_card, manifest_path)


def load_agent_template_card(manifest_path: str | Path) -> AgentTemplateCard:
    """Read only ``manifest.json`` and build its card.

    Args:
        manifest_path: The package's ``manifest.json``.

    Raises:
        ValueError: The manifest is missing, is not JSON, or is not an
            ``agent_template`` package.
    """
    return parse_agent_template_card(read_manifest_mapping(Path(manifest_path).expanduser()))


def read_manifest_mapping(path: Path) -> dict[str, Any]:
    """Read a JSON manifest, mapping every read / parse failure to ``ValueError``."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"manifest not readable: {path}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"manifest is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"manifest must contain a JSON object: {path}")
    return payload


def manifest_text(value: Any) -> str:
    """A manifest text field, stripped; anything but a string reads as empty."""
    return value.strip() if isinstance(value, str) else ""


def normalize_localized(value: Any) -> LocalizedText:
    """Normalize a manifest display value: keep strings, stringify map values, drop anything else."""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return {str(key): str(text) for key, text in value.items() if text is not None}
    return ""


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _skill_names(items: Any) -> tuple[str, ...]:
    names: list[str] = []
    for item in _as_list(items):
        raw = item if isinstance(item, str) else item.get("dir") if isinstance(item, Mapping) else None
        name = PurePath(raw).name if isinstance(raw, str) else ""
        if name and name not in names:
            names.append(name)
    return tuple(names)


def _connector_names(items: Any) -> tuple[str, ...]:
    names: list[str] = []
    for item in _as_list(items):
        connector = item.get("connector") if isinstance(item, Mapping) else None
        name = connector.strip() if isinstance(connector, str) else ""
        if name and name not in names:
            names.append(name)
    return tuple(names)


__all__ = [
    "AgentTemplateCard",
    "LocalizedText",
    "load_agent_template_card",
    "localized_text",
    "manifest_text",
    "normalize_localized",
    "parse_agent_template_card",
    "read_agent_template_card",
    "read_manifest_mapping",
]
