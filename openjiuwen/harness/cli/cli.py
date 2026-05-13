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

        if "pytest" in sys.modules:
            return

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


# -------------------------------------------------------------------
# auto-harness subcommand group
# -------------------------------------------------------------------


@dataclass
class AutoHarnessRunRequest:
    """Named request payload for auto-harness run parameters."""

    task: str | None = None
    task_file: str | None = None
    dry_run: bool = False
    stage: str | None = None
    no_push: bool = False
    budget: float | None = None
    goal: str | None = None
    pipeline: str | None = None

    @classmethod
    def from_kwargs(
        cls, kwargs: dict[str, Any]
    ) -> "AutoHarnessRunRequest":
        """Build request from click callback kwargs."""
        return cls(
            task=kwargs.get("task"),
            task_file=kwargs.get("task_file"),
            dry_run=bool(kwargs.get("dry_run", False)),
            stage=kwargs.get("stage"),
            no_push=bool(kwargs.get("no_push", False)),
            budget=kwargs.get("budget"),
            goal=kwargs.get("goal"),
            pipeline=kwargs.get("pipeline"),
        )


async def _run_auto_harness(
    opts: CLIOptions,
    request: AutoHarnessRunRequest,
) -> int:
    """Execute an auto-harness session.

    Returns:
        Exit code (0 = success).
    """
    import json
    import logging
    import time
    from pathlib import Path

    from openjiuwen.auto_harness.schema import (
        normalize_pipeline_preference,
        OptimizationTask,
        is_placeholder_local_repo,
        load_auto_harness_config,
    )
    from openjiuwen.auto_harness.pipelines import (
        META_EVOLVE_PIPELINE,
    )
    from openjiuwen.auto_harness.orchestrator import (
        create_auto_harness_orchestrator,
    )
    from openjiuwen.auto_harness.stages.assess import (
        run_assess_stream,
    )
    from openjiuwen.auto_harness.experience.experience_store import (
        ExperienceStore,
    )
    from openjiuwen.auto_harness.infra.ci_gate_runner import (
        CIGateRunner,
    )
    from openjiuwen.auto_harness.infra.github_cli import (
        ensure_github_cli_ready,
    )
    from openjiuwen.core.foundation.llm.model import Model
    from openjiuwen.core.foundation.llm.schema.config import (
        ModelClientConfig,
        ModelRequestConfig,
    )
    from openjiuwen.harness.cli.agent.config import (
        load_config as load_cli_config,
    )

    if opts.verbose:
        for name in (
            "auto_harness",
            "openjiuwen.auto_harness",
        ):
            logging.getLogger(name).setLevel(
                logging.DEBUG,
            )

    # data_dir 由 CLI home 决定
    cli_home = opts.workspace or str(
        Path.home() / ".openjiuwen"
    )
    data_dir = str(Path(cli_home) / "auto_harness")
    config_path = str(
        Path(data_dir) / "config.yaml"
    )

    # 从 YAML 加载配置
    config = load_auto_harness_config(
        config_path, workspace_hint=opts.workspace or "",
    )
    config.data_dir = data_dir
    if (
        config.local_repo
        and (
            is_placeholder_local_repo(
                config.local_repo
            )
            or not Path(config.local_repo).exists()
        )
    ):
        click.echo(
            "忽略无效的 local_repo 配置: "
            f"{config.local_repo}"
        )
        config.local_repo = ""
    if config.config_bootstrapped:
        click.echo(
            "已初始化 auto-harness 配置模板: "
            f"{config.config_path}"
        )
    if not config.local_repo and config.suggested_local_repo:
        config.local_repo = config.suggested_local_repo
        click.echo(
            "检测到本地仓库，临时使用 "
            f"local_repo={config.local_repo}。"
            "建议写回 config.yaml。"
        )
    elif not config.local_repo:
        click.echo(
            "未配置 local_repo，auto-harness 将使用 "
            "clone 缓存。请编辑 "
            f"{config.config_path or config_path} "
            "补充 local_repo。"
        )
    if config.local_repo:
        config.workspace = config.local_repo
    elif not config.workspace:
        config.workspace = opts.workspace or ""

    debug_dir = Path(config.runs_dir)
    debug_dir.mkdir(parents=True, exist_ok=True)

    # 从 CLI 选项构建 Model
    cli_cfg = load_cli_config(
        provider=opts.provider,
        model=opts.model,
        api_key=opts.api_key,
        api_base=opts.api_base,
        workspace=opts.workspace,
        verbose=opts.verbose,
    )
    model = Model(
        model_client_config=ModelClientConfig(
            client_provider=cli_cfg.provider,
            api_key=cli_cfg.api_key,
            api_base=cli_cfg.api_base,
            timeout=config.model_timeout_secs,
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(
            model=cli_cfg.model,
            temperature=0.2,
            top_p=0.9,
        ),
    )

    # 将 Model 和 CLI 参数覆盖到已加载的 config 上
    config.model = model
    if request.budget is not None:
        config.session_budget_secs = request.budget
        config.task_timeout_secs = min(
            config.task_timeout_secs, request.budget * 0.95,
        )
    if request.no_push:
        config.git_remote = ""
    if request.goal:
        config.optimization_goal = request.goal
    config.pipeline_preference = normalize_pipeline_preference(
        request.pipeline or META_EVOLVE_PIPELINE
    )

    if request.stage in (None, "assess", "plan"):
        ensure_github_cli_ready(click.echo)

    # Load tasks
    tasks: list[OptimizationTask] = []
    if request.task:
        tasks = [OptimizationTask(topic=request.task)]
    elif request.task_file:
        raw = json.loads(
            Path(request.task_file).read_text(encoding="utf-8"),
        )
        if isinstance(raw, dict):
            raw = [raw]
        for item in raw:
            tasks.append(OptimizationTask(
                topic=item["topic"],
                description=item.get("description", ""),
                files=item.get("files", []),
            ))

    # Stage dispatch
    if request.stage == "assess":
        from rich.console import Console
        from openjiuwen.harness.cli.ui.renderer import (
            render_stream,
        )

        experience_store = ExperienceStore(
            config.resolved_experience_dir
        )
        console = Console()
        stream = run_assess_stream(
            config, experience_store
        )
        result = await render_stream(stream, console)
        report = result.text or ""
        if report:
            out = debug_dir / "assessment.md"
            out.write_text(report, encoding="utf-8")
        return 0

    if request.stage == "verify":
        ci = CIGateRunner(
            workspace=(
                config.local_repo
                or config.cache_repo_dir
            ),
            config_path=config.ci_gate_config,
            python_executable=(
                config.resolve_ci_gate_python_executable()
            ),
            install_command=(
                config.ci_gate_install_command
            ),
        )
        result = await ci.run("all")
        passed = result.get("passed", False)
        click.echo(
            f"CI Gate: {'PASSED' if passed else 'FAILED'}"
        )
        if not passed:
            for err in result.get("errors", [])[:10]:
                click.echo(f"  {err}", err=True)
        return 0 if passed else 1

    # Full session or dry-run
    t0 = time.monotonic()

    if tasks:
        click.echo(
            f"使用手动指定的 {len(tasks)} 个任务",
        )
    else:
        click.echo(
            "未指定手动任务，将执行 "
            "assess → plan → implement → learnings"
        )

    if request.dry_run:
        task_data = [
            {
                "topic": t.topic,
                "description": t.description,
                "files": t.files,
            }
            for t in tasks
        ]
        out_path = debug_dir / "tasks.json"
        out_path.write_text(
            json.dumps(
                task_data, ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        click.echo(json.dumps(
            task_data, ensure_ascii=False, indent=2,
        ))
        click.echo(f"[dry-run] 任务列表 → {out_path}")
        return 0

    from openjiuwen.harness.cli.rails.tool_tracker import (
        ToolTrackingRail,
    )

    orch = create_auto_harness_orchestrator(
        config,
        stream_rails=[ToolTrackingRail()],
    )
    stream = orch.run_session_stream(tasks=tasks or None)
    from rich.console import Console
    from openjiuwen.harness.cli.ui.renderer import (
        render_stream,
    )

    console = Console()

    async def _on_activate_interaction(
        iid: str, value: Any,
    ) -> str:
        if (
            isinstance(value, dict)
            and value.get("interaction_type")
            == "activate_confirm"
        ):
            ext_name = value.get(
                "extension_name", "unknown"
            )
            click.echo()
            click.echo(f"扩展 {ext_name} 已就绪")
            if value.get("runtime_path"):
                click.echo(
                    f"  路径: {value['runtime_path']}"
                )
            click.echo()
            click.echo("  [A] 接受并热加载")
            click.echo("  [R] 拒绝并清理")
            click.echo()
            while True:
                choice = click.prompt(
                    "选择 (A/R)",
                    default="A",
                ).strip().lower()
                if choice in ("a", "accept"):
                    action = "accept"
                    break
                if choice in ("r", "reject"):
                    action = "reject"
                    break
                click.echo("请输入 A 或 R")
            orch.run_session_stream(
                message={
                    "interaction_id": iid,
                    "action": action,
                },
            )
            return action
        return ""

    await render_stream(
        stream,
        console,
        on_interaction=_on_activate_interaction,
    )
    results = orch.results
    elapsed = time.monotonic() - t0
    ok = sum(1 for r in results if r.success)
    click.echo(
        f"Session 完成: {ok}/{len(results)} 成功, "
        f"耗时 {elapsed:.1f}s"
    )
    for i, r in enumerate(results):
        s = "OK" if r.success else "FAIL"
        click.echo(
            f"Task {i + 1}: {s}"
            f" | pr={r.pr_url or 'N/A'}"
            f" | error={r.error or 'none'}"
        )
        if r.summary:
            click.echo(f"  summary={r.summary}")
    return 0


async def _run_experience_search(
    workspace: str, query: str,
) -> None:
    """Search the experience store."""
    import os
    from pathlib import Path

    from openjiuwen.auto_harness.experience.experience_store import (
        ExperienceStore,
    )

    ws = workspace or os.getcwd()
    store = ExperienceStore(
        str(Path(ws) / "auto_harness/experience"),
    )
    results = await store.search(query, top_k=10)
    if not results:
        click.echo("无匹配结果")
        return
    for m in results:
        click.echo(
            f"[{m.type.value}] {m.topic}: "
            f"{m.summary or m.outcome}"
        )


async def _run_experience_list(
    workspace: str,
    mem_type: str | None,
    limit: int,
) -> None:
    """List recent experience entries."""
    import os
    from pathlib import Path

    from openjiuwen.auto_harness.experience.experience_store import (
        ExperienceStore,
    )

    ws = workspace or os.getcwd()
    store = ExperienceStore(
        str(Path(ws) / "auto_harness/experience"),
    )
    entries = await store.list_recent(limit=limit)
    if mem_type:
        entries = [
            e for e in entries
            if e.type.value == mem_type
        ]
    if not entries:
        click.echo("无记录")
        return
    for m in entries:
        click.echo(
            f"[{m.type.value}] {m.topic}: "
            f"{m.summary or m.outcome}"
        )


async def _run_gap_analyze(
    workspace: str,
) -> None:
    """Run competitive gap analysis."""
    import os

    from openjiuwen.auto_harness.schema import (
        AutoHarnessConfig,
    )
    from openjiuwen.auto_harness.stages.assess import (
        run_gap_analysis,
    )

    ws = workspace or os.getcwd()
    config = AutoHarnessConfig(workspace=ws)
    gaps = await run_gap_analysis(
        config, harness_state="",
    )
    if not gaps:
        click.echo(
            "Phase 1 占位: 差距分析尚未接入 LLM"
        )
        return
    for g in gaps:
        click.echo(
            f"[{g.priority:.1f}] {g.feature}: "
            f"{g.gap_description}"
        )


# -------------------------------------------------------------------
# auto-harness Click group + subcommands
# -------------------------------------------------------------------


@cli.group("auto-harness")
@click.pass_context
def auto_harness(ctx: click.Context) -> None:
    """Auto Harness Agent — 自主优化 harness 框架。"""
    pass


@auto_harness.command("run")
@click.option(
    "--task", default=None,
    help="手动指定单个任务描述。",
)
@click.option(
    "--task-file", default=None,
    help="从 JSON 文件加载任务列表。",
)
@click.option(
    "--dry-run", is_flag=True,
    help="只执行 assess + plan，不 implement。",
)
@click.option(
    "--stage",
    type=click.Choice(
        ["assess", "plan", "implement", "verify"],
    ),
    default=None,
    help="只执行指定阶段。",
)
@click.option(
    "--no-push", is_flag=True,
    help="不 push / 不创建 MR。",
)
@click.option(
    "--budget", type=float, default=None,
    help="覆盖 session 预算（秒）。",
)
@click.option(
    "--goal", default=None,
    help="指定本轮自然语言优化目标，驱动 assess/plan 全流程。",
)
@click.option(
    "--pipeline",
    type=click.Choice(["meta", "extended", "auto"]),
    default=None,
    help="选择 session pipeline；默认 meta。",
)
@click.pass_context
def auto_harness_run(
    ctx: click.Context,
    **kwargs: Any,
) -> None:
    """执行优化周期。"""
    opts: CLIOptions = ctx.obj["opts"]
    try:
        request = AutoHarnessRunRequest.from_kwargs(kwargs)
        exit_code = asyncio.run(
            _run_auto_harness(opts, request)
        )
        ctx.exit(exit_code)
    except KeyboardInterrupt:
        pass


@auto_harness.group("experience")
def auto_harness_experience() -> None:
    """经验库操作。"""
    pass


@auto_harness_experience.command("search")
@click.argument("query")
@click.pass_context
def experience_search(
    ctx: click.Context, query: str,
) -> None:
    """搜索经验库。"""
    opts: CLIOptions = ctx.obj["opts"]
    asyncio.run(
        _run_experience_search(
            opts.workspace or "", query
        ),
    )


@auto_harness_experience.command("list")
@click.option(
    "--type", "mem_type", default=None,
    help="按类型过滤 (optimization/failure/insight)。",
)
@click.option(
    "--limit", default=10, type=int,
    help="返回条数。",
)
@click.pass_context
def experience_list(
    ctx: click.Context,
    mem_type: str | None,
    limit: int,
) -> None:
    """列出经验库记录。"""
    opts: CLIOptions = ctx.obj["opts"]
    asyncio.run(
        _run_experience_list(
            opts.workspace or "", mem_type, limit,
        ),
    )


@auto_harness.command("verify-ext")
@click.option(
    "--ext-path",
    default=None,
    type=click.Path(exists=True, file_okay=False),
    help=(
        "扩展包根目录（含 harness_config.yaml）。"
        "不传则自动生成 smoke-test scaffold。"
    ),
)
@click.pass_context
def auto_harness_verify_ext(
    ctx: click.Context,
    ext_path: str | None,
) -> None:
    """轻量验证扩展包（结构+lint）。"""
    asyncio.run(_run_verify_ext(ext_path))


def _generate_smoke_scaffold(
    base_dir: str | None = None,
) -> str:
    """Generate a minimal extension scaffold and return its path."""
    import tempfile
    from pathlib import Path as _P

    ext_name = "smoke_test_ext"
    if base_dir:
        root = _P(base_dir) / ext_name
    else:
        root = _P(
            tempfile.mkdtemp(prefix="verify_ext_")
        ) / ext_name
    rails_dir = root / "rails"
    tools_dir = root / "tools"
    for d in (root, rails_dir, tools_dir):
        d.mkdir(parents=True, exist_ok=True)
        (d / "__init__.py").write_text(
            "", encoding="utf-8",
        )
    mod = (
        f"openjiuwen.extensions.harness.{ext_name}"
    )
    (rails_dir / "smoke_rail.py").write_text(
        "from openjiuwen.harness.rails.base "
        "import DeepAgentRail\n\n\n"
        "class SmokeRail(DeepAgentRail):\n"
        '    """Smoke-test rail."""\n\n'
        "    pass\n",
        encoding="utf-8",
    )
    (tools_dir / "helper.py").write_text(
        f"EXTENSION_NAME = '{ext_name}'\n",
        encoding="utf-8",
    )
    (tools_dir / "smoke_tool.py").write_text(
        "from __future__ import annotations\n\n"
        "from typing import Any, AsyncIterator, "
        "Dict\n\n"
        "from .helper import EXTENSION_NAME\n"
        "from openjiuwen.core.foundation.tool "
        "import Tool, ToolCard\n\n\n"
        "class SmokeTool(Tool):\n"
        "    def __init__(self) -> None:\n"
        "        super().__init__(\n"
        "            ToolCard(\n"
        "                id='smoke_tool',\n"
        "                name='smoke_tool',\n"
        "                description=(\n"
        '                    "Smoke test tool "\n'
        "                    + EXTENSION_NAME\n"
        "                ),\n"
        "            )\n"
        "        )\n\n"
        "    async def invoke(\n"
        "        self,\n"
        "        inputs: Dict[str, Any],\n"
        "        **kwargs: Any,\n"
        "    ) -> Dict[str, Any]:\n"
        "        return {'ext': EXTENSION_NAME}\n\n"
        "    async def stream(\n"
        "        self,\n"
        "        inputs: Dict[str, Any],\n"
        "        **kwargs: Any,\n"
        "    ) -> AsyncIterator[Dict[str, Any]]:\n"
        "        yield await self.invoke("
        "inputs, **kwargs)\n",
        encoding="utf-8",
    )
    (root / "harness_config.yaml").write_text(
        "schema_version: harness_config.v0.1\n"
        f"name: {ext_name}\n"
        "resources:\n"
        "  rails:\n"
        "    - type: package\n"
        f"      module: {mod}.rails.smoke_rail\n"
        "      class: SmokeRail\n"
        "  tools:\n"
        "    - type: package\n"
        f"      module: {mod}.tools.smoke_tool\n"
        "      class: SmokeTool\n",
        encoding="utf-8",
    )
    return str(root)


async def _run_verify_ext(
    ext_path: str | None,
) -> None:
    import uuid
    from pathlib import Path as _Path

    from openjiuwen.auto_harness.infra.runtime_extension_loader import (
        load_runtime_rails,
        load_runtime_skill_dirs,
        load_runtime_tools,
    )
    from openjiuwen.auto_harness.schema import (
        RuntimeExtensionArtifact,
    )
    from openjiuwen.auto_harness.stages.verify import (
        _check_ruff,
    )

    generated = False
    if ext_path is None:
        click.echo("== Generating smoke-test scaffold ==")
        ext_path = _generate_smoke_scaffold()
        generated = True
        click.echo(f"  {ext_path}")

    root = _Path(ext_path).resolve()
    manifest = root / "harness_config.yaml"
    if not manifest.is_file():
        raise click.ClickException(
            f"{manifest} not found"
        )

    ext_name = root.name
    session_id = f"cli_verify_{uuid.uuid4().hex[:8]}"
    runtime_ext = RuntimeExtensionArtifact(
        extension_name=ext_name,
        runtime_path=str(root),
        config_path=str(manifest),
    )

    errors: list[str] = []
    rails_count = 0
    tools_count = 0
    skills_count = 0

    # Layer 1: structure check
    click.echo("== Layer 1: structure check ==")
    try:
        rails = load_runtime_rails(
            runtime_ext, session_id=session_id,
        )
        tools = load_runtime_tools(
            runtime_ext, session_id=session_id,
        )
        for cls in rails:
            cls()
        for cls in tools:
            cls()
        rails_count = len(rails)
        tools_count = len(tools)
        skill_dirs = load_runtime_skill_dirs(runtime_ext)
        for sd in skill_dirs:
            sd_path = _Path(sd)
            skill_mds = list(sd_path.rglob("SKILL.md"))
            skills_count += len(skill_mds)
            if not skill_mds:
                errors.append(
                    f"Skill dir has no SKILL.md: {sd}"
                )
        click.echo(
            f"  rails={rails_count} tools={tools_count}"
            f" skills={skills_count}"
        )
    except Exception as exc:
        errors.append(f"Structure check failed: {exc}")
        click.echo(f"  FAILED: {exc}", err=True)

    # Layer 2: ruff lint
    click.echo("== Layer 2: ruff lint ==")
    if root.is_dir():
        lint_errors = await _check_ruff(root)
        errors.extend(lint_errors)
        if lint_errors:
            for e in lint_errors:
                click.echo(f"  {e}", err=True)
        else:
            click.echo("  OK")

    # Cleanup generated scaffold
    if generated:
        import shutil
        shutil.rmtree(root.parent, ignore_errors=True)

    if errors:
        click.echo(
            f"\nFAILED ({len(errors)} error(s)):",
            err=True,
        )
        for e in errors:
            click.echo(f"  - {e}", err=True)
        raise click.ClickException(
            f"verify_ext failed ({len(errors)} error(s))"
        )
    click.echo(
        f"\nPASSED: rails={rails_count}"
        f" tools={tools_count}"
        f" skills={skills_count}"
    )


@auto_harness.command("gap-analyze")
@click.pass_context
def gap_analyze(
    ctx: click.Context,
) -> None:
    """差距分析。"""
    opts: CLIOptions = ctx.obj["opts"]
    asyncio.run(
        _run_gap_analyze(
            opts.workspace or "",
        ),
    )


@auto_harness.command("history")
@click.option(
    "--limit", default=20, type=int,
    help="返回条数。",
)
@click.pass_context
def auto_harness_history(
    ctx: click.Context, limit: int,
) -> None:
    """查看优化历史。"""
    opts: CLIOptions = ctx.obj["opts"]
    asyncio.run(
        _run_experience_list(
            opts.workspace or "", None, limit
        ),
    )
