# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Real TeamSkillCreateRail example with DeepAgent.

Run with:
    uv run python -m \
      examples.agent_evolving.team_skill_create_rail_example

Or with a local .env file:
    set -a; source .env; set +a; uv run python -m \
      examples.agent_evolving.team_skill_create_rail_example
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
from openjiuwen.harness.rails import TeamSkillCreateRail


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


def build_model_from_env() -> Model:
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

    return Model(
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


def prepare_workspace(workspace: str | None) -> Path:
    if workspace:
        root = Path(workspace).expanduser().resolve()
    else:
        root = Path(tempfile.mkdtemp(prefix="team_skill_example_create_")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "skills").mkdir(parents=True, exist_ok=True)
    return root


def build_session_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


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
    "请以团队 leader 身份为“AI 行业周报”组织一个最小协作流程。"
    "你必须按顺序完成："
    "1. 调用 build_team 建立团队；"
    "2. 调用 spawn_member 至少两次，分别创建 researcher 和 writer；"
    "3. 调用 create_task 给这两个成员各创建一项任务；"
    "4. 最后简要总结团队结构与分工。"
    "不要只给口头方案，必须实际调用工具。"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Real TeamSkillCreateRail example")
    parser.add_argument("--workspace", help="Workspace root. Defaults to a temp directory.")
    parser.add_argument("--query", default=DEFAULT_QUERY, help="User query to run.")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    configure_example_logging()

    model = build_model_from_env()
    workspace = prepare_workspace(args.workspace)
    session_id = build_session_id("team_skill_create")
    team_name = f"team_skill_create_demo_{session_id.rsplit('_', 1)[-1]}"

    await Runner.start()
    try:
        async with leader_team_tools_context(
            workspace=workspace,
            session_id=session_id,
            team_name=team_name,
        ) as (_, team_tools):
            create_rail = TeamSkillCreateRail(
                skills_dir=str(workspace / "skills"),
                min_team_members_for_create=2,
            )
            agent = create_deep_agent(
                model=model,
                system_prompt=(
                    "你是一个严格执行工具流程的团队 leader。"
                    "当用户要求组建协作团队时，优先通过 team tools 真正创建团队和任务。"
                ),
                tools=team_tools,
                rails=[create_rail],
                enable_task_loop=True,
                max_iterations=6,
                workspace=str(workspace),
                language="cn",
            )
            session = Session(session_id=session_id, card=agent.card)

            result = await Runner.run_agent(
                agent,
                {"query": args.query},
                session=session,
            )

            print("workspace:", workspace)
            print("team name:", team_name)
            print("team skill create triggered:", create_rail._proposal_sent)
            print("final output:", result.get("output", result))
            if create_rail._proposal_sent:
                print(
                    "The final output above should now be the follow-up that asks whether to create a reusable team skill."
                )
            else:
                print(
                    "TeamSkillCreateRail did not trigger. Check whether the model actually called spawn_member enough times."
                )
    finally:
        await Runner.stop()


if __name__ == "__main__":
    asyncio.run(main())
