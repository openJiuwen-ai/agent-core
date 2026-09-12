# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Copy portable skill bundles into each CLI's project discovery directory."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

_SCAN_DIRS = {"claudecode": ".claude/skills", "codex": ".agents/skills", "dsh": ".dsh/skills"}
_COPY_LOCK = threading.Lock()


@dataclass(frozen=True)
class SkillSource:
    """A manifest skill directory or a library containing skill directories."""

    dir: str
    mode: str = "all"
    enabled_skills: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.dir, str) or not self.dir:
            raise ValueError("skill dir must be a non-empty path")
        if self.mode not in {"all", "auto_list"}:
            raise ValueError("skill mode must be all or auto_list")
        if self.enabled_skills is not None:
            named = isinstance(self.enabled_skills, (list, tuple))
            if not named or any(not isinstance(v, str) for v in self.enabled_skills):
                raise TypeError("enabled_skills must be an array of names")
            object.__setattr__(self, "enabled_skills", tuple(self.enabled_skills))


def normalize_skills(values: Any, conflict: str) -> tuple[SkillSource, ...]:
    """Freeze provider config without accessing source files at construction."""
    if conflict not in {"skip", "replace"}:
        raise ValueError("skill_conflict must be skip or replace")
    if not isinstance(values, (list, tuple)):
        raise TypeError("skills must be an array")
    result = []
    for value in values:
        if isinstance(value, SkillSource):
            result.append(value)
        elif isinstance(value, str):
            result.append(SkillSource(dir=value))
        elif isinstance(value, Mapping):
            result.append(SkillSource(**dict(value)))
        else:
            raise TypeError("skill source must be a directory or manifest skill declaration")
    return tuple(result)


def _name(directory: Path) -> str:
    document = directory / "SKILL.md"
    if not document.resolve().is_relative_to(directory.resolve()):
        raise ValueError("SKILL.md must stay inside its bundle")
    text = document.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    metadata: dict = {}
    if lines and lines[0].strip() == "---":
        try:
            end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        except StopIteration as exc:
            raise ValueError(f"unterminated skill front matter: {directory}") from exc
        metadata = yaml.safe_load("\n".join(lines[1:end])) or {}
        if not isinstance(metadata, dict):
            raise ValueError(f"invalid skill front matter: {directory}")
    name = metadata.get("name", directory.name)
    if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name) is None:
        raise ValueError(f"invalid portable skill name: {name!r}")
    return name


def _discover(root: Path) -> list[Path]:
    if (root / "SKILL.md").is_file():
        return [root]
    result = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.is_symlink():
            result.extend(_discover(child))
    return result


def _validate_tree(path: Path, root: Path, ancestors: frozenset[Path] = frozenset()) -> None:
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError(f"skill symlink escapes its bundle: {path}")
    if resolved.is_dir():
        if resolved in ancestors:
            raise ValueError(f"cyclic skill directory: {path}")
        for child in resolved.iterdir():
            _validate_tree(child, root, ancestors | {resolved})
    elif not resolved.is_file():
        raise ValueError(f"skill contains a non-regular file: {path}")


def _matches(scan: Path, name: str) -> list[Path]:
    matches = []
    for child in scan.iterdir():
        existing = child.name
        if not child.is_symlink() and child.is_dir() and (child / "SKILL.md").is_file():
            try:
                existing = _name(child)
            except (ValueError, yaml.YAMLError):
                pass
        if name.casefold() in {child.name.casefold(), existing.casefold()}:
            matches.append(child)
    return matches


def install_skills(sources: tuple[SkillSource, ...], *, provider: str,
                   cwd: str | None, conflict: str = "skip") -> tuple[Path, ...]:
    """Copy complete bundles before runtime startup, preserving project skills.

    Replacement is staged outside the discovery directory and rolls back a
    failed rename. Copies remain in the project after harness stop. Internal
    symlinks are materialized; escaping/cyclic links are rejected before copy.
    """
    if not sources:
        return ()
    if conflict not in {"skip", "replace"}:
        raise ValueError("skill_conflict must be skip or replace")
    project = Path(cwd or os.getcwd()).expanduser().resolve(strict=True)
    scan = project / _SCAN_DIRS[provider]
    planned = []
    for source in sources:
        root = Path(source.dir).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"skill source is not a directory: {root}")
        bundles = _discover(root)
        if not bundles:
            raise ValueError(f"no SKILL.md bundles found in {root}")
        for bundle in bundles:
            name = _name(bundle)
            if not source.enabled_skills or name in source.enabled_skills:
                planned.append((name, bundle))
    installed = []
    # Multiple members can start on the same project within this host process.
    with _COPY_LOCK:
        if not scan.resolve().is_relative_to(project):
            raise ValueError("skill discovery directory escapes the project")
        scan.mkdir(parents=True, exist_ok=True)
        for name, source in planned:
            matches = _matches(scan, name)
            if matches and conflict == "skip":
                continue
            if len(matches) > 1:
                raise ValueError(f"multiple existing directories declare skill {name!r}")
            destination = matches[0] if matches else scan / name
            target_location = destination.parent.resolve() / destination.name
            if source.resolve() == target_location:
                continue
            if source.is_relative_to(target_location) or target_location.is_relative_to(source):
                raise ValueError("skill source and destination must not contain each other")
            _validate_tree(source, source)
            with tempfile.TemporaryDirectory(prefix=".openjiuwen-skill-", dir=project) as temporary:
                stage = Path(temporary)
                copied, backup = stage / "new", stage / "previous"
                shutil.copytree(source, copied)
                existed = destination.exists() or destination.is_symlink()
                if existed:
                    destination.rename(backup)
                try:
                    copied.rename(destination)
                except BaseException:
                    if existed:
                        backup.rename(destination)
                    raise
            installed.append(destination)
    return tuple(installed)
