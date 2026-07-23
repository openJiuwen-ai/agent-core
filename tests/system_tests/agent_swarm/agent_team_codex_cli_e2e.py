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
_VERIFY_POLL_INTERVAL_S = 0.2
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
                "mcp_default_tools_approval_mode": "approve",
                "codex_bypass_approvals_and_sandbox": True,
                "mcp_server_command": _MCP_SERVER_COMMAND,
            },
        ],
    }
    return TeamAgentSpec.model_validate(cfg)


def _god_view_query(workspace_path: Path) -> str:
    roster = "\n".join(f"   - {name}：任务 ID 为 task-{name}，写入 {name}.md" for name in _MEMBERS)
    return (
        "请组建一个 4 人临时团队，完成 Codex CLI 协同写文件验证，全程自主推进：\n\n"
        "1. 调用 build_team 建立团队。\n"
        "2. 调用 spawn_external_cli 创建以下成员，cli_agent 都必须是 codex：\n"
        f"{roster}\n"
        "3. 团队使用自主认领模式。成员全部创建成功后，用一次 create_task "
        "批量创建 4 个任务。tasks 中每项只填写 task_id、title、content，"
        "不得添加 assignee、depends_on、depended_by 或 reviewer。"
        "title 和 content 都必须是单行短文本；content 不得包含双引号、反斜杠、"
        "换行、绝对路径、JSON 示例或具体操作步骤。严格使用以下四项：\n"
        "   - task-codex-1｜codex-1 写入 codex-1.md｜仅 codex-1 认领 task-codex-1 并产出 codex-1.md\n"
        "   - task-codex-2｜codex-2 写入 codex-2.md｜仅 codex-2 认领 task-codex-2 并产出 codex-2.md\n"
        "   - task-codex-3｜codex-3 写入 codex-3.md｜仅 codex-3 认领 task-codex-3 并产出 codex-3.md\n"
        "   - task-codex-4｜codex-4 写入 codex-4.md｜仅 codex-4 认领 task-codex-4 并产出 codex-4.md\n"
        "4. 任务创建成功后，分别用 send_message 把完整执行要求发给对应成员。"
        "消息中写明对应任务 ID、共享工作目录和文件名，并要求成员严格依次执行："
        "先调用 claim_task(claimed) 认领指定任务，再写文件，"
        "然后调用 claim_task(completed) 完成任务，最后用 send_message 向 leader 汇报。\n"
        f"   共享工作目录：{workspace_path}\n"
        "   文件内容固定为一行：<member> reporting in.\n"
        "5. 用 view_task 跟踪，只有四个任务都 completed 才继续。\n"
        "6. 四个任务都 completed 且四个文件都存在后，保持团队和工作区不变，"
        "等待 GodView 发送‘外部文件验证已通过，可以清理团队’。在收到这条消息前，"
        "严禁调用 shutdown_member 或 clean_team。\n"
        "7. 收到该 GodView 消息后，先再次确认四个任务都 completed，"
        "再对四个成员逐一调用 shutdown_member(member_name=<member>, force=true)，"
        "不要发 shutdown 提示词。全部关停成功后调用 clean_team，最后简要汇报。\n"
    )


def _expected_files(workspace_path: Path) -> list[Path]:
    return [workspace_path / f"{member}.md" for member in _MEMBERS]


def _verified_files(workspace_path: Path) -> list[Path]:
    """Return files whose contents already satisfy the E2E contract."""
    verified: list[Path] = []
    for member, path in zip(_MEMBERS, _expected_files(workspace_path)):
        try:
            content = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            continue
        if content == f"{member} reporting in.":
            verified.append(path)
    return verified


async def _verify_before_cleanup(
    workspace_path: Path,
    *,
    team_name: str,
    stream_task: asyncio.Task[None],
    runtime_ready: asyncio.Event,
) -> list[Path]:
    """Release cleanup only after artifacts and team tasks are complete."""
    from openjiuwen.agent_teams.schema.status import TaskStatus
    from sqlalchemy.exc import OperationalError

    await runtime_ready.wait()
    expected_task_ids = {f"task-{member}" for member in _MEMBERS}
    monitor = None
    while True:
        verified = _verified_files(workspace_path)
        if monitor is None:
            monitor = await Runner.get_agent_team_monitor(
                team_name=team_name,
                session_id=_SESSION_ID,
            )

        completed_task_ids: set[str] = set()
        if monitor is not None:
            try:
                tasks = await monitor.get_tasks()
            except OperationalError as exc:
                # ``team.runtime_ready`` is emitted after the runtime enters
                # the pool but just before ``TeamAgent.stream()`` creates the
                # session-scoped task tables. Treat only that startup race as
                # transient; every other database error must still fail fast.
                if "no such table: team_task_" not in str(exc):
                    raise
                await asyncio.sleep(_VERIFY_POLL_INTERVAL_S)
                continue
            completed_task_ids = {
                task.task_id
                for task in tasks
                if task.task_id in expected_task_ids and task.status == TaskStatus.COMPLETED.value
            }

        files_ready = len(verified) == len(_MEMBERS)
        tasks_ready = completed_task_ids == expected_task_ids
        if files_ready and tasks_ready:
            for path in verified:
                print(f"[verified-before-clean] {path}")
            print(f"[verified-before-clean] completed tasks: {sorted(completed_task_ids)}")
            result = await Runner.interact_agent_team(
                "外部文件验证已通过，可以清理团队。",
                team_name=team_name,
                session_id=_SESSION_ID,
            )
            if not result.ok:
                raise RuntimeError(f"failed to release team cleanup: {result.reason}")
            return verified
        if stream_task.done():
            await stream_task
            raise RuntimeError(
                "team stream ended before all four artifacts and tasks were completed: "
                f"files={len(verified)}/{len(_MEMBERS)}, "
                f"completed_tasks={sorted(completed_task_ids)}"
            )
        await asyncio.sleep(_VERIFY_POLL_INTERVAL_S)


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
    verified_files: list[Path] = []
    cleanup_gate_passed = False
    runtime_ready = asyncio.Event()

    async def _on_runtime_ready(_team_name: str, _session_id: str) -> None:
        runtime_ready.set()

    stream_task = asyncio.create_task(
        consume_stream(
            spec,
            _god_view_query(workspace_path),
            _SESSION_ID,
            on_runtime_ready=_on_runtime_ready,
            ordered_output=True,
        )
    )
    verification_task = asyncio.create_task(
        _verify_before_cleanup(
            workspace_path,
            team_name=team_name,
            stream_task=stream_task,
            runtime_ready=runtime_ready,
        )
    )
    try:
        _, verified_files = await asyncio.wait_for(
            asyncio.gather(stream_task, verification_task),
            timeout=_RUN_TIMEOUT_S,
        )
        cleanup_gate_passed = True
    except asyncio.TimeoutError:
        logger.error("run exceeded {}s budget; checking partial output", _RUN_TIMEOUT_S)
    finally:
        for task in (stream_task, verification_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(stream_task, verification_task, return_exceptions=True)
        if not verified_files:
            verified_files = _verified_files(workspace_path)
        present = set(verified_files)
        missing_files = [path for path in _expected_files(workspace_path) if path not in present]
        for path in verified_files:
            print(f"[ok-before-clean] {path}")
        for path in missing_files:
            print(f"[MISSING] {path}")
        try:
            await Runner.delete_agent_team(team_name=team_name, session_ids=[_SESSION_ID], force=True)
        except BaseException as exc:  # noqa: BLE001 - best-effort teardown
            logger.warning("cleanup failed for team {}: {}", team_name, exc)
        await Runner.stop()

    passed = cleanup_gate_passed and not missing_files
    print(
        f"RESULT: {'PASS' if passed else 'FAIL'} — "
        f"{len(present)}/{len(_MEMBERS)} files; cleanup_gate_passed={cleanup_gate_passed}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
