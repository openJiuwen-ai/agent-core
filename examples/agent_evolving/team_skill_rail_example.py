# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Real TeamSkillRail example with DeepAgent.

Run with:
    uv run python -m \
      examples.agent_evolving.team_skill_rail_example

Or with a local .env file:
    set -a; source .env; set +a; uv run python -m \
      examples.agent_evolving.team_skill_rail_example
"""

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from dotenv import load_dotenv

from openjiuwen.agent_teams.messager.base import MessagerTransportConfig, create_messager
from openjiuwen.agent_teams.spawn.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.team_tools import create_team_tools
from openjiuwen.core.common.logging import (
    logger,
    runner_logger,
    session_logger,
    sys_operation_logger,
    team_logger,
)
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails import TeamSkillRail


def configure_example_logging() -> None:
    config = {
        "level": "WARNING",
        "output": ["console"],
        "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    }
    for named_logger in (
        logger,
        runner_logger,
        session_logger,
        sys_operation_logger,
        team_logger,
    ):
        named_logger.reconfigure(config)


def load_env_if_present() -> None:
    candidate_files = (
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    )
    loaded_files: set[Path] = set()
    for env_file in candidate_files:
        resolved = env_file.resolve()
        if resolved in loaded_files or not resolved.exists():
            continue
        load_dotenv(resolved, override=False)
        loaded_files.add(resolved)


def build_model_from_env() -> tuple[Model, str]:
    load_env_if_present()
    api_key = os.getenv("API_KEY", "")
    api_base = os.getenv("API_BASE", "")
    model_name = os.getenv("MODEL_NAME", "")
    provider = os.getenv("MODEL_PROVIDER", "OpenAI")
    timeout = int(os.getenv("MODEL_TIMEOUT", "120"))

    missing = [
        name
        for name, value in (
            ("API_KEY", api_key),
            ("API_BASE", api_base),
            ("MODEL_NAME", model_name),
        )
        if not value
    ]
    if missing:
        raise SystemExit("Missing required environment variables: " + ", ".join(missing) + ".")

    model = Model(
        model_client_config=ModelClientConfig(
            client_provider=provider,
            api_key=api_key,
            api_base=api_base,
            timeout=timeout,
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(
            model=model_name,
            temperature=0.2,
            top_p=0.9,
        ),
    )
    return model, model_name


def prepare_workspace(workspace: str | None) -> Path:
    if workspace:
        root = Path(workspace).expanduser().resolve()
    else:
        root = Path(tempfile.mkdtemp(prefix="team_skill_example_evolve_")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "skills").mkdir(parents=True, exist_ok=True)
    return root


def build_session_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def write_team_skill(skill_dir: Path, skill_name: str) -> Path:
    target = skill_dir / skill_name
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(
        "---\n"
        f"name: {skill_name}\n"
        "description: Rapid collaboration workflow for lightweight research tasks.\n"
        "kind: team-skill\n"
        "---\n\n"
        "# Workflow\n\n"
        "1. Call `build_team` to initialize the team.\n"
        "2. Call `spawn_member` for at least two specialized roles.\n"
        "3. Call `create_task` to split the work.\n"
        "4. Call `view_task` before summarizing the current state.\n",
        encoding="utf-8",
    )
    return target


@asynccontextmanager
async def leader_team_tools_context(
    *,
    workspace: Path,
    session_id: str,
    team_name: str,
    member_name: str = "leader",
    lang: str = "cn",
) -> AsyncIterator[tuple[TeamBackend, list]]:
    token = set_session_id(session_id)
    db = TeamDatabase(
        DatabaseConfig(
            db_type=DatabaseType.SQLITE,
            connection_string=str(workspace / "team.sqlite"),
        )
    )
    messager = create_messager(
        MessagerTransportConfig(
            backend="inprocess",
            team_name=team_name,
            node_id=member_name,
        )
    )

    try:
        await db.initialize()
        await messager.start()
        backend = TeamBackend(
            team_name=team_name,
            member_name=member_name,
            is_leader=True,
            db=db,
            messager=messager,
        )
        tools = create_team_tools(
            role="leader",
            agent_team=backend,
            teammate_mode="build_mode",
            lang=lang,
        )
        yield backend, tools
    finally:
        await messager.stop()
        await db.close()
        reset_session_id(token)


DEFAULT_QUERY = (
    "请先调用 skill_tool 使用 research-team 团队技能，然后为“AI 行业周报”组织一个最小协作流程。"
    "你必须："
    "1. build_team；"
    "2. spawn_member 两次；"
    "3. create_task 创建至少两项任务；"
    "4. view_task 查看当前状态；"
    "5. 最后给出简短汇报。"
)
DEFAULT_USER_INTENT = "增加 reviewer 角色，并要求 leader 在总结前统一检查交付格式。"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Real TeamSkillRail example")
    parser.add_argument("--workspace", help="Workspace root. Defaults to a temp directory.")
    parser.add_argument("--query", default=DEFAULT_QUERY, help="User query to run.")
    parser.add_argument(
        "--user-intent",
        default=DEFAULT_USER_INTENT,
        help="Improvement request used for request_user_evolution().",
    )
    parser.add_argument(
        "--approve-patch",
        action="store_true",
        help="Approve and persist the generated patch after it is proposed.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    configure_example_logging()

    model, model_name = build_model_from_env()
    workspace = prepare_workspace(args.workspace)
    skills_dir = workspace / "skills"
    skill_dir = write_team_skill(skills_dir, "research-team")
    session_id = build_session_id("team_skill_evolve")
    team_name = f"team_skill_rail_demo_{session_id.rsplit('_', 1)[-1]}"

    await Runner.start()
    try:
        async with leader_team_tools_context(
            workspace=workspace,
            session_id=session_id,
            team_name=team_name,
        ) as (_, team_tools):
            team_rail = TeamSkillRail(
                skills_dir=str(skills_dir),
                llm=model,
                model=model_name,
                auto_save=False,
                async_evolution=False,
            )
            agent = create_deep_agent(
                model=model,
                system_prompt=(
                    "你是一个严格执行技能和工具流程的团队 leader。"
                    "当用户给出 team skill 时，优先调用 skill_tool 加载技能后再组织协作。"
                ),
                tools=team_tools,
                rails=[team_rail],
                skills=["research-team"],
                enable_task_loop=False,
                max_iterations=8,
                workspace=str(workspace),
                language="cn",
            )
            session = Session(session_id=session_id, card=agent.card)
            print("workspace:", workspace)
            print("team name:", team_name)
            print("skill file:", skill_dir / "SKILL.md")

            try:
                result = await Runner.run_agent(
                    agent,
                    {"query": args.query},
                    session=session,
                )
            except Exception as exc:
                print("run failed:", exc)
                return

            print("final output:", result.get("output", result))
            print("requesting evolution patch with user intent:", args.user_intent)

            request_id = await team_rail.request_user_evolution(
                "research-team",
                args.user_intent,
                auto_approve=False,
            )
            if not request_id:
                print("No patch was generated from the user intent.")
                return

            events = await team_rail.drain_pending_approval_events(wait=True, timeout=5.0)
            approval_events = [
                event
                for event in events
                if event.type == "chat.ask_user_question" and event.payload.get("request_id") == request_id
            ]
            print("patch request id:", request_id)
            if approval_events:
                question = approval_events[0].payload["questions"][0]["question"]
                print("patch preview:")
                print(question)
            else:
                print("Patch was generated, but no approval preview event was drained in time.")

            if args.approve_patch:
                await team_rail.on_approve_patch(request_id)
                evo_path = skills_dir / "research-team" / "evolutions.json"
                print("patch approved and persisted to:", evo_path)
    finally:
        await Runner.stop()


if __name__ == "__main__":
    asyncio.run(main())
