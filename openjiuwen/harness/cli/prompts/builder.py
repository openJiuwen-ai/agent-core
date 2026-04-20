"""System prompt builder for the CLI agent.

Builds the system prompt using the harness ``SystemPromptBuilder``
with custom sections for environment info and OPENJIUWEN.md project
memory.

The harness builder provides built-in sections for identity, tools,
safety, etc. — we only add CLI-specific dynamic sections on top.
"""

from __future__ import annotations

import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from openjiuwen.core.single_agent.prompts.builder import (
    PromptSection,
)
from openjiuwen.harness.prompts import (
    PromptMode,
    SystemPromptBuilder,
    resolve_language,
)

# ---------------------------------------------------------------------------
# OPENJIUWEN.md memory loading (inlined from former memory.py)
# ---------------------------------------------------------------------------

MAX_MEMORY_CHARS = 40_000

ROOT_MARKERS = (
    ".git",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
)


def _find_project_root(cwd: str) -> Optional[Path]:
    """Walk up from *cwd* to find the first directory with a root marker."""
    current = Path(cwd).resolve()
    for parent in [current, *current.parents]:
        for marker in ROOT_MARKERS:
            if (parent / marker).exists():
                return parent
    return None


def _load_openjiuwen_md(cwd: str) -> Optional[str]:
    """Load and merge OPENJIUWEN.md memory files.

    Reads two layers:
        1. User-level:    ``~/.openjiuwen/OPENJIUWEN.md``
        2. Project-level: ``{project_root}/OPENJIUWEN.md``

    Args:
        cwd: Current working directory (used to locate project root).

    Returns:
        Merged memory text, or ``None`` when no memory files exist.
        Total length is capped at :data:`MAX_MEMORY_CHARS`.
    """
    parts: list[str] = []

    # User-level
    user_file = Path.home() / ".openjiuwen" / "OPENJIUWEN.md"
    if user_file.exists():
        parts.append(
            f"### User-level memory\n"
            f"{user_file.read_text(encoding='utf-8')}"
        )

    # Project-level
    project_root = _find_project_root(cwd)
    if project_root:
        proj_file = project_root / "OPENJIUWEN.md"
        if proj_file.exists():
            parts.append(
                f"### Project-level memory\n"
                f"{proj_file.read_text(encoding='utf-8')}"
            )

    if not parts:
        return None

    combined = "\n\n".join(parts)
    if len(combined) > MAX_MEMORY_CHARS:
        combined = combined[:MAX_MEMORY_CHARS] + "\n[...truncated]"
    return combined


# ---------------------------------------------------------------------------
# Environment section
# ---------------------------------------------------------------------------


def _get_git_branch(cwd: str) -> Optional[str]:
    """Return the current git branch name, or ``None`` on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return None


def _build_environment_section(
    cwd: str,
    model: str,
    provider: str,
) -> str:
    """Build environment info (regenerated each session)."""
    git_branch = _get_git_branch(cwd)
    return (
        "## Environment\n"
        f"- CWD: {cwd}\n"
        f"- Platform: {platform.system()} {platform.machine()}\n"
        f"- Python: {platform.python_version()}\n"
        f"- Model: {model} ({provider})\n"
        f"- Git branch: {git_branch or 'N/A'}\n"
        f"- Date: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')}\n"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_system_prompt(
    cwd: str,
    model: str,
    provider: str,
    language: str = "en",
) -> str:
    """Assemble the full system prompt using the harness builder.

    The harness ``SystemPromptBuilder`` provides built-in sections
    (identity, tools, safety, runtime) based on the prompt mode.
    We add CLI-specific sections on top:

    - **environment** (priority 20): CWD, platform, model, git branch
    - **project_memory** (priority 120): OPENJIUWEN.md content

    Args:
        cwd: Current working directory.
        model: Model name for self-awareness.
        provider: Provider name.
        language: Prompt language (``"en"`` or ``"cn"``).

    Returns:
        Complete system prompt string.
    """
    lang = resolve_language(language)
    builder = SystemPromptBuilder(
        language=lang,
        mode=PromptMode.FULL,
    )

    # Inject environment info
    env_text = _build_environment_section(cwd, model, provider)
    builder.add_section(
        PromptSection(
            name="environment",
            content={lang: env_text},
            priority=20,
        )
    )

    # Inject OPENJIUWEN.md project memory
    memory = _load_openjiuwen_md(cwd)
    if memory:
        builder.add_section(
            PromptSection(
                name="project_memory",
                content={lang: f"## Project Memory\n{memory}"},
                priority=120,
            )
        )

    return builder.build()
