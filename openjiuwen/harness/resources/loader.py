# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Load canonical ExpertHarness manifests as pure data."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from openjiuwen.harness.schema.expert_harness_spec import ExpertHarnessSpec, ResourceSource

_CANONICAL_SPEC_FIELDS = frozenset(ExpertHarnessSpec.model_fields)

_MANIFEST_NAMES = ("harness_config.yaml", "expert_harness.yaml", "harness.yaml")
# Legacy package manifests that require sidecar aggregation + normalization before
# they validate as a canonical ExpertHarnessSpec. Precedence: harness_config.yaml
# takes priority over harness.yaml (mirrors build_from_expert_harness_pkg).
_LEGACY_PACKAGE_MANIFEST_NAMES = ("harness_config.yaml", "harness.yaml")
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
    "progressive_tool_always_visible_tools",
    "progressive_tool_default_visible_tools",
    "progressive_tool_enabled",
    "progressive_tool_max_loaded_tools",
    "prompt_mode",
    "restrict_to_work_dir",
    "stop_eval_conditions",
    "workspace",
}


def find_expert_harness_manifest(path: str | Path) -> Path:
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


def load_expert_harness_spec(path: str | Path) -> ExpertHarnessSpec:
    manifest = find_expert_harness_manifest(path)
    payload = _normalize_manifest_payload(manifest)
    spec = ExpertHarnessSpec.model_validate(payload)
    source = ResourceSource(uri=str(manifest), root=str(manifest.parent))
    return spec.model_copy(update={"source": source})


def _normalize_manifest_payload(manifest: Path) -> dict[str, Any]:
    payload = _load_yaml_mapping(manifest)
    if payload.get("schema_version") == "expert_harness.v1":
        return payload
    if manifest.name not in _LEGACY_PACKAGE_MANIFEST_NAMES:
        return payload

    package_dir = manifest.parent
    normalized = dict(payload)
    resources = normalized.pop("resources", None)
    if isinstance(resources, dict):
        for key in ("tools", "mcps", "rails", "prompt_sections", "skills", "subagents"):
            _merge_items(normalized, key, resources.get(key))

    _merge_legacy_prompt_sections(normalized)
    _drop_legacy_agent_control_fields(normalized)
    normalized["schema_version"] = "expert_harness.v1"
    normalized.setdefault("id", str(normalized.get("name") or package_dir.name))
    normalized.setdefault("name", package_dir.name)
    _merge_sidecar_prompt_sections(normalized, package_dir)
    _merge_list_file(normalized, package_dir / "tools" / "tools.yaml", "tools")
    _merge_list_file(normalized, package_dir / "mcps" / "mcps.yaml", "mcps")
    _merge_list_file(normalized, package_dir / "rails" / "rails.yaml", "rails")
    _merge_list_file(normalized, package_dir / "skills" / "skills.yaml", "skills")
    _merge_list_file(normalized, package_dir / "subagents" / "subagents.yaml", "subagents")
    normalized["subagents"] = _normalize_subagents(normalized.get("subagents", []), package_dir)
    normalized["skills"] = _normalize_skills(normalized.get("skills", []))
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
    return {key: value for key, value in normalized.items() if key in _CANONICAL_SPEC_FIELDS}


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
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"ExpertHarness manifest must contain a mapping: {path}")
    return payload


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
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if isinstance(data, dict):
        data = data.get(key, [])
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


def _normalize_subagents(items: Any, package_dir: Path) -> list[Any]:
    """Normalize legacy subagent manifest entries into deep SubAgentSpec dicts.

    Provider short forms (``{type: ...}`` / ``{builtin: ...}``) become
    ``factory_name`` SubAgentSpec shapes. ``{config: <path>}`` CustomSubAgent
    sidecars are no longer supported.
    """
    normalized: list[Any] = []
    for item in _as_list(items):
        if isinstance(item, dict) and item.get("config") is not None:
            raise ValueError(
                "CustomSubAgentSpec sidecars ({config: ...}) are no longer supported; "
                f"migrate '{item['config']}' to a SubAgentSpec with factory_name or full fields"
            )
        normalized.append(_normalize_subagent_item(item))
    return normalized


def _normalize_subagent_item(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    raw = dict(item)
    if raw.get("kind") == "configured":
        raw.pop("kind", None)
        return raw
    if "factory_name" in raw or "agent_card" in raw:
        return raw
    if "builtin" in raw:
        builtin = str(raw.pop("builtin"))
        type_name = builtin if "." in builtin else f"core.{builtin}"
        params = dict(raw.pop("params", None) or {})
        params.update(dict(raw.pop("kwargs", None) or {}))
        name = type_name.rsplit(".", 1)[-1]
        return {
            "agent_card": {"name": name, "description": ""},
            "system_prompt": "",
            "factory_name": type_name,
            "factory_kwargs": params,
        }
    if "type" in raw and "agent_card" not in raw:
        type_name = str(raw.pop("type"))
        params = dict(raw.pop("params", None) or {})
        params.update(dict(raw.pop("kwargs", None) or {}))
        name = type_name.rsplit(".", 1)[-1]
        return {
            "agent_card": {"name": name, "description": ""},
            "system_prompt": "",
            "factory_name": type_name,
            "factory_kwargs": params,
        }
    return raw


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
    "find_expert_harness_manifest",
    "load_expert_harness_spec",
]
