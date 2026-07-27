# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""External Claude CLI pause/resume E2E.

This script validates the Claude external backend resume path with a marker
delivered through a first-round direct message:

* Run 1 starts a persistent team and spawns one ``claude`` external member.
* The leader sends the marker to the member with ``send_message`` and asks it
  to acknowledge receiving the session marker without writing or reporting it.
* The first run exits normally, letting the persistent team enter PAUSED.
* Run 2 resumes the same ``team_name`` + ``session_id`` and asks the member to
  write the marker it was asked to remember into ``after_resume.md``. The
  second prompt does not contain the marker.

The test passes when ``after_resume.md`` contains the original marker after
the second run.

Run manually:
    source .venv/bin/activate && export PYTHONPATH=.:$PYTHONPATH
    python tests/system_tests/agent_swarm/agent_team_external_cli_pause_resume_e2e.py
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

logger = LazyLogger(lambda: LogManager.get_logger("external_cli_pause_resume_e2e"))

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")

_REQUIRED_ENV = ("API_BASE", "MODEL_NAME")
_SESSION_ID = "external_cli_pause_resume_session"
_MEMBER_NAME = "claude-1"
_ACK_PHRASE = "会话暗号已接收"
_RUN_TIMEOUT_S = 1200.0
_MCP_SERVER_COMMAND = [sys.executable, "-m", "openjiuwen.agent_teams.mcp"]


def _leader_api_key() -> str | None:
    """Return the leader LLM key from LEADER_API_KEY or API_KEY."""
    return os.environ.get("LEADER_API_KEY") or os.environ.get("API_KEY")


def _use_ssh_for_claude() -> bool:
    """Return whether claude should be launched through local SSH."""
    return os.environ.get("EXTERNAL_CLI_E2E_CLAUDE_TRANSPORT", "").strip().lower() == "ssh"


def _local_ssh_transport() -> dict[str, Any]:
    """Build the SSH endpoint config for localhost port 23."""
    username = os.environ.get("EXTERNAL_CLI_SSH_USER") or os.environ.get("USERNAME") or os.environ.get("USER")
    config: dict[str, Any] = {
        "host": "127.0.0.1",
        "port": 23,
        "agent": True,
        "disable_host_key_check": True,
    }
    if username:
        config["username"] = username
    return config


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
    """Build a persistent one-Claude external team spec."""
    claude_cli_config: dict[str, Any] = {
        "cli_agent": "claude",
        "cwd": str(workspace_path),
        "inject_mcp": True,
        "mcp_server_command": _MCP_SERVER_COMMAND,
    }
    if _use_ssh_for_claude():
        claude_cli_config["ssh_transport"] = _local_ssh_transport()

    cfg: dict[str, Any] = {
        "team_name": team_name,
        "lifecycle": "persistent",
        "teammate_mode": "build_mode",
        "spawn_mode": "inprocess",
        "language": "cn",
        "leader": {
            "member_name": "team_leader",
            "display_name": "TeamLeader",
            "persona": "资深技术项目经理，负责调度外部 Claude CLI 成员完成可验证任务。",
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
        "workspace": {
            "enabled": True,
            "version_control": True,
        },
        "transport": {
            "type": "pyzmq",
            "params": {
                "team_name": team_name,
                "node_id": "team_leader",
                "direct_addr": "tcp://127.0.0.1:15605",
                "pubsub_publish_addr": "tcp://127.0.0.1:15606",
                "pubsub_subscribe_addr": "tcp://127.0.0.1:15607",
                "metadata": {"pubsub_bind": True},
            },
        },
        "storage": {"type": "sqlite"},
        "external_cli_agents": [claude_cli_config],
    }
    return TeamAgentSpec.model_validate(cfg)


def _first_query(workspace_path: Path, marker: str) -> str:
    """Build the first-round leader instruction."""
    return (
        "请组建一个 persistent 团队，只拉起一个外部 CLI 成员："
        f"{_MEMBER_NAME}，cli_agent='claude'，role_type='external_cli'。\n\n"
        "第一轮分两步：先创建并指派任务，再发送包含暗号的 direct message 唤醒成员完成任务。\n\n"
        "1. 用 create_task 创建一个 before_pause.md 任务。任务标题和任务内容都不能包含下面的暗号。"
        "任务内容必须明确要求 claude-1 严格按顺序执行："
        "claim_task(status=\"claimed\")；"
        f"根据它已收到的 <team-inbound> direct message 中 <session-resume-check> 里的 ACK_PHRASE，在共享工作目录 {workspace_path} 写入 before_pause.md；"
        "文件内容必须只包含 ACK_PHRASE，不能写 SESSION_TOKEN；"
        "调用 claim_task(status=\"completed\") 标记任务完成；"
        "调用 send_message 向 team_leader 汇报 before_pause.md 已完成。"
        "必须写清楚：只写文件不算完成，claim_task(completed) 和 send_message 都做过才算完成。\n"
        "2. 用 update_task 把该任务指派给 claude-1。\n"
        "3. 通过 send_message(to=\"claude-1\") 把下面这段消息直接发给 claude-1，只有这条消息可以包含暗号：\n"
        "「请阅读并遵守这个会话恢复检查块：\n"
        "<session-resume-check>\n"
        f"SESSION_TOKEN: {marker}\n"
        f"ACK_PHRASE: {_ACK_PHRASE}\n"
        "规则：\n"
        "1. SESSION_TOKEN 是后续恢复同一 Claude 会话时需要回忆的值。\n"
        "2. ACK_PHRASE 只是完成汇报时使用的确认短语，不是 SESSION_TOKEN。\n"
        "3. 不要把 SESSION_TOKEN 写入 before_pause.md。\n"
        "4. 不要把 SESSION_TOKEN 写入任何其他文件。\n"
        "5. 不要在 send_message 汇报中复述 SESSION_TOKEN。\n"
        "6. 完成 before_pause.md 任务后，只在汇报中包含 ACK_PHRASE。\n"
        "</session-resume-check>\n"
        "现在请完成你被指派的 before_pause.md 任务。」\n"
        "4. 持续用 view_task 跟踪任务状态，只有 before_pause.md 任务 status=completed 才算成员完成。"
        "如果 claude-1 只写了文件但没有 completed，用 send_message 明确催它立即 claim_task(status=\"completed\") 并汇报。\n"
        f"5. 任务 completed 后，读取共享工作目录下的 before_pause.md，确认文件存在且内容为 {_ACK_PHRASE}，并且不包含实际暗号值。确认后本轮结束。"
        "不要 clean_team，不要 shutdown_member，不要继续轮询。"
    )


def _resume_query(workspace_path: Path) -> str:
    """Build the resume-round leader instruction without the marker."""
    return (
        "这是同一 session 的恢复轮。必须先 create_task，再 update_task 指派给 claude-1，然后 send_message 唤醒成员。"
        "不要在 create_task、update_task、send_message、任务标题、任务内容或任何给成员看的文本里重复上一轮暗号。\n\n"
        "1. 用 create_task 创建一个 after_resume.md 任务。任务内容必须明确要求 claude-1 严格按顺序执行："
        "claim_task(status=\"claimed\")；"
        f"根据它上一轮收到的 <team-inbound> direct message 中 <session-resume-check> 里的 SESSION_TOKEN，在共享工作目录 {workspace_path} 写入 after_resume.md；"
        "文件内容必须只包含 SESSION_TOKEN，不要写 ACK_PHRASE；如果确实无法回忆 SESSION_TOKEN 才写 UNKNOWN；"
        "调用 claim_task(status=\"completed\") 标记任务完成；"
        "调用 send_message 向 team_leader 汇报 after_resume.md 已完成。"
        "必须写清楚：只写文件不算完成，claim_task(completed) 和 send_message 都做过才算完成。\n"
        "2. 用 update_task 把该任务指派给 claude-1。\n"
        "3. 通过 send_message(to=\"claude-1\") 告诉它：请按你被指派的 after_resume.md 任务执行，"
        "回忆并写入上一轮收到的 <team-inbound> direct message 中 <session-resume-check> 里的 SESSION_TOKEN，不要写 ACK_PHRASE；"
        "如果确实无法回忆 SESSION_TOKEN 才写 UNKNOWN；完成后必须 claim_task(status=\"completed\") 并 send_message 汇报。"
        "注意：这条 send_message 也不能包含实际暗号值。\n"
        "4. 持续用 view_task 跟踪任务状态，只有 after_resume.md 任务 status=completed 才算成员完成。"
        "如果 claude-1 只写了文件但没有 completed，用 send_message 明确催它立即 claim_task(status=\"completed\") 并汇报。\n"
        "5. 任务 completed 后，读取 after_resume.md，确认文件存在后本轮结束。"
        "不要 clean_team，不要 shutdown_member，不要继续轮询。"
    )


def _validate_env() -> list[str]:
    """Return missing environment variables."""
    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
    if _leader_api_key() is None:
        missing.append("LEADER_API_KEY|API_KEY")
    return missing


async def _run() -> int:
    missing = _validate_env()
    if missing:
        logger.error("missing required env: {}", ", ".join(missing))
        print(f"[skip] set the required env first: {', '.join(missing)}")
        return 1

    team_name = f"external_cli_pause_resume_{uuid.uuid4().hex[:8]}"
    marker = f"PAUSE_RESUME_MARKER_{uuid.uuid4().hex}"
    workspace_path = team_home(team_name) / "team-workspace"
    workspace_path.mkdir(parents=True, exist_ok=True)
    before_path = workspace_path / "before_pause.md"
    after_path = workspace_path / "after_resume.md"
    spec = _build_spec(team_name, workspace_path)

    print("=" * 70)
    print(f"External Claude pause/resume E2E team={team_name}")
    print(f"session={_SESSION_ID}")
    print(f"workspace={workspace_path}")
    print(f"marker={marker}")
    print(f"claude transport={'ssh://127.0.0.1:23' if _use_ssh_for_claude() else 'local'}")
    print("=" * 70)

    ok = False
    await Runner.start()
    try:
        await asyncio.wait_for(
            consume_stream(
                spec,
                _first_query(workspace_path, marker),
                _SESSION_ID,
            ),
            timeout=_RUN_TIMEOUT_S,
        )
        if not before_path.is_file():
            raise RuntimeError(f"before file missing: {before_path}")
        print(f"[ok] before file exists: {before_path}")
        before_content = before_path.read_text(encoding="utf-8", errors="replace").strip()
        print(f"[before_pause.md] {before_content!r}")
        if before_content != _ACK_PHRASE:
            raise RuntimeError("before_pause.md did not contain the expected ACK_PHRASE")
        if marker in before_content:
            raise RuntimeError("before_pause.md leaked the SESSION_TOKEN")

        await asyncio.wait_for(
            consume_stream(
                spec,
                _resume_query(workspace_path),
                _SESSION_ID,
            ),
            timeout=_RUN_TIMEOUT_S,
        )
        if not after_path.is_file():
            raise RuntimeError(f"after file missing: {after_path}")

        content = after_path.read_text(encoding="utf-8", errors="replace")
        print(f"[after_resume.md] {content!r}")
        ok = marker in content
        if not ok:
            raise RuntimeError("after_resume.md did not contain the first-round marker")
        return 0
    except Exception as exc:
        logger.error("pause/resume e2e failed: {}", exc)
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
