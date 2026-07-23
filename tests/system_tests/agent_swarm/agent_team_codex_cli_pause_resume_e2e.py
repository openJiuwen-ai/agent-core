# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Codex SDK external-member pause/resume E2E.

This script mirrors ``agent_team_external_cli_pause_resume_e2e.py`` but uses
the Codex Python SDK backend exclusively:

* Run 1 starts a persistent team and spawns one ``codex`` external member.
* The leader sends a random session marker to that member only through a direct
  message. The member acknowledges the message and completes a first file task
  without writing or reporting the marker.
* The first run ends without shutting down the member or cleaning the team, so
  the persistent team enters its pause/recovery lifecycle.
* Run 2 resumes the same ``team_name`` and Jiuwen ``session_id``. Its prompt
  never contains the marker. The resumed Codex member must recall the marker
  from its original Codex thread and write it to ``after_resume.md``.

The test passes only when ``before_pause.md`` contains the fixed acknowledgement
phrase and ``after_resume.md`` contains the exact first-run marker.

Run manually from the repository root::

    PYTHONPATH="$PWD" .venv/bin/python \
      tests/system_tests/agent_swarm/agent_team_codex_cli_pause_resume_e2e.py
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

logger = LazyLogger(lambda: LogManager.get_logger("codex_cli_pause_resume_e2e"))

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")

_REQUIRED_ENV = ("API_BASE", "MODEL_NAME")
_SESSION_ID = "codex_cli_pause_resume_session"
_MEMBER_NAME = "codex-1"
_BEFORE_TASK_ID = "before-pause-task"
_AFTER_TASK_ID = "after-resume-task"
_ACK_PHRASE = "Codex会话暗号已接收"
_RUN_TIMEOUT_S = 1200.0
_MCP_SERVER_COMMAND = [sys.executable, "-m", "openjiuwen.agent_teams.mcp"]


def _leader_api_key() -> str | None:
    """Return the leader LLM key from LEADER_API_KEY or API_KEY."""
    return os.environ.get("LEADER_API_KEY") or os.environ.get("API_KEY")


def _local_tcp_address(env_name: str, default_port: int) -> str:
    """Return a configurable localhost PyZMQ address."""
    raw_port = os.environ.get(env_name, str(default_port)).strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError(f"{env_name} must be an integer TCP port, got {raw_port!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{env_name} must be between 1 and 65535, got {port}")
    return f"tcp://127.0.0.1:{port}"


def _leader_model() -> dict[str, Any]:
    """Build the leader TeamModelConfig dict from the environment."""
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
    """Build a persistent one-Codex external team spec."""
    publish_addr = _local_tcp_address("CODEX_PAUSE_RESUME_PUBLISH_PORT", 15616)
    subscribe_addr = _local_tcp_address("CODEX_PAUSE_RESUME_SUBSCRIBE_PORT", 15617)
    cfg: dict[str, Any] = {
        "team_name": team_name,
        "lifecycle": "persistent",
        "teammate_mode": "build_mode",
        "spawn_mode": "inprocess",
        "language": "cn",
        "leader": {
            "member_name": "team_leader",
            "display_name": "TeamLeader",
            "persona": "技术项目经理，负责调度 Codex SDK 成员并验证 thread 续接。",
        },
        "agents": {
            "leader": {
                "model": _leader_model(),
                "rails": [{"type": "core.sys_operation"}],
                "language": "cn",
                "max_iterations": 180,
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
                "direct_addr": _local_tcp_address("CODEX_PAUSE_RESUME_DIRECT_PORT", 15615),
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
                "mcp_default_tools_approval_mode": "approve",
                "codex_bypass_approvals_and_sandbox": True,
                "mcp_server_command": _MCP_SERVER_COMMAND,
            }
        ],
    }
    return TeamAgentSpec.model_validate(cfg)


def _first_query(workspace_path: Path, marker: str) -> str:
    """Build the first-round instruction containing the marker only in a DM."""
    return (
        "请建立一个 persistent 团队，只创建一个 Codex SDK 外部成员。\n"
        "1. 先调用 build_team。\n"
        f"2. 调用 spawn_external_cli 创建 {_MEMBER_NAME}，cli_agent='codex'。\n\n"
        "第一轮必须先创建任务和指派任务，再发送包含暗号的 direct message：\n"
        f"3. 用 create_task 创建 task_id={_BEFORE_TASK_ID} 的 before_pause.md 任务。"
        "任务标题和内容都不能包含下面的 SESSION_TOKEN。"
        f"任务内容要求 {_MEMBER_NAME} 严格按顺序执行："
        f'先用 view_task(action="get", task_id="{_BEFORE_TASK_ID}") 读取已指派给自己的任务；'
        f"根据它收到的 <team-inbound> direct message 中 <session-resume-check> 的 ACK_PHRASE，在 {workspace_path} 写入 before_pause.md；"
        "文件内容只能是 ACK_PHRASE，不能写 SESSION_TOKEN；"
        '调用 claim_task(status="completed")；'
        "调用 send_message 向 team_leader 汇报。\n"
        f"4. 用 update_task 把 {_BEFORE_TASK_ID} 指派给 {_MEMBER_NAME}。"
        "update_task 指派后任务已是 in_progress，不要再要求成员 claim_task(status=claimed)。\n"
        f'5. 通过 send_message(to="{_MEMBER_NAME}") 发送下面这段消息，只有这条消息可以包含 SESSION_TOKEN：\n'
        "「请阅读并遵守这个 Codex thread 恢复检查块：\n"
        "<session-resume-check>\n"
        f"SESSION_TOKEN: {marker}\n"
        f"ACK_PHRASE: {_ACK_PHRASE}\n"
        "规则：\n"
        "1. SESSION_TOKEN 只用于下一轮恢复同一 Codex thread 后回忆。\n"
        "2. 不要把 SESSION_TOKEN 写入 before_pause.md 或其他文件。\n"
        "3. 不要在 send_message 汇报中复述 SESSION_TOKEN。\n"
        "4. 完成 before_pause.md 后，汇报只包含 ACK_PHRASE。\n"
        "</session-resume-check>\n"
        f"任务 {_BEFORE_TASK_ID} 已由 Leader 指派给你并处于 in_progress。"
        f'请直接调用 view_task(action="get", task_id="{_BEFORE_TASK_ID}") 读取任务；'
        "不要调用 view_task(action=claimable)，不要重复 claim_task(status=claimed)。"
        "现在请完成 before_pause.md 任务。」\n"
        f'6. 指派后最多调用一次 view_task(action="get", task_id="{_BEFORE_TASK_ID}") 确认任务进入 in_progress，'
        "然后结束当前 turn，等待 task_completed 事件或成员完成消息自动唤醒；严禁循环轮询 view_task。"
        "收到完成事件后只再查询一次，确认 status=completed 才继续。\n"
        f"7. 任务完成后读取 {workspace_path / 'before_pause.md'}，确认内容是 ACK_PHRASE 且不包含 SESSION_TOKEN。\n"
        "确认后结束本轮。不要 clean_team，不要 shutdown_member，不要继续轮询。"
    )


def _resume_query(workspace_path: Path) -> str:
    """Build the second-round instruction without the first-round marker."""
    return (
        "这是同一 team_name 和 Jiuwen session_id 的恢复轮。"
        "不要新建成员，必须恢复已有的 codex-1，并让它续接原来的 Codex thread。"
        "不要在 create_task、update_task、send_message、任务标题、任务内容或任何新文本里重复上一轮 SESSION_TOKEN。\n\n"
        f"1. 用 create_task 创建 task_id={_AFTER_TASK_ID} 的 after_resume.md 任务。"
        f"任务内容要求 {_MEMBER_NAME} 严格按顺序执行："
        f'先用 view_task(action="get", task_id="{_AFTER_TASK_ID}") 读取已指派给自己的任务；'
        f"仅依靠自己续接的 Codex thread 回忆第一轮 <session-resume-check> 里的 SESSION_TOKEN，并写入 {workspace_path / 'after_resume.md'}；"
        "文件内容必须只包含 SESSION_TOKEN，不要写 ACK_PHRASE；"
        "如果确实无法回忆才写 UNKNOWN；"
        '调用 claim_task(status="completed")；'
        "调用 send_message 向 team_leader 汇报。\n"
        f"2. 用 update_task 把 {_AFTER_TASK_ID} 指派给 {_MEMBER_NAME}。"
        "update_task 指派后任务已是 in_progress，不要再要求成员 claim_task(status=claimed)。\n"
        f'3. 用 send_message(to="{_MEMBER_NAME}") 告诉它按 after_resume.md 任务执行，'
        f'明确任务 ID 是 {_AFTER_TASK_ID}，让它直接调用 view_task(action="get", task_id="{_AFTER_TASK_ID}")；'
        "不要调用 view_task(action=claimable)，不要重复 claim_task(status=claimed)；"
        "只从续接的 Codex thread 回忆上一轮 SESSION_TOKEN，不要查找工作区文件中的暗号；"
        '完成后必须 claim_task(status="completed") 并 send_message 汇报。\n'
        f'4. 指派后最多调用一次 view_task(action="get", task_id="{_AFTER_TASK_ID}") 确认任务进入 in_progress，'
        "然后结束当前 turn，等待 task_completed 事件或成员完成消息自动唤醒；严禁循环轮询 view_task。"
        "收到完成事件后只再查询一次，确认 status=completed 才继续。\n"
        "5. 任务完成后确认 after_resume.md 存在，然后结束本轮。"
        "不要 clean_team，不要 shutdown_member，不要继续轮询。"
    )


def _validate_env() -> list[str]:
    """Return missing environment variables."""
    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
    if _leader_api_key() is None:
        missing.append("LEADER_API_KEY|API_KEY")
    return missing


async def _run() -> int:
    """Execute both persistent-team rounds and verify Codex thread memory."""
    missing = _validate_env()
    if missing:
        logger.error("missing required env: {}", ", ".join(missing))
        print(f"[skip] set the required env first: {', '.join(missing)}")
        return 1

    team_name = f"codex_cli_pause_resume_{uuid.uuid4().hex[:8]}"
    marker = f"CODEX_PAUSE_RESUME_MARKER_{uuid.uuid4().hex}"
    workspace_path = team_home(team_name) / "team-workspace"
    workspace_path.mkdir(parents=True, exist_ok=True)
    before_path = workspace_path / "before_pause.md"
    after_path = workspace_path / "after_resume.md"
    spec = _build_spec(team_name, workspace_path)

    print("=" * 70)
    print(f"Codex SDK pause/resume E2E team={team_name}")
    print(f"session={_SESSION_ID}")
    print(f"workspace={workspace_path}")
    print(f"marker={marker}")
    print("transport=local PyZMQ; backend=openai-codex SDK")
    print("=" * 70)

    ok = False
    await Runner.start()
    try:
        await asyncio.wait_for(
            consume_stream(
                spec,
                _first_query(workspace_path, marker),
                _SESSION_ID,
                ordered_output=True,
            ),
            timeout=_RUN_TIMEOUT_S,
        )
        if not before_path.is_file():
            raise RuntimeError(f"before file missing: {before_path}")
        before_content = before_path.read_text(encoding="utf-8", errors="replace").strip()
        print(f"[before_pause.md] {before_content!r}")
        if before_content != _ACK_PHRASE:
            raise RuntimeError("before_pause.md did not contain the expected ACK_PHRASE")
        if marker in before_content:
            raise RuntimeError("before_pause.md leaked the SESSION_TOKEN")

        print("[pause] first run completed; resuming the same persistent team session")
        await asyncio.wait_for(
            consume_stream(
                spec,
                _resume_query(workspace_path),
                _SESSION_ID,
                ordered_output=True,
            ),
            timeout=_RUN_TIMEOUT_S,
        )
        if not after_path.is_file():
            raise RuntimeError(f"after file missing: {after_path}")

        after_content = after_path.read_text(encoding="utf-8", errors="replace").strip()
        print(f"[after_resume.md] {after_content!r}")
        if after_content != marker:
            raise RuntimeError("after_resume.md did not exactly match the first-round marker")
        ok = True
        return 0
    except Exception as exc:
        logger.error("Codex pause/resume e2e failed: {}", exc)
        print(f"[FAIL] {exc}")
        return 1
    finally:
        try:
            await Runner.delete_agent_team(team_name=team_name, session_ids=[_SESSION_ID], force=True)
        except BaseException as exc:  # noqa: BLE001 - best-effort teardown
            logger.warning("cleanup failed for team {}: {}", team_name, exc)
        await Runner.stop()
        print("-" * 70)
        print(f"RESULT: {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
