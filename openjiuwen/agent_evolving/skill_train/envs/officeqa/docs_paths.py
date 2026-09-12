# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""OfficeQA corpus discovery and sandboxed local document tools."""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

_READ_CHAR_CAP = 4000
_GREP_LINE_CAP = 20
_GLOB_HIT_CAP = 50


def _tokenize_dir_blob(raw: str) -> list[str]:
    out: list[str] = []
    for segment in raw.split(os.pathsep):
        for piece in segment.split(","):
            cleaned = piece.strip()
            if cleaned:
                out.append(cleaned)
    return out


def _normalize_dir_specs(data_dirs: Sequence[str] | str | None) -> list[str]:
    if data_dirs is None:
        return []
    if isinstance(data_dirs, str):
        return _tokenize_dir_blob(data_dirs)
    return [str(entry).strip() for entry in data_dirs if str(entry).strip()]


def _absolutize(base: Path, specs: Iterable[str]) -> list[str]:
    paths: list[str] = []
    for spec in specs:
        path = Path(spec).expanduser()
        if not path.is_absolute():
            path = base / path
        paths.append(str(path))
    return paths


def _prefer_transformed(path: Path) -> str | None:
    if not path.is_dir():
        return None
    nested = path / "transformed"
    chosen = nested if nested.is_dir() else path
    return str(chosen.resolve())


def _builtin_probes(data_root: Path, repo_root: Path) -> list[str]:
    home = Path.home()
    return [
        str(data_root / "officeqa_docs_official"),
        str(data_root / "officeqa_smoke_docs"),
        str(repo_root / "data" / "officeqa_docs_official"),
        str(repo_root / "data" / "officeqa_smoke_docs"),
        str(home / "officeqa-sparse" / "treasury_bulletins_parsed"),
        str(home / "officeqa" / "treasury_bulletins_parsed"),
    ]


def resolve_docs_roots(data_dirs: Sequence[str] | str | None = None) -> list[str]:
    """Return unique readable document roots (prefer ``transformed`` subdirs)."""
    from openjiuwen.agent_evolving.skill_train.paths import skill_train_data_root, skill_train_root

    package_root = skill_train_root()
    data_root = skill_train_data_root()
    repo_root = Path(__file__).resolve().parents[5]
    env_blob = os.environ.get("OFFICEQA_DOCS_DIR", "").strip()

    candidates: list[str] = []
    for base in (repo_root, package_root):
        candidates.extend(_absolutize(base, _normalize_dir_specs(data_dirs)))
        candidates.extend(_absolutize(base, _normalize_dir_specs(env_blob or None)))
    candidates.extend(_builtin_probes(data_root, repo_root))

    roots: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        resolved = _prefer_transformed(Path(raw).expanduser())
        if resolved is None or resolved in seen:
            continue
        seen.add(resolved)
        roots.append(resolved)

    if not roots:
        raise FileNotFoundError(
            "No OfficeQA document root found. Configure OFFICEQA_DOCS_DIR or env.data_dirs."
        )
    return roots


def _path_allowed(
    path: str,
    allowed_roots: Sequence[str],
    allowed_basenames: Sequence[str],
) -> bool:
    try:
        absolute = str(Path(path).resolve())
    except FileNotFoundError:
        return False
    inside = any(
        absolute == root or absolute.startswith(root + os.sep) for root in allowed_roots
    )
    if not inside:
        return False
    if not allowed_basenames:
        return True
    return Path(absolute).name in set(allowed_basenames)


def resolve_candidate_files(source_files: list[str], allowed_roots: list[str]) -> list[str]:
    """Collect absolute paths whose basename appears in ``source_files``."""
    wanted = set(source_files) if source_files else None
    ordered: list[str] = []
    seen: set[str] = set()
    for root in allowed_roots:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                if wanted is not None and name not in wanted:
                    continue
                absolute = str(Path(dirpath, name).resolve())
                if absolute in seen:
                    continue
                seen.add(absolute)
                ordered.append(absolute)
    return ordered


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return "".join(list(text)[:limit])


def _tool_glob(
    arguments: dict,
    *,
    allowed_roots: Sequence[str],
    allowed_basenames: Sequence[str],
) -> tuple[str, str]:
    pattern = str(arguments.get("pattern") or "*")
    matches: list[str] = []
    for root in allowed_roots:
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                if allowed_basenames and name not in allowed_basenames:
                    continue
                relative = os.path.relpath(os.path.join(dirpath, name), root)
                if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(name, pattern):
                    matches.append(os.path.join(dirpath, name))
                if len(matches) >= _GLOB_HIT_CAP:
                    return f"glob(pattern={pattern!r})", "\n".join(matches)
    body = "\n".join(matches) if matches else "[no matches]"
    return f"glob(pattern={pattern!r})", body


def _tool_read(
    arguments: dict,
    *,
    allowed_roots: Sequence[str],
    allowed_basenames: Sequence[str],
) -> tuple[str, str]:
    path = str(arguments.get("path") or "")
    label = f"read(path={path!r})"
    if not path:
        return "read(path='')", "[read error: missing path]"
    if not _path_allowed(path, allowed_roots, allowed_basenames):
        return label, "[read error: path not allowed]"

    start_line = max(int(arguments.get("start") or 1), 1)
    line_budget = max(int(arguments.get("limit") or 80), 1)
    with open(path, encoding="utf-8") as handle:
        all_lines = handle.readlines()
    begin = start_line - 1
    end = begin + line_budget
    window = "".join(all_lines[begin:end])
    label = f"read(path={path!r}, start={start_line}, limit={line_budget})"
    clipped = _clip(window, _READ_CHAR_CAP)
    return label, clipped if clipped else "[empty file]"


def _tool_grep(
    arguments: dict,
    *,
    allowed_roots: Sequence[str],
    allowed_basenames: Sequence[str],
) -> tuple[str, str]:
    needle = str(arguments.get("pattern") or "").lower()
    path = str(arguments.get("path") or "")
    label = f"grep(pattern={needle!r}, path={path!r})"
    if not needle or not path:
        return label, "[grep error: missing pattern or path]"
    if not _path_allowed(path, allowed_roots, allowed_basenames):
        return label, "[grep error: path not allowed]"

    hits: list[str] = []
    with open(path, encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            if needle in line.lower():
                hits.append(f"{lineno}: {line.rstrip()}")
            if len(hits) >= _GREP_LINE_CAP:
                break
    return label, "\n".join(hits) if hits else "[no matches]"


_HANDLERS: dict[str, Callable[..., tuple[str, str]]] = {
    "glob": _tool_glob,
    "read": _tool_read,
    "grep": _tool_grep,
}


def run_tool(
    name: str,
    arguments: dict,
    *,
    allowed_roots: list[str],
    allowed_files: list[str],
) -> tuple[str, str]:
    """Dispatch a local corpus tool under the given path allow-lists."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return name, f"[tool error: unknown tool {name}]"
    return handler(
        arguments,
        allowed_roots=allowed_roots,
        allowed_basenames=allowed_files,
    )
