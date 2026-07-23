# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Codex SDK Team E2E: four external Codex members write files.

Each member owns one long-lived ``AsyncCodex`` client and one isolated thread.
The SDK manages its app-server transport internally. The team uses local PyZMQ
for external MCP traffic, so this test does not require the Team Event Gateway
WebSocket.

Prerequisites:
    * ``openai-codex`` is installed and Codex is authenticated.
    * ``API_BASE``, ``MODEL_NAME`` and ``LEADER_API_KEY`` (or ``API_KEY``)
      configure the leader model.

Run from the repository root::

    PYTHONPATH="$PWD" ../../swarm/jiuwenswarm/bin/python \
      tests/system_tests/agent_swarm/agent_team_codex_cli_e2e.py
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

from _e2e_utils import consume_stream

from openjiuwen.agent_teams.paths import team_home
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.core.common.logging import LazyLogger
from openjiuwen.core.common.logging.log_config import configure_log, configure_log_config
from openjiuwen.core.common.logging.loguru.constant import DEFAULT_INNER_LOG_CONFIG
from openjiuwen.core.common.logging.manager import LogManager
from openjiuwen.core.runner.runner import Runner

_LOG_CONFIG_PATH = _HERE / "logging.yaml"
if _LOG_CONFIG_PATH.is_file():
    configure_log(str(_LOG_CONFIG_PATH))
else:
    configure_log_config(DEFAULT_INNER_LOG_CONFIG)

logger = LazyLogger(lambda: LogManager.get_logger("codex_cli_e2e"))
os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")

_MEMBERS = ("codex-1", "codex-2", "codex-3", "codex-4")
_SESSION_ID = "codex_cli_session"
_RUN_TIMEOUT_S = 1200.0
_MCP_SERVER_COMMAND = [sys.executable, "-m", "openjiuwen.agent_teams.mcp"]


def _leader_api_key() -> str | None:
    return os.environ.get("LEADER_API_KEY") or os.environ.get("API_KEY")


def _local_tcp_address(env_name: str, default_port: int) -> str:
    raw_port = os.environ.get(env_name, str(default_port)).strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError(f"{env_name} must be an integer TCP port, got {raw_port!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{env_name} must be between 1 and 65535, got {port}")
    return f"tcp://127.0.0.1:{port}"


def _leader_model() -> dict[str, Any]:
    return {
        "model_client_config": {
            "client_provider": "OpenAI",
            "api_base": os.environ["API_BASE"],
            "api_key": _leader_api_key(),
            "timeout": 120,
            "verify_ssl": False,
        },
        "model_request_config": {
            "model_name": os.environ["MODEL_NAME"],
            "temperature": 0.2,
        },
    }


def _build_spec(team_name: str, workspace_path: Path) -> TeamAgentSpec:
    publish_addr = _local_tcp_address("CODEX_CLI_E2E_PUBLISH_PORT", 15556)
    subscribe_addr = _local_tcp_address("CODEX_CLI_E2E_SUBSCRIBE_PORT", 15557)
    cfg: dict[str, Any] = {
        "team_name": team_name,
        "lifecycle": "temporary",
        "teammate_mode": "build_mode",
        "spawn_mode": "inprocess",
        "language": "cn",
        "leader": {
            "member_name": "team_leader",
            "display_name": "TeamLeader",
            "persona": "技术项目经理，负责分派任务并验证四个 Codex 成员的产出",
        },
        "agents": {
            "leader": {
                "model": _leader_model(),
                "rails": [{"type": "core.sys_operation"}],
                "language": "cn",
                "max_iterations": 200,
                "enable_task_planning": False,
                "workspace": {"stable_base": True},
            },
        },
        "workspace": {"enabled": True, "version_control": True},
        "transport": {
            "type": "pyzmq",
            "params": {
                "team_name": team_name,
                "node_id": "team_leader",
                "direct_addr": _local_tcp_address("CODEX_CLI_E2E_DIRECT_PORT", 15555),
                "pubsub_publish_addr": publish_addr,
                "pubsub_subscribe_addr": subscribe_addr,
                "metadata": {"pubsub_bind": True},
            },
        },
        "external_transport": {
            "type": "pyzmq",
            "params": {
                "team_name": team_name,
                "node_id": "external_cli",
                "direct_addr": "tcp://127.0.0.1:*",
                "pubsub_publish_addr": publish_addr,
                "pubsub_subscribe_addr": subscribe_addr,
                "request_timeout": 30.0,
            },
        },
        "storage": {"type": "sqlite"},
        "external_cli_agents": [
            {
                "cli_agent": "codex",
                "cwd": str(workspace_path),
                "inject_mcp": True,
                "mcp_server_command": _MCP_SERVER_COMMAND,
            },
        ],
    }
    return TeamAgentSpec.model_validate(cfg)


def _god_view_query(workspace_path: Path) -> str:
    roster = "\n".join(f"   - {name}：写入 {name}.md" for name in _MEMBERS)
    return (
        "请组建一个 4 人临时团队，完成 Codex CLI 协同写文件验证，全程自主推进：\n\n"
        "1. 调用 build_team 建立团队。\n"
        "2. 用一次 create_task 批量创建 4 个任务，每个任务要求成员："
        "claim_task(claimed) 认领，写文件，claim_task(completed) 完成，"
        "再用 send_message 向 leader 汇报。\n"
        f"   共享工作目录：{workspace_path}\n"
        "   文件内容固定为一行：<member> reporting in.\n"
        "3. 调用 spawn_external_cli 创建以下成员，cli_agent 都必须是 codex：\n"
        f"{roster}\n"
        "4. 将任务分配给对应成员，并用 send_message 下发任务。\n"
        "5. 用 view_task 跟踪，只有四个任务都 completed 才继续。\n"
        "6. 确认四个文件都存在后，对四个成员逐一调用 "
        "shutdown_member(member_name=<member>, force=true)，不要发 shutdown 提示词。\n"
        "7. 全部关停成功后调用 clean_team，最后简要汇报。\n"
    )


def _expected_files(workspace_path: Path) -> list[Path]:
    return [workspace_path / f"{member}.md" for member in _MEMBERS]


async def _run() -> int:
    missing_env = [name for name in ("API_BASE", "MODEL_NAME") if not os.environ.get(name)]
    if _leader_api_key() is None:
        missing_env.append("LEADER_API_KEY|API_KEY")
    if missing_env:
        print(f"[skip] set required env first: {', '.join(missing_env)}")
        return 1

    team_name = f"codex_cli_team_{uuid.uuid4().hex[:8]}"
    workspace_path = team_home(team_name) / "team-workspace"
    workspace_path.mkdir(parents=True, exist_ok=True)
    spec = _build_spec(team_name, workspace_path)

    print(f"team={team_name}")
    print(f"workspace={workspace_path}")
    print("transport=local PyZMQ; Gateway not required")
    print("codex=one AsyncCodex client and one independent thread per member")

    await Runner.start()
    try:
        await asyncio.wait_for(
            consume_stream(spec, _god_view_query(workspace_path), _SESSION_ID),
            timeout=_RUN_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.error("run exceeded {}s budget; checking partial output", _RUN_TIMEOUT_S)
    finally:
        present = [path for path in _expected_files(workspace_path) if path.is_file()]
        missing_files = [path for path in _expected_files(workspace_path) if not path.is_file()]
        for path in present:
            print(f"[ok]      {path}")
        for path in missing_files:
            print(f"[MISSING] {path}")
        try:
            await Runner.delete_agent_team(team_name=team_name, session_ids=[_SESSION_ID], force=True)
        except BaseException as exc:  # noqa: BLE001 - best-effort teardown
            logger.warning("cleanup failed for team {}: {}", team_name, exc)
        await Runner.stop()

    print(f"RESULT: {'PASS' if not missing_files else 'FAIL'} — {len(present)}/{len(_MEMBERS)} files")
    return 0 if not missing_files else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
