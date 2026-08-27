"""PersonalContext-only filesystem tool assembly and bounded text search."""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
from pathlib import Path
from typing import Any, Iterable

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.prompts.tools import ToolCardBuildOptions, build_tool_card
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.filesystem import (
    EditFileTool,
    GlobTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)

_DEFAULT_HEAD_LIMIT = 250
_MAX_HEAD_LIMIT = 1_000
_MAX_SEARCH_FILES = 5_000
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_CONTEXT_LINES = 20
_VCS_DIRECTORIES = frozenset({".git", ".svn", ".hg", ".bzr", ".jj", ".sl"})
_FILE_TYPE_GLOBS: dict[str, tuple[str, ...]] = {
    "json": ("*.json",),
    "markdown": ("*.md", "*.mdx"),
    "md": ("*.md", "*.mdx"),
    "python": ("*.py", "*.pyi"),
    "py": ("*.py", "*.pyi"),
    "text": ("*.txt",),
    "txt": ("*.txt",),
    "yaml": ("*.yaml", "*.yml"),
}


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_search_path(sandbox: Path, value: object) -> Path:
    root = sandbox.resolve(strict=True)
    raw = str(value or ".")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=True)
    if not _is_within(resolved, root):
        raise ValueError("grep path is outside the PersonalContext sandbox")
    return resolved


def _expand_braces(pattern: str) -> list[str]:
    match = re.search(r"\{([^{}]*)\}", pattern)
    if match is None:
        return [pattern]
    start = match.start()
    end = match.end()
    prefix = pattern[:start]
    suffix = pattern[end:]
    expanded: list[str] = []
    for option in match.group(1).split(","):
        expanded.extend(_expand_braces(prefix + option.strip() + suffix))
    return expanded


def _glob_patterns(value: object, file_type: object) -> tuple[str, ...]:
    patterns: list[str] = []
    if value:
        for chunk in str(value).split():
            if "{" in chunk and "}" in chunk:
                patterns.extend(_expand_braces(chunk))
            else:
                patterns.extend(item for item in chunk.split(",") if item)
    if file_type:
        type_patterns = _FILE_TYPE_GLOBS.get(str(file_type).casefold())
        if type_patterns is None:
            raise ValueError("unsupported grep type filter")
        patterns.extend(type_patterns)
    return tuple(patterns)


def _matches_glob(path: Path, sandbox: Path, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return True
    relative = path.relative_to(sandbox).as_posix()
    return any(fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(path.name, pattern) for pattern in patterns)


def _iter_files(path: Path, sandbox: Path, patterns: tuple[str, ...]) -> Iterable[Path]:
    if path.is_file():
        if _matches_glob(path, sandbox, patterns):
            yield path
        return

    emitted = 0
    for current, directories, filenames in os.walk(path, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(
            directory
            for directory in directories
            if directory not in _VCS_DIRECTORIES and not (current_path / directory).is_symlink()
        )
        for filename in sorted(filenames):
            candidate = current_path / filename
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            if not _is_within(resolved, sandbox) or not resolved.is_file():
                continue
            if not _matches_glob(resolved, sandbox, patterns):
                continue
            yield resolved
            emitted += 1
            if emitted >= _MAX_SEARCH_FILES:
                return


def _bounded_int(value: object, default: int, *, maximum: int) -> int:
    try:
        parsed = int(str(value)) if value is not None and value != "" else default
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, 0), maximum)


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def _read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw:
        return None
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _matching_line_numbers(pattern: re.Pattern[str], text: str, multiline: bool) -> list[int]:
    if multiline:
        return [text.count("\n", 0, match.start()) for match in pattern.finditer(text)]
    return [index for index, line in enumerate(text.splitlines()) if pattern.search(line) is not None]


def _render_content_matches(
    relative: str,
    lines: list[str],
    matched: list[int],
    *,
    before: int,
    after: int,
    show_line_numbers: bool,
) -> list[str]:
    rendered: list[str] = []
    emitted: set[int] = set()
    for line_index in matched:
        start = max(0, line_index - before)
        end = min(len(lines), line_index + after + 1)
        for current in range(start, end):
            if current in emitted:
                continue
            emitted.add(current)
            separator = ":" if current == line_index else "-"
            if show_line_numbers:
                rendered.append(f"{relative}{separator}{current + 1}{separator}{lines[current]}")
            else:
                rendered.append(f"{relative}:{lines[current]}")
    return rendered


def _search_bounded(sandbox: Path, inputs: dict[str, Any]) -> ToolOutput:
    pattern_value = inputs.get("pattern")
    if not pattern_value:
        return ToolOutput(success=False, error="pattern is required")
    try:
        root = sandbox.resolve(strict=True)
        search_path = _resolve_search_path(root, inputs.get("path"))
        patterns = _glob_patterns(inputs.get("glob"), inputs.get("type"))
        flags = re.MULTILINE
        if _as_bool(inputs.get("-i", inputs.get("ignore_case"))):
            flags |= re.IGNORECASE
        multiline = _as_bool(inputs.get("multiline"))
        if multiline:
            flags |= re.DOTALL
        compiled = re.compile(str(pattern_value), flags)
    except (OSError, ValueError, re.error) as exc:
        return ToolOutput(success=False, error=str(exc))

    output_mode = str(inputs.get("output_mode") or "content")
    if output_mode not in {"content", "files_with_matches", "count"}:
        return ToolOutput(
            success=False,
            error="output_mode must be one of: content, files_with_matches, count",
        )

    context = inputs.get("context", inputs.get("-C"))
    before = _bounded_int(
        context if context is not None else inputs.get("-B"),
        0,
        maximum=_MAX_CONTEXT_LINES,
    )
    after = _bounded_int(
        context if context is not None else inputs.get("-A"),
        0,
        maximum=_MAX_CONTEXT_LINES,
    )
    show_line_numbers = _as_bool(inputs.get("-n"), default=True)
    raw_lines: list[str] = []
    total_matches = 0
    matching_files = 0

    for file_path in _iter_files(search_path, root, patterns):
        text = _read_text(file_path)
        if text is None:
            continue
        matched = _matching_line_numbers(compiled, text, multiline)
        if not matched:
            continue
        relative = file_path.relative_to(root).as_posix()
        matching_files += 1
        total_matches += len(matched)
        if output_mode == "files_with_matches":
            raw_lines.append(relative)
        elif output_mode == "count":
            raw_lines.append(f"{relative}:{len(matched)}")
        else:
            raw_lines.extend(
                _render_content_matches(
                    relative,
                    text.splitlines(),
                    matched,
                    before=before,
                    after=after,
                    show_line_numbers=show_line_numbers,
                )
            )

    offset = _bounded_int(inputs.get("offset"), 0, maximum=_MAX_HEAD_LIMIT)
    requested_limit = _bounded_int(
        inputs.get("head_limit"),
        _DEFAULT_HEAD_LIMIT,
        maximum=_MAX_HEAD_LIMIT,
    )
    effective_limit = requested_limit or _MAX_HEAD_LIMIT
    end = offset + effective_limit
    selected = raw_lines[offset:end]
    was_truncated = len(raw_lines) - offset > effective_limit
    content = "\n".join(selected)
    data: dict[str, Any] = {
        "stdout": content,
        "stderr": "",
        "exit_code": 0 if raw_lines else 1,
        "mode": output_mode,
        "content": content,
        "appliedOffset": offset if offset else None,
        "appliedLimit": effective_limit if was_truncated else None,
    }
    if output_mode == "content":
        data.update(
            {
                "filenames": [],
                "numFiles": matching_files,
                "numLines": len(selected),
                "count": len(selected),
            }
        )
    elif output_mode == "count":
        data.update(
            {
                "filenames": [],
                "numFiles": matching_files,
                "numMatches": total_matches,
                "count": total_matches,
            }
        )
    else:
        data.update(
            {
                "filenames": selected,
                "numFiles": len(selected),
                "count": len(selected),
            }
        )
    return ToolOutput(success=True, data=data)


def _make_bounded_grep_tool(sandbox: Path) -> LocalFunction:
    async def grep(**inputs: Any) -> ToolOutput:
        return await asyncio.to_thread(_search_bounded, sandbox, inputs)

    card = build_tool_card(
        "grep",
        "GrepTool",
        "en",
        options=ToolCardBuildOptions(parallel_safe=True),
    )
    return LocalFunction(card=card, func=grep)


def make_personal_context_file_tools(
    operation: SysOperation,
    sandbox: Path,
) -> list[Tool | ToolCard]:
    """Return the exact model-visible file tool set for PersonalContext."""

    return [
        ReadFileTool(operation, "en", enable_image_multimodal=False),
        WriteFileTool(operation, "en"),
        EditFileTool(operation, "en"),
        GlobTool(operation, "en"),
        ListDirTool(operation, "en"),
        _make_bounded_grep_tool(sandbox),
    ]


__all__ = ["make_personal_context_file_tools"]
