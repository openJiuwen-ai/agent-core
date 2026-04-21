# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared edit-scope rules for auto-harness planning and implementation."""

from __future__ import annotations

from pathlib import Path

from openjiuwen.core.sys_operation.cwd import (
    get_cwd,
    get_project_root,
)

ALLOWED_SOURCE_EDIT_PREFIXES = (
    "openjiuwen/harness/",
    "openjiuwen/core/",
)
ALLOWED_SUPPORT_EDIT_PREFIXES = (
    "tests/",
    "examples/",
    "docs/en/",
    "docs/zh/",
)
ALLOWED_EDIT_PREFIXES = (
    *ALLOWED_SOURCE_EDIT_PREFIXES,
    *ALLOWED_SUPPORT_EDIT_PREFIXES,
)


def normalize_repo_path(path: str) -> str:
    """Normalize a tool path into a repo-relative POSIX path when possible."""
    raw = str(path or "").strip()
    if not raw:
        return ""

    current_cwd = Path(get_cwd()).resolve()
    project_root = Path(
        get_project_root() or str(current_cwd)
    ).resolve()
    expanded = Path(raw).expanduser()
    resolved = (
        expanded.resolve()
        if expanded.is_absolute()
        else (current_cwd / expanded).resolve()
    )
    try:
        return resolved.relative_to(project_root).as_posix()
    except ValueError:
        return resolved.as_posix()


def is_allowed_repo_edit_path(path: str) -> bool:
    """Return whether a path is inside the allowed auto-harness edit scope."""
    normalized = normalize_repo_path(path)
    return any(
        normalized.startswith(prefix)
        for prefix in ALLOWED_EDIT_PREFIXES
    )


def render_edit_scope(
    header: str = "本轮允许变更范围",
) -> str:
    """Render a stable edit-scope block for prompts."""
    return (
        f"{header}:\n"
        "- 源码路径只允许 `openjiuwen/harness/**`、`openjiuwen/core/**`\n"
        "- `openjiuwen/harness/**`、`openjiuwen/core/**` 下的模块内 "
        "README/Markdown 视为源码目录内容，可正常修改，例如 "
        "`openjiuwen/harness/cli/README.md`\n"
        "- 配套文件允许新增或修改 `tests/**`、`examples/**`\n"
        "- 如果任务需要新增或更新仓库级文档，只能写入 `docs/en/` 和 `docs/zh/` 下的 Markdown 文件；不要在 `docs/` 根目录或其他子目录新增文档\n"
        "- 不要修改 `openjiuwen/auto_harness/**` 或其他源码目录\n"
        "- 如果任务必须改到范围外路径，停止并明确报告范围冲突，不要自行越界"
    )
