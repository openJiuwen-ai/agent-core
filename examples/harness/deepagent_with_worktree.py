# coding: utf-8

"""DeepAgent + worktree minimal demo.

Shows that the worktree tools — previously gated behind the team
framework — are now ordinary harness tools that any single agent can
mount on its tool list. The demo runs without an LLM: it constructs the
tool instances, sets the cwd / workspace context the same way a real
``DeepAgent`` would, and drives ``EnterWorktreeTool`` /
``ExitWorktreeTool`` directly to prove the round-trip works against a
real git repository.

Run it from the project root:

    source .venv/bin/activate
    export PYTHONPATH=.:$PYTHONPATH
    python examples/harness/deepagent_with_worktree.py
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile

from tests.test_logger import logger
from openjiuwen.core.sys_operation.cwd import get_cwd, init_cwd
from openjiuwen.harness.tools.worktree import (
    EnterWorktreeTool,
    ExitWorktreeTool,
    WorktreeConfig,
    WorktreeCreatedEvent,
    WorktreeEvent,
    WorktreeManager,
    WorktreeRemovedEvent,
)


def _git(cwd: str, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(path: str) -> None:
    """Create a tiny repo so worktree creation has a real ``origin/main``."""
    os.makedirs(path, exist_ok=True)
    _git(path, "init", "--quiet", "--initial-branch=main")
    _git(path, "config", "user.email", "demo@example.com")
    _git(path, "config", "user.name", "Demo User")
    readme = os.path.join(path, "README.md")
    with open(readme, "w") as f:
        f.write("# demo\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "--quiet", "-m", "initial commit")
    _git(path, "update-ref", "refs/remotes/origin/main", "HEAD")


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dg-wt-demo-") as tmp:
        repo = os.path.join(tmp, "repo")
        workspace = os.path.join(tmp, "workspace")
        os.makedirs(workspace, exist_ok=True)
        _init_repo(repo)

        # The harness ``DeepAgent`` calls ``init_cwd`` internally; here we
        # do it by hand so the worktree manager can resolve the workspace
        # for ``.worktrees/<slug>`` placement and the cwd for the canonical
        # git root lookup.
        os.chdir(repo)
        init_cwd(repo, workspace=workspace)

        events: list[WorktreeEvent] = []

        async def on_event(event: WorktreeEvent) -> None:
            events.append(event)
            kind = type(event).__name__
            logger.info(
                "[worktree-event] %s name=%s path=%s owner=%s",
                kind,
                event.worktree_name,
                event.worktree_path,
                event.owner_id,
            )

        manager = WorktreeManager(
            WorktreeConfig(enabled=True),
            event_handler=on_event,
        )

        # Mount the two tools just like a DeepAgent would:
        #   create_deep_agent(model=..., tools=[EnterWorktreeTool(mgr), ExitWorktreeTool(mgr)])
        enter_tool = EnterWorktreeTool(manager, language="en")
        exit_tool = ExitWorktreeTool(manager, language="en")
        logger.info(
            "Tool cards mounted: %s / %s",
            enter_tool.card.name,
            exit_tool.card.name,
        )

        # 1) Enter a fresh worktree. The single-agent demo does not need
        #    team-style owner identifiers, so we leave them unset; the
        #    handler will still receive a WorktreeCreatedEvent.
        enter_result = await enter_tool.invoke({"name": "demo-feature"})
        if not enter_result.success:
            logger.error("enter_worktree failed: %s", enter_result.error)
            return
        logger.info("enter_worktree -> %s", enter_result.data["message"])
        logger.info("cwd is now: %s", get_cwd())
        assert get_cwd().startswith(enter_result.data["worktree_path"])

        # 2) Make a change so the exit tool exercises the discard path.
        with open(os.path.join(get_cwd(), "feature.txt"), "w") as f:
            f.write("work in progress\n")

        # 3) Exit and remove the worktree (discard the in-flight change).
        exit_result = await exit_tool.invoke(
            {"action": "remove", "discard_changes": True},
        )
        if not exit_result.success:
            logger.error("exit_worktree failed: %s", exit_result.error)
            return
        logger.info("exit_worktree -> %s", exit_result.data["message"])

        # 4) Sanity check: the events fired, the worktree is gone, and
        #    cwd has been restored.
        assert isinstance(events[0], WorktreeCreatedEvent)
        assert isinstance(events[1], WorktreeRemovedEvent)
        assert not os.path.isdir(enter_result.data["worktree_path"])
        # ``/var`` is a symlink to ``/private/var`` on macOS; compare
        # canonical paths so the demo doesn't trip on tmp-dir aliasing.
        assert os.path.realpath(get_cwd()) == os.path.realpath(repo)
        logger.info("demo completed cleanly: %d events, cwd back to %s", len(events), get_cwd())


if __name__ == "__main__":
    asyncio.run(main())
