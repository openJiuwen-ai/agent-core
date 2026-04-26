# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, FrozenSet, List, Optional, Set, Tuple, Union

from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.single_agent.skills.skill_manager import Skill
from openjiuwen.core.sys_operation.sys_operation import SysOperation
from openjiuwen.harness.prompts.sections.tools import build_tool_card
from openjiuwen.core.context_engine.active_skill_bodies import (
    normalize_skill_relative_file_path,
)
from openjiuwen.harness.tools import ToolOutput

_TREE_SKIP_DIR_NAMES: frozenset[str] = frozenset({
    "output",
    "temp",
    "assets",
    "node_modules"
})


def _truthy_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    s = str(value).strip().lower()
    return s in ("1", "true", "yes", "on")


def _opt_in_flag_default_true(value: Any) -> bool:
    """Default True when omitted or null; explicit false/0/no/off turns off."""
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if not s:
        return True
    if s in ("0", "false", "no", "off"):
        return False
    if s in ("1", "true", "yes", "on"):
        return True
    return True


def _clamp_int(value: Any, *, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _skill_tree_skip_name(name: str, *, is_dir: bool) -> bool:
    if name.startswith("."):
        return True
    if is_dir and name in _TREE_SKIP_DIR_NAMES:
        return True
    return False


def _append_skill_ascii_tree_lines(
    directory: Path,
    prefix: str,
    *,
    max_depth: int,
    dir_depth: int,
    out: List[str],
    max_lines: int,
) -> bool:
    """Append UTF-8 tree lines under ``directory``; return True if line budget exhausted.

    ``max_depth`` aligns with ``LocalFsOperation._walk_path`` / ``list_files(..., max_depth=...)``:
    list contents when ``dir_depth <= max_depth``, stop descending into children at ``dir_depth == max_depth``.
    """
    if dir_depth > max_depth or len(out) >= max_lines:
        return len(out) >= max_lines
    try:
        raw = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except (OSError, PermissionError):
        return False
    children = [
        p
        for p in raw
        if not _skill_tree_skip_name(p.name, is_dir=p.is_dir())
    ]
    for i, path in enumerate(children):
        if len(out) >= max_lines:
            return True
        is_last = i == len(children) - 1
        connector = "└── " if is_last else "├── "
        child_prefix = "    " if is_last else "│   "
        display = f"{path.name}/" if path.is_dir() else path.name
        out.append(f"{prefix}{connector}{display}")
        if path.is_dir():
            if _append_skill_ascii_tree_lines(
                path,
                prefix + child_prefix,
                max_depth=max_depth,
                dir_depth=dir_depth + 1,
                out=out,
                max_lines=max_lines,
            ):
                return True
    return False


def _build_skill_directory_ascii_tree(
    skill_root: Path,
    *,
    max_depth: int,
    max_lines: int,
) -> Tuple[List[str], bool, Optional[str]]:
    """Build the same style tree as legacy ``load_skill_tools._build_tree`` (pathlib, ASCII connectors)."""
    try:
        resolved = skill_root.resolve()
    except OSError:
        resolved = skill_root
    if not resolved.exists():
        return [], False, "skill directory does not exist"
    if not resolved.is_dir():
        return [], False, "skill path is not a directory"
    root_name = resolved.name or "."
    out: List[str] = [f"{root_name}/"]
    cap = max(2, max_lines)
    truncated = _append_skill_ascii_tree_lines(
        resolved,
        "",
        max_depth=max_depth,
        dir_depth=0,
        out=out,
        max_lines=cap,
    )
    text = "\n".join(out)
    return [text], truncated, None


def _normalize_skill_lookup_key(skill_name: str) -> str:
    """Strip path separators; match directory basename only (same as legacy load_skill_tools)."""
    clean = skill_name.replace("\\", "/").strip("/")
    if "/" in clean:
        clean = clean.rsplit("/", 1)[-1]
    return clean.strip()


def _recursive_find_skill_directory(directory: Path, skill_name: str) -> Optional[Path]:
    """Depth-first: first directory named ``skill_name`` that contains ``SKILL.md``."""
    if not skill_name:
        return None
    try:
        children = sorted(directory.iterdir(), key=lambda p: p.name.lower())
    except (OSError, PermissionError):
        return None
    for child in children:
        if not child.is_dir():
            continue
        if child.name in _TREE_SKIP_DIR_NAMES or child.name.startswith("."):
            continue
        if child.name == skill_name and (child / "SKILL.md").is_file():
            return child
        found = _recursive_find_skill_directory(child, skill_name)
        if found is not None:
            return found
    return None


def _collect_discovered_skill_names(
    roots: List[Path],
    *,
    max_names: int,
    enabled_skill_names: Optional[FrozenSet[str]] = None,
    disabled_skill_names: Optional[FrozenSet[str]] = None,
) -> Tuple[List[str], bool]:
    """Recursively collect distinct directory names that contain ``SKILL.md`` under each root."""
    names: List[str] = []
    seen: Set[str] = set()
    truncated = False

    def allow_name(name: str) -> bool:
        if disabled_skill_names and name in disabled_skill_names:
            return False
        if enabled_skill_names is not None and len(enabled_skill_names) > 0:
            return name in enabled_skill_names
        return True

    def visit(directory: Path) -> None:
        nonlocal truncated
        if len(names) >= max_names:
            truncated = True
            return
        try:
            children = sorted(directory.iterdir(), key=lambda p: p.name.lower())
        except (OSError, PermissionError):
            return
        for child in children:
            if len(names) >= max_names:
                truncated = True
                return
            if not child.is_dir():
                continue
            if child.name in _TREE_SKIP_DIR_NAMES or child.name.startswith("."):
                continue
            if (child / "SKILL.md").is_file() and child.name not in seen:
                if allow_name(child.name):
                    names.append(child.name)
                    seen.add(child.name)
            visit(child)

    for base in roots:
        if base.exists() and base.is_dir():
            visit(base)

    names.sort()
    return names, truncated


def _normalize_search_roots(skill_search_roots: Optional[Union[List[Path], List[str]]]) -> Optional[List[Path]]:
    if not skill_search_roots:
        return None
    out: List[Path] = []
    for raw in skill_search_roots:
        try:
            out.append(Path(raw).expanduser().resolve())
        except (OSError, TypeError, ValueError):
            continue
    return out or None


class SkillTool(Tool):
    """View the skill contents of a certain skill"""
    operation: SysOperation
    get_skills: Callable[[], List[Skill]]
    _skill_search_roots: Optional[List[Path]]
    _enabled_skill_names: Optional[FrozenSet[str]]
    _disabled_skill_names: Optional[FrozenSet[str]]

    def __init__(
        self,
        operation: SysOperation,
        get_skills: Callable[[], List[Skill]],
        language: str = "cn",
        agent_id: Optional[str] = None,
        skill_search_roots: Optional[Union[List[Path], List[str]]] = None,
        enabled_skill_names: Optional[Set[str]] = None,
        disabled_skill_names: Optional[Set[str]] = None,
    ):
        """Initialize SkillTool.

        Args:
            operation: SysOperation for file system operations to read files
            get_skills: Callable that returns current enabled skills.
            skill_search_roots: Optional filesystem roots (e.g. ``skills_dir``) used to
                resolve skills by recursive directory search when the name is missing from
                ``get_skills()``, and to enumerate ``discovered_skill_names``.
            enabled_skill_names: When non-empty, filesystem discovery only returns skills whose
                basename is in this set (same semantics as SkillUseRail allow-list).
            disabled_skill_names: Basenames in this set are never returned from filesystem discovery.
        """
        super().__init__(
            build_tool_card("skill_tool", "SkillTool", language, agent_id=agent_id)
        )
        self.operation = operation
        self.get_skills = get_skills
        self.language = language
        self._skill_search_roots = _normalize_search_roots(skill_search_roots)
        self._enabled_skill_names = (
            frozenset(enabled_skill_names) if enabled_skill_names else None
        )
        self._disabled_skill_names = (
            frozenset(disabled_skill_names) if disabled_skill_names else None
        )

    async def _collect_skill_directory_tree(
        self,
        skill_root: Path,
        *,
        max_depth: int,
        max_entries: int,
    ) -> Tuple[List[str], bool, Optional[str]]:
        """Build an ASCII directory tree under the skill root (pathlib; same style as legacy load_skill_tools)."""
        tree, truncated, detail = _build_skill_directory_ascii_tree(
            skill_root,
            max_depth=max_depth,
            max_lines=max_entries,
        )
        return tree, truncated, detail

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        """Invoke skill_tool tool."""
        skill_name = str(inputs.get("skill_name", "") or "").strip()
        relative_file_path = normalize_skill_relative_file_path(
            str(inputs.get("relative_file_path") or "")
        )
        include_tree = _opt_in_flag_default_true(inputs.get("include_directory_tree"))
        tree_max_depth = _clamp_int(
            inputs.get("tree_max_depth"),
            default=4,
            lo=1,
            hi=12,
        )
        tree_max_entries = _clamp_int(
            inputs.get("tree_max_entries"),
            default=200,
            lo=20,
            hi=800,
        )
        include_discovered = _opt_in_flag_default_true(inputs.get("include_discovered_skill_names"))
        max_discovered = _clamp_int(
            inputs.get("max_discovered_skill_names"),
            default=400,
            lo=20,
            hi=800,
        )

        try:
            skill = self._resolve_skill(skill_name)
            if not skill:
                err = f"Skill not found: {skill_name}"
                if self._skill_search_roots and include_discovered:
                    discovered, _ = _collect_discovered_skill_names(
                        self._skill_search_roots,
                        max_names=max_discovered,
                        enabled_skill_names=self._enabled_skill_names,
                        disabled_skill_names=self._disabled_skill_names,
                    )
                    if discovered:
                        err = f"{err}. Discovered skill names (sample): {', '.join(discovered[:30])}"
                return ToolOutput(success=False, error=err)
            
            file_path = str(Path(skill.directory) / relative_file_path)
            read_file_result = await self.operation.fs().read_file(file_path)
            if read_file_result.code != 0:
                return ToolOutput(
                    success=False,
                    error=read_file_result.message
                )

            skill_file_content = read_file_result.data.content

            data: Dict[str, Any] = {
                "skill_directory": str(skill.directory),
                "skill_content": skill_file_content,
            }

            if include_tree:
                tree, truncated, tree_err = await self._collect_skill_directory_tree(
                    Path(str(skill.directory)),
                    max_depth=tree_max_depth,
                    max_entries=tree_max_entries,
                )
                data["directory_tree"] = tree
                data["directory_tree_truncated"] = truncated
                if tree_err:
                    data["directory_tree_partial_errors"] = tree_err

            if include_discovered and self._skill_search_roots:
                discovered, disc_trunc = _collect_discovered_skill_names(
                    self._skill_search_roots,
                    max_names=max_discovered,
                    enabled_skill_names=self._enabled_skill_names,
                    disabled_skill_names=self._disabled_skill_names,
                )
                data["discovered_skill_names"] = discovered
                data["discovered_skill_names_truncated"] = disc_trunc

            return ToolOutput(
                success=True,
                data=data,
                extra_metadata={
                    "is_skill_body": True,
                    "skill_name": skill_name,
                    "relative_file_path": relative_file_path,
                },
            )
        
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=str(exc),
            )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        if False:
            yield None

    def _get_skill_by_name(self, skill_name: str) -> Optional[Skill]:
        """Select skill object from the rail-managed registry (exact name match)."""
        if not skill_name:
            return None

        skills = self.get_skills() or []
        skill_map = {skill.name: skill for skill in skills}
        return skill_map.get(skill_name)

    def _resolve_skill(self, skill_name: str) -> Optional[Skill]:
        """Registry first, then recursive filesystem search under ``skill_search_roots``."""
        reg = self._get_skill_by_name(skill_name)
        if reg is not None:
            return reg
        if not self._skill_search_roots:
            return None
        key = _normalize_skill_lookup_key(skill_name)
        if not key:
            return None
        if self._disabled_skill_names and key in self._disabled_skill_names:
            return None
        if self._enabled_skill_names is not None and len(self._enabled_skill_names) > 0:
            if key not in self._enabled_skill_names:
                return None
        for root in self._skill_search_roots:
            found = _recursive_find_skill_directory(root, key)
            if found is not None:
                try:
                    resolved = found.resolve()
                except OSError:
                    resolved = found
                return Skill(
                    name=resolved.name,
                    description=f"Filesystem-discovered skill under {root.name}",
                    directory=resolved,
                )
        return None