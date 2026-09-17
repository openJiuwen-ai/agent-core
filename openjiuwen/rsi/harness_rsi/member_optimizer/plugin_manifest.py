# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Keep RSI's editable registries and the native Plugin manifest in sync."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from openjiuwen.harness.resources import find_plugin_manifest

_REGISTRIES = {
    "prompt_sections": ("prompt_sections/sections.yaml", "sections"),
    "skills": ("skills/skills.yaml", "skills"),
    "tools": ("tools/tools.yaml", "tools"),
    "rails": ("rails/rails.yaml", "rails"),
}


def prepare_plugin_registries(package: Path) -> None:
    """Seed action registries only in the optimizer's private worktree."""
    try:
        manifest = find_plugin_manifest(package)
    except FileNotFoundError:
        return
    manifest = Path(manifest)
    is_json = manifest.name == "manifest.json"
    payload = (
        json.loads(manifest.read_text(encoding="utf-8"))
        if is_json
        else yaml.safe_load(manifest.read_text(encoding="utf-8"))
    )
    if not is_json and payload.get("schema_version") != "expert_harness.v1":
        return
    for field, (relative, key) in _REGISTRIES.items():
        entries = payload.get(field, [])
        if not isinstance(entries, list):
            raise ValueError(f"Plugin {field} must be a list")
        if field == "prompt_sections":
            entries = [dict(item, name=item.get("name") or Path(item.get("file", "")).stem) for item in entries]
        path = package / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({key: entries}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    if not is_json:
        # Move canonical declarations to editable sidecars, rather than keep
        # two owners whose merged entries would resurrect removed resources.
        payload["schema_version"] = "1.0"
        for field in _REGISTRIES:
            payload.pop(field, None)
        manifest.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def synchronize_plugin_manifest(package: Path) -> None:
    """Compile edited registries into JSON before verification/publication.

    Root declarations are replaced, not appended: otherwise removals would
    reappear and updated resources could be loaded twice. Display metadata and
    all non-optimized manifest fields stay intact.
    """
    manifest = package / "manifest.json"
    if not manifest.is_file():
        return
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    for field, (relative, key) in _REGISTRIES.items():
        path = package / relative
        if not path.is_file():
            continue
        registry = yaml.safe_load(path.read_text(encoding="utf-8"))
        entries = registry.get(key) if isinstance(registry, dict) else registry
        if not isinstance(entries, list):
            raise ValueError(f"Plugin registry {relative} must contain a {key} list")
        if field == "prompt_sections":
            entries = [_prompt_entry(package, item) for item in entries]
        payload[field] = entries
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _prompt_entry(package: Path, item: dict) -> dict:
    entry = dict(item)
    if "file" in entry:
        path = package / entry["file"]
        if not path.is_file():
            path = package / "prompt_sections" / "files" / entry["file"]
        entry["file"] = path.resolve().relative_to(package.resolve()).as_posix()
    elif isinstance(entry.get("content"), str):
        entry["content"] = {"en": entry["content"]}
    return entry
