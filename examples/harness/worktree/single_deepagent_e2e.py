# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""End-to-end demo: a single ``DeepAgent`` with full coding capability +
``enter_worktree`` / ``exit_worktree`` tools, **plus a mid-run abort
and resume cycle** that verifies the worktree state survives the
interrupt.

The demo drives a real LLM (no mocking). The model client is built from
environment variables — see ``README.md`` next to this file for the
required variables and how to run it.

The conversation is split into two ``Runner.run_agent`` calls that
share the same agent ``session_id``. Between them the demo simulates
a process restart by clearing the worktree / cwd ContextVars; the
second invoke is expected to recover the worktree purely from the
agent ``Session`` checkpoint via ``WorktreeRail.before_invoke``.

What the agent is asked to do:

Run 1 (the agent is aborted right after step 2):

1. ``enter_worktree(name=...)`` — create an isolated git worktree on a
   fresh ``worktree-<slug>`` branch.
2. ``write_file`` — author ``hello.py`` under the worktree's cwd.

A demo-local rail (``_AbortAfterWriteFileRail``) watches for the
``write_file`` tool call and issues ``ctx.request_force_finish(...)``
in ``after_tool_call``. The ReAct loop honours the request, so the
agent returns *immediately after* step 2 with a synthetic
``abort_simulated`` result, the same way an external SIGINT-style
abort would land. The lifecycle's ``after_invoke`` hooks still fire,
so ``WorktreeRail`` persists the worktree session into
``Session.state`` and ``InMemoryCheckpointer`` saves the full state
blob under ``session_id``.

Run 2 (same ``session_id``, the agent picks up where Run 1 stopped):

3. ``bash`` — run ``python hello.py`` and verify the output.
4. ``bash`` — ``git add`` + ``git commit`` inside the worktree so the
   change lives on its dedicated branch.
5. ``exit_worktree(action="keep")`` — back to the repo's cwd; the
   ``worktree-<slug>`` branch still carries the new commit.
6. ``bash`` — ``git merge worktree-<slug>`` from the main repo to fold
   the commit into ``main``.

After the run we assert:

- The worktree was created exactly once across the two invokes.
- ``hello.py`` was committed on the ``worktree-<slug>`` branch.
- After the merge, ``hello.py`` also exists at the main repo root with
  matching contents (proving the merge picked up the commit).
- The cwd was restored to ``repo`` so the post-exit ``git merge`` ran
  against the right working tree.
- The worktree session ContextVar visible to ``main()`` was cleared
  between invokes (so the second invoke can only succeed via the
  Session-side checkpoint restore, not by leftover process state).

The demo writes to fixed on-disk directories so the artifacts survive
the run and can be inspected. ``repo`` and ``workspace`` are configured
independently via env vars and may sit on completely separate paths:

- ``WORKTREE_DEMO_REPO_DIR`` (default ``/Users/alan/Developer/worktree_demo/repo``)
- ``WORKTREE_DEMO_WORKSPACE_DIR`` (default ``/Users/alan/Developer/worktree_demo/wkspc``)

How the cwd / workspace decoupling works:

``DeepAgent._ensure_initialized`` pins the agent's cwd ContextVar to
``workspace.root_path`` on first invoke. ``WorktreeManager`` then walks
up from that cwd to find the canonical git root — which means the
default flow only works when the workspace is itself inside a git
repo. To break that coupling we drive the lifecycle by hand:

1. Build the agent with ``workspace=<workspace>``.
2. Call ``await agent.ensure_initialized()`` BEFORE invoking. This
   builds workspace artifacts and flips ``_initialized=True``, so
   future ``invoke()`` calls early-return out of ``_ensure_initialized``
   and do not re-bind the ContextVar.
3. Call ``set_cwd(<repo>)`` to overwrite the cwd field on the
   ContextVar (workspace stays put). Now ``get_cwd()`` reports
   ``<repo>``.
4. ``Runner.run_agent(...)`` — the spawned task inherits the parent
   ContextVar binding (cwd=repo, workspace=<workspace>); worktree tools
   walk up from cwd and find ``<repo>/.git`` even though workspace
   lives elsewhere.

Both directories are wiped on every entry to keep runs deterministic.
The two paths must not nest inside each other; the script aborts early
when they do, so the wipe step never touches the sibling.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

# Allow running from anywhere: the example expects ``openjiuwen`` and
# ``tests`` to be importable. Adding the repo root to ``sys.path`` here
# avoids forcing the user to ``export PYTHONPATH`` just to run the demo.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Logging — reuse the loguru config shared with examples/agent_teams/*
# so console formatting and file sinks behave identically across these
# demos. This block must run BEFORE any other ``openjiuwen.*`` import:
# many submodules log at import time via decorator-driven side effects
# (e.g. ``@register_parser`` emits ``Registered parser ...``). If
# ``configure_log`` lands after those imports, the early messages go to
# loguru's default stderr sink and the later ones go to the configured
# file sinks — producing exactly the "split log output" symptom we
# want to avoid. Sink paths inside the yaml are relative; loguru
# resolves them against the cwd at configure-time, so callers should
# run from the repo root (the shell launcher in
# ``run_single_deepagent_e2e.sh`` already does ``cd "${REPO_ROOT}"``).
from openjiuwen.core.common.logging.log_config import (  # noqa: E402
    configure_log,
    configure_log_config,
)
from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG  # noqa: E402

_HERE = Path(__file__).resolve().parent
_LOG_CONFIG_PATH = _HERE.parent.parent / "agent_teams" / "logging.yaml"

if _LOG_CONFIG_PATH.is_file():
    configure_log(str(_LOG_CONFIG_PATH))
else:
    configure_log_config(DEFAULT_INNER_LOG_CONFIG)

# Force-materialize one ``LoguruLogger`` so its ``__init__`` calls
# ``loguru.logger.remove()`` — that strips loguru's default stderr
# sink right now, before the next import-time ``logger.info`` call
# from a transitive dependency could otherwise hit it.
from openjiuwen.core.common.logging import logger  # noqa: E402
from openjiuwen.core.common.logging.manager import LogManager  # noqa: E402

LogManager.get_logger("common")

from openjiuwen.core.foundation.llm import (  # noqa: E402
    Model,
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.core.runner import Runner  # noqa: E402
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext  # noqa: E402
from openjiuwen.core.single_agent.schema.agent_card import AgentCard  # noqa: E402
from openjiuwen.core.sys_operation.cwd import get_cwd, set_cwd  # noqa: E402
from openjiuwen.harness import create_deep_agent  # noqa: E402
from openjiuwen.harness.rails.base import DeepAgentRail  # noqa: E402
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail  # noqa: E402
from openjiuwen.harness.tools.worktree import (  # noqa: E402
    WorktreeCreatedEvent,
    WorktreeEvent,
    WorktreeRail,
    WorktreeRemovedEvent,
    worktree_branch_name,
)
from openjiuwen.harness.tools.worktree.session import (  # noqa: E402
    get_current_session as get_worktree_session,
    set_current_session as set_worktree_session,
)


class _AbortAfterWriteFileRail(DeepAgentRail):
    """Force the ReAct loop to exit right after the first ``write_file``.

    Why this exists: the demo wants to simulate an external abort that
    lands *after* the agent has finished writing ``hello.py`` but
    *before* it runs/commits the file. ``ctx.request_force_finish``
    is the cooperative kill-switch the rail framework exposes — it
    lets the agent leave the loop cleanly so ``ctx.lifecycle``'s
    ``after_invoke`` hooks still fire. That matters because
    :class:`WorktreeRail` only persists the worktree session in
    ``after_invoke``; a hard ``raise`` would skip it and leave the
    Session checkpoint without a worktree pointer, defeating the
    whole point of the resume test.

    The rail only fires once (``_fired`` flips on the first
    ``write_file`` it sees) so the second invoke, which shares the
    same agent instance and therefore the same rail, does not
    re-abort during the resume flow.
    """

    priority = 50

    def __init__(self) -> None:
        super().__init__()
        self._fired = False

    @property
    def fired(self) -> bool:
        """True after the rail has aborted at least one invoke."""
        return self._fired

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Force-finish the loop once ``write_file`` has executed."""
        if self._fired:
            return
        tool_call = getattr(ctx.inputs, "tool_call", None)
        tool_name = getattr(tool_call, "name", None) or getattr(ctx.inputs, "tool_name", None)
        if tool_name != "write_file":
            return
        self._fired = True
        ctx.request_force_finish(
            {
                "output": "abort_simulated: write_file done, resuming via Session checkpoint",
                "result_type": "abort_simulated",
            },
        )
        logger.info("[abort-rail] write_file done -> requesting force_finish to simulate abort")


def _git(cwd: str, *args: str) -> None:
    """Run a git command inside ``cwd``, surfacing stderr on failure."""
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(repo_path: str) -> None:
    """Create a tiny repo with one commit and an ``origin/main`` ref.

    ``GitBackend._resolve_base`` looks up ``origin/<default-branch>`` to
    pick the base for the new worktree branch, so we have to populate
    that ref locally even though there is no real remote.
    """
    os.makedirs(repo_path, exist_ok=True)
    _git(repo_path, "init", "--quiet")
    # Repoint HEAD before the first commit so ``main`` is the initial
    # branch regardless of git version (``--initial-branch`` requires
    # git >= 2.28).
    _git(repo_path, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(repo_path, "config", "user.email", "demo@example.com")
    _git(repo_path, "config", "user.name", "Demo User")
    readme = Path(repo_path) / "README.md"
    readme.write_text("# demo repo\n", encoding="utf-8")
    _git(repo_path, "add", "README.md")
    _git(repo_path, "commit", "--quiet", "-m", "initial commit")
    _git(repo_path, "update-ref", "refs/remotes/origin/main", "HEAD")


def _build_model() -> Model:
    """Build a real ``Model`` from environment variables.

    Required: ``API_KEY``. Optional: ``API_BASE``, ``MODEL_NAME``,
    ``MODEL_PROVIDER``, ``MODEL_TIMEOUT``.
    """
    api_key = os.getenv("API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "API_KEY is required for this end-to-end demo. See examples/harness/worktree/README.md for setup steps.",
        )
    return Model(
        model_client_config=ModelClientConfig(
            client_provider=os.getenv("MODEL_PROVIDER", "OpenAI"),
            api_key=api_key,
            api_base=os.getenv("API_BASE", "https://api.openai.com/v1"),
            timeout=int(os.getenv("MODEL_TIMEOUT", "120")),
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(
            model=os.getenv("MODEL_NAME", "gpt-4.1-mini"),
            temperature=0.2,
            top_p=0.9,
        ),
    )


# Persistent demo directories — kept on disk after the run so the user
# can inspect the resulting repo + worktree. ``repo`` and ``workspace``
# are configured independently via env vars; the demo decouples them at
# runtime by manually driving ``ensure_initialized`` + ``set_cwd``.
_DEFAULT_REPO_DIR = "/Users/alan/Developer/worktree_demo/repo"
_DEFAULT_WORKSPACE_DIR = "/Users/alan/Developer/worktree_demo/wkspc"


def _is_subpath(child: Path, parent: Path) -> bool:
    """Return True when ``child`` lies inside ``parent`` (resolved)."""
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _resolve_demo_paths() -> tuple[Path, Path]:
    """Resolve the configured repo and workspace paths.

    Refuses to proceed if the two paths overlap (one being a subpath of
    the other), since the wipe-and-recreate prep step would otherwise
    delete the sibling directory's contents mid-setup.
    """
    repo = Path(os.getenv("WORKTREE_DEMO_REPO_DIR", _DEFAULT_REPO_DIR)).expanduser().resolve()
    workspace = Path(os.getenv("WORKTREE_DEMO_WORKSPACE_DIR", _DEFAULT_WORKSPACE_DIR)).expanduser().resolve()
    if repo == workspace:
        raise SystemExit(f"repo and workspace cannot point at the same path: {repo}")
    if _is_subpath(repo, workspace) or _is_subpath(workspace, repo):
        raise SystemExit(
            f"repo ({repo}) and workspace ({workspace}) must not nest inside each other for the demo's wipe step.",
        )
    return repo, workspace


def _prepare_demo_paths(repo: Path, workspace: Path) -> None:
    """Wipe + recreate both demo directories.

    The wipe is intentionally aggressive — every run starts from a
    clean slate so prior worktrees, ``origin`` refs, and stale ``.git``
    state cannot bleed across runs.
    """
    for path in (repo, workspace):
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)


async def main() -> None:
    """Drive a real LLM through the worktree round-trip."""
    repo, workspace = _resolve_demo_paths()
    _prepare_demo_paths(repo, workspace)
    _init_repo(str(repo))
    logger.info("repo:      %s", repo)
    logger.info("workspace: %s (kept on disk for inspection after the run)", workspace)

    events: list[WorktreeEvent] = []

    async def on_event(event: WorktreeEvent) -> None:
        events.append(event)
        logger.info(
            "[worktree-event] %s name=%s path=%s",
            type(event).__name__,
            event.worktree_name,
            event.worktree_path,
        )

    await Runner.start()
    try:
        slug = f"demo-{uuid.uuid4().hex[:6]}"

        abort_rail = _AbortAfterWriteFileRail()

        agent = create_deep_agent(
            model=_build_model(),
            card=AgentCard(
                name="worktree_demo",
                description="single deepagent + worktree e2e",
            ),
            workspace=str(workspace),
            language="cn",
            max_iterations=20,
            # SysOperationRail injects read_file / write_file /
            # edit_file / glob / list_dir / grep / bash so the agent
            # has the full coding toolset routed through the local
            # SysOperation backend. WorktreeRail then layers
            # enter_worktree / exit_worktree on top — it owns the
            # WorktreeManager internally, so the demo no longer has
            # to construct and pass it by hand. The abort rail is
            # what makes Run 1 stop after write_file.
            rails=[
                SysOperationRail(),
                WorktreeRail(event_handler=on_event),
                abort_rail,
            ],
            # DirectoryBuilder would otherwise drop a tree of
            # IDENTITY.md / AGENT.md / SOUL.md / memory/ / skills/ ...
            # at the workspace root and bury the worktree we want to
            # showcase. The demo only exercises the worktree path, so
            # skip the standard workspace scaffolding.
            auto_create_workspace=False,
            system_prompt=(
                "你是一个编程助手。请严格按用户给出的步骤、依次调用工具完成任务，"
                "每一步都必须用工具落地，不要凭空假设结果。"
            ),
        )

        # Run lazy init now (in *this* task, before any invoke). This
        # binds the cwd ContextVar to the workspace and flips
        # ``agent._initialized``, which makes the next call to
        # ``invoke()`` early-return out of ``_ensure_initialized``
        # instead of clobbering whatever cwd we set below.
        await agent.ensure_initialized()

        # Now repoint cwd at the repo so worktree tools can locate
        # ``.git`` by walking up from cwd. The workspace ContextVar
        # field is left untouched, so artifact-aware tools (bash's
        # workspace fallback, todo / memory rails) still see the
        # configured workspace path.
        set_cwd(str(repo))

        # Fixed session_id glues Run 1 and Run 2 together. The
        # default ``InMemoryCheckpointer`` keys its agent state
        # blobs by session_id, so reusing the same string is what
        # makes the worktree session ride through the abort/resume
        # cycle. The Runner builds a fresh ``Session`` object for
        # each invoke, but ``pre_agent_execute`` restores its state
        # from the checkpoint before any rail runs.
        session_id = f"worktree-demo-{slug}"
        wt_branch = worktree_branch_name(slug)

        # ── Run 1: enter + write, then forcibly bail out ──────
        query_run1 = (
            "请按下面的步骤依次执行，每一步都必须调用对应的工具：\n"
            f'1. 调用 enter_worktree，参数 name="{slug}"，'
            f"进入一个隔离的 git worktree（创建在分支 {wt_branch} 上）；\n"
            "2. 调用 write_file，在当前工作目录下创建 hello.py，"
            '文件内容为一行：print("hello from worktree")\n'
            "完成后简短确认即可。"
        )
        logger.info("=== Run 1 query (enter + write, then abort) ===\n%s", query_run1)
        result_run1 = await Runner.run_agent(
            agent,
            {"query": query_run1},
            session=session_id,
        )
        logger.info("Run 1 result_type=%s", result_run1.get("result_type"))
        logger.info(
            "Run 1 output: %s",
            str(result_run1.get("output", ""))[:500],
        )

        # Sanity check: the abort rail really fired. If not, the
        # agent finished step 2 *naturally* and the resume test
        # below would be meaningless — fail loudly.
        assert abort_rail.fired, "abort rail never fired; write_file was not observed in Run 1"
        assert result_run1.get("result_type") == "abort_simulated", (
            f"expected abort_simulated result, got {result_run1.get('result_type')!r}"
        )

        # Post-Run-1 disk state should already reflect step 1+2.
        created = [e for e in events if isinstance(e, WorktreeCreatedEvent)]
        assert len(created) == 1, f"expected 1 created event after Run 1, got {len(created)}"
        assert created[0].worktree_name == slug, f"unexpected worktree name: {created[0].worktree_name}"

        worktree_path = Path(created[0].worktree_path)
        assert worktree_path.is_dir(), f"worktree dir missing after Run 1: {worktree_path}"

        hello = worktree_path / "hello.py"
        assert hello.is_file(), f"hello.py not created in Run 1: {hello}"
        hello_contents = hello.read_text(encoding="utf-8")
        assert "hello from worktree" in hello_contents

        # ── Simulate process restart ─────────────────────────
        # Wipe the worktree-session and cwd ContextVars on main's
        # task so Run 2 cannot ride on leftover process state.
        # Once these are gone, the only path that lets Run 2's
        # tools find the worktree is ``WorktreeRail.before_invoke``
        # rehydrating from ``Session.state``. That is exactly the
        # interrupt/resume contract the demo is meant to prove.
        set_worktree_session(None)
        set_cwd(str(repo))
        assert get_worktree_session() is None, "ContextVar wipe failed; resume test would be meaningless"
        logger.info("=== Simulated restart: worktree ContextVar cleared, cwd=%s ===", get_cwd())

        # ── Run 2: same session_id, finish the workflow ──────
        query_run2 = (
            "上一轮你已经进入了 git worktree 并创建了 hello.py，"
            "现在请继续把剩下的步骤做完，每一步都必须调用对应的工具：\n"
            "1. 调用 bash，执行命令 `python hello.py`，"
            "确认 stdout 包含 hello from worktree；\n"
            "2. 调用 bash，执行命令 "
            '`git add hello.py && git commit -m "feat: add hello.py from worktree"`，'
            f"把这次变更提交到 {wt_branch} 分支上；\n"
            '3. 调用 exit_worktree，参数 action="keep"，'
            "保留 worktree 以便复查；\n"
            "4. 调用 bash，执行命令 "
            f'`git merge --no-ff -m "merge {wt_branch} into main" {wt_branch}`，'
            "把 worktree 分支合入当前的 main 分支；\n"
            "5. 用一句中文总结你完成的事情。"
        )
        logger.info("=== Run 2 query (resume + finish) ===\n%s", query_run2)
        result_run2 = await Runner.run_agent(
            agent,
            {"query": query_run2},
            session=session_id,
        )
        logger.info("Run 2 result_type=%s", result_run2.get("result_type"))
        logger.info(
            "Run 2 output: %s",
            str(result_run2.get("output", ""))[:500],
        )

        # Resume must not re-create the worktree — ``enter_worktree``
        # should not have fired again. Exactly one created event
        # for the whole demo.
        created = [e for e in events if isinstance(e, WorktreeCreatedEvent)]
        assert len(created) == 1, f"worktree was re-created across resume: {len(created)} events"

        # ``ExitWorktreeTool`` restores cwd to the session's
        # ``original_cwd`` (= cwd at enter time, which is ``repo``
        # because we set_cwd(repo) before invoke). ``/var`` ↔
        # ``/private/var`` on macOS, so compare canonical paths.
        assert os.path.realpath(get_cwd()) == os.path.realpath(repo), f"cwd not restored: {get_cwd()!r} vs {repo!r}"

        # We asked the agent to keep the worktree, so no remove
        # event should have fired.
        removed = [e for e in events if isinstance(e, WorktreeRemovedEvent)]
        assert not removed, f"unexpected remove events: {removed}"

        # ── post-merge verifications ──────────────────────────
        # The agent committed ``hello.py`` on the worktree branch
        # then merged the branch into main. Verify the file now
        # exists on main with matching content, and that the merge
        # commit (or fast-forwarded commit) actually landed.
        repo_hello = repo / "hello.py"
        assert repo_hello.is_file(), (
            f"hello.py did not land in the main repo after merge: {repo_hello}. Did the agent skip the merge step?"
        )
        assert repo_hello.read_text(encoding="utf-8") == hello_contents, (
            "hello.py content differs between worktree and merged main"
        )

        # Check git log on main shows at least one commit beyond the
        # initial commit — i.e. the merged change is recorded.
        log_proc = subprocess.run(
            ["git", "log", "--oneline", "main"],
            cwd=str(repo),
            check=True,
            capture_output=True,
            text=True,
        )
        commit_count = len([line for line in log_proc.stdout.splitlines() if line.strip()])
        assert commit_count >= 2, (
            f"expected at least 2 commits on main after merge, got {commit_count}:\n{log_proc.stdout}"
        )

        logger.info("✓ end-to-end verification passed (abort + resume)")
        logger.info("  main repo  : %s", repo)
        logger.info("  workspace  : %s", workspace)
        logger.info("  worktree   : %s", worktree_path)
        logger.info("  wt branch  : %s", wt_branch)
        logger.info("  session_id : %s", session_id)
        logger.info("  hello.py   : %s (worktree)", hello)
        logger.info("             : %s (merged into main)", repo_hello)
        logger.info("  main log   :\n%s", log_proc.stdout.rstrip())
    finally:
        await Runner.stop()


if __name__ == "__main__":
    asyncio.run(main())
