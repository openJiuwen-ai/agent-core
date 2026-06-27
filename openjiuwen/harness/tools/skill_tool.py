# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
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

_skill_tool_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="skill_tool_")

_TREE_SKIP_DIR_NAMES: frozenset[str] = frozenset({
    "output",
    "temp",
    "assets",
    "node_modules"
})

_ALLOWED_SKILL_RELATIVE_FILE = "SKILL.md"


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


def _is_allowed_skill_relative_file(relative_file_path: str) -> bool:
    """``skill_tool`` may only read the primary ``SKILL.md`` at the skill root."""
    normalized = normalize_skill_relative_file_path(relative_file_path)
    return normalized.replace("\\", "/").removeprefix("./") == _ALLOWED_SKILL_RELATIVE_FILE


def _parse_skill_name(skill_name: str) -> Tuple[str, str, bool]:
    """Return ``(parent_relative_path, leaf_name, is_namespaced)`` for resolver."""
    clean = skill_name.replace("\\", "/").strip().strip("/")
    if not clean:
        return "", "", False
    if "/" in clean:
        parts = [part for part in clean.split("/") if part]
        if not parts:
            return "", "", False
        leaf = parts[-1]
        parent = "/".join(parts[:-1])
        return parent, leaf, True
    return "", clean, False


def _skill_path_prefixes(skill_name: str) -> List[str]:
    """Return cumulative path prefixes for allowlist matching (e.g. ``a/b`` → ``[a, a/b]``)."""
    clean = skill_name.replace("\\", "/").strip().strip("/")
    if not clean:
        return []
    parts = [part for part in clean.split("/") if part]
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def _skill_allowlist_permits(
    skill_name: str,
    *,
    is_namespaced: bool,
    leaf: str,
    enabled_skill_names: Optional[FrozenSet[str]] = None,
    disabled_skill_names: Optional[FrozenSet[str]] = None,
) -> bool:
    """Allowlist semantics for skill paths relative to search roots.

    - Bare ``pptx-craft``: match by full path (same as leaf for bare names).
    - Namespaced ``pptx-craft/A``: allow when an enabled prefix covers the path
      from the root (umbrella inheritance); leaf-only matches do not apply.
    - Namespaced ``A/pptx-craft``: not allowed when only ``pptx-craft`` is enabled.
    """
    if disabled_skill_names:
        if is_namespaced:
            for prefix in _skill_path_prefixes(skill_name):
                if prefix in disabled_skill_names:
                    return False
        elif leaf in disabled_skill_names:
            return False
    if enabled_skill_names is not None and len(enabled_skill_names) > 0:
        clean = skill_name.replace("\\", "/").strip().strip("/")
        if clean in enabled_skill_names:
            return True
        if is_namespaced:
            for prefix in _skill_path_prefixes(skill_name):
                if prefix in enabled_skill_names:
                    return True
            return False
        return False
    return True


def _recursive_find_skill_directory(directory: Path, skill_name: str) -> List[Path]:
    """Depth-first: all directories named ``skill_name`` that contain ``SKILL.md``."""
    if not skill_name:
        return []
    results: List[Path] = []
    try:
        children = sorted(directory.iterdir(), key=lambda p: p.name.lower())
    except (OSError, PermissionError):
        return results
    for child in children:
        if not child.is_dir():
            continue
        if child.name in _TREE_SKIP_DIR_NAMES or child.name.startswith("."):
            continue
        if child.name == skill_name and (child / "SKILL.md").is_file():
            results.append(child)
        results.extend(_recursive_find_skill_directory(child, skill_name))
    return results


def _resolve_namespaced_skill_directory(
    roots: List[Path],
    parent_rel: str,
    leaf: str,
) -> Optional[Path]:
    """Resolve ``parent_rel/leaf/SKILL.md`` exactly under each search root."""
    if not leaf:
        return None
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        if parent_rel:
            candidate = root / Path(parent_rel) / leaf
        else:
            candidate = root / leaf
        if candidate.is_dir() and (candidate / "SKILL.md").is_file():
            try:
                return candidate.resolve()
            except OSError:
                return candidate
    return None


def _relative_skill_path_from_roots(path: Path, roots: List[Path]) -> str:
    """Return skill path relative to the first matching search root."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    for root in roots:
        try:
            root_resolved = root.resolve()
        except OSError:
            root_resolved = root
        try:
            rel = resolved.relative_to(root_resolved)
        except ValueError:
            continue
        return str(rel).replace("\\", "/")
    return resolved.name


def _collect_discovered_skill_names(
    roots: List[Path],
    *,
    max_names: int,
    enabled_skill_names: Optional[FrozenSet[str]] = None,
    disabled_skill_names: Optional[FrozenSet[str]] = None,
) -> Tuple[List[str], bool]:
    """Recursively collect distinct skill relative paths that contain ``SKILL.md`` under each root."""
    names: List[str] = []
    seen: Set[str] = set()
    truncated = False

    def allow_rel_path(rel_path: str) -> bool:
        _, leaf, is_namespaced = _parse_skill_name(rel_path)
        return _skill_allowlist_permits(
            rel_path,
            is_namespaced=is_namespaced,
            leaf=leaf,
            enabled_skill_names=enabled_skill_names,
            disabled_skill_names=disabled_skill_names,
        )

    def visit(directory: Path, base: Path) -> None:
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
            if (child / "SKILL.md").is_file():
                rel_path = str(child.relative_to(base)).replace("\\", "/")
                if rel_path not in seen and allow_rel_path(rel_path):
                    names.append(rel_path)
                    seen.add(rel_path)
            visit(child, base)

    for base in roots:
        if base.exists() and base.is_dir():
            visit(base, base)

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
                full path, any path prefix (for namespaced paths), or leaf basename (for bare
                names) is in this set. Enabling an umbrella skill (e.g. ``pptx-craft``) also
                permits nested sub-skills (e.g. ``pptx-craft/designer``), but not unrelated
                paths that only share the leaf name (e.g. ``other/pptx-craft``).
            disabled_skill_names: Skills whose leaf (bare names) or any path prefix (namespaced
                paths) is in this set are excluded.
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
        if not _is_allowed_skill_relative_file(relative_file_path):
            return ToolOutput(
                success=False,
                error=(
                    f"skill_tool only supports reading {_ALLOWED_SKILL_RELATIVE_FILE}; "
                    f"got relative_file_path={relative_file_path!r}. "
                    "Use filesystem tools for other files under the skill directory."
                ),
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
            skill, resolve_err = self._resolve_skill(skill_name)
            if resolve_err or not skill:
                err = resolve_err or f"Skill not found: {skill_name}"
                if self._skill_search_roots and include_discovered:
                    loop = asyncio.get_running_loop()
                    discovered, _ = await loop.run_in_executor(
                        _skill_tool_executor,
                        lambda: _collect_discovered_skill_names(
                            self._skill_search_roots,
                            max_names=max_discovered,
                            enabled_skill_names=self._enabled_skill_names,
                            disabled_skill_names=self._disabled_skill_names,
                        ),
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
                loop = asyncio.get_running_loop()
                discovered, disc_trunc = await loop.run_in_executor(
                    _skill_tool_executor,
                    lambda: _collect_discovered_skill_names(
                        self._skill_search_roots,
                        max_names=max_discovered,
                        enabled_skill_names=self._enabled_skill_names,
                        disabled_skill_names=self._disabled_skill_names,
                    ),
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

    def _reject_leaf_lookup(
        self,
        skill_name: str,
        leaf: str,
        *,
        is_namespaced: bool,
    ) -> Optional[str]:
        """Return not-found message when allowlist preconditions fail."""
        not_found = f"Skill not found: {skill_name}"
        if not leaf:
            return not_found
        if not _skill_allowlist_permits(
            skill_name,
            is_namespaced=is_namespaced,
            leaf=leaf,
            enabled_skill_names=self._enabled_skill_names,
            disabled_skill_names=self._disabled_skill_names,
        ):
            return not_found
        return None

    def _resolve_skill(self, skill_name: str) -> Tuple[Optional[Skill], Optional[str]]:
        """Resolve skill by namespaced path or bare name with ambiguity detection."""
        parent_rel, leaf, is_namespaced = _parse_skill_name(skill_name)
        rejection = self._reject_leaf_lookup(skill_name, leaf, is_namespaced=is_namespaced)
        if rejection:
            return None, rejection
        not_found = f"Skill not found: {skill_name}"
        if is_namespaced:
            if not self._skill_search_roots:
                return None, not_found
            found = _resolve_namespaced_skill_directory(
                self._skill_search_roots,
                parent_rel,
                leaf,
            )
            if found is None:
                return None, not_found
            rel_path = f"{parent_rel}/{leaf}".strip("/")
            return Skill(
                name=found.name,
                description=f"Filesystem-discovered skill at {rel_path}",
                directory=found,
            ), None
        reg = self._get_skill_by_name(skill_name)
        if reg is not None:
            return reg, None
        if not self._skill_search_roots:
            return None, not_found
        all_matches: List[Path] = []
        for root in self._skill_search_roots:
            all_matches.extend(_recursive_find_skill_directory(root, leaf))
        deduped_matches: List[Path] = []
        seen_paths: Set[str] = set()
        for match in all_matches:
            try:
                key = str(match.resolve())
            except OSError:
                key = str(match)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            deduped_matches.append(match)
        if not deduped_matches:
            return None, not_found
        if len(deduped_matches) == 1:
            found = deduped_matches[0]
            try:
                resolved = found.resolve()
            except OSError:
                resolved = found
            rel_path = _relative_skill_path_from_roots(resolved, self._skill_search_roots)
            return Skill(
                name=resolved.name,
                description=f"Filesystem-discovered skill at {rel_path}",
                directory=resolved,
            ), None
        candidates = sorted(
            {
                _relative_skill_path_from_roots(match, self._skill_search_roots)
                for match in deduped_matches
            }
        )
        example = candidates[0] if candidates else f"parent/{leaf}"
        err = (
            f"Skill name {leaf!r} is ambiguous; multiple skills match. "
            f"Retry with a namespaced path (parent/subskill), e.g. {example!r}. "
            f"Candidates: {', '.join(candidates)}"
        )
        return None, err
