# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared registration rules for writable RSI candidate packages."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any

import yaml

PROMPT_REGISTRY = "prompt_sections/sections.yaml"
SKILL_REGISTRY = "skills/skills.yaml"
REGISTRY_GUIDANCE = """Package paths are relative to the integration package root.
Prompt example: sections: [{name: foo, file: prompt_sections/files/foo.md, priority: 30}].
Use file, not path. Each effective prompt section name must be unique.
Skill example: skills: [{dir: skills/foo}]; dir mounts a directory containing
SKILL.md, or a parent directory of skills, never the SKILL.md file itself.
Equivalent aliases may be collapsed. Conflicting content, names, priorities or
mount options require an explicit repair; do not discard unrelated entries.
"""


@dataclass(frozen=True)
class RegistryIssue:
    code: str
    manifest: str
    index: int
    message: str

    def describe(self) -> str:
        return f"{self.code}: {self.manifest}[{self.index}]: {self.message}"


@dataclass
class RegistryNormalizationReport:
    changed_files: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    issues: list[RegistryIssue] = field(default_factory=list)


def _relative(root: Path, raw: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("resource reference must be a non-empty string")
    value = raw.strip().replace("\\", "/")
    path = Path(value)
    if path.is_absolute() or PureWindowsPath(value).drive or ".." in path.parts:
        raise ValueError(f"unsafe package-relative reference: {raw!r}")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"resource reference escapes package: {raw!r}")
    return resolved


def resolve_registry_resource(root: Path, raw: str, *, skill: bool = False) -> str:
    """Resolve known aliases only; never search the package by basename."""
    root = root.resolve()
    direct = _relative(root, raw)
    parts = list(Path(raw.strip().replace("\\", "/")).parts)
    prefix = "skills" if skill else "prompt_sections"
    if parts and parts[0] == prefix:
        parts.pop(0)
    repeated = "skills" if skill else "files"
    while parts and parts[0] == repeated:
        parts.pop(0)
    tail = Path(*parts) if parts else Path(".")
    mount = Path("skills") if skill else Path("prompt_sections/files")
    refs = [mount / tail]
    if not skill:
        refs.append(mount / Path(raw.replace("\\", "/")))
    candidates = {_relative(root, str(ref)) for ref in refs if _relative(root, str(ref)).exists()}
    if direct.exists():
        candidates.add(direct)
    if len(candidates) != 1:
        raise ValueError(f"reference {raw!r} has {len(candidates)} existing targets; expected one")
    resolved = next(iter(candidates))
    if skill and resolved.is_file() and resolved.name == "SKILL.md":
        resolved = resolved.parent
    if skill:
        if not resolved.is_dir():
            raise ValueError(f"skill dir must be a directory: {raw!r}")
        definitions = [resolved / "SKILL.md", *resolved.glob("*/SKILL.md")]
        present = [p for p in definitions if p.is_file()]
        if any(not p.resolve().is_relative_to(root) for p in present):
            raise ValueError(f"skill mount contains a package-escaping definition: {raw!r}")
        if not present:
            raise ValueError(f"no package-local SKILL.md in skill mount: {raw!r}")
    elif not resolved.is_file():
        raise ValueError(f"prompt reference must be a file: {raw!r}")
    return resolved.relative_to(root).as_posix()


def _registry_path(root: Path, manifest: str) -> Path:
    path = root
    for part in Path(manifest).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"registry writes cannot follow symbolic links: {manifest!r}")
    return _relative(root, manifest)


def _read(root: Path, manifest: str, key: str) -> tuple[dict[str, Any], list[Any]]:
    path = _registry_path(root, manifest)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if isinstance(raw, list):
        raw = {key: raw}
    if not isinstance(raw, dict):
        raise ValueError("registry must be a mapping or list")
    data = dict(raw)
    if key == "sections" and "prompt_sections" in data:
        if "sections" in data and data["sections"] != data["prompt_sections"]:
            raise ValueError("sections and prompt_sections conflict")
        data["sections"] = data.pop("prompt_sections")
    entries = data.get(key, [])
    if not isinstance(entries, list):
        raise ValueError(f"{key} must be a list")
    return data, entries


def _prompt_entry(root: Path, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("prompt section must be an object")
    item = dict(raw)
    if not isinstance(item.get("name"), str) or not item["name"].strip():
        raise ValueError("prompt section name must be a non-empty string")
    for alias in ("path", "file_path"):
        if alias in item:
            target = resolve_registry_resource(root, item.pop(alias))
            if "file" in item and resolve_registry_resource(root, item["file"]) != target:
                raise ValueError(f"file and {alias} refer to different resources")
            item["file"] = target
    if "file" in item:
        item["file"] = resolve_registry_resource(root, item["file"])
        if "content" in item:
            raise ValueError("prompt section must declare file or content, not both")
    elif not isinstance(item.get("content"), (str, dict)):
        raise ValueError("prompt section requires file or content")
    priority = item.get("priority", 100)
    if type(priority) is not int:
        raise ValueError("prompt priority must be an integer")
    item["priority"] = priority
    return item


def _skill_entry(root: Path, raw: Any) -> dict[str, Any]:
    item = {"dir": raw} if isinstance(raw, str) else dict(raw) if isinstance(raw, dict) else {}
    item["dir"] = resolve_registry_resource(root, item.get("dir"), skill=True)
    return item


def expand_skill_mounts(root: Path, entries: list[Any]) -> list[dict[str, Any]]:
    """Compile parent mounts to native JSON leaf directories, preserving options."""
    root = root.resolve()
    mounted: dict[str, dict[str, Any]] = {}
    for raw in entries:
        expanded = [raw]
        if isinstance(raw, dict) and isinstance(raw.get("dirs"), list):
            expanded = [{**{k: v for k, v in raw.items() if k != "dirs"}, "dir": d} for d in raw["dirs"]]
        for value in expanded:
            item = _skill_entry(root, value)
            directory = root / item["dir"]
            leaves = [directory] if (directory / "SKILL.md").is_file() else sorted(
                p.parent for p in directory.glob("*/SKILL.md") if p.is_file()
            )
            for leaf in leaves:
                relative = leaf.resolve().relative_to(root).as_posix()
                compiled = {**item, "dir": relative}
                previous = mounted.get(relative)
                if previous is not None and previous != compiled:
                    raise ValueError(f"overlapping skill mounts have conflicting options: {previous!r}; {compiled!r}")
                mounted[relative] = compiled
    return list(mounted.values())


def _document(root: Path, manifest: str) -> tuple[dict[str, Any], list[RegistryIssue]]:
    prompt = manifest == PROMPT_REGISTRY
    key = "sections" if prompt else "skills"
    data, entries = _read(root, manifest, key)
    result: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    issues: list[RegistryIssue] = []
    for index, raw in enumerate(entries):
        try:
            # Preserve the native multi-directory mount form and its options.
            expanded = [raw]
            if not prompt and isinstance(raw, dict) and isinstance(raw.get("dirs"), list):
                expanded = [{**{k: v for k, v in raw.items() if k != "dirs"}, "dir": d} for d in raw["dirs"]]
            for value in expanded:
                item = _prompt_entry(root, value) if prompt else _skill_entry(root, value)
                identity = item["name"] if prompt else item["dir"]
                previous = seen.get(identity)
                if previous is not None:
                    if previous != item:
                        code = "prompt_section_duplicate" if prompt else "registry_conflict"
                        issues.append(RegistryIssue(code, manifest, index, f"conflicting registrations: {previous!r}; {item!r}"))
                    continue
                seen[identity] = item
                result.append(item)
        except (ValueError, TypeError, OSError) as exc:
            issues.append(RegistryIssue("resource_path_invalid", manifest, index, str(exc)))
    data[key] = result
    if not prompt and not issues:
        try:
            expand_skill_mounts(root, result)
        except ValueError as exc:
            issues.append(RegistryIssue("registry_conflict", manifest, -1, str(exc)))
    return data, issues


def validate_package_registries(root: Path) -> list[RegistryIssue]:
    """Validate aliases and conflicts without modifying package files."""
    root = root.resolve()
    issues: list[RegistryIssue] = []
    for manifest in (PROMPT_REGISTRY, SKILL_REGISTRY):
        if not (root / manifest).exists():
            continue
        try:
            _, errors = _document(root, manifest)
            issues.extend(errors)
        except (ValueError, TypeError, OSError, yaml.YAMLError) as exc:
            issues.append(RegistryIssue("registry_schema_invalid", manifest, -1, str(exc)))
    return issues


def _writable(path: str, writable_paths: list[str]) -> bool:
    return any(path == value.rstrip("/") or path.startswith(value.rstrip("/") + "/") for value in writable_paths)


def _references(entries: list[Any]) -> list[Any]:
    """Report references without copying arbitrary mount metadata into feedback."""
    return [
        {key: item[key] for key in ("name", "file", "file_path", "path", "dir", "dirs") if key in item}
        if isinstance(item, dict) else item
        for item in entries
    ]


def normalize_package_registries(root: Path, *, writable_paths: list[str]) -> RegistryNormalizationReport:
    """Apply unambiguous repairs transactionally across the permitted registries."""
    root = root.resolve()
    report = RegistryNormalizationReport()
    pending: list[tuple[Path, str, str, str]] = []
    for manifest in (PROMPT_REGISTRY, SKILL_REGISTRY):
        if not _writable(manifest, writable_paths) or not (root / manifest).exists():
            continue
        try:
            path = _registry_path(root, manifest)
            data, errors = _document(root, manifest)
            report.issues.extend(errors)
            original = path.read_text(encoding="utf-8")
            updated = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
            if original != updated:
                key = "sections" if manifest == PROMPT_REGISTRY else "skills"
                _, original_entries = _read(root, manifest, key)
                change = f"{manifest}: {_references(original_entries)!r} -> {_references(data[key])!r}"
                pending.append((path, manifest, updated, change))
        except (ValueError, TypeError, OSError, yaml.YAMLError) as exc:
            report.issues.append(RegistryIssue("registry_schema_invalid", manifest, -1, str(exc)))
    if report.issues:
        return report
    for path, manifest, updated, change in pending:
        path.write_text(updated, encoding="utf-8")
        report.changed_files.append(manifest)
        report.changes.append(change)
    return report


def upsert_prompt_section(
    root: Path, *, name: str, file_ref: str, priority: int | None, writable_paths: list[str],
    replace_existing: bool = False,
) -> None:
    """Register an action's existing file without duplicating an equivalent alias."""
    root = root.resolve()
    if not _writable(PROMPT_REGISTRY, writable_paths):
        raise ValueError(f"prompt action must declare {PROMPT_REGISTRY}")
    target = resolve_registry_resource(root, file_ref)
    data, entries = _read(root, PROMPT_REGISTRY, "sections") if (root / PROMPT_REGISTRY).exists() else ({}, [])
    kept: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None
    for raw in entries:
        item = _prompt_entry(root, raw)
        if item["name"] != name:
            kept.append(item)
            continue
        if item.get("file") != target:
            if not replace_existing:
                raise ValueError(f"prompt_section_duplicate: {name!r} already refers to {item.get('file', 'inline content')!r}")
            item.pop("content", None)
            item["file"] = target
        if selected is not None:
            left, right = dict(selected), dict(item)
            if priority is not None:
                left.pop("priority", None)
                right.pop("priority", None)
            if left != right:
                raise ValueError(f"prompt_section_duplicate: conflicting metadata for {name!r}")
        selected = item
    entry = selected or {"name": name, "file": target, "priority": 30}
    if priority is not None:
        entry["priority"] = priority
    data["sections"] = [*kept, entry]
    path = _registry_path(root, PROMPT_REGISTRY)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
