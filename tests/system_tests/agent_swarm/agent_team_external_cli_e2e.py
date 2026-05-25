# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""External-CLI team E2E: 4 third-party CLI members write files, leader verifies.

Scenario (one autonomous leader round):

* The leader spawns a 4-member temporary team of external CLI agents —
  two ``claude`` (claudecode) and two ``codex`` — via
  ``spawn_member(role_type='external_cli', cli_agent=...)``.
* Each member is a real third-party CLI subprocess. The spawn path
  auto-injects the team MCP server (``openjiuwen-team-mcp``) so the CLI
  gets the team collaboration tools (read_inbox / claim_task /
  complete_task / send_message), and launches it with ``cwd`` set to the
  shared team workspace.
* The leader creates one task per member: write a file
  ``<member>.md`` into the team workspace, then complete the task and
  report. Members do the file write with their native filesystem ability.
* The leader confirms the four files exist, then ``clean_team`` disbands
  the temporary team.

This is a system test: it launches real ``claude`` / ``codex`` binaries and
needs a real leader LLM endpoint, so it is never run in CI. Run it manually
after exporting the required environment.

Prerequisites:
    * ``claude`` and ``codex`` on PATH, each already authenticated locally.
    * ``openjiuwen-team-mcp`` on PATH (installed with this package, e.g.
      ``uv sync`` / ``pip install -e .`` exposes the console script).
    * Leader LLM endpoint via env:
        API_BASE, LEADER_API_KEY, MODEL_NAME

Run:
    source .venv/bin/activate && export PYTHONPATH=.:$PYTHONPATH
    python tests/system_tests/agent_swarm/agent_team_external_cli_e2e.py

Exit code is 0 when all four files are present, 1 otherwise.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from openjiuwen.agent_teams.paths import team_home
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.core.common.logging import LazyLogger
from openjiuwen.core.common.logging.log_config import (
    configure_log,
    configure_log_config,
)
from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG
from openjiuwen.core.common.logging.manager import LogManager
from openjiuwen.core.runner.runner import Runner

from _e2e_utils import consume_stream

_LOG_CONFIG_PATH = _HERE / "logging.yaml"

if _LOG_CONFIG_PATH.is_file():
    configure_log(str(_LOG_CONFIG_PATH))
else:
    configure_log_config(DEFAULT_INNER_LOG_CONFIG)

logger = LazyLogger(lambda: LogManager.get_logger("external_cli_e2e"))

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")

# Required leader LLM endpoint env. The external CLI members carry their own
# credentials (claude / codex are authenticated locally), so only the leader
# needs an endpoint here.
_REQUIRED_ENV = ("API_BASE", "LEADER_API_KEY", "MODEL_NAME")

# (member_name, cli_agent) roster: two claude + two codex.
_MEMBERS: tuple[tuple[str, str], ...] = (
    ("claude-1", "claude"),
    ("claude-2", "claude"),
    ("codex-1", "codex"),
    ("codex-2", "codex"),
)

_SESSION_ID = "external_cli_session"


def _leader_model() -> dict[str, Any]:
    """Build the leader TeamModelConfig dict from the environment."""
    return {
        "model_client_config": {
            "client_provider": "OpenAI",
            "api_base": os.environ["API_BASE"],
            "api_key": os.environ["LEADER_API_KEY"],
            "timeout": 120,
            "verify_ssl": False,
        },
        "model_request_config": {
            "model_name": os.environ["MODEL_NAME"],
            "temperature": 0.2,
        },
    }


def _build_spec(team_name: str, workspace_path: Path) -> TeamAgentSpec:
    """Assemble the team spec for the external-CLI scenario.

    External CLI members run in separate processes, so the team uses a
    cross-process ``pyzmq`` messager (the leader binds the pub/sub broker;
    each member's MCP server connects with its own node id) and a
    file-backed sqlite db. ``external_cli_agents`` statically declares the
    launch config for each CLI kind — the leader's ``spawn_member`` call
    only references it by name.
    """
    cfg: dict[str, Any] = {
        "team_name": team_name,
        "lifecycle": "temporary",
        "teammate_mode": "build_mode",
        "spawn_mode": "inprocess",
        "language": "cn",
        "leader": {
            "member_name": "team_leader",
            "display_name": "TeamLeader",
            "persona": "资深技术项目经理，负责拉起外部 CLI 协作者、分派写文件任务并校验产出",
        },
        "agents": {
            "leader": {
                "model": _leader_model(),
                "rails": [{"type": "filesystem"}],
                "language": "cn",
                "max_iterations": 200,
                "enable_task_planning": False,
                "workspace": {"stable_base": True},
            },
        },
        "workspace": {
            "enabled": True,
            "version_control": True,
        },
        "transport": {
            "type": "pyzmq",
            "params": {
                "team_name": team_name,
                "node_id": "team_leader",
                "direct_addr": "tcp://127.0.0.1:15555",
                "pubsub_publish_addr": "tcp://127.0.0.1:15556",
                "pubsub_subscribe_addr": "tcp://127.0.0.1:15557",
                "metadata": {"pubsub_bind": True},
            },
        },
        "storage": {"type": "sqlite"},
        "external_cli_agents": [
            {"cli_agent": "claude", "cwd": str(workspace_path), "inject_mcp": True},
            {"cli_agent": "codex", "cwd": str(workspace_path), "inject_mcp": True},
        ],
    }
    return TeamAgentSpec.model_validate(cfg)


def _god_view_query(workspace_path: Path) -> str:
    """Build the leader's seed instruction for the whole scenario."""
    roster_lines = "\n".join(
        f"   - 成员 {name}：cli_agent='{cli}'，写文件 {name}.md" for name, cli in _MEMBERS
    )
    return (
        "请组建一个 4 人临时团队，完成一次「外部 CLI 协同写文件」验证，全程自主推进：\n\n"
        "1. 用 spawn_member 拉起 4 个外部 CLI 成员（role_type='external_cli'）：\n"
        f"{roster_lines}\n"
        "   每个成员的 desc 说明它是负责写文件的外部 CLI 协作者。\n\n"
        "2. 用 create_task 为每个成员各建一个任务，要求成员在团队共享工作目录里写入一个文件：\n"
        f"   - 共享工作目录绝对路径：{workspace_path}\n"
        "   - 文件名见上表（如 claude-1.md），文件内容写一行：'<member_name> reporting in.'\n"
        "   - 任务内容里明确告诉成员：写完后用 complete_task 标记完成，并用 send_message 向 team_leader 汇报。\n\n"
        "3. 把任务分派给对应成员（send_message 会自动启动未启动的成员）。\n\n"
        "4. 等四个成员都汇报完成后，逐一确认这 4 个文件都已真实存在"
        "（通过本地文件系统读取共享工作目录）。\n\n"
        "5. 全部确认存在后，调用 clean_team 解散这个临时团队，并简要汇报结果。\n"
    )


def _expected_files(workspace_path: Path) -> list[Path]:
    """Return the absolute paths the members are expected to create."""
    return [workspace_path / f"{name}.md" for name, _cli in _MEMBERS]


async def _run() -> int:
    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        logger.error("missing required env: {}", ", ".join(missing))
        print(f"[skip] set the required env first: {', '.join(missing)}")
        return 1

    team_name = f"external_cli_team_{uuid.uuid4().hex[:8]}"
    workspace_path = team_home(team_name) / "team-workspace"
    spec = _build_spec(team_name, workspace_path)

    logger.info("team={} workspace={}", team_name, workspace_path)
    print("=" * 70)
    print(f"External-CLI team E2E — team={team_name}")
    print(f"workspace={workspace_path}")
    print("members: " + ", ".join(f"{n}({c})" for n, c in _MEMBERS))
    print("=" * 70)

    await Runner.start()
    try:
        await consume_stream(spec, _god_view_query(workspace_path), _SESSION_ID)
    finally:
        present = [p for p in _expected_files(workspace_path) if p.is_file()]
        missing_files = [p for p in _expected_files(workspace_path) if not p.is_file()]
        for p in present:
            print(f"[ok]      {p}")
        for p in missing_files:
            print(f"[MISSING] {p}")

        try:
            await Runner.delete_agent_team(team_name=team_name, session_ids=[_SESSION_ID], force=True)
        except BaseException as exc:  # noqa: BLE001 - best-effort teardown
            logger.warning("cleanup failed for team {}: {}", team_name, exc)
        await Runner.stop()

    ok = not missing_files
    print("-" * 70)
    print(f"RESULT: {'PASS' if ok else 'FAIL'} — {len(present)}/{len(_MEMBERS)} files present")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
