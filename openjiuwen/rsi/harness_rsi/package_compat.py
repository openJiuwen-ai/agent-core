"""RSI package validation and compatibility for the upstream Plugin runtime.

Previously accepted manifests are translated to PluginSpec. Native packages
also pass through the RSI resource and capability checks.
"""

import json
import shutil
from pathlib import Path

import yaml

from openjiuwen.harness.resources import (
    load_plugin_package as _load_native_plugin,
)
from openjiuwen.harness.resources import resolve_plugin_parts as resolve_plugin_parts
from openjiuwen.harness.resources.extension_loader import (
    _normalize_prompt_section as _stable_normalize_prompt_section,
)
from openjiuwen.harness.resources.extension_loader import (
    _resolve_section_file as _stable_resolve_section_file,
)
from openjiuwen.harness.resources.extension_loader import (
    build_unversioned_plugin_spec,
)
from openjiuwen.harness.schema.extension_spec import PluginSpec, PromptSectionSpec


def resolve_rg_binary():
    return shutil.which("rg")


def find_plugin_manifest(path):
    path = Path(path).expanduser().resolve(strict=True)
    names = ("manifest.json", "harness_config.yaml", "expert_harness.yaml", "harness.yaml")
    if path.is_file() and (path.name in names or path.suffix in {".yaml", ".yml"}):
        return path
    if path.is_dir():
        for name in names:
            candidate = path / name
            if candidate.is_file():
                if not candidate.resolve().is_relative_to(path):
                    raise ValueError("Harness manifest escapes package")
                return candidate
    raise FileNotFoundError(f"Harness manifest not found: {path}")


def _local(root, value, *, directory=False):
    path = (root / str(value)).resolve(strict=True)
    if not path.is_relative_to(root) or path.is_dir() != directory:
        raise ValueError(f"Invalid package-local resource: {value}")
    return path


def _resolve_section_file(package_dir, file_name):
    path = _stable_resolve_section_file(package_dir, file_name).resolve(strict=True)
    if not path.is_relative_to(package_dir.resolve()):
        raise ValueError("Harness evidence reference leaves its package")
    return path


def _normalize_prompt_section(section, package_dir):
    if "file" in section:
        _resolve_section_file(package_dir, section["file"])
    return _stable_normalize_prompt_section(section, package_dir)


def _preflight(value, root):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"mcps", "subagents"} and item:
                raise ValueError("RSI stable packages do not support MCP or subagents")
            if key in {"file", "file_path", "dir", "cwd"} and isinstance(item, str):
                # Legacy prompt aliases are resolved by the stable loader below.
                target = (root / item).resolve()
                if not target.is_relative_to(root):
                    raise ValueError(f"Harness resource escapes package: {item}")
            _preflight(item, root)
    elif isinstance(value, list):
        for item in value:
            _preflight(item, root)


def _legacy_spec(manifest):
    root = manifest.parent.resolve()
    raw = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    _preflight(raw, root)
    for directory, name in (("prompt_sections", "sections.yaml"), ("tools", "tools.yaml"),
                            ("rails", "rails.yaml"), ("skills", "skills.yaml"),
                            ("mcps", "mcps.yaml"), ("subagents", "subagents.yaml")):
        sidecar = root / directory / name
        if sidecar.is_file():
            data = yaml.safe_load(sidecar.read_text(encoding="utf-8")) or {}
            if directory in {"mcps", "subagents"} and data:
                entries = data.get(directory, []) if isinstance(data, dict) else data
                if entries:
                    raise ValueError("RSI stable packages do not support MCP or subagents")
            _preflight(data, root)
    if raw.get("schema_version") == "expert_harness.v1":
        spec = _load_native_plugin(manifest)
        if not raw.get("id"):
            spec.id = raw.get("extension_name") or raw.get("name") or root.name
        return spec
    if manifest.name in {"harness_config.yaml", "expert_harness.yaml", "harness.yaml"}:
        return _load_native_plugin(manifest)
    return build_unversioned_plugin_spec(raw, root)


def _build_prompt_section_specs(items, *, base_dir, package_root):
    result = []
    for raw in items or []:
        item = dict(raw)
        if "file" in item:
            path = _local(package_root, item.pop("file"))
            if not path.is_relative_to(package_root / "prompt_sections") or path.suffix != ".md":
                raise ValueError("Prompt files must be Markdown under prompt_sections/")
            item.setdefault("name", path.stem)
            item["content"] = {"en": path.read_text(encoding="utf-8")}
        elif isinstance(item.get("content"), str):
            item["content"] = {"en": item["content"]}
        result.append(PromptSectionSpec.model_validate(item))
    return result


def load_plugin_package(path):
    manifest = find_plugin_manifest(path)
    root = manifest.parent.resolve()
    # Reject links before the legacy loader reads sidecar content.
    for entry in root.rglob("*"):
        if entry.is_symlink() and not entry.resolve().is_relative_to(root):
            raise ValueError(f"Harness resource escapes package: {entry}")
    if manifest.name != "manifest.json":
        spec = _legacy_spec(manifest)
    else:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        _preflight(raw, root)
        allowed = {
            "package_type", "packageType", "schema_version", "schemaVersion", "id", "name", "description",
            "version", "display_name", "metadata", "tools", "rails", "skills", "prompt_sections", "mcps",
        }
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(f"Unsupported stable Harness manifest fields: {sorted(unknown)}")
        if raw.get("package_type", "plugin") != "plugin":
            raise ValueError("Only Harness capability packages are supported")
        if "packageType" in raw:
            spec = _load_native_plugin(manifest)
            validate_package_spec(spec, root)
            return spec
        payload = {key: raw[key] for key in ("name", "description", "metadata") if key in raw}
        payload["id"] = raw.get("id") or raw.get("name") or root.name
        payload["prompt_sections"] = _build_prompt_section_specs(
            raw.get("prompt_sections", []), base_dir=root, package_root=root,
        )
        for field, kind in (("tools", "tool"), ("rails", "rail")):
            payload[field] = []
            for item in raw.get(field, []):
                if set(item) - {"file", "class", "class_name"}:
                    raise ValueError(f"Unsupported {field} declaration: {item}")
                resource = _local(root, item["file"])
                payload[field].append({"type": f"harness.{kind}.file", "params": {
                    "file_path": str(resource), "class_name": item.get("class") or item.get("class_name"),
                }})
        payload["skills"] = []
        for raw_skill in raw.get("skills", []):
            item = {"dir": raw_skill} if isinstance(raw_skill, str) else dict(raw_skill)
            item["dir"] = str(_local(root, item["dir"], directory=True))
            payload["skills"].append(item)
        payload["mcps"] = raw.get("mcps", [])
        spec = PluginSpec.model_validate(payload)
    # The in-memory Plugin loader requires absolute resource paths.
    for item in [*spec.tools, *spec.rails]:
        if item.params.get("file_path"):
            item.params = dict(item.params, file_path=str(_local(root, item.params["file_path"])))
    for item in spec.skills:
        item.dir = str(_local(root, item.dir, directory=True))
    validate_package_spec(spec, root)
    return spec


def validate_package_spec(spec, root):
    """Validate capability paths before runtime construction or publication."""
    if spec.mcps:
        raise ValueError("RSI stable packages do not support MCP or subagent declarations")
    for item in [*spec.tools, *spec.rails]:
        value = item.params.get("file_path")
        if value:
            _local(root, value)
    for item in spec.skills:
        _local(root, item.dir, directory=True)


def validate_harness_config(path, *, package_dir=None):
    """Shared Swarm materialization/install validation without AutoHarness."""
    spec = load_plugin_package(path)
    if package_dir is not None:
        validate_package_spec(spec, Path(package_dir).resolve())
    return spec


def build_read_image_multimodal_resolver(agent, explicit_value=None):
    """Resolve images using the stable host policy."""
    from openjiuwen.harness.rails._multimodal import should_enable_read_image_multimodal
    return should_enable_read_image_multimodal(agent, explicit_value)


async def cleanup_task_resources(agent):
    """Use optional task cleanup, then leave tool teardown to the owning caller."""
    cleanup = getattr(agent, "cleanup_task_resources", None)
    if callable(cleanup):
        await cleanup()
