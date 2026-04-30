# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Workspace prompt section for DeepAgent - dynamically scans real directory structure."""
from __future__ import annotations

from typing import Dict, List, Optional

from openjiuwen.harness.prompts.workspace_content.workspace_header import (
    WORKSPACE_HEADER,
    DIRECTORY_DESCRIPTIONS,
    IMPORTANT_FILES,
)
from openjiuwen.harness.prompts.sections import SectionName


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
DirNode = Dict


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _get_directory_description(name: str, language: str = "cn") -> str:
    """Get description for a directory/file name.

    Args:
        name: File or directory name.
        language: 'cn' or 'en'.

    Returns:
        Description string, or empty string if no match.
    """
    descs = DIRECTORY_DESCRIPTIONS.get(language, DIRECTORY_DESCRIPTIONS["cn"])
    return descs.get(name, "")


async def _scan_directory_structure(
        sys_operation,
        root_path: str,
        current_depth: int = 0,
        max_depth: int = 2,
        language: str = "cn",
) -> List[DirNode]:
    """Scan directory structure using sys_operation (max depth limited).

    Args:
        sys_operation: SysOperation instance.
        root_path: Current directory to scan.
        current_depth: Current recursion depth (0 = root).
        max_depth: Maximum depth to scan (2 = scan up to level 2 directories).
        language: 'cn' or 'en'.

    Returns:
        List of directory node dicts.
    """
    if sys_operation is None or current_depth > max_depth:
        return []

    nodes: List[DirNode] = []

    dir_result = await sys_operation.fs().list_directories(root_path, recursive=False)
    if dir_result.code == 0 and dir_result.data and dir_result.data.list_items:
        for item in sorted(dir_result.data.list_items, key=lambda x: x.name):
            dir_name = item.name
            full_path = f"{root_path}/{dir_name}" if root_path else dir_name
            desc = _get_directory_description(dir_name, language)

            children: List[DirNode] = []
            if current_depth < max_depth:
                children = await _scan_directory_structure(
                    sys_operation, full_path, current_depth + 1, max_depth, language
                )

            nodes.append({
                "name": dir_name,
                "path": full_path,
                "description": desc,
                "is_file": False,
                "children": children,
            })

    if current_depth < max_depth:
        file_result = await sys_operation.fs().list_files(root_path, recursive=False)
        if file_result.code == 0 and file_result.data and file_result.data.list_items:
            for item in sorted(file_result.data.list_items, key=lambda x: x.name):
                file_name = item.name
                full_path = f"{root_path}/{file_name}" if root_path else file_name
                desc = _get_directory_description(file_name, language)

                nodes.append({
                    "name": file_name,
                    "path": full_path,
                    "description": desc,
                    "is_file": True,
                    "children": [],
                })

    return nodes


def _format_node(
        node: DirNode,
        lines: List[str],
        prefix: str,
        is_last: bool,
        language: str,
) -> None:
    """Format a directory node and recurse into its children.

    Args:
        node: Directory node dict.
        lines: Output lines list (modified in place).
        prefix: Indentation prefix for the current level.
        is_last: Whether this node is the last sibling at its level.
        language: 'cn' or 'en'.
    """
    connector = "└── " if is_last else "├── "
    name = node.get("name", "")
    is_file = node.get("is_file", False)
    desc = node.get("description", "")

    if is_file:
        lines.append(f"{prefix}{connector}{name}")
    else:
        line = f"{prefix}{connector}{name}/"
        if desc:
            line += f"  # {desc}"
        lines.append(line)

    children = node.get("children", [])
    for i, child in enumerate(children):
        child_is_last = (i == len(children) - 1)
        if child_is_last:
            child_prefix = prefix + "    "
        else:
            child_prefix = prefix + "│   "
        _format_node(child, lines, child_prefix, child_is_last, language)


def _format_tree(nodes: List[DirNode], language: str) -> List[str]:
    """Format top-level nodes as a tree, returns list of lines."""
    lines: List[str] = []
    for i, node in enumerate(nodes):
        is_last = (i == len(nodes) - 1)
        _format_node(node, lines, prefix="", is_last=is_last, language=language)
    return lines


async def build_workspace_content(
        sys_operation,
        workspace,
        language: str = "cn",
) -> str:
    """Build the workspace content with header and path declaration.

    Args:
        sys_operation: SysOperation instance (unused, kept for API compat).
        workspace: Workspace object with root_path attribute.
        language: 'cn' or 'en'.

    Returns:
        Formatted workspace content string with header and path.
    """
    root_path = str(getattr(workspace, "root_path", "") or "")

    header = WORKSPACE_HEADER.get(language, WORKSPACE_HEADER["cn"])
    important_files = IMPORTANT_FILES.get(language, IMPORTANT_FILES["cn"])

    if language == "cn":
        path_line = f"你的工作目录是：`{root_path}`\n\n{important_files}"
    else:
        path_line = f"Your working directory is: `{root_path}`\n\n{important_files}"

    return header + path_line


async def build_workspace_section(
        sys_operation,
        workspace,
        language: str = "cn",
) -> Optional["PromptSection"]:
    """Build a PromptSection for workspace directory structure.

    Args:
        sys_operation: SysOperation instance.
        workspace: Workspace object with root_path attribute.
        language: 'cn' or 'en'.

    Returns:
        A PromptSection instance with workspace content, or None if workspace is None.
    """
    from openjiuwen.harness.prompts.builder import PromptSection

    if workspace is None:
        return None

    content = await build_workspace_content(sys_operation, workspace, language)

    return PromptSection(
        name=SectionName.WORKSPACE,
        content={language: content},
        priority=70,
    )
