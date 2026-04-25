# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""LspRail — initializes LSP subsystem and registers LspTool on DeepAgent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openjiuwen.core.common.logging import logger
from openjiuwen.harness.rails.base import DeepAgentRail

if TYPE_CHECKING:
    from openjiuwen.core.foundation.tool.base import Tool
    from openjiuwen.harness.deep_agent import DeepAgent
    from openjiuwen.harness.lsp import InitializeOptions


class LspRail(DeepAgentRail):
    """Rail that initializes LSP subsystem and registers LspTool on DeepAgent.

    This rail provides AI agents with code navigation capabilities through
    the Language Server Protocol. It:

    1. Initializes the LSP subsystem (LSPServerManager) on ``init()``.
       Servers are started lazily on first LSP request.
    2. Registers a single ``LspTool`` instance on the agent's
       ``ability_manager`` so the LLM can call it.
       The tool description (including operations, parameters, and usage notes)
       is automatically included in the system prompt via the tool metadata registry.
    3. Cleans up on ``uninit()`` — removes the tool and shuts down LSP.

    Usage::

        # Via DeepAgentConfig (recommended)
        config = DeepAgentConfig(rails=[LspRail()])
        agent = DeepAgent(config)

        # Or add dynamically
        agent.add_rail(LspRail())

    Attributes:
        priority: Execution priority (60 = moderate-high).
        options: Optional LSP initialization options (cwd, custom servers, etc.).
    """

    priority = 60

    def __init__(
        self,
        options: "InitializeOptions | None" = None,
        verbose: bool = False,
    ) -> None:
        """Initialize LspRail.

        Args:
            options: Optional LSP initialization options.
                If not provided, uses default configuration with
                cwd from workspace or current working directory.
            verbose: When True, every ``before_model_call`` diagnostic snapshot
                is appended to a timestamped file under ``logs/logs/`` in the
                project root, created fresh on each run.
        """
        super().__init__()
        self.options = options
        self.verbose = verbose
        self._lsp_tool: "Tool | None" = None
        self._initialized = False
        self._log_file: "Path | None" = None

    def init(self, agent: Any) -> None:
        """Initialize LSP subsystem and register LspTool on the agent.

        This method:
        1. Initializes the LSP subsystem (LSPServerManager).
           Idempotent — safe to call multiple times.
        2. Creates an LspTool instance.
        3. Registers it on the agent's ability_manager.

        Args:
            agent: The DeepAgent instance to register tools on.
        """
        from openjiuwen.harness.deep_agent import DeepAgent
        from openjiuwen.harness.tools import LspTool

        # 类型检查：仅在 DeepAgent 上生效
        if not (
            isinstance(agent, DeepAgent)
            and agent.deep_config
            and hasattr(agent, "ability_manager")
        ):
            logger.warning("LspRail: agent is not a DeepAgent or lacks ability_manager, skipping")
            return

        # 设置 sys_operation 和 workspace（如果尚未设置）
        if not self.sys_operation:
            self.set_sys_operation(agent.deep_config.sys_operation)
        if not self.workspace:
            self.set_workspace(agent.deep_config.workspace)

        # 确定 cwd：优先使用 options 中的 cwd，否则使用 workspace 路径
        effective_cwd: str | None = None
        if self.options and self.options.cwd:
            effective_cwd = self.options.cwd
        elif self.workspace:
            try:
                effective_cwd = str(self.workspace.root_path)
            except Exception as exc:
                logger.warning("LspRail: failed to get workspace root: %s", exc)

        # Create a fresh timestamped log file for this run (verbose mode only)
        if self.verbose:
            from datetime import datetime, timezone
            from pathlib import Path as _Path
            _log_dir = _Path(__file__).parent.parent.parent.parent / "logs" / "logs" / "lsp"
            try:
                _log_dir.mkdir(parents=True, exist_ok=True)
                _now = datetime.now(tz=timezone.utc)
                self._log_file = _log_dir / f"lsp_{_now.strftime('%Y%m%d_%H%M%S')}.log"
                self._log_file.write_text(
                    f"# LspRail diagnostic log — run started {_now.isoformat()}\n\n",
                    encoding="utf-8",
                )
                logger.info("LspRail: diagnostic log file: %s", self._log_file)
            except Exception as exc:
                logger.warning("LspRail: failed to create log file: %s", exc)

        # 如果 options 中没有 cwd，则构建一个
        import asyncio
        import copy
        opts = self.options
        if opts is None:
            from openjiuwen.harness.lsp import InitializeOptions
            opts = InitializeOptions(cwd=effective_cwd)
        elif not opts.cwd and effective_cwd:
            opts = copy.deepcopy(opts)
            opts.cwd = effective_cwd

        # Initialize LSP subsystem (synchronously blocking, with 15s timeout)
        try:
            import asyncio
            from openjiuwen.harness.lsp.core.manager import LSPServerManager

            if LSPServerManager.get_instance() is not None:
                logger.info("LspRail: LSP subsystem already initialized")
            else:
                async def _init_with_timeout():
                    try:
                        await asyncio.wait_for(self._async_init_lsp(opts), timeout=15.0)
                    except asyncio.TimeoutError:
                        logger.warning("LspRail: LSP initialization timed out after 15s")
                    except Exception as e:
                        logger.warning("LspRail: LSP initialization failed: %s", e)

                try:
                    loop = asyncio.get_running_loop()
                    # Block until initialization completes (or timeout)
                    future = asyncio.run_coroutine_threadsafe(
                        _init_with_timeout(), loop
                    )
                    future.result(timeout=20.0)
                except RuntimeError:
                    # No running loop — use asyncio.run
                    asyncio.run(_init_with_timeout())

        except Exception as exc:
            logger.warning("LspRail: failed to initialize LSP subsystem: %s", exc)

        # 创建 LspTool 实例（从 config 获取语言设置、sys_operation、workspace 和 agent_id）
        language = getattr(agent, "deep_config", None)
        language = getattr(language, "language", "cn") if language else "cn"
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        sys_op = getattr(agent.deep_config, "sys_operation", None) if agent.deep_config else None
        workspace_root = self.workspace.root_path if self.workspace else None
        self._lsp_tool = LspTool(
            operation=sys_op,
            language=language,
            workspace=str(workspace_root) if workspace_root else None,
            agent_id=agent_id,
        )

        # 注册到 ability_manager
        try:
            from openjiuwen.core.runner import Runner
            Runner.resource_mgr.add_tool(self._lsp_tool)
            agent.ability_manager.add(self._lsp_tool.card)
            self._initialized = True
            logger.info(
                "LspRail: initialized LSP subsystem and registered LspTool (cwd=%s)",
                effective_cwd,
            )
        except Exception as exc:
            logger.warning("LspRail: failed to register LspTool, error: %s", exc)

    async def _async_init_lsp(self, options: "InitializeOptions") -> None:
        """异步初始化 LSP 子系统。

        Args:
            options: LSP 初始化选项。
        """
        from openjiuwen.harness.lsp import initialize_lsp

        try:
            await initialize_lsp(options)
            logger.info("LspRail: LSP subsystem initialized successfully")
        except Exception as exc:
            logger.warning("LspRail: failed to initialize LSP subsystem: %s", exc)

    def uninit(self, agent: Any) -> None:
        """Remove LspTool from the agent and shutdown LSP subsystem.

        This method:
        1. Removes LspTool from ability_manager.
        2. Shuts down the LSP subsystem (stops all server processes).

        Args:
            agent: The DeepAgent instance to unregister tools from.
        """
        from openjiuwen.harness.deep_agent import DeepAgent

        # 从 ability_manager 移除 LspTool
        if self._lsp_tool and isinstance(agent, DeepAgent) and hasattr(agent, "ability_manager"):
            try:
                name = self._lsp_tool.card.name
                tool_id = self._lsp_tool.card.id

                agent.ability_manager.remove(name)

                from openjiuwen.core.runner import Runner
                Runner.resource_mgr.remove_tool(tool_id)

                logger.info("LspRail: removed LspTool (name=%s, id=%s)", name, tool_id)
            except Exception as exc:
                logger.warning("LspRail: failed to remove LspTool: %s", exc)

        # Shutdown LSP subsystem (synchronously blocking, with 10s timeout)
        try:
            import asyncio
            loop = asyncio.get_running_loop()

            async def _shutdown_with_timeout():
                try:
                    await asyncio.wait_for(self._async_shutdown_lsp(), timeout=10.0)
                except asyncio.TimeoutError:
                    logger.warning("LspRail: shutdown timed out after 10s, forcing")
                except Exception as e:
                    logger.warning("LspRail: shutdown error: %s", e)

            future = asyncio.run_coroutine_threadsafe(_shutdown_with_timeout(), loop)
            future.result(timeout=15.0)
        except RuntimeError:
            try:
                asyncio.run(self._async_shutdown_lsp())
            except Exception as exc:
                logger.warning("LspRail: failed to shutdown LSP subsystem: %s", exc)
        except Exception as exc:
            logger.warning("LspRail: shutdown failed: %s", exc)

        self._lsp_tool = None
        self._initialized = False

    async def _async_shutdown_lsp(self) -> None:
        from openjiuwen.harness.lsp import shutdown_lsp

        try:
            await shutdown_lsp()
            logger.info("LspRail: LSP subsystem shutdown successfully")
        except Exception as exc:
            logger.warning("LspRail: failed to shutdown LSP subsystem: %s", exc)
    
    # Tool names that modify file content and should trigger LSP re-analysis.
    _WRITE_TOOL_NAMES: frozenset[str] = frozenset({"edit_file", "write_file"})

    async def after_tool_call(self, ctx: Any) -> None:
        """Trigger LSP re-analysis after a file-write or file-edit tool call.

        When ``edit_file`` or ``write_file`` finishes, sends
        ``textDocument/didChange`` to the LSP server so it re-analyses the
        updated content and emits fresh ``publishDiagnostics`` notifications.
        The call is fire-and-forget — diagnostics can be retrieved later via
        ``get_pending_lsp_diagnostics()``.
        """
        inputs = ctx.inputs
        tool_name = getattr(inputs, "tool_name", "")
        if tool_name not in self._WRITE_TOOL_NAMES:
            return

        tool_args = getattr(inputs, "tool_args", None) or {}
        if isinstance(tool_args, str):
            import json
            try:
                tool_args = json.loads(tool_args)
            except Exception:
                tool_args = {}
        file_path = tool_args.get("file_path", "") if isinstance(tool_args, dict) else ""
        if not file_path:
            return

        import asyncio
        from openjiuwen.harness.lsp.core.manager import LSPServerManager

        manager = LSPServerManager.get_instance()
        if manager is None:
            return

        # Resolve to absolute path now (on the calling thread) so _trigger
        # always works regardless of cwd changes inside the coroutine.
        from pathlib import Path as _Path
        _p = _Path(file_path)
        if _p.is_absolute():
            resolved_path = str(_p.resolve())
        else:
            _root = self.workspace.root_path if self.workspace else None
            resolved_path = str((_Path(_root) / _p).resolve() if _root else (_Path.cwd() / _p).resolve())

        async def _trigger() -> None:
            try:
                from pathlib import Path
                from openjiuwen.harness.lsp.core.utils.file_uri import path_to_file_uri
                ext = Path(resolved_path).suffix.lower()
                language_id = "python" if ext in {".py", ".pyi"} else ext.lstrip(".") or "plaintext"
                # Some servers (e.g. pyright) require a prior didOpen before they
                # accept didChange.  Send didOpen first when the file is unknown.
                uri = path_to_file_uri(resolved_path)
                if not manager.is_file_open(uri):
                    await manager.open_file(resolved_path, language_id)
                else:
                    await manager.change_file(resolved_path, language_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug("LspRail: post-edit diagnostic trigger failed for %s: %s", resolved_path, exc)

        asyncio.ensure_future(_trigger())

    # -- lifecycle hooks --

    async def before_model_call(self, ctx: Any) -> None:
        """Inject pending LSP diagnostics into the message list before the LLM call.
        If ``after_tool_call`` fired an LSP re-analysis after an ``edit_file`` or
        ``write_file``, pyright will have published diagnostics to
        ``LspDiagnosticRegistry`` by the time the next model call begins.  This
        hook drains those diagnostics and appends a ``UserMessage`` so the LLM
        sees the errors without the agent having to call any diagnostic tool
        explicitly.
        """
        from openjiuwen.harness.lsp import get_pending_lsp_diagnostics

        diagnostics = get_pending_lsp_diagnostics()
        if not diagnostics:
            return

        if self.verbose:
            self._write_diagnostics_info(diagnostics)

        messages = getattr(ctx.inputs, "messages", None)
        if messages is None:
            return

        text = self._format_diagnostics(diagnostics)
        if not text:
            return

        try:
            from openjiuwen.core.foundation.llm.schema.message import UserMessage
            messages.append(UserMessage(content=text))
            logger.debug("LspRail: injected %d diagnostic file(s) into model context", len(diagnostics))
        except Exception as exc:
            logger.debug("LspRail: failed to inject diagnostics into messages: %s", exc)

    def _write_diagnostics_info(self, diagnostics: list) -> None:
        """Append LSP diagnostics to the run's log file (verbose mode)."""
        if self._log_file is None:
            return
        try:
            sev_labels = {1: "Error", 2: "Warning", 3: "Info", 4: "Hint"}
            with self._log_file.open("a", encoding="utf-8") as fh:
                for f in diagnostics:
                    file_label = f.local_path if f.local_path else f.uri
                    fh.write(f"[before_model_call] server={f.server_name!r}  local_path={file_label!r}\n")
                    for d in f.diagnostics:
                        sev = sev_labels.get(d.severity, f"S{d.severity}")
                        line = d.range.get("start", {}).get("line", 0) + 1
                        char = d.range.get("start", {}).get("character", 0) + 1
                        code = f" ({d.code})" if d.code else ""
                        msg = d.message.splitlines()[0]
                        fh.write(f"  [{sev}] line {line}, col {char}{code}  {msg}\n")
                    fh.write("\n")
        except Exception as exc:
            logger.debug("LspRail: failed to write diagnostics to log file: %s", exc)

    @staticmethod
    def _format_diagnostics(diagnostics: list) -> str:
        """Format LSP diagnostic entries into a concise user-message string."""
        sev_labels = {1: "Error", 2: "Warning", 3: "Info", 4: "Hint"}
        lines: list[str] = ["[LSP Diagnostics] The following issues were detected after the last file edit:"]
        for f in diagnostics:
            file_label = f.local_path if f.local_path else f.uri
            lines.append(f"\nFile: {file_label}")
            for d in f.diagnostics:
                sev = sev_labels.get(d.severity, f"S{d.severity}")
                line = d.range.get("start", {}).get("line", 0) + 1
                char = d.range.get("start", {}).get("character", 0) + 1
                code = f" ({d.code})" if d.code else ""
                lines.append(f"  [{sev}] line {line}, col {char}{code}  {d.message}")
        lines.append("\nPlease review and fix these issues.")
        return "\n".join(lines)


__all__ = [
    "LspRail",
]
