"""Click CLI entry point for OpenJiuWen.

Commands:
    ``openjiuwen``            — interactive REPL (default)
    ``openjiuwen chat``       — interactive REPL (explicit)
    ``openjiuwen run PROMPT`` — non-interactive single run
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Any, Optional

import click

from openjiuwen.harness.cli import __version__
from openjiuwen.harness.cli.agent.config import load_config


def _bootstrap_logging() -> None:
    """Ensure SDK LogManager is initialised before any SDK import.

    In standalone CLI mode the ``extensions.common.configs.log_config``
    entry-point is absent, causing a ``RuntimeError`` on the first
    SDK import that touches the logger.  Setting ``_initialized = True``
    lets the SDK fall through to stdlib-backed loggers.
    """
    try:
        import logging
        import os

        # Suppress ALL SDK logs in CLI mode.
        logging.getLogger("openjiuwen").setLevel(logging.CRITICAL)
        logging.getLogger().setLevel(logging.WARNING)

        from openjiuwen.core.common.logging import (
            manager as log_mgr,
        )

        LogMgr = log_mgr.LogManager  # noqa: N806
        if not getattr(LogMgr, "_initialized", False):

            class _NullLogger:
                """Completely silent logger for CLI mode."""

                def __init__(self, lt: str, cfg: object = None) -> None:
                    self._lt = lt

                def _noop(self, *a: object, **kw: object) -> None:
                    pass

                debug = info = warning = error = critical = _noop
                warn = exception = log = _noop

                def __getattr__(self, n: str) -> object:
                    return self._noop

                def logger(self) -> object:
                    lg = logging.getLogger(f"openjiuwen.{self._lt}")
                    lg.setLevel(logging.CRITICAL)
                    lg.handlers.clear()
                    return lg

            _orig = LogMgr.get_logger.__func__

            @classmethod  # type: ignore[misc]
            def _safe(cls: type, lt: str = "default") -> object:
                try:
                    result = _orig(cls, lt)
                    # Silence any already-created loggers too
                    if hasattr(result, "logger"):
                        real_lg = result.logger()
                        if hasattr(real_lg, "setLevel"):
                            real_lg.setLevel(logging.CRITICAL)
                            real_lg.handlers.clear()
                    return result
                except RuntimeError:
                    if lt not in cls._loggers:
                        cls._loggers[lt] = _NullLogger(lt)
                    return cls._loggers[lt]

            LogMgr.get_logger = _safe  # type: ignore[assignment]
            setattr(LogMgr, "_initialized", True)

            # Also silence the root openjiuwen logger
            # and disable propagation
            for name in list(logging.Logger.manager.loggerDict):
                if name.startswith("openjiuwen"):
                    lg = logging.getLogger(name)
                    lg.setLevel(logging.CRITICAL)
                    lg.handlers.clear()

    except Exception:  # noqa: BLE001
        pass


_bootstrap_logging()


# ---------------------------------------------------------------------------
# Interactive onboarding (when API key is missing)
# ---------------------------------------------------------------------------


def _interactive_setup() -> dict[str, str]:
    """Guide the user through first-time API configuration.

    Returns:
        Dict with provider, model, api_key, api_base values.
    """
    from openjiuwen.harness.cli.agent.config import (
        save_settings_json,
    )

    click.echo()
    click.secho(
        "  Welcome to OpenJiuWen CLI!", fg="cyan", bold=True
    )
    click.echo(
        "  No API key found. Let's set one up.\n"
    )

    provider = click.prompt(
        "  LLM Provider",
        type=click.Choice(
            ["OpenAI", "DashScope", "SiliconFlow"],
            case_sensitive=False,
        ),
        default="OpenAI",
    )

    default_bases = {
        "OpenAI": "https://api.openai.com/v1",
        "DashScope": (
            "https://dashscope.aliyuncs.com"
            "/compatible-mode/v1"
        ),
        "SiliconFlow": "https://api.siliconflow.cn/v1",
    }

    api_base = click.prompt(
        "  API Base URL",
        default=default_bases.get(
            provider, default_bases["OpenAI"]
        ),
    )
    model = click.prompt(
        "  Model name", default="gpt-4o"
    )
    api_key = click.prompt(
        "  API Key", hide_input=True
    )

    # Save to ~/.openjiuwen/settings.json
    saved_path = save_settings_json(
        {
            "provider": provider,
            "model": model,
            "apiKey": api_key,
            "apiBase": api_base,
            "maxTokens": 8192,
            "maxIterations": 30,
        }
    )
    click.secho(
        f"\n  Config saved to {saved_path}",
        fg="green",
    )
    click.echo()

    return {
        "provider": provider,
        "model": model,
        "api_key": api_key,
        "api_base": api_base,
    }


# ---------------------------------------------------------------------------
# CLI option bundle
# ---------------------------------------------------------------------------


@dataclass
class CLIOptions:
    """Bundle of CLI options passed through click commands.

    Attributes:
        provider: LLM provider name.
        model: Model name.
        api_key: API key.
        api_base: API base URL.
        remote: Remote agent-server URL.
        verbose: Verbose logging flag.
        workspace: Agent workspace directory.
    """

    provider: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    remote: Optional[str] = None
    verbose: bool = False
    workspace: Optional[str] = None


# ---------------------------------------------------------------------------
# Async runners (called from click commands)
# ---------------------------------------------------------------------------


async def _run_chat(opts: CLIOptions) -> None:
    """Start the interactive REPL session."""
    from openjiuwen.harness.cli.agent.factory import create_backend
    from openjiuwen.harness.cli.ui.repl import run_repl
    from openjiuwen.harness.cli.storage.session_store import SessionStore

    cfg = load_config(
        provider=opts.provider,
        model=opts.model,
        api_key=opts.api_key,
        api_base=opts.api_base,
        server_url=opts.remote,
        workspace=opts.workspace,
        verbose=opts.verbose,
    )

    backend = create_backend(cfg)
    store = SessionStore()
    store.new_session(
        getattr(backend, "_session_id", "cli"), cfg.model
    )

    try:
        await backend.start()
        await run_repl(backend, cfg, store)
    finally:
        await backend.stop()


async def _run_once(
    opts: CLIOptions,
    prompt: str,
    output_format: str,
) -> int:
    """Execute a non-interactive run."""
    from openjiuwen.harness.cli.ui.runner import run_once

    cfg = load_config(
        provider=opts.provider,
        model=opts.model,
        api_key=opts.api_key,
        api_base=opts.api_base,
        server_url=opts.remote,
        workspace=opts.workspace,
        verbose=opts.verbose,
    )
    return await run_once(cfg, prompt, output_format)


# ---------------------------------------------------------------------------
# Click commands
# ---------------------------------------------------------------------------


@click.group(invoke_without_command=True)
@click.version_option(
    version=__version__, prog_name="openjiuwen"
)
@click.option(
    "--model", "-m", default=None, help="Model name."
)
@click.option(
    "--provider", default=None, help="LLM provider."
)
@click.option(
    "--api-key", default=None, help="API key."
)
@click.option(
    "--api-base", default=None, help="API base URL."
)
@click.option(
    "--remote",
    default=None,
    help="Remote agent-server URL.",
)
@click.option(
    "--verbose", "-v", is_flag=True, help="Verbose logging."
)
@click.option(
    "--workspace",
    "-w",
    default=None,
    help="Agent workspace directory (default: ~/.openjiuwen/workspace).",
)
@click.pass_context
def cli(ctx: click.Context, **kwargs: Any) -> None:
    """OpenJiuWen \u2014 terminal interactive AI programming assistant."""
    ctx.ensure_object(dict)
    ctx.obj["opts"] = CLIOptions(
        model=kwargs.get("model"),
        provider=kwargs.get("provider"),
        api_key=kwargs.get("api_key"),
        api_base=kwargs.get("api_base"),
        remote=kwargs.get("remote"),
        verbose=kwargs.get("verbose", False),
        workspace=kwargs.get("workspace"),
    )

    if ctx.invoked_subcommand is None:
        if sys.stdin.isatty():
            ctx.invoke(chat)
        else:
            # Non-TTY stdin → treat as pipe to 'run'
            ctx.invoke(run)


@cli.command()
@click.pass_context
def chat(ctx: click.Context) -> None:
    """Interactive REPL mode (default)."""
    opts: CLIOptions = ctx.obj["opts"]
    try:
        asyncio.run(_run_chat(opts))
    except ValueError as exc:
        if "API key" in str(exc) and sys.stdin.isatty():
            # Interactive onboarding
            setup = _interactive_setup()
            patched = CLIOptions(
                provider=setup["provider"],
                model=setup["model"],
                api_key=setup["api_key"],
                api_base=setup["api_base"],
                remote=opts.remote,
                verbose=opts.verbose,
                workspace=opts.workspace,
            )
            asyncio.run(_run_chat(patched))
        else:
            click.echo(f"Error: {exc}", err=True)
            ctx.exit(1)
    except KeyboardInterrupt:
        pass


@cli.command()
@click.argument("prompt", required=False)
@click.option(
    "--output-format",
    "-f",
    type=click.Choice(["text", "json", "stream-json"]),
    default="text",
    help="Output format.",
)
@click.pass_context
def run(
    ctx: click.Context,
    prompt: str | None,
    output_format: str,
) -> None:
    """Non-interactive run mode."""
    opts: CLIOptions = ctx.obj["opts"]

    # Support stdin pipe: openjiuwen run -
    if prompt == "-" or (
        prompt is None and not sys.stdin.isatty()
    ):
        prompt = sys.stdin.read().strip()
    if not prompt:
        raise click.UsageError(
            "A prompt argument is required, or pipe via stdin."
        )

    try:
        exit_code = asyncio.run(
            _run_once(opts, prompt, output_format)
        )
        ctx.exit(exit_code)
    except ValueError as exc:
        click.echo(f"Error: {exc}", err=True)
        ctx.exit(1)
    except KeyboardInterrupt:
        pass
