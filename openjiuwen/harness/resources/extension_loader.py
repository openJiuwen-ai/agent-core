# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Load Plugin / AgentTemplate packages into Specs (paths absolutized here)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.schema.deep_agent_spec import BuiltinToolSpec, ModelSpec, RailSpec
from openjiuwen.harness.schema.extension_spec import (
    AgentTemplateSpec,
    McpServerSpec,
    MemorySpec,
    PluginSpec,
    PromptSectionSpec,
    RubricSpec,
    SkillSpec,
)

# Allowed top-level keys when converting legacy plugin YAML before
# ``_load_plugin_legacy_yaml`` cherry-picks the ``PluginSpec`` fields it needs.
_CANONICAL_PLUGIN_YAML_FIELDS = frozenset(
    {
        "schema_version",
        "id",
        "source",
        "name",
        "description",
        "tools",
        "mcps",
        "rails",
        "prompt_sections",
        "skills",
        "metadata",
    }
)

_MANIFEST_NAMES = ("harness_config.yaml", "expert_harness.yaml", "harness.yaml")
_PACKAGE_MANIFEST_NAME = "manifest.json"
_SUBAGENT_MANIFEST_NAME = ".subagent.json"
# Legacy YAML package manifests that need sidecar aggregation + normalization
# before they validate as ``PluginSpec``. Precedence: harness_config.yaml over
# harness.yaml.
_LEGACY_PACKAGE_MANIFEST_NAMES = ("harness_config.yaml", "harness.yaml")
_MCP_DIR_MANIFEST_NAMES = ("mcps.json", "mcps.yaml")
_MCP_TRANSPORT_ALIASES = {
    "stdio": "stdio",
    "sse": "sse",
    "http": "streamable_http",
    "streamable-http": "streamable_http",
    "streamable_http": "streamable_http",
}
_LEGACY_AGENT_CONTROL_KEYS = {
    "add_general_purpose_agent",
    "completion_timeout",
    "context",
    "default_mode",
    "enable_async_subagent",
    "enable_task_loop",
    "enable_task_planning",
    "language",
    "max_iterations",
    "meta",
    "permissions",
    "progressive_tool_enabled",
    "prompt_mode",
    "restrict_to_work_dir",
    "stop_eval_conditions",
    "workspace",
}
# Legacy YAML often listed rail-owned capabilities under ``tools``. Those names
# are RAIL providers, not TOOL providers — remap before PluginSpec build.
_LEGACY_TOOL_TO_RAIL = {
    "todo": "core.task_planning",
    "core.todo": "core.task_planning",
    "lsp": "core.lsp",
    "core.lsp": "core.lsp",
    "ask_user": "core.ask_user",
    "core.ask_user": "core.ask_user",
    "ask_user_tool": "core.ask_user",
    "core.ask_user_tool": "core.ask_user",
    "filesystem": "core.sys_operation",
    "core.filesystem": "core.sys_operation",
    "shell": "core.sys_operation",
    "core.shell": "core.sys_operation",
    "bash": "core.sys_operation",
    "core.bash": "core.sys_operation",
    "powershell": "core.sys_operation",
    "core.powershell": "core.sys_operation",
    "code": "core.sys_operation",
    "core.code": "core.sys_operation",
}


def _find_legacy_yaml_manifest(path: str | Path) -> Path:
    """Locate a legacy ``harness_config.yaml`` / ``expert_harness.yaml`` / ``harness.yaml``.

    Internal helper for ``find_plugin_manifest``'s YAML fallback; not part of
    the public loader surface (there is no standalone legacy-only entry point).
    """
    requested_path = Path(path).expanduser()
    if requested_path.is_dir():
        for manifest_name in _MANIFEST_NAMES:
            manifest = requested_path / manifest_name
            if manifest.is_file():
                return manifest.resolve()
        expected = " or ".join(_MANIFEST_NAMES)
        raise FileNotFoundError(f"{expected} not found: {requested_path}")

    manifest = requested_path
    if manifest.name not in _MANIFEST_NAMES or not manifest.is_file():
        expected = " or ".join(_MANIFEST_NAMES)
        raise FileNotFoundError(f"{expected} not found: {requested_path}")
    return manifest.resolve()


def normalize_package_mcps(mcps: Any, package_dir: str | Path) -> list[dict[str, Any]]:
    """Expand ``{dir: ./mcps}`` refs and normalize config.yaml-style MCP entries.

    Used by Plugin package loading and by AgentTemplate packages whose
    ``manifest.json`` only points at an MCP directory.
    """
    return _normalize_mcps(mcps, Path(package_dir).expanduser().resolve())


# ---------------------------------------------------------------------------
# Plugin / AgentTemplate package loaders
# ---------------------------------------------------------------------------


def find_plugin_manifest(path: str | Path) -> Path:
    """Locate the manifest for ``load_plugin(path)``.

    An explicit file is parsed as-is (no sibling search). A directory is
    checked for ``manifest.json`` first; once present it is used regardless of
    its ``packageType`` (``load_plugin_package`` rejects the wrong type — no
    legacy YAML fallback is attempted once ``manifest.json`` exists). Only
    when ``manifest.json`` is absent does the legacy
    ``harness_config.yaml`` / ``expert_harness.yaml`` / ``harness.yaml``
    search apply.
    """
    requested_path = Path(path).expanduser()
    if requested_path.is_dir():
        manifest_json = requested_path / _PACKAGE_MANIFEST_NAME
        if manifest_json.is_file():
            return manifest_json.resolve()
        return _find_legacy_yaml_manifest(requested_path)

    if requested_path.name == _PACKAGE_MANIFEST_NAME:
        if not requested_path.is_file():
            raise FileNotFoundError(f"Plugin manifest not found: {requested_path}")
        return requested_path.resolve()
    return _find_legacy_yaml_manifest(requested_path)


def find_agent_template_manifest(path: str | Path) -> Path:
    """Locate the ``manifest.json`` for ``load_agent_template(path)``.

    Only ``manifest.json`` is accepted — no legacy YAML fallback. The caller
    passes either that file directly or a directory containing it.
    """
    requested_path = Path(path).expanduser()
    if requested_path.is_dir():
        manifest_json = requested_path / _PACKAGE_MANIFEST_NAME
        if not manifest_json.is_file():
            raise FileNotFoundError(f"{_PACKAGE_MANIFEST_NAME} not found: {requested_path}")
        return manifest_json.resolve()

    if requested_path.name != _PACKAGE_MANIFEST_NAME or not requested_path.is_file():
        raise FileNotFoundError(f"{_PACKAGE_MANIFEST_NAME} not found: {requested_path}")
    return requested_path.resolve()


def load_plugin_package(manifest_path: str | Path) -> PluginSpec:
    """Parse a Plugin ``manifest.json`` or legacy YAML manifest into a ``PluginSpec``.

    ``manifest_path`` must already be an absolute manifest path (as returned
    by ``find_plugin_manifest``). All resource paths written onto the
    returned ``PluginSpec`` are normalized absolute paths.
    """
    manifest = Path(manifest_path).expanduser().resolve(strict=True)
    if manifest.name == _PACKAGE_MANIFEST_NAME:
        return _load_plugin_manifest_json(manifest)
    return _load_plugin_legacy_yaml(manifest)


def load_agent_template_package(manifest_path: str | Path) -> AgentTemplateSpec:
    """Parse a root ``packageType=agent_template`` ``manifest.json`` into an ``AgentTemplateSpec``.
    """
    manifest = Path(manifest_path).expanduser().resolve(strict=True)
    if manifest.name != _PACKAGE_MANIFEST_NAME:
        raise ValueError(f"AgentTemplate manifest must be named {_PACKAGE_MANIFEST_NAME!r}: {manifest}")
    package_root = manifest.parent.resolve(strict=True)
    payload = _read_json_mapping(manifest)
    if payload.get("packageType") != "agent_template":
        raise ValueError(f"{manifest} declares packageType={payload.get('packageType')!r}, expected 'agent_template'")

    agent_card_payload = payload.get("agentCard")
    if not agent_card_payload:
        raise ValueError(f"AgentTemplate manifest {manifest} is missing required 'agentCard'")

    base_dir = manifest.parent
    subagents = [
        _load_agent_subtemplate(
            _resolve_new_manifest_path(
                str(item["dir"]), base_dir=base_dir, package_root=package_root, must_be_dir=True
            ),
            package_root=package_root,
        )
        for item in _as_list(payload.get("subagents"))
    ]
    return AgentTemplateSpec(
        agent_card=AgentCard.model_validate(agent_card_payload),
        model=_build_model_spec(payload.get("model"), base_dir=base_dir, package_root=package_root),
        prompt_sections=_build_persona_prompt_sections(
            payload.get("persona"), base_dir=base_dir, package_root=package_root
        ),
        tools=_build_tool_specs(payload.get("tools"), base_dir=base_dir, package_root=package_root),
        mcps=_build_mcp_specs(payload.get("mcps"), base_dir=base_dir, package_root=package_root),
        rails=_build_rail_specs(payload.get("rails"), base_dir=base_dir, package_root=package_root),
        skills=_build_skill_specs(payload.get("skills"), base_dir=base_dir, package_root=package_root),
        memories=[MemorySpec.model_validate(item) for item in _as_list(payload.get("memories"))],
        rubrics=[RubricSpec.model_validate(item) for item in _as_list(payload.get("rubrics"))],
        subagents=subagents,
        metadata=dict(payload.get("metadata") or {}),
    )


def _load_plugin_manifest_json(manifest: Path) -> PluginSpec:
    payload = _read_json_mapping(manifest)
    package_type = payload.get("packageType")
    if package_type != "plugin":
        raise ValueError(f"{manifest} declares packageType={package_type!r}, expected 'plugin'")
    for forbidden_key in ("persona", "agentCard", "model", "subagents", "memories", "rubrics"):
        if forbidden_key in payload:
            raise ValueError(f"Plugin manifest {manifest} must not declare {forbidden_key!r}")

    plugin_id = payload.get("id")
    if not plugin_id:
        raise ValueError(f"Plugin manifest {manifest} is missing required 'id'")

    base_dir = manifest.parent
    package_root = base_dir.resolve(strict=True)
    return PluginSpec(
        id=str(plugin_id),
        name=payload.get("name"),
        description=payload.get("description"),
        prompt_sections=[PromptSectionSpec.model_validate(item) for item in _as_list(payload.get("prompt_sections"))],
        tools=_build_tool_specs(payload.get("tools"), base_dir=base_dir, package_root=package_root),
        mcps=_build_mcp_specs(payload.get("mcps"), base_dir=base_dir, package_root=package_root),
        rails=_build_rail_specs(payload.get("rails"), base_dir=base_dir, package_root=package_root),
        skills=_build_skill_specs(payload.get("skills"), base_dir=base_dir, package_root=package_root),
        metadata=dict(payload.get("metadata") or {}),
    )


def _load_plugin_legacy_yaml(manifest: Path) -> PluginSpec:
    payload = _normalize_legacy_plugin_yaml(manifest)
    _remap_legacy_tools_to_rails(payload)
    package_dir = manifest.parent
    plugin_id = str(payload.get("id") or package_dir.name)
    return PluginSpec(
        id=plugin_id,
        name=payload.get("name"),
        description=payload.get("description"),
        prompt_sections=[PromptSectionSpec.model_validate(item) for item in payload.get("prompt_sections", [])],
        tools=[
            BuiltinToolSpec.model_validate(_absolutize_legacy_resource(item, package_dir=package_dir, kind="tool"))
            for item in payload.get("tools", [])
        ],
        mcps=[
            McpServerSpec.model_validate(_absolutize_legacy_mcp(item, package_dir=package_dir))
            for item in payload.get("mcps", [])
        ],
        rails=[
            RailSpec.model_validate(_absolutize_legacy_resource(item, package_dir=package_dir, kind="rail"))
            for item in payload.get("rails", [])
        ],
        skills=[
            SkillSpec.model_validate(_absolutize_legacy_skill(item, package_dir=package_dir))
            for item in payload.get("skills", [])
        ],
        metadata=dict(payload.get("metadata") or {}),
    )


def _load_agent_subtemplate(subagent_dir: Path, *, package_root: Path) -> AgentTemplateSpec:
    manifest = subagent_dir / _SUBAGENT_MANIFEST_NAME
    if not manifest.is_file():
        raise FileNotFoundError(f"{_SUBAGENT_MANIFEST_NAME} not found for subagent template: {subagent_dir}")
    payload = _read_json_mapping(manifest)
    if "subagents" in payload:
        raise ValueError(
            f"Subagent template {manifest} must not declare 'subagents' "
            "(only the root agent_template may declare direct subagents)"
        )
    agent_name = payload.get("agentName")
    if not agent_name:
        raise ValueError(f"Subagent template {manifest} is missing required 'agentName'")

    base_dir = manifest.parent
    agent_card = AgentCard(
        id=str(agent_name),
        name=str(agent_name),
        description=_first_display_description(payload.get("displayDescription")),
    )
    return AgentTemplateSpec(
        agent_card=agent_card,
        model=_build_model_spec(payload.get("model"), base_dir=base_dir, package_root=package_root),
        prompt_sections=_build_persona_prompt_sections(
            payload.get("persona"), base_dir=base_dir, package_root=package_root
        ),
        tools=_build_tool_specs(payload.get("tools"), base_dir=base_dir, package_root=package_root),
        mcps=_build_mcp_specs(payload.get("mcps"), base_dir=base_dir, package_root=package_root),
        rails=_build_rail_specs(payload.get("rails"), base_dir=base_dir, package_root=package_root),
        skills=_build_skill_specs(payload.get("skills"), base_dir=base_dir, package_root=package_root),
    )


def _first_display_description(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    if value.get("en"):
        return str(value["en"])
    if value.get("zh"):
        return str(value["zh"])
    for text in value.values():
        if text:
            return str(text)
    return ""


def _read_json_mapping(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Manifest must contain a mapping: {path}")
    return payload


def _resolve_new_manifest_path(
    raw_path: str,
    *,
    base_dir: Path,
    package_root: Path,
    must_be_dir: bool,
) -> Path:
    """Resolve a package-relative manifest path field per the new-manifest rules.

    Absolute inputs are rejected; the resolved candidate must stay within
    ``package_root`` and must exist as the expected file/directory kind.
    """
    if Path(raw_path).expanduser().is_absolute():
        raise ValueError(f"manifest path must be package-relative, got absolute path: {raw_path!r}")
    candidate = (base_dir / raw_path).resolve(strict=True)
    if not candidate.is_relative_to(package_root):
        raise ValueError(f"manifest path {raw_path!r} escapes package root {package_root}")
    if must_be_dir and not candidate.is_dir():
        raise ValueError(f"manifest path {raw_path!r} must be a directory: {candidate}")
    if not must_be_dir and not candidate.is_file():
        raise ValueError(f"manifest path {raw_path!r} must be a file: {candidate}")
    return candidate


def _build_persona_prompt_sections(persona: Any, *, base_dir: Path, package_root: Path) -> list[PromptSectionSpec]:
    if not persona:
        return []
    if not isinstance(persona, dict) or "dir" not in persona:
        raise ValueError(f"persona must be a mapping with 'dir': {persona!r}")

    persona_dir = _resolve_new_manifest_path(
        str(persona["dir"]), base_dir=base_dir, package_root=package_root, must_be_dir=True
    )
    markdown_files = sorted(
        (candidate for candidate in persona_dir.rglob("*.md") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(persona_dir).as_posix(),
    )
    if not markdown_files:
        raise ValueError(f"persona dir {persona_dir} has no markdown files")

    text = "\n\n".join(md_file.read_text(encoding="utf-8") for md_file in markdown_files)
    return [PromptSectionSpec(name="identity", content={"cn": text, "en": text}, priority=10)]


def _build_model_spec(model_ref: Any, *, base_dir: Path, package_root: Path) -> ModelSpec | None:
    if not model_ref:
        return None
    if not isinstance(model_ref, dict) or "file" not in model_ref:
        raise ValueError(f"model must be a mapping with 'file': {model_ref!r}")

    model_path = _resolve_new_manifest_path(
        str(model_ref["file"]), base_dir=base_dir, package_root=package_root, must_be_dir=False
    )
    payload = _read_json_mapping(model_path)
    model_payload = payload.get("model")
    if model_payload is None:
        raise ValueError(f"model file {model_path} is missing the outer 'model' key")
    return ModelSpec.model_validate(model_payload)


def _build_tool_specs(items: Any, *, base_dir: Path, package_root: Path) -> list[BuiltinToolSpec]:
    specs: list[BuiltinToolSpec] = []
    for item in _as_list(items):
        if not isinstance(item, dict) or "file" not in item:
            raise ValueError(f"tool entry must be a mapping with 'file': {item!r}")
        file_path = _resolve_new_manifest_path(
            str(item["file"]), base_dir=base_dir, package_root=package_root, must_be_dir=False
        )
        class_name = item.get("class") or item.get("class_name")
        specs.append(
            BuiltinToolSpec(
                type="harness.tool.file",
                params={"file_path": str(file_path), "class_name": class_name},
            )
        )
    return specs


def _build_rail_specs(items: Any, *, base_dir: Path, package_root: Path) -> list[RailSpec]:
    specs: list[RailSpec] = []
    for item in _as_list(items):
        if not isinstance(item, dict) or "file" not in item:
            raise ValueError(f"rail entry must be a mapping with 'file': {item!r}")
        file_path = _resolve_new_manifest_path(
            str(item["file"]), base_dir=base_dir, package_root=package_root, must_be_dir=False
        )
        class_name = item.get("class") or item.get("class_name")
        specs.append(
            RailSpec(
                type="harness.rail.file",
                params={"file_path": str(file_path), "class_name": class_name},
            )
        )
    return specs


def _build_skill_specs(items: Any, *, base_dir: Path, package_root: Path) -> list[SkillSpec]:
    specs: list[SkillSpec] = []
    for raw_item in _as_list(items):
        item = {"dir": raw_item} if isinstance(raw_item, str) else raw_item
        if not isinstance(item, dict) or "dir" not in item:
            raise ValueError(f"skill entry must be a mapping with 'dir': {raw_item!r}")
        directory = _resolve_new_manifest_path(
            str(item["dir"]), base_dir=base_dir, package_root=package_root, must_be_dir=True
        )
        specs.append(
            SkillSpec(
                dir=str(directory),
                mode=_skill_mode(item.get("mode", "all")),
                enabled_skills=_skill_enabled_list(item.get("enabled_skills")),
            )
        )
    return specs


def _skill_mode(value: Any) -> Literal["all", "auto_list"]:
    if value in ("all", "auto_list"):
        return value
    raise ValueError(f"skill mode must be 'all' or 'auto_list', got {value!r}")


def _skill_enabled_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item) for item in value]
    raise ValueError(f"enabled_skills must be a list, got {value!r}")


def _build_mcp_specs(items: Any, *, base_dir: Path, package_root: Path) -> list[McpServerSpec]:
    specs: list[McpServerSpec] = []
    for item in _as_list(items):
        if not isinstance(item, dict):
            raise ValueError(f"mcp entry must be a mapping: {item!r}")
        if "dir" in item and not _looks_like_mcp_server_entry(item):
            mcp_dir = _resolve_new_manifest_path(
                str(item["dir"]), base_dir=base_dir, package_root=package_root, must_be_dir=True
            )
            for raw_entry in _load_mcp_dir_entries(mcp_dir):
                specs.append(_build_mcp_server_spec(raw_entry, base_dir=base_dir, package_root=package_root))
            continue
        specs.append(_build_mcp_server_spec(item, base_dir=base_dir, package_root=package_root))
    return specs


def _load_mcp_dir_entries(mcp_dir: Path) -> list[dict[str, Any]]:
    for filename in _MCP_DIR_MANIFEST_NAMES:
        path = mcp_dir / filename
        if not path.is_file():
            continue
        payload = _load_data_file(path)
        servers = payload.get("servers", payload.get("mcps", [])) if isinstance(payload, dict) else payload
        return [item for item in _as_list(servers) if isinstance(item, dict)]
    raise FileNotFoundError(f"MCP dir {mcp_dir} must contain one of: {', '.join(_MCP_DIR_MANIFEST_NAMES)}")


def _build_mcp_server_spec(item: dict[str, Any], *, base_dir: Path, package_root: Path) -> McpServerSpec:
    entry = _normalize_mcp_server_entry(item)
    if entry is None:
        raise ValueError(f"mcp entry is disabled and cannot be used: {item!r}")
    # MCP cwd is the stdio child process working directory, not an executable
    # resource: keep containment + reject-absolute (per package path contract)
    # but do NOT strict-resolve or is_dir() it — the dir need only exist when
    # the child starts, not at load time.
    raw_cwd = entry.get("cwd")
    if raw_cwd:
        cwd_path = Path(raw_cwd).expanduser()
        if cwd_path.is_absolute():
            raise ValueError(f"mcp cwd must be package-relative, got absolute path: {raw_cwd!r}")
        candidate = (base_dir / cwd_path).resolve()
        if not candidate.is_relative_to(package_root):
            raise ValueError(f"mcp cwd {raw_cwd!r} escapes package root {package_root}")
        entry["cwd"] = str(candidate)
    else:
        entry["cwd"] = str(base_dir)
    return McpServerSpec.model_validate(entry)


def _absolutize_legacy_resource(item: dict[str, Any], *, package_dir: Path, kind: str) -> dict[str, Any]:
    if not isinstance(item, dict) or item.get("type") != f"harness.{kind}.file":
        return item
    params = dict(item.get("params") or {})
    file_path = params.get("file_path")
    if file_path:
        params["file_path"] = str(_resolve_legacy_path(str(file_path), package_dir))
    return {**item, "params": params}


def _absolutize_legacy_skill(item: dict[str, Any], *, package_dir: Path) -> dict[str, Any]:
    directory = item.get("dir") if isinstance(item, dict) else None
    if not directory:
        return item
    return {**item, "dir": str(_resolve_legacy_path(str(directory), package_dir))}


def _absolutize_legacy_mcp(item: dict[str, Any], *, package_dir: Path) -> dict[str, Any]:
    if not isinstance(item, dict):
        return item
    cwd = item.get("cwd")
    if not cwd:
        # absolute path so runtime never receives an unset cwd.
        return {**item, "cwd": str(package_dir.resolve())}
    return {**item, "cwd": str(_resolve_legacy_path(str(cwd), package_dir))}


def _resolve_legacy_path(raw_path: str, package_dir: Path) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = package_dir / candidate
    return candidate.resolve(strict=True)


def _normalize_legacy_plugin_yaml(manifest: Path) -> dict[str, Any]:
    payload = _load_yaml_mapping(manifest)
    package_dir = manifest.parent
    if payload.get("schema_version") == "expert_harness.v1":
        normalized = dict(payload)
        normalized["mcps"] = _normalize_mcps(normalized.get("mcps"), package_dir)
        return normalized
    if manifest.name not in _LEGACY_PACKAGE_MANIFEST_NAMES:
        normalized = dict(payload)
        if "mcps" in normalized:
            normalized["mcps"] = _normalize_mcps(normalized.get("mcps"), package_dir)
        return normalized

    normalized = dict(payload)
    resources = normalized.pop("resources", None)
    if isinstance(resources, dict):
        for key in ("tools", "mcps", "rails", "prompt_sections", "skills"):
            _merge_items(normalized, key, resources.get(key))

    _merge_legacy_prompt_sections(normalized)
    _drop_legacy_agent_control_fields(normalized)
    normalized["schema_version"] = "expert_harness.v1"
    normalized.setdefault("id", str(normalized.get("name") or package_dir.name))
    normalized.setdefault("name", package_dir.name)
    _merge_sidecar_prompt_sections(normalized, package_dir)
    _merge_list_file(normalized, package_dir / "tools" / "tools.yaml", "tools")
    _merge_list_file(normalized, package_dir / "mcps" / "mcps.yaml", "mcps")
    _merge_list_file(normalized, package_dir / "mcps" / "mcps.json", "mcps")
    _merge_list_file(normalized, package_dir / "rails" / "rails.yaml", "rails")
    _merge_list_file(normalized, package_dir / "skills" / "skills.yaml", "skills")
    normalized["skills"] = _normalize_skills(normalized.get("skills", []))
    normalized["mcps"] = _normalize_mcps(normalized.get("mcps"), package_dir)
    normalized["tools"] = _normalize_resource_items(
        normalized.get("tools", []),
        kind="tool",
        package_dir=package_dir,
    )
    normalized["rails"] = _normalize_resource_items(
        normalized.get("rails", []),
        kind="rail",
        package_dir=package_dir,
    )
    # Drop stray legacy top-level keys (e.g. role/version in hand-written harness.yaml).
    # The old build_from_expert_harness_pkg read only known keys; mirror that so the
    # canonical spec's extra="forbid" does not reject legacy packages.
    return {key: value for key, value in normalized.items() if key in _CANONICAL_PLUGIN_YAML_FIELDS}


def _remap_legacy_tools_to_rails(payload: dict[str, Any]) -> None:
    """Move legacy tool-group aliases onto rails; drop them from tools."""
    tools = list(payload.get("tools") or [])
    rails = list(payload.get("rails") or [])
    rail_types: set[str] = set()
    for rail in rails:
        if isinstance(rail, dict) and rail.get("type") is not None:
            rail_types.add(str(rail["type"]))

    kept_tools: list[Any] = []
    for tool in tools:
        if isinstance(tool, str):
            raw_type = tool
        elif isinstance(tool, dict) and tool.get("type") is not None:
            raw_type = str(tool["type"])
        else:
            kept_tools.append(tool)
            continue

        rail_type = _LEGACY_TOOL_TO_RAIL.get(raw_type)
        if rail_type is None:
            kept_tools.append(tool)
            continue
        if rail_type not in rail_types:
            rails.append({"type": rail_type, "params": {}})
            rail_types.add(rail_type)

    payload["tools"] = kept_tools
    payload["rails"] = rails


def _drop_legacy_agent_control_fields(payload: dict[str, Any]) -> None:
    for key in _LEGACY_AGENT_CONTROL_KEYS:
        payload.pop(key, None)


def _merge_legacy_prompt_sections(payload: dict[str, Any]) -> None:
    prompts = payload.pop("prompts", None)
    if not isinstance(prompts, dict):
        return

    for section in _as_list(prompts.get("sections")):
        if not isinstance(section, dict):
            continue
        normalized = _normalize_legacy_prompt_section(section)
        if "filename" in normalized:
            _merge_items(payload, "file_sections", normalized)
            continue
        _merge_items(payload, "prompt_sections", normalized)


def _normalize_legacy_prompt_section(section: dict[str, Any]) -> dict[str, Any]:
    content = _normalize_legacy_section_content(section.get("content"))
    render_params = dict(section.get("render_params") or {})
    file_name = section.get("file")
    if file_name is not None:
        return {
            "filename": str(file_name),
            "content": content,
            "render_params": render_params,
        }

    name = str(section["name"])
    priority = section.get("priority")
    if priority is None:
        priority = 10 if name == "identity" else 30
    return {
        "name": name,
        "content": content,
        "priority": priority,
        "render_params": render_params,
    }


def _normalize_legacy_section_content(content: Any) -> dict[str, str]:
    if content is None:
        return {}
    if isinstance(content, str):
        return _section_content(content)
    if isinstance(content, dict):
        return {str(language): str(text) for language, text in content.items()}
    return {}


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    payload = _load_data_file(path)
    if not isinstance(payload, dict):
        raise ValueError(f"Harness manifest must contain a mapping: {path}")
    return payload


def _load_data_file(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return yaml.safe_load(text) or {}


def _normalize_mcps(items: Any, package_dir: Path) -> list[dict[str, Any]]:
    """Expand ``{dir: ...}`` refs and normalize config.yaml-style MCP entries."""
    normalized: list[dict[str, Any]] = []
    for item in _as_list(items):
        if not isinstance(item, dict):
            continue
        if "dir" in item and not _looks_like_mcp_server_entry(item):
            normalized.extend(_load_mcps_from_dir(package_dir, str(item["dir"])))
            continue
        entry = _normalize_mcp_server_entry(item)
        if entry is not None:
            normalized.append(entry)
    return normalized


def _looks_like_mcp_server_entry(item: dict[str, Any]) -> bool:
    for key in (
        "type",
        "transport",
        "server_name",
        "name",
        "command",
        "url",
        "server_id",
    ):
        if key in item:
            return True
    return False


def _load_mcps_from_dir(package_dir: Path, relative_dir: str) -> list[dict[str, Any]]:
    mcp_dir = Path(relative_dir)
    if not mcp_dir.is_absolute():
        mcp_dir = (package_dir / mcp_dir).resolve()
    else:
        mcp_dir = mcp_dir.resolve()

    for filename in _MCP_DIR_MANIFEST_NAMES:
        path = mcp_dir / filename
        if not path.is_file():
            continue
        payload = _load_data_file(path)
        servers = payload
        if isinstance(payload, dict):
            servers = payload.get("servers", payload.get("mcps", []))
        normalized: list[dict[str, Any]] = []
        for item in _as_list(servers):
            if not isinstance(item, dict):
                continue
            entry = _normalize_mcp_server_entry(item)
            if entry is not None:
                normalized.append(entry)
        return normalized

    raise FileNotFoundError(f"MCP dir {mcp_dir} must contain one of: {', '.join(_MCP_DIR_MANIFEST_NAMES)}")


def _normalize_mcp_server_entry(item: dict[str, Any]) -> dict[str, Any] | None:
    """Map jiuwenswarm ``mcp.servers`` fields onto ``McpServerSpec``."""
    raw = dict(item)
    if "enabled" in raw and not bool(raw.pop("enabled")):
        return None

    transport = str(raw.pop("transport", raw.pop("type", "stdio")) or "stdio").strip().lower()
    client_type = _MCP_TRANSPORT_ALIASES.get(transport)
    if client_type is None:
        raise ValueError(f"Unsupported MCP transport/type: {transport!r}")

    server_name = raw.pop("server_name", None) or raw.pop("name", None)
    server_id = raw.pop("server_id", None)
    url = raw.pop("url", None)
    command = str(raw.pop("command", "") or "")
    args = raw.pop("args", None) or []
    env = raw.pop("env", None) or {}
    cwd = raw.pop("cwd", None)
    params = dict(raw.pop("params", None) or {})
    auth_headers = dict(raw.pop("auth_headers", None) or raw.pop("headers", None) or {})
    auth_query_params = dict(raw.pop("auth_query_params", None) or {})
    timeout_s = raw.pop("timeout_s", None)
    if timeout_s is not None and "timeout_s" not in params:
        params["timeout_s"] = int(timeout_s)

    # Ignore unknown leftover keys from package metadata (e.g. description).
    raw.pop("description", None)
    raw.pop("dir", None)

    entry: dict[str, Any] = {
        "type": client_type,
        "server_name": str(server_name).strip() if server_name else None,
        "command": command,
        "args": [str(value) for value in args] if isinstance(args, list) else [],
        "env": {str(key): str(value) for key, value in dict(env).items()},
        "params": params,
        "auth_headers": {str(key): str(value) for key, value in auth_headers.items()},
        "auth_query_params": {str(key): str(value) for key, value in auth_query_params.items()},
    }
    if server_id is not None and str(server_id).strip():
        entry["server_id"] = str(server_id).strip()
    if url is not None and str(url).strip():
        entry["url"] = str(url).strip()
    if cwd is not None and str(cwd).strip():
        entry["cwd"] = str(cwd).strip()
    return entry


def _merge_sidecar_prompt_sections(payload: dict[str, Any], package_dir: Path) -> None:
    for filename, name, priority in (
        ("identity.md", "identity", 10),
        ("soul.md", "soul", 20),
    ):
        path = package_dir / filename
        if path.is_file():
            _merge_items(
                payload,
                "prompt_sections",
                {
                    "name": name,
                    "content": _section_content(path.read_text(encoding="utf-8")),
                    "priority": priority,
                },
            )

    sections_path = package_dir / "prompt_sections" / "sections.yaml"
    if not sections_path.is_file():
        return
    sections = _load_yaml_mapping(sections_path).get("sections", [])
    for section in _as_list(sections):
        if isinstance(section, dict):
            _merge_items(payload, "prompt_sections", _normalize_prompt_section(section, package_dir))


def _normalize_prompt_section(section: dict[str, Any], package_dir: Path) -> dict[str, Any]:
    normalized = dict(section)
    file_name = normalized.pop("file", None)
    if file_name is not None:
        section_path = _resolve_section_file(package_dir, str(file_name))
        normalized["content"] = _section_content(section_path.read_text(encoding="utf-8"))
    elif isinstance(normalized.get("content"), str):
        normalized["content"] = _section_content(str(normalized["content"]))
    return normalized


def _resolve_section_file(package_dir: Path, file_name: str) -> Path:
    path = Path(file_name)
    if path.is_absolute():
        return path
    direct = package_dir / path
    if direct.is_file():
        return direct
    return package_dir / "prompt_sections" / "files" / path


def _merge_list_file(payload: dict[str, Any], path: Path, key: str) -> None:
    if not path.is_file():
        return
    data = _load_data_file(path)
    if isinstance(data, dict):
        data = data.get(key, data.get("servers", []))
    _merge_items(payload, key, data)


def _merge_items(payload: dict[str, Any], key: str, value: Any) -> None:
    if value is None:
        return
    payload.setdefault(key, [])
    payload[key].extend(_as_list(value))


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _section_content(content: str) -> dict[str, str]:
    return {"cn": content, "en": content}


def _normalize_skill(item: Any) -> Any:
    if isinstance(item, str):
        return {"dir": item}
    return item


def _normalize_skills(items: Any) -> list[Any]:
    normalized: list[Any] = []
    for item in _as_list(items):
        if isinstance(item, dict) and isinstance(item.get("dirs"), list):
            normalized.extend({"dir": value} for value in item["dirs"])
            continue
        normalized.append(_normalize_skill(item))
    return normalized


def _normalize_resource_items(items: Any, *, kind: str, package_dir: Path) -> list[Any]:
    normalized: list[Any] = []
    for item in _as_list(items):
        normalized.extend(_as_list(_normalize_resource_item(item, kind=kind, package_dir=package_dir)))
    return normalized


def _has_module_class_resource_spec(normalized: dict[str, Any], params: dict[str, Any]) -> bool:
    if not isinstance(normalized.get("module"), str):
        return False
    class_keys = ("class", "class_name")
    return any(key in normalized for key in class_keys) or params.get("class_name") is not None


def _normalize_resource_item(item: Any, *, kind: str, package_dir: Path) -> Any:
    if isinstance(item, str):
        return {"type": f"core.{item}", "params": {}}
    if not isinstance(item, dict):
        return item

    normalized = dict(item)
    if normalized.get("type") == "builtin":
        return _normalize_builtin_resource_item(normalized, kind=kind)
    if normalized.get("type") == "entry_point":
        return _normalize_entry_point_resource_item(normalized, kind=kind)
    if "type" in normalized and normalized["type"] != "package":
        return _normalize_params(normalized)

    params = _pop_legacy_params(normalized)
    file_name = normalized.pop("file", None)
    if file_name is not None:
        params.setdefault("file_path", file_name)
        if "class" in normalized:
            params.setdefault("class_name", normalized.pop("class"))
        if "class_name" in normalized:
            params.setdefault("class_name", normalized.pop("class_name"))
        return {"type": f"harness.{kind}.file", "params": params}

    if _has_module_class_resource_spec(normalized, params):
        module_name = str(normalized.pop("module"))
        class_name = normalized.pop("class_name", None)
        if class_name is None:
            class_name = normalized.pop("class", None)
        if class_name is None:
            class_name = params.pop("class_name", None)
        file_path = _module_to_package_file(module_name, package_dir)
        if file_path is None:
            params.setdefault("import_path", f"{module_name}.{class_name}")
            return {"type": f"harness.{kind}.import", "params": params}
        params.setdefault("file_path", file_path)
        params.setdefault("class_name", class_name)
        return {"type": f"harness.{kind}.file", "params": params}

    return item


def _normalize_builtin_resource_item(item: dict[str, Any], *, kind: str) -> list[dict[str, Any]]:
    params = _pop_legacy_params(item)
    names = item.pop("names", None)
    if names is None and item.get("name") is not None:
        names = [item.pop("name")]

    normalized: list[dict[str, Any]] = []
    for name in _as_list(names):
        normalized.append({"type": f"core.{name}", "params": dict(params)})
    return normalized


def _normalize_entry_point_resource_item(item: dict[str, Any], *, kind: str) -> dict[str, Any]:
    params = _pop_legacy_params(item)
    name = item.pop("name", None)
    if name is not None:
        params.setdefault("name", name)
    return {"type": f"harness.{kind}.entry_point", "params": params}


def _normalize_params(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    normalized["params"] = _pop_legacy_params(normalized)
    return normalized


def _pop_legacy_params(item: dict[str, Any]) -> dict[str, Any]:
    params = dict(item.pop("params", None) or {})
    params.update(dict(item.pop("kwargs", None) or {}))
    return params


def _module_to_package_file(module_name: str, package_dir: Path) -> str | None:
    prefix = f"openjiuwen.extensions.harness.{package_dir.name}."
    if not module_name.startswith(prefix):
        return None
    relative_module = module_name[len(prefix):]
    return f"{relative_module.replace('.', '/')}.py"


__all__ = [
    "find_agent_template_manifest",
    "find_plugin_manifest",
    "load_agent_template_package",
    "load_plugin_package",
    "normalize_package_mcps",
]
