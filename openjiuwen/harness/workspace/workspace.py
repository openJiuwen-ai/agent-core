from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Union

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger


# =============================================================================
# Default Content Loading
# =============================================================================


def _get_content_base_dir() -> Path:
    """Get the base directory for workspace content files."""
    return Path(__file__).parent.parent / "prompts" / "workspace_content"


def _load_default_content(language: str, file_path: str) -> str:
    """Load default content from workspace_content directory.

    Args:
        language: 'cn' for Chinese, 'en' for English.
        file_path: Relative path to the content file (e.g., "AGENT.md" or "memory/MEMORY.md").

    Returns:
        The file content as string, or empty string if file doesn't exist.
    """
    content_dir = _get_content_base_dir()
    full_path = content_dir / language / file_path
    if full_path.exists():
        return full_path.read_text(encoding="utf-8")
    return ""


# =============================================================================
# Type Definitions
# =============================================================================


DirectoryNode = Dict[str, Any]


class WorkspaceNode(Enum):
    """Common workspace directory node names.

    Provides type-safe access to standard workspace directories.
    """
    AGENT_MD = "AGENT.md"
    SOUL_MD = "SOUL.md"
    HEARTBEAT_MD = "HEARTBEAT.md"
    IDENTITY_MD = "IDENTITY.md"
    USER_MD = "USER.md"
    MEMORY = "memory"
    CODING_MEMORY = "coding_memory"
    TODO = "todo"
    MESSAGES = "messages"
    SKILLS = "skills"
    AGENTS = "agents"
    MEMORY_MD = "MEMORY.md"
    DAILY_MEMORY = "daily_memory"
    TEAM_LINKS = ".team"
    WORKTREE_LINKS = ".worktree"


@dataclass
class Workspace:
    """Workspace schema definition for DeepAgents.

    - root_path: workspace root directory (string or Path).
    - directories: list of directory node definitions at the workspace root.
    - language: workspace language ('cn' for Chinese, 'en' for English).

    Directories behavior:
    - Required top-level directories are defined by DEFAULT_WORKSPACE_SCHEMA
      (currently: agent, user, skills, sessions).
    - If user-provided `directories` misses any of them, they will be
      auto-supplemented in __post_init__ and logged.
    - get_directory(name) falls back to DEFAULT_WORKSPACE_SCHEMA when the
      directory is not explicitly provided.
    """

    root_path: str | Path = "./"
    # Default value is wired through __post_init__ to avoid referencing
    # DEFAULT_WORKSPACE_SCHEMA before it is defined.
    directories: List[DirectoryNode] = field(default_factory=list)
    language: str = "cn"

    def get_directory(self, name: str | WorkspaceNode) -> str | None:
        """Return the `path` field of the directory node with the given name.

        Top-level entries in `directories` are inspected first. If not found
        but the name exists in the default schema for the current language,
        returns that default path. Otherwise returns None.

        Args:
            name: Directory name as string or WorkspaceNode enum value.
        """
        name_str = name.value if isinstance(name, WorkspaceNode) else name

        def find_in_nodes(nodes: List[DirectoryNode]) -> str | None:
            for node in nodes:
                if node.get("name") == name_str:
                    return node.get("path")
                children = node.get("children")
                if children:
                    result = find_in_nodes(children)
                    if result is not None:
                        return result
            return None

        result = find_in_nodes(self.directories)
        if result is not None:
            return result
        return find_in_nodes(self._get_default_schema())

    def get_node_path(self, node: str | WorkspaceNode) -> Path | None:
        """Return the full absolute filesystem path for a top-level workspace node.

        This method only looks at top-level nodes (direct children of directories).
        Nested nodes (children of top-level nodes) are not supported.

        Args:
            node: Node name as string (e.g., "memory", "AGENT.md") or WorkspaceNode enum.

        Returns:
            Path object with the full absolute path to the node.
            Returns None if the node is not found at the top level.
        """
        name_str = node.value if isinstance(node, WorkspaceNode) else node

        for node_def in self.directories:
            if node_def.get("name") == name_str:
                relative_path = node_def.get("path", name_str)
                return Path(self.root_path) / relative_path

        return None

    def set_directory(
        self, nodes: Union[DirectoryNode, List[DirectoryNode]]
    ) -> None:
        """Add or update top-level directory node(s) by name.

        Accepts a single directory node (dict) or a list of nodes. Each node
        is validated; if a node with the same `name` exists it is replaced,
        otherwise the node is appended.
        """
        if isinstance(nodes, dict):
            nodes = [nodes]
        elif isinstance(nodes, list):
            nodes = nodes
        else:
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg="set_directory expects a directory node (dict) or list of nodes.",
            )
        for node in nodes:
            _validate_directory_node(node)
            name = node.get("name")
            for i, existing in enumerate(self.directories):
                if existing.get("name") == name:
                    self.directories[i] = dict(node)
                    break
            else:
                self.directories.append(dict(node))

    # ── Link management (.team/ and .worktree/ symlinks) ──────

    TEAM_LINKS_DIR = ".team"
    WORKTREE_LINKS_DIR = ".worktree"

    def _ensure_link_dir(self, subdir: str) -> Path:
        """Create and return the link directory under workspace root.

        Args:
            subdir: Subdirectory name (".team" or ".worktree").

        Returns:
            Absolute path to the link directory.
        """
        link_dir = Path(self.root_path) / subdir
        link_dir.mkdir(parents=True, exist_ok=True)
        return link_dir

    def link_team(self, team_id: str, target_path: str) -> Path:
        """Create .team/{team_id} symlink pointing to a team workspace.

        Args:
            team_id: Team identifier used as the symlink directory name.
            target_path: Absolute path to the team workspace directory.

        Returns:
            Path to the created symlink.
        """
        link_dir = self._ensure_link_dir(self.TEAM_LINKS_DIR)
        link = link_dir / team_id
        if not link.exists():
            os.symlink(target_path, str(link), target_is_directory=True)
        return link

    def unlink_team(self, team_id: str) -> bool:
        """Remove .team/{team_id} symlink.

        Args:
            team_id: Team identifier of the symlink to remove.

        Returns:
            True if the symlink was removed, False if it didn't exist.
        """
        link = Path(self.root_path) / self.TEAM_LINKS_DIR / team_id
        if link.is_symlink():
            link.unlink()
            return True
        return False

    def link_worktree(self, slug: str, target_path: str) -> Path:
        """Create .worktree/{slug} symlink pointing to a git worktree.

        Args:
            slug: Worktree slug used as the symlink directory name.
            target_path: Absolute path to the git worktree directory.

        Returns:
            Path to the created symlink.
        """
        link_dir = self._ensure_link_dir(self.WORKTREE_LINKS_DIR)
        link = link_dir / slug
        if not link.exists():
            os.symlink(target_path, str(link), target_is_directory=True)
        return link

    def unlink_worktree(self, slug: str) -> bool:
        """Remove .worktree/{slug} symlink.

        Args:
            slug: Worktree slug of the symlink to remove.

        Returns:
            True if the symlink was removed, False if it didn't exist.
        """
        link = Path(self.root_path) / self.WORKTREE_LINKS_DIR / slug
        if link.is_symlink():
            link.unlink()
            return True
        return False

    def list_team_links(self) -> list[tuple[str, str]]:
        """List all .team/ symlinks.

        Returns:
            List of (team_id, resolved_target_path) tuples.
        """
        return self._list_links(self.TEAM_LINKS_DIR)

    def list_worktree_links(self) -> list[tuple[str, str]]:
        """List all .worktree/ symlinks.

        Returns:
            List of (slug, resolved_target_path) tuples.
        """
        return self._list_links(self.WORKTREE_LINKS_DIR)

    def _list_links(self, subdir: str) -> list[tuple[str, str]]:
        """List all symlinks in a subdirectory.

        Args:
            subdir: Subdirectory name (".team" or ".worktree").

        Returns:
            List of (name, resolved_target_path) tuples.
        """
        link_dir = Path(self.root_path) / subdir
        if not link_dir.is_dir():
            return []
        result = []
        for entry in sorted(link_dir.iterdir()):
            if entry.is_symlink():
                target = str(entry.resolve())
                result.append((entry.name, target))
        return result

    @classmethod
    def get_default_directory(cls, language: str = "cn") -> List[DirectoryNode]:
        """Return a deep copy of the default directory schema.

        Args:
            language: 'cn' for Chinese, 'en' for English. Defaults to 'cn'.

        Returns:
            A deep copy of the workspace schema for the specified language.
        """
        return get_workspace_schema(language)

    def _get_default_schema(self) -> List[DirectoryNode]:
        """Get the default schema based on the workspace language."""
        return get_workspace_schema(self.language)

    def __post_init__(self) -> None:
        # Fill in default schema when no directories are explicitly provided.
        if not self.directories:
            # Use a deepcopy so that callers can mutate their instance safely.
            self.directories = deepcopy(self._get_default_schema())

        if not isinstance(self.directories, list):
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg="`directories` must be a list of directory definitions.",
            )
        for node in self.directories:
            _validate_directory_node(node)

        # Supplement any default directories missing from user-provided list.
        default_schema = self._get_default_schema()
        existing_names = {node.get("name") for node in self.directories}
        for default_node in default_schema:
            name = default_node.get("name")
            if name and name not in existing_names:
                self.directories.append(deepcopy(default_node))
                existing_names.add(name)
                logger.info(
                    "Workspace: supplemented missing default directory %r (path=%r).",
                    name,
                    default_node.get("path"),
                )


DEFAULT_WORKSPACE_SCHEMA: List[DirectoryNode] = [
    {
        "name": "AGENT.md",
        "description": "基础配置和能力",
        "path": "AGENT.md",
        "children": [],
        "is_file": True,
        "default_content": _load_default_content("cn", "AGENT.md"),
    },
    {
        "name": "SOUL.md",
        "description": "人格、性格和价值观",
        "path": "SOUL.md",
        "children": [],
        "is_file": True,
        "default_content": _load_default_content("cn", "SOUL.md"),
    },
    {
        "name": "HEARTBEAT.md",
        "description": "心跳日志和状态记录",
        "path": "HEARTBEAT.md",
        "children": [],
        "is_file": True,
        "default_content": _load_default_content("cn", "HEARTBEAT.md"),
    },
    {
        "name": "IDENTITY.md",
        "description": "身份凭证和权限",
        "path": "IDENTITY.md",
        "children": [],
        "is_file": True,
        "default_content": _load_default_content("cn", "IDENTITY.md"),
    },
    {
        "name": "USER.md",
        "description": "用户数据目录",
        "path": "USER.md",
        "children": [],
        "is_file": True,
        "default_content": "",
    },
    {
        "name": "memory",
        "description": "记忆核心模块",
        "path": "memory",
        "children": [
            {
                "name": "MEMORY.md",
                "description": "长期记忆索引和摘要",
                "path": "MEMORY.md",
                "children": [],
                "is_file": True,
                "default_content": _load_default_content("cn", "memory/MEMORY.md"),
            },
            {
                "name": "daily_memory",
                "description": "每日结构化记忆",
                "path": "daily_memory",
                "children": [],
            },
        ],
    },
    {
        "name": "coding_memory",
        "description": "Coding Agent 记忆模块",
        "path": "coding_memory",
        "children": [
            {
                "name": "MEMORY.md",
                "description": "Coding 记忆索引",
                "path": "MEMORY.md",
                "children": [],
                "is_file": True,
                "default_content": "",
            },
        ],
    },
    {
        "name": "todo",
        "description": "待办事项目录",
        "path": "todo",
        "children": [],
    },
    {
        "name": "messages",
        "description": "消息历史目录",
        "path": "messages",
        "children": [],
    },
    {
        "name": "skills",
        "description": "技能库目录",
        "path": "skills",
        "children": [],
    },
    {
        "name": "agents",
        "description": "子智能体嵌套目录",
        "path": "agents",
        "children": [],
    },
    {
        "name": "context",
        "description": "上下文offload以及session memory目录",
        "path": "context",
        "children": [
            {
                "name": "session_memory.md",
                "description": "session memory模版",
                "path": "session_memory.md",
                "children": [],
                "is_file": True,
                "default_content": _load_default_content("cn", "context/session_memory.md"),
            },
        ],
    },
]


def _validate_directory_node(node: DirectoryNode) -> None:
    """Validate a single directory node (dict). Raises on invalid format or fields."""
    if not isinstance(node, dict):
        raise build_error(
            StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
            error_msg="Each directory entry must be a dict.",
        )

    name = node.get("name")
    if not isinstance(name, str) or not name:
        raise build_error(
            StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
            error_msg="Directory `name` must be a non-empty string.",
        )
    if "/" in name or "\\" in name:
        raise build_error(
            StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
            error_msg=f"Directory `name` must not contain path separators: {name!r}",
        )

    path = node.get("path")
    if path is not None and not isinstance(path, str):
        raise build_error(
            StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
            error_msg="Directory `path` must be a string when provided.",
        )

    description = node.get("description")
    if description is not None and not isinstance(description, str):
        raise build_error(
            StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
            error_msg="Directory `description` must be a string when provided.",
        )

    is_file = node.get("is_file")
    if is_file is not None and not isinstance(is_file, bool):
        raise build_error(
            StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
            error_msg="`is_file` must be a bool when provided.",
        )

    default_content = node.get("default_content")
    if default_content is not None and not isinstance(default_content, str):
        raise build_error(
            StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
            error_msg="`default_content` must be a string when provided.",
        )

    children = node.get("children")
    if children is not None and not isinstance(children, list):
        raise build_error(
            StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
            error_msg="Directory `children` must be a list when provided.",
        )

    if isinstance(children, list):
        for child in children:
            _validate_directory_node(child)


# =============================================================================
# English Workspace Schema
# =============================================================================
DEFAULT_WORKSPACE_SCHEMA_EN: List[DirectoryNode] = [
    {
        "name": "AGENT.md",
        "description": "Basic agent configuration and capabilities",
        "path": "AGENT.md",
        "children": [],
        "is_file": True,
        "default_content": _load_default_content("en", "AGENT.md"),
    },
    {
        "name": "SOUL.md",
        "description": "Agent personality, character, values, and behavioral guidelines",
        "path": "SOUL.md",
        "children": [],
        "is_file": True,
        "default_content": _load_default_content("en", "SOUL.md"),
    },
    {
        "name": "HEARTBEAT.md",
        "description": "Heartbeat log / status recording",
        "path": "HEARTBEAT.md",
        "children": [],
        "is_file": True,
        "default_content": _load_default_content("en", "HEARTBEAT.md"),
    },
    {
        "name": "IDENTITY.md",
        "description": "Identity credentials, unique identifier, and permission information",
        "path": "IDENTITY.md",
        "children": [],
        "is_file": True,
        "default_content": _load_default_content("en", "IDENTITY.md"),
    },
    {
        "name": "USER.md",
        "description": "User data directory",
        "path": "USER.md",
        "children": [],
        "is_file": True,
        "default_content":"",
    },
    {
        "name": "memory",
        "description": "Memory core module",
        "path": "memory",
        "children": [
            {
                "name": "MEMORY.md",
                "description": "Memory overview, index, and important memory summaries",
                "path": "MEMORY.md",
                "children": [],
                "is_file": True,
                "default_content": _load_default_content("en", "memory/MEMORY.md"),
            },
            {
                "name": "daily_memory",
                "description": "Daily structured memory",
                "path": "daily_memory",
                "children": [],
            },
        ],
    },
    {
        "name": "todo",
        "description": "Todo items",
        "path": "todo",
        "children": [],
    },
    {
        "name": "messages",
        "description": "Message history module",
        "path": "messages",
        "children": [],
    },
    {
        "name": "skills",
        "description": "Skills library directory",
        "path": "skills",
        "children": [],
    },
    {
        "name": "agents",
        "description": "Sub-agent nesting directory",
        "path": "agents",
        "children": [],
    },
    {
        "name": "context",
        "description": "context offload and session memory file",
        "path": "context",
        "children": [
            {
                "name": "session_memory.md",
                "description": "session memory模版",
                "path": "session_memory.md",
                "children": [],
                "is_file": True,
                "default_content": _load_default_content("en", "context/session_memory.md"),
            },
        ],
    },
]


def get_workspace_schema(language: str = "cn") -> List[DirectoryNode]:
    """Get the workspace schema based on language.

    Args:
        language: 'cn' for Chinese, 'en' for English. Defaults to 'cn'.

    Returns:
        A deep copy of the workspace schema for the specified language.
    """
    if language == "en":
        return deepcopy(DEFAULT_WORKSPACE_SCHEMA_EN)
    return deepcopy(DEFAULT_WORKSPACE_SCHEMA)


__all__ = [
    "Workspace",
    "WorkspaceNode",
    "get_workspace_schema",
]
