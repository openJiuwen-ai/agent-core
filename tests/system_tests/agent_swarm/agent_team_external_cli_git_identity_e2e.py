# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""External harness members commit under the platform-resolved Git identity.

A platform ``BuildContext`` resolves each member's execution environment from
the team database's session settings. Native members apply it per tool call;
an external harness member (Claude Code / Codex) receives it in its provider
process environment on every start. This script drives real CLIs and checks
the resulting commit, not the CLI's own report:

* ``leader``: the leader runs on the external CLI and commits itself.
* ``teammate``: a Claude Code leader spawns an external teammate ``dev-1``
  (``spawn_teammate(cli_agent=...)``) which commits.

Each case runs with the identity switch on and off. The host process exports
``GIT_AUTHOR_*`` / ``GIT_COMMITTER_*`` = ``host``; with the switch on the commit
must carry the member's name, with it off it must keep ``host``.

The CLIs run on their own login; no team model is needed. Set ``CODEX_MODEL``
when the Codex login cannot use the CLI's configured default model. The
environment needs the ``claude`` / ``codex`` extras (``uv sync --extra claude
--extra codex``).

Run manually:
    source .venv/bin/activate && export PYTHONPATH=.:$PYTHONPATH
    python tests/system_tests/agent_swarm/agent_team_external_cli_git_identity_e2e.py \
        [--cli claude|codex] [--role leader|teammate|all] [--switch on|off|all]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.agent_teams.rails.team_context import get_team_backend
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.build_context import BuildContext
from openjiuwen.agent_teams.tools.database import TeamDatabase
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.sys_operation.execution_environment import ExecutionEnvironment

_HOST_IDENTITY = {
    "GIT_AUTHOR_NAME": "host",
    "GIT_AUTHOR_EMAIL": "host@example.test",
    "GIT_COMMITTER_NAME": "host",
    "GIT_COMMITTER_EMAIL": "host@example.test",
}
_TEAMMATE = "dev-1"
_RUN_TIMEOUT_S = 900.0


@dataclass
class _IdentityBuildContext(BuildContext):
    """Minimal platform policy: a session switch turns on per-member Git identities."""

    session_id: str = ""

    async def resolve_execution_environment(self) -> ExecutionEnvironment:
        backend = get_team_backend(self)
        if backend is None:
            raise RuntimeError("member team backend was not injected")
        record = await backend.db.session_settings.get(self.session_id)
        if record is None or not record["settings"].get("independent_git_identity"):
            return ExecutionEnvironment()
        name = self.member_name
        email = f"{name}@example.test"
        return ExecutionEnvironment(overrides={
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
        })


def _cli_entry(kind: str, repo: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {"cli_agent": kind, "cwd": str(repo)}
    codex_model = os.environ.get("CODEX_MODEL")
    if kind == "codex" and codex_model:
        entry["model_config"] = {"model": codex_model}
    return entry


def _last_commit(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%an|%ae|%cn|%ce|%s"],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip()


def _query(role: str, cli: str, command: str) -> str:
    if role == "leader":
        return (
            "Do not build or plan a team. Use your shell tool exactly once to run:\n"
            f"{command}\n"
            "Do not set or change any git config or environment variables. Then reply DONE."
        )
    return (
        f"Call build_team, then spawn_teammate with member_name='{_TEAMMATE}' and cli_agent='{cli}' "
        f"(no other external_cli fields). Then send_message to {_TEAMMATE} with exactly this instruction: "
        "'Use your shell tool exactly once to run the following command, without setting or changing any git "
        f"config or environment variables, then reply DONE to team_leader: {command}'. "
        f"Do NOT run the git command yourself and do not create tasks. Wait for {_TEAMMATE} to reply, then stop."
    )


async def _run_case(cli: str, role: str, enabled: bool) -> bool:
    repo = Path(tempfile.mkdtemp(prefix="e2e-git-identity-"))
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    session_id = f"e2e_git_identity_{uuid.uuid4().hex[:8]}"
    team_name = f"e2e_git_identity_{role}_{cli}_{uuid.uuid4().hex[:6]}"
    subject = f"e2e-identity-{uuid.uuid4().hex[:6]}"
    command = f"git -C {repo} -c commit.gpgsign=false commit --allow-empty -m {subject}"
    leader_cli = cli if role == "leader" else "claude"
    committer = "team_leader" if role == "leader" else _TEAMMATE

    spec = TeamAgentSpec.model_validate({
        "team_name": team_name,
        "spawn_mode": "inprocess",
        "language": "en",
        "agents": {"leader": {"system_prompt": "Follow the user's instructions exactly."}},
        "leader": {"cli_agent": leader_cli},
        "external_cli_agents": [_cli_entry(kind, repo) for kind in sorted({cli, leader_cli})],
        "storage": {"type": "sqlite"},
        "transport": {"type": "inprocess"},
    })
    spec.build_context = _IdentityBuildContext(session_id=session_id)

    database = TeamDatabase(spec.resolve_db_config())
    await database.initialize()
    try:
        await database.session_settings.initialize(session_id, {"independent_git_identity": enabled})
    finally:
        await database.close()

    print(f"[case] cli={cli} role={role} switch={'on' if enabled else 'off'} repo={repo}", flush=True)
    try:
        async def drive() -> None:
            stream = Runner.run_agent_team_streaming(
                agent_team=spec, inputs={"query": _query(role, cli, command)}, session=session_id,
            )
            async with contextlib.aclosing(stream):
                async for chunk in stream:
                    payload = getattr(chunk, "payload", None)
                    settled = isinstance(payload, dict) and payload.get("event_type") in {"team.idle", "team.completed"}
                    if subject in _last_commit(repo) or (role == "leader" and settled):
                        return

        try:
            await asyncio.wait_for(drive(), timeout=_RUN_TIMEOUT_S)
        except asyncio.TimeoutError:
            print("[warn] the stream did not settle before the timeout", flush=True)
    finally:
        try:
            await Runner.delete_agent_team(team_name=team_name, session_ids=[session_id], force=True)
        except Exception as exc:  # noqa: BLE001 - best-effort teardown
            print(f"[warn] cleanup failed for team {team_name}: {exc!r}", flush=True)

    identity = f"{committer}|{committer}@example.test" if enabled else "host|host@example.test"
    expected = f"{identity}|{identity}|{subject}"
    record = _last_commit(repo)
    ok = record == expected
    print(f"[{'ok' if ok else 'FAIL'}] git log: {record!r} expected: {expected!r}", flush=True)
    shutil.rmtree(repo, ignore_errors=True)
    return ok


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cli", choices=("claude", "codex"), default="claude")
    parser.add_argument("--role", choices=("leader", "teammate", "all"), default="all")
    parser.add_argument("--switch", choices=("on", "off", "all"), default="all")
    args = parser.parse_args(argv)
    roles = ("leader", "teammate") if args.role == "all" else (args.role,)
    switches = (True, False) if args.switch == "all" else (args.switch == "on",)

    # The CLI processes inherit this; only the member policy may override it.
    os.environ.update(_HOST_IDENTITY)
    results: list[bool] = []
    await Runner.start()
    try:
        for role in roles:
            for enabled in switches:
                results.append(await _run_case(args.cli, role, enabled))
    finally:
        await Runner.stop()
    passed = all(results)
    print("-" * 70)
    print(f"RESULT: {'PASS' if passed else 'FAIL'} ({sum(results)}/{len(results)})")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
