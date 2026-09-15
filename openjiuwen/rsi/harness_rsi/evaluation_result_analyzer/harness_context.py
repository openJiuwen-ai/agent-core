# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Project evaluated Harness declarations into a read-only evidence workspace."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.harness.resources import find_plugin_manifest
from openjiuwen.harness.resources.extension_loader import (
    _build_prompt_section_specs,
    _normalize_prompt_section,
    _resolve_section_file,
)
from openjiuwen.rsi.harness_rsi.member_optimizer.loader import load_eval_ref, resolve_candidate_roles


def _local_path(root: Path, path: str | Path) -> Path:
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Harness evidence reference leaves its package: {path}")
    return resolved


def _read_mapping(root: Path, path: Path) -> dict[str, Any]:
    text = _local_path(root, path).read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"Harness evidence must contain a mapping: {path}")
    return data


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else ([] if value is None else [value])


def _declarations(path: Path) -> tuple[Path, dict[str, Any]]:
    root = path.resolve() if path.is_dir() else path.resolve().parent
    main = find_plugin_manifest(path)
    raw = _read_mapping(root, main)
    if main.name != "manifest.json":
        config = root / "config.json"
        if config.exists():
            for key, value in _read_mapping(root, config).items():
                raw.setdefault(key, value)
        for key, relative, nested in (
            ("prompt_sections", "prompt_sections/sections.yaml", "sections"),
            ("skills", "skills/skills.yaml", "skills"),
            ("tools", "tools/tools.yaml", "tools"),
            ("rails", "rails/rails.yaml", "rails"),
        ):
            source = _local_path(root, relative)
            if not source.exists():
                continue
            items = yaml.safe_load(source.read_text(encoding="utf-8")) or []
            if isinstance(items, dict):
                items = items.get(nested, [])
            if not isinstance(items, list) or not isinstance(raw.get(key, []), list):
                raise ValueError(f"Harness evidence {relative} must contain a list")
            raw.setdefault(key, []).extend(items)
    # Do not use the full runtime loader: it can read subagent configs and credentials.
    return root, {key: raw[key] for key in ("prompt_sections", "prompts", "skills", "tools", "rails") if key in raw}


def _resource_names(items: Any) -> list[dict[str, Any]]:
    declarations = []
    for item in _as_list(items):
        if isinstance(item, str):
            declarations.append({"builtin": item})
        elif isinstance(item, dict):
            declarations.append(
                {
                    key: item[key]
                    for key in ("builtin", "builtins", "name", "names", "file", "class", "import_path", "class_name")
                    if key in item
                }
            )
    return declarations


def _skill_files(root: Path, declarations: Any) -> list[Path]:
    """Match SkillManager's file, skill-directory, or immediate-parent discovery."""
    found: list[Path] = []
    for declaration in _as_list(declarations):
        value = declaration.get("dir") if isinstance(declaration, dict) else declaration
        if not isinstance(value, str) or not value:
            raise ValueError("Declared skill must identify a local directory or SKILL.md")
        source = _local_path(root, value)
        if source.is_file():
            found.append(source)
            continue
        if not source.is_dir():
            raise FileNotFoundError(f"Declared skill path not found: {source}")
        children = sorted(source.iterdir())
        direct = next((child for child in children if child.name.lower() == "skill.md"), None)
        if direct is not None:
            found.append(_local_path(root, direct))
            continue
        for child in children:
            directory = _local_path(root, child)
            if directory.is_dir():
                skill = next((file for file in sorted(directory.iterdir()) if file.name.lower() == "skill.md"), None)
                if skill is not None:
                    found.append(_local_path(root, skill))
    return list(dict.fromkeys(found))


def prepare_harness_context(
    *,
    eval_ref_path: str,
    harness_refs_path: str,
    runtime_dir: Path,
) -> dict[str, Any]:
    """Expose full declared instructions, never model settings or environment data.

    Eval references take precedence over a caller's current Harness: attribution
    must describe the object that produced the recorded evaluation. Legacy team
    runs without package references keep their existing evidence-only behavior.
    """
    evaluation = load_eval_ref(eval_ref_path)
    refs = evaluation.harness_refs_path or harness_refs_path
    if not refs:
        return {"status": "not_provided", "roles": []}
    refs_path = Path(refs).expanduser()
    if not refs_path.is_absolute():
        refs_path = Path(eval_ref_path).resolve().parent / refs_path
    if not refs_path.exists():
        raise FileNotFoundError(f"Evaluated Harness references not found: {refs_path}")
    roles = resolve_candidate_roles(str(refs_path), evaluation)
    if not roles:
        raise ValueError("Evaluated Harness references contain no roles")
    destination = runtime_dir / "current_harness"
    destination.mkdir(exist_ok=False)
    result: dict[str, Any] = {
        "status": "available",
        "scope": "package declarations, not activation evidence; runtime defaults and configuration are excluded",
        "roles": [],
    }
    for index, role in enumerate(roles, 1):
        root, raw = _declarations(Path(role.harness_ref_path))
        out = destination / f"role_{index:03d}"
        out.mkdir()
        row: dict[str, Any] = {
            "role": role.role,
            "member_name": role.member_name,
            "aliases": role.metadata.get("aliases", []),
            "prompt_sections": [],
            "skills": [],
            "tools": _resource_names(raw.get("tools")),
            "rails": _resource_names(raw.get("rails")),
        }
        for name in ("identity.md", "soul.md"):
            _local_path(root, name)
        sections = raw.get("prompt_sections") or raw.get("prompts") or []
        if isinstance(sections, dict):
            sections = sections.get("sections", [])
        for section in _as_list(sections):
            if isinstance(section, dict) and section.get("file") is not None:
                _local_path(root, _resolve_section_file(root, str(section["file"])))
        if (root / "manifest.json").is_file():
            prompt_specs = _build_prompt_section_specs(sections, base_dir=root, package_root=root)
            normalized = [spec.model_dump() for spec in prompt_specs]
        else:
            normalized = [_normalize_prompt_section(section, root) for section in _as_list(sections)]
        for name in ("identity", "soul"):
            source = _local_path(root, name + ".md")
            if source.is_file() and not (root / "manifest.json").is_file():
                normalized.append({"name": name, "content": source.read_text(encoding="utf-8")})
        for number, section in enumerate(normalized, 1):
            target = out / f"prompt_{number:03d}.json"
            target.write_text(json.dumps(section.get("content", {}), ensure_ascii=False, indent=2), encoding="utf-8")
            row["prompt_sections"].append(
                {"name": section.get("name", ""), "path": target.relative_to(runtime_dir).as_posix()}
            )
        for number, source in enumerate(_skill_files(root, raw.get("skills")), 1):
            target = out / f"skill_{number:03d}.md"
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            row["skills"].append({"name": source.parent.name, "path": target.relative_to(runtime_dir).as_posix()})
        result["roles"].append(row)
    (destination / "index.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
