"""PersonalContext-only filesystem tool assembly and bounded text search."""

from __future__ import annotations

import asyncio
import fnmatch
import ntpath
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.personal_context.config import (
    DEFAULT_MAX_PAGES_PER_DIRECTORY,
    DEFAULT_MAX_SUBDIRECTORIES_PER_DIRECTORY,
)
from openjiuwen.harness.personal_context.path_safety import (
    SEMANTIC_NAME_MAX_CHARS,
    assert_existing_chain_is_plain,
    is_reparse_point,
    resolve_context_relative_path,
    semantic_context_segment_is_safe,
)
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
_DIRECTORY_STATS_UNAVAILABLE_GUIDANCE = "目录统计暂不可用；请先使用 list_files 确认目录内容。"
_CONTEXT_ROOT_GUIDANCE = "这里只能保留 description.md 和目录。"
_CONTEXT_NEAR_CAPACITY_GUIDANCE = "该目录接近建议上限，优先考虑其他目录或拆分子目录。"
_CONTEXT_AT_CAPACITY_GUIDANCE = "该目录已达到建议上限，不要继续放入普通页面；请选择其他目录或建立子目录。"
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


def _extended_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    absolute = str(path.absolute())
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path(ntpath.join("\\\\?\\UNC", absolute[2:]))
    drive, tail = ntpath.splitdrive(absolute)
    namespace_root = f"\\\\?\\{drive}\\"
    return Path(ntpath.join(namespace_root, tail.lstrip("\\/")))


def _path_exists(path: Path) -> bool:
    return _extended_path(path).exists()


def _path_is_file(path: Path) -> bool:
    return _extended_path(path).is_file()


def _path_is_dir(path: Path) -> bool:
    return _extended_path(path).is_dir()


def _path_is_link_or_reparse(path: Path) -> bool:
    target = _extended_path(path)
    return path.is_symlink() or is_reparse_point(path) or target.is_symlink() or is_reparse_point(target)


def _directory_entries(path: Path) -> list[Path]:
    return [path / entry.name for entry in _extended_path(path).iterdir()]


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _new_context_path_error(
    sandbox: Path,
    value: object,
    *,
    path_kind: str = "file",
) -> str | None:
    """Validate a not-yet-created user-visible Context path without mutating disk."""

    if not isinstance(value, str) or not value.strip():
        return None
    root = sandbox.absolute()
    raw = value.strip()
    candidate_value = Path(raw)
    if candidate_value.is_absolute():
        candidate = candidate_value.absolute()
    else:
        if "\\" in raw:
            return None
        pure = PurePosixPath(raw)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            return None
        candidate = root.joinpath(*pure.parts)
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return None
    if not relative.parts or relative.parts[0].casefold() != "context":
        return None
    if _path_exists(candidate):
        return None
    context_parts = relative.parts[1:]
    if not context_parts:
        return None
    if path_kind == "file" and len(context_parts) == 1 and context_parts[0].casefold() != "description.md":
        return "ordinary Markdown pages cannot be placed directly under context/"

    context_root = root / "context"
    current = context_root
    for segment in context_parts[:-1]:
        current /= segment
        if _path_is_dir(current) and not _path_is_link_or_reparse(current):
            continue
        if not semantic_context_segment_is_safe(segment, markdown_file=False):
            prefix = PurePosixPath(*current.relative_to(context_root).parts).as_posix()
            return (
                "candidate Context directory name must be portable and at most "
                f"{SEMANTIC_NAME_MAX_CHARS} Unicode characters: {prefix}"
            )

    leaf = context_parts[-1]
    if leaf.casefold() == "description.md":
        return None
    markdown_file = path_kind == "file"
    if not semantic_context_segment_is_safe(leaf, markdown_file=markdown_file):
        kind = "Markdown file stem" if markdown_file else "directory name"
        relative_label = PurePosixPath(*context_parts).as_posix()
        return (
            f"candidate Context {kind} must be portable and at most "
            f"{SEMANTIC_NAME_MAX_CHARS} Unicode characters: {relative_label}"
        )
    return None


def _make_personal_context_write_file_tool(
    operation: SysOperation,
    sandbox: Path,
) -> LocalFunction:
    delegate = WriteFileTool(operation, "en")

    async def write_file(**inputs: Any) -> ToolOutput:
        error = _new_context_path_error(sandbox, inputs.get("file_path"))
        if error is not None:
            return ToolOutput(success=False, error=error)
        return await delegate.invoke(inputs)

    return LocalFunction(card=delegate.card, func=write_file)


def _make_personal_context_edit_file_tool(
    operation: SysOperation,
    sandbox: Path,
) -> LocalFunction:
    delegate = EditFileTool(operation, "en")

    async def edit_file(**inputs: Any) -> ToolOutput:
        error = _new_context_path_error(sandbox, inputs.get("file_path"))
        if error is not None:
            return ToolOutput(success=False, error=error)
        return await delegate.invoke(inputs)

    return LocalFunction(card=delegate.card, func=edit_file)


def _safe_relative_directory(sandbox: Path, directory: Path) -> str:
    """Return a sandbox-relative label without exposing an absolute path."""

    try:
        root = sandbox.expanduser().resolve(strict=False)
        candidate = directory.expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        return candidate.resolve(strict=False).relative_to(root).as_posix()
    except (OSError, RuntimeError, ValueError):
        return "."


def _directory_snapshot(
    sandbox: Path,
    directory: Path,
    *,
    max_pages_per_directory: int = DEFAULT_MAX_PAGES_PER_DIRECTORY,
    max_subdirectories_per_directory: int = DEFAULT_MAX_SUBDIRECTORIES_PER_DIRECTORY,
) -> dict[str, Any]:
    """Count only direct children and return safe Agent-facing guidance."""

    relative_directory = _safe_relative_directory(sandbox, directory)
    try:
        root = sandbox.expanduser().resolve(strict=True)
        candidate = directory.expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        if _path_is_link_or_reparse(candidate):
            raise OSError("directory is linked")
        resolved = candidate.absolute()
        if not _is_within(resolved, root):
            raise OSError("directory is unavailable")
        assert_existing_chain_is_plain(resolved, stop=root)
        if not _path_is_dir(resolved):
            raise OSError("directory is unavailable")
        entries = _directory_entries(resolved)
        direct_directories = 0
        direct_files = 0
        ordinary_markdown = 0
        for entry in entries:
            if _path_is_link_or_reparse(entry):
                direct_files += 1
                continue
            if _path_is_dir(entry):
                direct_directories += 1
                continue
            if _path_is_file(entry):
                direct_files += 1
                if entry.suffix.casefold() == ".md" and entry.name.casefold() != "description.md":
                    ordinary_markdown += 1

        context_root = (root / "context").resolve(strict=False)
        page_state = (
            "full"
            if ordinary_markdown >= max_pages_per_directory
            else "near_limit"
            if ordinary_markdown >= max(1, (max_pages_per_directory * 4 + 4) // 5)
            else "normal"
        )
        subdirectory_state = (
            "full"
            if direct_directories >= max_subdirectories_per_directory
            else "near_limit"
            if direct_directories >= max(1, (max_subdirectories_per_directory * 4 + 4) // 5)
            else "normal"
        )
        if resolved == context_root:
            guidance_parts = [_CONTEXT_ROOT_GUIDANCE]
            if subdirectory_state == "full":
                guidance_parts.append("根目录的子目录数已达到上限，请选择已有目录或先重新分组。")
            elif subdirectory_state == "near_limit":
                guidance_parts.append("根目录的子目录数接近上限，优先使用已有目录或拆分层级。")
            guidance = "".join(guidance_parts)
        elif _is_within(resolved, context_root):
            guidance_parts = []
            if page_state == "full":
                guidance_parts.append(_CONTEXT_AT_CAPACITY_GUIDANCE)
            elif page_state == "near_limit":
                guidance_parts.append(_CONTEXT_NEAR_CAPACITY_GUIDANCE)
            if subdirectory_state == "full":
                guidance_parts.append("该目录的子目录数已达到上限，请选择其他父目录或先重新分组。")
            elif subdirectory_state == "near_limit":
                guidance_parts.append("该目录的子目录数接近上限，优先使用其他父目录或拆分层级。")
            guidance = "".join(guidance_parts)
        else:
            guidance = ""
        return {
            "relative_directory": relative_directory,
            "direct_directory_count": direct_directories,
            "direct_file_count": direct_files,
            "ordinary_markdown_count": ordinary_markdown,
            "max_pages_per_directory": max_pages_per_directory,
            "remaining_page_capacity": max(0, max_pages_per_directory - ordinary_markdown),
            "page_capacity_state": page_state,
            "max_subdirectories_per_directory": max_subdirectories_per_directory,
            "remaining_subdirectory_capacity": max(0, max_subdirectories_per_directory - direct_directories),
            "subdirectory_capacity_state": subdirectory_state,
            "guidance": guidance,
        }
    except (OSError, RuntimeError, ValueError):
        return {
            "relative_directory": relative_directory,
            "stats_unavailable": True,
            "guidance": _DIRECTORY_STATS_UNAVAILABLE_GUIDANCE,
        }


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


def _assert_markdown_move_tree(source: Path) -> str:
    if _path_is_link_or_reparse(source):
        raise ValueError("Context move source contains a link or reparse point")
    if _path_is_file(source):
        if source.suffix.casefold() != ".md":
            raise ValueError("Context move supports only Markdown files")
        return "file"
    if not _path_is_dir(source):
        raise ValueError("Context move source is not a regular file or directory")

    pending = [source]
    while pending:
        directory = pending.pop()
        if _path_is_link_or_reparse(directory):
            raise ValueError("Context move source contains a link or reparse point")
        try:
            entries = _directory_entries(directory)
        except OSError as exc:
            raise ValueError("Context move source tree is unavailable") from exc
        for entry in entries:
            if _path_is_link_or_reparse(entry):
                raise ValueError("Context move source contains a link or reparse point")
            if _path_is_dir(entry):
                pending.append(entry)
            elif not _path_is_file(entry) or entry.suffix.casefold() != ".md":
                raise ValueError("Context move directory may contain only directories and Markdown files")
    return "directory"


def _move_context_path(
    sandbox: Path,
    inputs: dict[str, Any],
    *,
    max_pages_per_directory: int = DEFAULT_MAX_PAGES_PER_DIRECTORY,
    max_subdirectories_per_directory: int = DEFAULT_MAX_SUBDIRECTORIES_PER_DIRECTORY,
) -> ToolOutput:
    try:
        sandbox_root = sandbox.resolve(strict=True)
        context_path = sandbox_root / "context"
        if context_path.is_symlink() or is_reparse_point(context_path):
            raise ValueError("candidate Context root contains a link or reparse point")
        context_root = context_path.resolve(strict=True)
        if not context_root.is_dir() or not _is_within(context_root, sandbox_root):
            raise ValueError("candidate Context root is unavailable")
        source = resolve_context_relative_path(context_root, inputs.get("source_path"), must_exist=True)
        destination = resolve_context_relative_path(context_root, inputs.get("destination_path"), must_exist=False)
        source_relative = source.relative_to(context_root).as_posix()
        destination_relative = destination.relative_to(context_root).as_posix()
        source_kind = _assert_markdown_move_tree(source)
        semantic_error = _new_context_path_error(
            sandbox_root,
            f"context/{destination_relative}",
            path_kind=source_kind,
        )
        if semantic_error is not None:
            raise ValueError(semantic_error)
        if source_relative == "description.md" or destination_relative == "description.md":
            raise ValueError("root description.md cannot be moved or replaced")
        if _path_exists(destination) or _path_is_link_or_reparse(destination):
            raise ValueError("Context move destination already exists")
        destination_parent = destination.parent
        if not _path_is_dir(destination_parent) or _path_is_link_or_reparse(destination_parent):
            raise ValueError("Context move destination parent is not a directory")

        if source_kind == "file" and destination.suffix.casefold() != ".md":
            raise ValueError("Markdown files must keep the .md extension")
        if source_kind == "directory" and destination.is_relative_to(source):
            raise ValueError("Context directory cannot be moved into its own subtree")
        if source_kind == "file" and destination_parent == context_root:
            raise ValueError("ordinary Markdown pages cannot be placed directly under context/")
        if destination_parent != source.parent:
            if source_kind == "file":
                ordinary_page_counts = []
                for matched_entry in _directory_entries(destination_parent):
                    if not (_path_is_file(matched_entry)):
                        continue
                    if _path_is_link_or_reparse(matched_entry):
                        continue
                    if matched_entry.suffix.casefold() != ".md":
                        continue
                    if matched_entry.name.casefold() == "description.md":
                        continue
                    ordinary_page_counts.append(1)
                ordinary_pages = sum(ordinary_page_counts)
                if ordinary_pages >= max_pages_per_directory:
                    raise ValueError("Context destination directory has reached its Markdown page capacity")
            else:
                child_directories = sum(
                    1
                    for entry in _directory_entries(destination_parent)
                    if _path_is_dir(entry) and not _path_is_link_or_reparse(entry)
                )
                if child_directories >= max_subdirectories_per_directory:
                    raise ValueError("Context destination directory has reached its subdirectory capacity")

        os.replace(_extended_path(source), _extended_path(destination))
    except ValueError as exc:
        return ToolOutput(success=False, error=str(exc))
    except OSError:
        return ToolOutput(success=False, error="Context path could not be moved")
    return ToolOutput(
        success=True,
        data={
            "source_path": source_relative,
            "destination_path": destination_relative,
            "kind": source_kind,
            "links_rewritten": False,
        },
    )


def _make_move_path_tool(
    sandbox: Path,
    *,
    max_pages_per_directory: int = DEFAULT_MAX_PAGES_PER_DIRECTORY,
    max_subdirectories_per_directory: int = DEFAULT_MAX_SUBDIRECTORIES_PER_DIRECTORY,
) -> LocalFunction:
    async def move_path(**inputs: Any) -> ToolOutput:
        return await asyncio.to_thread(
            _move_context_path,
            sandbox,
            inputs,
            max_pages_per_directory=max_pages_per_directory,
            max_subdirectories_per_directory=max_subdirectories_per_directory,
        )

    return LocalFunction(
        card=ToolCard(
            id="personal_context_move_path",
            name="move_path",
            description=(
                "Move or rename one Markdown file or directory inside candidate context/. "
                "Paths are POSIX paths relative to context/, destination must not exist, and links are not rewritten."
            ),
            input_params={
                "type": "object",
                "properties": {
                    "source_path": {"type": "string"},
                    "destination_path": {"type": "string"},
                },
                "required": ["source_path", "destination_path"],
                "additionalProperties": False,
            },
            parallel_safe=False,
            idempotent=False,
        ),
        func=move_path,
    )


def make_personal_context_file_tools(
    operation: SysOperation,
    sandbox: Path,
    *,
    max_pages_per_directory: int = DEFAULT_MAX_PAGES_PER_DIRECTORY,
    max_subdirectories_per_directory: int = DEFAULT_MAX_SUBDIRECTORIES_PER_DIRECTORY,
) -> list[Tool | ToolCard]:
    """Return the exact model-visible file tool set for PersonalContext."""

    return [
        ReadFileTool(operation, "en", enable_image_multimodal=False),
        _make_personal_context_write_file_tool(operation, sandbox),
        _make_personal_context_edit_file_tool(operation, sandbox),
        GlobTool(operation, "en"),
        ListDirTool(operation, "en"),
        _make_bounded_grep_tool(sandbox),
        _make_move_path_tool(
            sandbox,
            max_pages_per_directory=max_pages_per_directory,
            max_subdirectories_per_directory=max_subdirectories_per_directory,
        ),
    ]


__all__ = ["make_personal_context_file_tools"]
