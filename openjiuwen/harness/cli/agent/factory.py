"""Agent factory and backend abstraction.

Provides:
- :func:`create_agent` — build a DeepAgent with rails
- :class:`LocalBackend` — direct SDK Runner backend (MVP)
- :func:`create_backend` — backend factory
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, AsyncIterator, List, Optional, Protocol
from uuid import uuid4

from openjiuwen.core.foundation.llm import init_model
from openjiuwen.core.runner import Runner
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
from openjiuwen.harness.tools import (
    create_web_tools,
)
from openjiuwen.harness.workspace.workspace import Workspace

from openjiuwen.harness.cli.agent.config import CLIConfig
from openjiuwen.harness.cli.prompts import build_system_prompt
from openjiuwen.harness.cli.rails import TokenTrackingRail
from openjiuwen.harness.cli.rails.tool_tracker import (
    ToolTrackingRail,
)
from openjiuwen.harness.rails import (
    AskUserRail,
    ConfirmInterruptRail,
    SkillUseRail,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default skill directories (priority high → low)
# ---------------------------------------------------------------------------

#: Skill directories scanned by the CLI agent on startup.
#: Listed in descending priority; when two directories contain a skill
#: with the same name, the first (higher-priority) one wins.
_DEFAULT_SKILL_DIRS: list[str] = [
    "~/.openjiuwen/workspace/skills",
    "~/.claude/skills",
    "~/.codex/skills",
    "~/.jiuwenclaw/workspace/skills",
]


def _get_cli_content_base_dir() -> Path:
    """Get the base directory for CLI-specific workspace content files."""
    return Path(__file__).parent.parent / "prompts" / "workspace_content"


def _load_cli_content(language: str, file_path: str) -> str:
    """Load CLI-specific workspace content.

    Falls back to empty string if the file does not exist.

    Args:
        language: ``'cn'`` or ``'en'``.
        file_path: Relative path inside the content directory
            (e.g. ``"IDENTITY.md"``).

    Returns:
        File content as a string, or ``""`` when missing.
    """
    full_path = _get_cli_content_base_dir() / language / file_path
    if full_path.exists():
        return full_path.read_text(encoding="utf-8")
    return ""


def _build_cli_workspace(cfg: CLIConfig, language: str) -> Workspace:
    """Build a :class:`Workspace` with CLI-specific content overrides.

    Starts from the default schema for *language*, then replaces
    ``IDENTITY.md`` with a pre-defined coding-assistant identity
    (instead of the generic "fill this in yourself" template).

    Args:
        cfg: CLI configuration (provides ``workspace`` root path).
        language: Resolved prompt language (``"en"`` or ``"cn"``).

    Returns:
        A fully-initialised :class:`Workspace` ready for
        ``create_deep_agent``.
    """
    workspace = Workspace(root_path=cfg.workspace, language=language)

    cli_identity = _load_cli_content(language, "IDENTITY.md")
    if cli_identity:
        workspace.set_directory({
            "name": "IDENTITY.md",
            "description": "Identity credentials and permissions",
            "path": "IDENTITY.md",
            "is_file": True,
            "children": [],
            "default_content": cli_identity,
        })

    return workspace


def _default_skill_dirs() -> list[str]:
    """Return default skill root directories.

    Each entry is a ``~``-prefixed path that
    :class:`SkillUseRail` will expand and scan.
    Non-existent directories are silently skipped
    by the rail at scan time.
    """
    return list(_DEFAULT_SKILL_DIRS)


# ---------------------------------------------------------------------------
# MemoryRail and Subagent helpers
# ---------------------------------------------------------------------------


def _build_memory_rail(cfg: CLIConfig) -> Any:
    """Build a :class:`MemoryRail` if embedding config is available.

    Reads embedding settings from environment variables:

    - ``EMBEDDING_MODEL_NAME`` — embedding model name
      (default ``"text-embedding-3-small"``).
    - ``EMBEDDING_BASE_URL`` — embedding API base URL
      (falls back to ``cfg.api_base``).
    - ``EMBEDDING_API_KEY`` — embedding API key
      (falls back to ``cfg.api_key``).

    Returns:
        :class:`MemoryRail` instance, or ``None`` when the
        required dependencies are not importable.
    """
    try:
        from openjiuwen.core.foundation.store.base_embedding import (
            EmbeddingConfig,
        )
        from openjiuwen.core.memory.lite.embeddings import (
            resolve_embedding_config_from_env,
        )
        from openjiuwen.harness.rails.memory.memory_rail import MemoryRail

        embedding_config = resolve_embedding_config_from_env(
            model_name="text-embedding-3-small",
            fallback_base_url=cfg.api_base,
            fallback_api_key=cfg.api_key,
        )
        if embedding_config is None:
            return None
        return MemoryRail(embedding_config=embedding_config)
    except Exception:  # noqa: BLE001
        logger.debug(
            "MemoryRail not available", exc_info=True
        )
        return None


def _build_subagents(
    model: Any,
) -> list[Any]:
    """Build subagent configs for the CLI agent.

    Creates configs for:

    - **code_agent** — software engineering / coding tasks
    - **research_agent** — research / investigation tasks
    - **browser_agent** — browser automation via Playwright

    Each is registered as a :class:`SubAgentConfig` so the
    ``SessionRail`` can materialise them on demand.

    Args:
        model: Pre-constructed :class:`Model` instance
            (shared with the parent agent).

    Returns:
        List of :class:`SubAgentConfig` instances.
    """
    from openjiuwen.core.single_agent.schema.agent_card import (
        AgentCard,
    )
    from openjiuwen.harness.rails.sys_operation_rail import (
        SysOperationRail as _SubAgentFSRail,
    )
    from openjiuwen.harness.schema.config import SubAgentConfig
    from openjiuwen.harness.subagents.code_agent import (
        DEFAULT_CODE_AGENT_DESCRIPTION,
        DEFAULT_CODE_AGENT_SYSTEM_PROMPT,
    )
    from openjiuwen.harness.subagents.research_agent import (
        DEFAULT_RESEARCH_AGENT_DESCRIPTION,
        DEFAULT_RESEARCH_AGENT_SYSTEM_PROMPT,
    )

    subagents: list[Any] = []

    # --- code_agent ---
    code_spec = SubAgentConfig(
        agent_card=AgentCard(
            name="code_agent",
            description=DEFAULT_CODE_AGENT_DESCRIPTION.get(
                "en",
                DEFAULT_CODE_AGENT_DESCRIPTION["cn"],
            ),
        ),
        system_prompt=DEFAULT_CODE_AGENT_SYSTEM_PROMPT.get(
            "en",
            DEFAULT_CODE_AGENT_SYSTEM_PROMPT["cn"],
        ),
        model=model,
        rails=[_SubAgentFSRail()],
        language="en",
    )
    subagents.append(code_spec)

    # --- research_agent ---
    research_spec = SubAgentConfig(
        agent_card=AgentCard(
            name="research_agent",
            description=DEFAULT_RESEARCH_AGENT_DESCRIPTION.get(
                "en",
                DEFAULT_RESEARCH_AGENT_DESCRIPTION["cn"],
            ),
        ),
        system_prompt=DEFAULT_RESEARCH_AGENT_SYSTEM_PROMPT.get(
            "en",
            DEFAULT_RESEARCH_AGENT_SYSTEM_PROMPT["cn"],
        ),
        model=model,
        rails=[_SubAgentFSRail()],
        language="en",
    )
    subagents.append(research_spec)

    # --- browser_agent ---
    try:
        from openjiuwen.harness.subagents.browser_agent import (
            build_browser_agent_config,
        )

        browser_spec = build_browser_agent_config(
            model, language="en"
        )
        subagents.append(browser_spec)
    except Exception:  # noqa: BLE001
        logger.debug(
            "Browser subagent not available",
            exc_info=True,
        )

    return subagents


# ---------------------------------------------------------------------------
# MCP config loading
# ---------------------------------------------------------------------------


def _filter_none_values(
    d: dict[str, Any],
) -> dict[str, Any]:
    """Return a copy of *d* with ``None`` values removed."""
    return {k: v for k, v in d.items() if v is not None}


def _load_mcp_configs() -> List[Any]:
    """Load MCP server configs from ``~/.openjiuwen/mcp.json``.

    The file format follows Claude Code conventions::

        {
            "mcpServers": {
                "server-name": {
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@mcp/server"],
                    "env": {}
                }
            }
        }

    Returns:
        List of :class:`McpServerConfig` instances, or empty.
    """
    mcp_path = Path.home() / ".openjiuwen" / "mcp.json"
    if not mcp_path.exists():
        return []

    try:
        from openjiuwen.core.foundation.tool import (
            McpServerConfig,
        )

        data = json.loads(mcp_path.read_text())
        servers = data.get("mcpServers", {})
        configs: List[Any] = []
        for name, spec in servers.items():
            transport = spec.get(
                "transport",
                spec.get("client_type", "stdio"),
            )
            config = McpServerConfig(
                server_name=name,
                server_path=spec.get(
                    "url",
                    spec.get("server_path", ""),
                ),
                client_type=transport,
                params=_filter_none_values({
                    "command": spec.get("command"),
                    "args": spec.get("args"),
                    "env": spec.get("env"),
                    "cwd": spec.get("cwd"),
                }),
                auth_headers=spec.get(
                    "auth_headers", {}
                ),
            )
            configs.append(config)
        return configs
    except Exception:  # noqa: BLE001
        logger.debug(
            "Failed to load MCP config from %s",
            mcp_path,
            exc_info=True,
        )
        return []


def _load_vision_config(cfg: CLIConfig) -> Any:
    """Load vision model config.

    If ``VISION_API_KEY`` is set, uses
    :meth:`VisionModelConfig.from_env`.  Otherwise falls back
    to the main model's ``api_key`` / ``api_base``.

    Returns:
        :class:`VisionModelConfig` or ``None``.
    """
    try:
        from openjiuwen.harness.schema.config import (
            VisionModelConfig,
        )

        if os.getenv("VISION_API_KEY"):
            return VisionModelConfig.from_env()
        # Fallback: reuse main model credentials
        return VisionModelConfig(
            api_key=cfg.api_key,
            base_url=cfg.api_base,
        )
    except Exception:  # noqa: BLE001
        logger.debug(
            "Failed to load VisionModelConfig",
            exc_info=True,
        )
        return None


def _load_audio_config(cfg: CLIConfig) -> Any:
    """Load audio model config.

    If ``AUDIO_API_KEY`` is set, uses
    :meth:`AudioModelConfig.from_env`.  Otherwise falls back
    to the main model's ``api_key`` / ``api_base``.

    Returns:
        :class:`AudioModelConfig` or ``None``.
    """
    try:
        from openjiuwen.harness.schema.config import (
            AudioModelConfig,
        )

        if os.getenv("AUDIO_API_KEY"):
            return AudioModelConfig.from_env()
        # Fallback: reuse main model credentials
        return AudioModelConfig(
            api_key=cfg.api_key,
            base_url=cfg.api_base,
        )
    except Exception:  # noqa: BLE001
        logger.debug(
            "Failed to load AudioModelConfig",
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# AgentBackend protocol
# ---------------------------------------------------------------------------


class AgentBackend(Protocol):
    """Abstraction over local / remote agent execution."""

    async def start(self) -> None:
        """Initialize the backend (start Runner / connect)."""
        ...

    async def stop(self) -> None:
        """Release resources (stop Runner / disconnect)."""
        ...

    async def run_streaming(
        self,
        query: Any,
        session_id: Optional[str] = None,
    ) -> AsyncIterator[Any]:
        """Execute *query* and stream OutputSchema chunks."""
        ...

    async def abort(self) -> None:
        """Abort the currently running query."""
        ...


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------


def create_agent(
    cfg: CLIConfig,
) -> tuple[Any, TokenTrackingRail]:
    """Create a :class:`DeepAgent` and its :class:`TokenTrackingRail`.

    Uses the harness ``SysOperationRail`` to auto-register file system
    tools (BashTool, ReadFileTool, etc.) and ``SecurityRail`` (auto-
    mounted by ``create_deep_agent``).  Web tools are added manually
    since they are not part of ``SysOperationRail``.

    Also integrates:
    - ``ToolTrackingRail`` — emits tool call/result chunks
    - ``AskUserRail`` — interrupt flow for asking the user
    - ``ConfirmInterruptRail`` — human-approval for bash/write/edit
    - ``ContextEngineeringRail`` — context window management
      (includes ``DialogueCompressor`` for ``/compact`` support)
    - ``MemoryRail`` — vector-memory tools (when embedding
      config is available)
    - ``SessionRail`` — async subagent spawning (auto-injected
      when subagents are present)
    - Subagents — ``code_agent``, ``research_agent``,
      ``browser_agent``
    - MCP servers — from ``~/.openjiuwen/mcp.json``
    - Vision/Audio — conditional on environment variables

    Args:
        cfg: CLI configuration.

    Returns:
        ``(agent, tracker)`` tuple.
    """
    model = init_model(
        provider=cfg.provider,
        model_name=cfg.model,
        api_key=cfg.api_key,
        api_base=cfg.api_base,
        max_tokens=cfg.max_tokens,
    )

    system_prompt = build_system_prompt(
        cwd=cfg.cwd,
        model=cfg.model,
        provider=cfg.provider,
    )

    tracker = TokenTrackingRail()
    tool_tracker = ToolTrackingRail()
    fs_rail = SysOperationRail()

    # Build rails list
    rails: list[Any] = [tracker, tool_tracker, fs_rail]

    # --- Interrupt rails ---
    # AskUserRail: intercepts ask_user tool calls and
    # presents questions to the user via interrupt flow.
    rails.append(AskUserRail())

    # ConfirmInterruptRail: human-approval gate for
    # potentially dangerous tool calls (bash, write, edit).
    rails.append(
        ConfirmInterruptRail(
            tool_names=["bash", "write_file", "edit_file"]
        )
    )

    # Default skill directories — SkillUseRail silently
    # skips directories that do not exist.
    skill_rail = SkillUseRail(
        skills_dir=_default_skill_dirs(),
        skill_mode="all",
        include_tools=False,
    )
    rails.append(skill_rail)

    # ContextProcessorRail — context window management
    # (includes DialogueCompressor for /compact support)
    try:
        from openjiuwen.harness.rails.context_engineer import (
            ContextProcessorRail,
            ContextAssembleRail
        )

        rails.append(ContextProcessorRail(preset=True))
        rails.append(ContextAssembleRail())
    except ImportError:
        logger.debug(
            "ContextProcessorRail or ContextAssembleRail not available"
        )

    # --- MemoryRail ---
    # Integrates vector-memory tools when embedding config
    # is available via environment variables.
    _memory_rail = _build_memory_rail(cfg)
    if _memory_rail is not None:
        rails.append(_memory_rail)

    # --- SessionRail ---
    # Enables spawning async subagent tasks (browser,
    # code, research) in the background.  Automatically
    # mounted by ``create_deep_agent`` when subagents are
    # present and ``enable_async_subagent=True``.
    # We do NOT add SessionRail manually here — it is
    # auto-injected by the factory.

    # Web tools are not part of SysOperationRail
    web_tools = create_web_tools(language="en")

    # MCP servers from ~/.openjiuwen/mcp.json
    mcp_configs = _load_mcp_configs()

    # Multimodal configs (fallback to main model credentials)
    vision_config = _load_vision_config(cfg)
    audio_config = _load_audio_config(cfg)

    # --- Subagents ---
    subagents = _build_subagents(model)

    # Build extra kwargs for create_deep_agent
    extra_kwargs: dict[str, Any] = {}
    if mcp_configs:
        extra_kwargs["mcps"] = mcp_configs
    if vision_config is not None:
        extra_kwargs["vision_model_config"] = (
            vision_config
        )
    if audio_config is not None:
        extra_kwargs["audio_model_config"] = audio_config

    # Build CLI-specific workspace with overridden IDENTITY.md
    workspace = _build_cli_workspace(cfg, language="en")

    agent = create_deep_agent(
        model,
        system_prompt=system_prompt,
        tools=web_tools,
        subagents=subagents or None,
        rails=rails,
        enable_task_loop=True,
        enable_task_planning=True,
        enable_async_subagent=bool(subagents),
        max_iterations=cfg.max_iterations,
        workspace=workspace,
        restrict_to_work_dir=False,
        language="en",
        **extra_kwargs,
    )

    # Override workspace root_path to bypass the factory's
    # automatic ``{agent_id}_workspace`` suffix.  This is safe
    # because ``restrict_to_work_dir=False`` makes SysOperation
    # scope independent of workspace path, and rails read from
    # ``deep_config.workspace`` lazily at first ``invoke()``.
    if agent.deep_config.workspace is not None:
        agent.deep_config.workspace.root_path = cfg.workspace

    return agent, tracker


# ---------------------------------------------------------------------------
# LocalBackend (MVP)
# ---------------------------------------------------------------------------


class LocalBackend:
    """Backend that calls the SDK Runner directly."""

    def __init__(self, cfg: CLIConfig) -> None:
        self.cfg = cfg
        self.agent: Any = None
        self.tracker: Optional[TokenTrackingRail] = None
        self._session_id: str = f"cli-{uuid4().hex[:8]}"

    async def start(self) -> None:
        """Create the agent and start the Runner."""
        self.agent, self.tracker = create_agent(self.cfg)
        await Runner.start()

    async def stop(self) -> None:
        """Stop the Runner."""
        await Runner.stop()

    async def run_streaming(
        self,
        query: Any,
        session_id: Optional[str] = None,
    ) -> AsyncIterator[Any]:
        """Stream OutputSchema chunks for *query*.

        *query* may be a plain string (normal user turn) or
        an ``InteractiveInput`` (interrupt resume).
        """
        sid = session_id or self._session_id
        stream = Runner.run_agent_streaming(
            self.agent,
            {"query": query},
            session=sid,
        )
        async for chunk in stream:
            yield chunk

    async def abort(self) -> None:
        """Request the agent to abort the current task loop."""
        if self.agent is not None:
            try:
                await self.agent.abort()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Backend factory
# ---------------------------------------------------------------------------


def create_backend(cfg: CLIConfig) -> LocalBackend:
    """Select and instantiate the appropriate backend.

    Args:
        cfg: CLI configuration.

    Returns:
        A :class:`LocalBackend` (MVP).

    Raises:
        NotImplementedError: If ``cfg.server_url`` is set
            (remote mode not yet supported).
    """
    if cfg.server_url:
        raise NotImplementedError(
            "RemoteBackend is not supported in the MVP. "
            "Remove OPENJIUWEN_SERVER_URL to use local mode."
        )
    return LocalBackend(cfg)
