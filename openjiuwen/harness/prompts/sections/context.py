# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Context prompt section for DeepAgent - reads stable workspace config files."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional
import re
import threading

from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.harness.prompts.workspace_content.workspace_header import (
    CONTEXT_HEADER,
    CONTEXT_FILE_TITLES,
    CONTEXT_FILES,
)
from openjiuwen.harness.workspace.workspace import WorkspaceNode
from openjiuwen.harness.prompts.sections import SectionName

if TYPE_CHECKING:
    from openjiuwen.harness.prompts.builder import PromptSection

CONTEXT_SECTION_BY_FILE = {
    "AGENT.md": "context.agent",
    "SOUL.md": "context.soul",
    "HEARTBEAT.md": "context.heartbeat",
    "USER.md": "context.user",
    "IDENTITY.md": "context.identity",
}

_IDENTITY_FILLED_NAME_RE = re.compile(
    r"^\s*[-*]?\s*(?:\*\*)?(?:名字|Name)[：:](?:\*\*)?\s*(?P<name>\S.*?)\s*$",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Template detection
# ---------------------------------------------------------------------------

# Marker phrases found only in unfilled workspace templates.
_TEMPLATE_MARKERS = (
    "此处应保存的内容",
    "What should be saved here",
    "在你们的第一次对话中填写",
    "Fill this in during your first",
    "在这里添加你需要",
    "Add your periodic tasks here",
)


def _is_unfilled_template(content: str, max_template_len: int = 500) -> bool:
    """Return True if *content* looks like an unfilled workspace template.

    Rules (applied in order):
    1. Files longer than *max_template_len* are never considered templates.
    2. After stripping HTML comments, if nothing remains → template.
    3. If any ``_TEMPLATE_MARKERS`` phrase appears in the raw content → template.
    4. After also stripping Markdown headings, if nothing remains → template.
    """
    if len(content) > max_template_len:
        return False
    text = re.sub(r'<!--.*?-->', '', content, flags=re.DOTALL).strip()
    if not text:
        return True
    for marker in _TEMPLATE_MARKERS:
        if marker in content:
            return True
    no_headings = re.sub(r'^#{1,6}\s+.*$', '', text, flags=re.MULTILINE).strip()
    return not no_headings


def _clean_agent_name(raw_name: str) -> str:
    name = raw_name.strip()
    name = re.sub(r"\s*[（(].*?(权威|见\s*IDENTITY\.md).*?[）)]\s*$", "", name).strip()
    return name.strip("`\"'“”‘’。；;,，")


def _identity_has_filled_name(content: str) -> bool:
    for match in _IDENTITY_FILLED_NAME_RE.finditer(content):
        name = _clean_agent_name(match.group("name"))
        if name and not name.startswith("_("):
            return True
    return False


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

DAILY_MEMORY_GUIDANCE = {
    "cn": (
        "每日记忆不会自动注入系统提示词。涉及今天、昨天、之前、继续、上次、记忆、偏好、历史"
        "等上下文时，先调用 `read_memory` 读取 `memory/daily_memory/YYYY-MM-DD.md`，"
        "或使用 `memory_search` 检索相关记忆。\n\n"
    ),
    "en": (
        "Daily memory is not automatically injected into the system prompt. When context involves "
        "today, yesterday, earlier, continue, last time, memory, preferences, history, or similar "
        "historical context, first call `read_memory` to read "
        "`memory/daily_memory/YYYY-MM-DD.md`, or use `memory_search` to retrieve relevant memories.\n\n"
    ),
}


# Resolved context-file content per path, keyed on the file's identity. These
# files (AGENT.md, SOUL.md, USER.md, ...) are re-read on every model call to be
# folded into the system prompt, but they hold an agent's identity and change
# about as often as its configuration does.
#
# Only LOCAL sys operations are cached. Under a sandbox the path names a file
# inside the sandbox, so a same-named file on the host would stamp content that
# was never read — the check below keeps that case on the uncached path rather
# than serving a wrong answer.
_CONTEXT_FILE_CACHE: dict[str, tuple[tuple[int, int], str | None]] = {}
_CONTEXT_FILE_CACHE_LOCK = threading.Lock()


def _local_context_file_stamp(sys_operation, full_path: Path) -> tuple[int, int] | None:
    """Return the identity a cached read of this context file is keyed on.

    Args:
        sys_operation: SysOperation the read will go through.
        full_path: Absolute path of the context file.

    Returns:
        ``(mtime_ns, size)`` for a local, stat-able file; None when the read is
        not local or the file cannot be stat'd, meaning it must not be cached.
    """
    if getattr(sys_operation, "mode", None) != OperationMode.LOCAL:
        return None
    try:
        stat_result = full_path.stat()
    except OSError:
        return None
    return stat_result.st_mtime_ns, stat_result.st_size


async def _read_context_file(
        sys_operation,
        workspace,
        file_key: str,
) -> str | None:
    """Read a single context file using sys_operation.

    The resolved result is cached against the file's mtime and size, so an
    unchanged file is not re-read on every model call. Both outcomes are cached:
    an unfilled template resolves to None just as durably as real content does.

    Args:
        sys_operation: SysOperation instance.
        workspace: Workspace instance.
        file_key: File identifier (e.g. "AGENT.md").

    Returns:
        File content string, or None if file doesn't exist or read fails.
    """
    if sys_operation is None:
        return None

    full_path: Path | None
    if file_key == WorkspaceNode.MEMORY_MD.value:
        memory_dir = workspace.get_node_path(WorkspaceNode.MEMORY)
        full_path = memory_dir / WorkspaceNode.MEMORY_MD.value if memory_dir else None
    else:
        full_path = workspace.get_node_path(file_key)

    if full_path is None:
        return None

    stamp = _local_context_file_stamp(sys_operation, full_path)
    cache_key = str(full_path)
    if stamp is not None:
        with _CONTEXT_FILE_CACHE_LOCK:
            cached = _CONTEXT_FILE_CACHE.get(cache_key)
        if cached is not None and cached[0] == stamp:
            return cached[1]

    resolved: str | None = None
    result = await sys_operation.fs().read_file(str(full_path))
    if result.code == 0 and result.data:
        content = result.data.content
        if file_key == WorkspaceNode.IDENTITY_MD.value and _identity_has_filled_name(content):
            resolved = content
        elif content and not _is_unfilled_template(content):
            resolved = content

    if stamp is not None:
        with _CONTEXT_FILE_CACHE_LOCK:
            _CONTEXT_FILE_CACHE[cache_key] = (stamp, resolved)
    return resolved


async def _build_context_content(
        sys_operation,
        workspace,
        language: str = "cn",
        extra_content: Optional[str] = None,
        timezone: Optional[str] = None,
        *,
        include_daily_memory: bool = True,
) -> str:
    """Build the complete context file contents section.

    Args:
        sys_operation: SysOperation instance.
        workspace: Workspace instance.
        language: 'cn' or 'en'.
        extra_content: Optional content to append at the end (e.g. tools list).
        timezone: Kept for API compatibility. Daily memory is not read here.
        include_daily_memory: Whether to include the stable daily-memory guidance.

    Returns:
        Formatted context content string.
    """
    header = CONTEXT_HEADER.get(language, CONTEXT_HEADER["cn"])
    titles = CONTEXT_FILE_TITLES.get(language, CONTEXT_FILE_TITLES["cn"])

    parts = [header]

    for file_key in CONTEXT_FILES:
        content = await _read_context_file(sys_operation, workspace, file_key)
        if content is None:
            continue
        title = titles.get(file_key, f"## {file_key}")
        parts.append(f"{title}\n\n{content}\n\n")

    if language == "cn":
        parts.append("[以下文件仅在有实际内容时注入，空文件跳过]\n\n")
    else:
        parts.append(
            "[The following files are injected only when they contain real content; "
            "empty files are skipped]\n\n"
        )

    if include_daily_memory:
        parts.append(DAILY_MEMORY_GUIDANCE.get(language, DAILY_MEMORY_GUIDANCE["cn"]))

    if extra_content:
        parts.append(extra_content)

    return "".join(parts)


async def build_context_section(
        sys_operation,
        workspace,
        language: str = "cn",
        tools_content: Optional[str] = None,
        timezone: Optional[str] = None,
        *,
        include_daily_memory: bool = True,
) -> Optional["PromptSection"]:
    """Build a PromptSection for context files.

    Args:
        sys_operation: SysOperation instance.
        workspace: Workspace object with root_path attribute.
        language: 'cn' or 'en'.
        tools_content: Optional pre-rendered tools content string for the given language.
        timezone: Kept for API compatibility. Daily memory is not read here.
        include_daily_memory: Whether to include the stable daily-memory guidance.

    Returns:
        A PromptSection instance with context content, or None if workspace is None.
    """
    from openjiuwen.harness.prompts.builder import PromptSection

    if workspace is None:
        return None

    content = await _build_context_content(
        sys_operation,
        workspace,
        language,
        extra_content=tools_content,
        timezone=timezone,
        include_daily_memory=include_daily_memory,
    )

    return PromptSection(
        name=SectionName.CONTEXT,
        content={language: content},
        priority=80,
        category="memory",
    )


async def build_context_file_sections(
        sys_operation,
        workspace,
        language: str = "cn",
) -> dict[str, "PromptSection"]:
    """Build one PromptSection per configured context file.

    Each section is named by ``CONTEXT_SECTION_BY_FILE`` so stable files can
    live in the system prompt while dynamic files can remain attachments.
    """
    from openjiuwen.harness.prompts.builder import PromptSection

    if workspace is None:
        return {}

    titles = CONTEXT_FILE_TITLES.get(language, CONTEXT_FILE_TITLES["cn"])
    sections: dict[str, PromptSection] = {}
    for file_key in CONTEXT_FILES:
        section_name = CONTEXT_SECTION_BY_FILE.get(file_key)
        if not section_name:
            continue
        content = await _read_context_file(sys_operation, workspace, file_key)
        if content is None:
            continue
        title = titles.get(file_key, f"## {file_key}")
        sections[section_name] = PromptSection(
            name=section_name,
            content={language: f"{title}\n\n{content}\n"},
            priority=80,
            category="memory",
        )
    return sections


def build_tools_content(
        ability_manager,
        language: str = "cn",
) -> Optional[str]:
    """Build general tool-usage rules when at least one tool is available."""
    if ability_manager is None:
        return None

    has_tools = any(
        isinstance(ability, ToolCard) and bool(ability.name)
        for ability in ability_manager.list()
    )
    if not has_tools:
        return None

    prompts = {
        "cn": """# 工具使用规则

- 只调用当前请求中实际可用的工具。
- 相同工具和相同参数已有结果时，不要重复调用。
- 上一次结果为空或没有新增信息时，调整参数、改用其他工具或说明结果不足。
- 文件搜索、读取、编辑和写入优先使用专用工具，不要用 Shell 重复实现。
- Shell 命令只有存在依赖关系时才串联；长时间运行的命令使用后台执行，不要用 `sleep` 轮询。
- 工具执行结果是事实来源，不要虚构或改写为尚未发生的结果。
""",
        "en": """# Tool Usage Rules

- Call only tools that are actually available for the current request.
- Do not repeat a tool call when the same tool and arguments already produced a result.
- If the previous result was empty or added no new information, adjust the arguments, use another tool, or explain that the result is insufficient.
- Prefer dedicated tools for searching, reading, editing, and writing files; do not reimplement those operations with Shell.
- Chain Shell commands only when they depend on one another; run long-lived commands in the background instead of polling with `sleep`.
- Tool results are the source of truth; do not fabricate or present operations that have not occurred as completed.
""",
    }
    return prompts.get(language, prompts["cn"])


def build_tools_section(
        ability_manager,
        language: str = "cn",
) -> Optional["PromptSection"]:
    """Build an independent PromptSection for tools (P:30).

    Args:
        ability_manager: AbilityManager instance (or None).
        language: 'cn' or 'en'.

    Returns:
        A PromptSection instance, or None if no tools available.
    """
    from openjiuwen.harness.prompts.builder import PromptSection

    content = build_tools_content(ability_manager, language)
    if not content:
        return None

    return PromptSection(
        name=SectionName.TOOLS,
        content={language: content},
        priority=30,
        # This section contains tool-use rules.  The actual tool schemas are
        # counted separately from ContextWindow.tools.
        category="system_prompt",
    )
