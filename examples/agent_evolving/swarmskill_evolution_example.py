# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Swarm Skill creation and evolution examples with DeepAgent.

The public Rail classes use the ``TeamSkill*`` names. This example uses the
current ``TeamSkillEvolutionRail`` API and keeps Swarm Skill creation and
evolution in one executable module.

Run both cases with:

    uv sync --extra observability
    uv run --extra observability python -m \
      examples.agent_evolving.swarmskill_evolution_example --scenario all
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

from openjiuwen.agent_evolving.trajectory import TrajectorySpanProcessor
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager.base import MessagerTransportConfig, create_messager
from openjiuwen.agent_teams.observability import (
    ObservabilityConfig,
    acquire_observability,
    finalize_team_trace,
    maybe_observability_rails,
    release_observability,
)
from openjiuwen.agent_teams.observability.span_context import get_or_create_team_span
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
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session.agent import Session
from openjiuwen.extensions.observability.demand import get_trajectory_span_processor
from openjiuwen.extensions.observability.setup import get_tracer
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails import TeamSkillCreateRail, TeamSkillEvolutionRail, configure_skill_evolution
from openjiuwen.harness.rails.evolution import build_evolve_review_command_prompt


SWARM_SKILL_NAME = "research-swarm"
DEFAULT_CREATE_QUERY = (
    "请以 leader 身份组织一个 AI 行业周报协作流程。必须实际调用 build_team，调用 spawn_member 两次创建 "
    "researcher 和 writer，调用 create_task 分配至少两项任务，再调用 view_task 查看状态，最后总结分工。"
)
DEFAULT_EVOLVE_QUERY = (
    "请先调用 skill_tool 使用 research-swarm，再组织“AI 行业周报”的最小协作流程。必须调用 build_team、"
    "spawn_member 两次、create_task 和 view_task，最后给出简短汇报。"
)
DEFAULT_USER_INTENT = "增加 reviewer 角色，并要求 leader 在总结前统一检查交付格式。"


def configure_example_logging() -> None:
    config = {
        "level": "WARNING",
        "output": ["console"],
        "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    }
    for named_logger in (logger, runner_logger, session_logger, sys_operation_logger, team_logger):
        named_logger.reconfigure(config)


def load_env_if_present() -> None:
    loaded: set[Path] = set()
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
        resolved = candidate.resolve()
        if resolved in loaded or not resolved.exists():
            continue
        load_dotenv(resolved, override=False)
        loaded.add(resolved)


def build_model_from_env() -> tuple[Model, str]:
    load_env_if_present()
    api_key = os.getenv("API_KEY", "")
    api_base = os.getenv("API_BASE", "")
    model_name = os.getenv("MODEL_NAME", "")
    missing = [
        name for name, value in (("API_KEY", api_key), ("API_BASE", api_base), ("MODEL_NAME", model_name)) if not value
    ]
    if missing:
        raise SystemExit("Missing required environment variables: " + ", ".join(missing) + ".")

    model = Model(
        model_client_config=ModelClientConfig(
            client_provider=os.getenv("MODEL_PROVIDER", "OpenAI"),
            api_key=api_key,
            api_base=api_base,
            timeout=int(os.getenv("MODEL_TIMEOUT", "120")),
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model=model_name, temperature=0.2, top_p=0.9),
    )
    return model, model_name


def prepare_workspace(workspace: str | None) -> Path:
    root = (
        Path(workspace).expanduser().resolve()
        if workspace
        else Path(tempfile.mkdtemp(prefix="swarmskill_evolution_example_")).resolve()
    )
    root.mkdir(parents=True, exist_ok=True)
    (root / "skills").mkdir(exist_ok=True)
    return root


def write_demo_swarm_skill(workspace: Path) -> Path:
    # Evolution requires an existing swarm-skill subject. Seed it separately
    # so the evolution scenario does not depend on running creation first.
    skill_dir = workspace / "skills" / SWARM_SKILL_NAME
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {SWARM_SKILL_NAME}\n"
        "description: Coordinate a small research and writing swarm.\n"
        "kind: swarm-skill\n"
        "---\n\n"
        "# Workflow\n\n"
        "1. Call `build_team` to initialize the team.\n"
        "2. Call `spawn_member` for researcher and writer roles.\n"
        "3. Call `create_task` to split research and writing.\n"
        "4. Call `view_task` before the leader summarizes.\n",
        encoding="utf-8",
    )
    return skill_dir


def build_session_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@asynccontextmanager
async def leader_team_tools_context(
    *,
    workspace: Path,
    session_id: str,
    team_name: str,
) -> AsyncIterator[list]:
    # Team tools and observability route work through the current session id.
    # Keep the token, database, and in-process transport scoped to one case so
    # creation and evolution remain isolated when --scenario all is used.
    token = set_session_id(session_id)
    database = TeamDatabase(
        DatabaseConfig(
            db_type=DatabaseType.SQLITE,
            connection_string=str(workspace / f"{team_name}.sqlite"),
        )
    )
    messager = create_messager(MessagerTransportConfig(backend="inprocess", team_name=team_name, node_id="leader"))

    try:
        await database.initialize()
        await messager.start()
        backend = TeamBackend(
            team_name=team_name,
            member_name="leader",
            is_leader=True,
            db=database,
            messager=messager,
        )
        yield create_team_tools(
            role="leader",
            agent_team=backend,
            teammate_mode="build_mode",
            lang="cn",
        )
    finally:
        await messager.stop()
        await database.close()
        reset_session_id(token)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Swarm Skill creation and evolution example")
    parser.add_argument("--workspace", help="Workspace root. Defaults to a temporary directory.")
    parser.add_argument(
        "--scenario",
        choices=("create", "evolve", "all"),
        default="all",
        help="Case to run. Defaults to both creation and evolution.",
    )
    parser.add_argument("--create-query", default=DEFAULT_CREATE_QUERY)
    parser.add_argument("--evolve-query", default=DEFAULT_EVOLVE_QUERY)
    parser.add_argument("--user-intent", default=DEFAULT_USER_INTENT)
    parser.add_argument(
        "--approve-record",
        action="store_true",
        help="Approve and persist an evolution proposal when the evolution case interrupts.",
    )
    return parser.parse_args()


async def run_creation_case(
    *,
    model: Model,
    processor: TrajectorySpanProcessor,
    workspace: Path,
    query: str,
) -> None:
    session_id = build_session_id("swarmskill_create")
    team_name = f"swarmskill_create_{session_id.rsplit('_', 1)[-1]}"
    try:
        async with leader_team_tools_context(
            workspace=workspace,
            session_id=session_id,
            team_name=team_name,
        ) as team_tools:
            # Open the Team root before Agent construction and keep it alive
            # through both the task and the optional creation self-check turn.
            get_or_create_team_span(team_name, get_tracer("swarmskill-creation-example"))
            creation_rail = TeamSkillCreateRail(
                skills_dir=str(workspace / "skills"),
                trajectory_span_processor=processor,
                min_team_members_for_create=2,
                language="cn",
            )
            agent = create_deep_agent(
                model=model,
                system_prompt=(
                    "你是严格执行工具流程的团队 leader。必须真正创建成员和任务。"
                    "任务完成后遵循系统中的 Swarm Skill 创建自检规则。"
                ),
                tools=team_tools,
                rails=[creation_rail, *maybe_observability_rails()],
                enable_task_loop=True,
                max_iterations=8,
                trajectory_span_processor=processor,
                workspace=str(workspace),
                language="cn",
            )
            session = Session(session_id=session_id, card=agent.card)
            result = await Runner.run_agent(agent, {"query": query}, session=session)

            print("\n=== Swarm Skill creation case ===")
            print("team name:", team_name)
            print("output:", result.get("output", result))
            # Team completion is an explicit lifecycle signal. Once accepted,
            # the next Agent turn evaluates whether the observed collaboration
            # is reusable enough to propose a Swarm Skill.
            completion_accepted = await creation_rail.notify_team_completed()
            print("team completion accepted by creation rail:", completion_accepted)
            if completion_accepted:
                followup_result = await Runner.run_agent(
                    agent,
                    {"query": "请结合刚才的协作结果完成总结。"},
                    session=session,
                )
                print("creation self-check output:", followup_result.get("output", followup_result))
            print(
                "When the self-check proposes creation, user confirmation lets the Agent invoke "
                "swarmskill-creator to create and validate the Swarm Skill."
            )
    finally:
        # Finalization exports the complete Team trace after every Agent turn,
        # including the creation self-check, has finished.
        finalize_team_trace(team_name)


async def run_evolution_case(
    *,
    model: Model,
    model_name: str,
    processor: TrajectorySpanProcessor,
    workspace: Path,
    query: str,
    user_intent: str,
    approve_record: bool,
) -> None:
    skill_dir = write_demo_swarm_skill(workspace)
    session_id = build_session_id("swarmskill_evolve")
    team_name = f"swarmskill_evolve_{session_id.rsplit('_', 1)[-1]}"
    try:
        async with leader_team_tools_context(
            workspace=workspace,
            session_id=session_id,
            team_name=team_name,
        ) as team_tools:
            # One Team root spans Skill use, the /evolve review, and a possible
            # approval resume so TeamSkillEvolutionRail sees one clean window.
            get_or_create_team_span(team_name, get_tracer("swarmskill-evolution-example"))
            agent = create_deep_agent(
                model=model,
                system_prompt=(
                    "你是严格执行技能和工具流程的团队 leader。"
                    "用户指定 Swarm Skill 时，先用 skill_tool 加载，再组织协作。"
                ),
                tools=team_tools,
                skills=[SWARM_SKILL_NAME],
                rails=maybe_observability_rails(),
                enable_task_loop=False,
                max_iterations=8,
                trajectory_span_processor=processor,
                workspace=str(workspace),
                language="cn",
            )
            configure_skill_evolution(
                agent,
                skills_dir=str(workspace / "skills"),
                llm=model,
                model=model_name,
                trajectory_span_processor=processor,
                team=True,
                signal_trigger=False,
                review_trigger=False,
                auto_save=False,
                async_evolution=False,
                language="cn",
            )
            # team=True installs TeamSkillEvolutionRail. Passive triggers stay
            # disabled because the host command below starts review explicitly.
            evolution_rail = next(
                rail
                for rail in agent.find_rails_by_type((TeamSkillEvolutionRail,))
                if rail.__class__ is TeamSkillEvolutionRail
            )
            session = Session(session_id=session_id, card=agent.card)

            print("\n=== Swarm Skill evolution case ===")
            print("team name:", team_name)
            initial_result = await Runner.run_agent(agent, {"query": query}, session=session)
            print("initial output:", initial_result.get("output", initial_result))

            # Resolve the canonical swarm-skill payload before constructing the
            # same follow-up prompt that a host-side /evolve handler dispatches.
            subject = evolution_rail.store.resolve_subject_payload(SWARM_SKILL_NAME)
            if subject is None:
                raise RuntimeError(f"Swarm Skill subject not found: {SWARM_SKILL_NAME}")
            followup_prompt = build_evolve_review_command_prompt(
                subject=subject,
                user_intent=user_intent,
                language="cn",
            )
            evolution_result = await Runner.run_agent(agent, {"query": followup_prompt}, session=session)
            print("evolution output:", evolution_result.get("output", evolution_result))
            await resume_evolution_if_requested(
                agent=agent,
                session=session,
                result=evolution_result,
                approve=approve_record,
                evolution_log=skill_dir / "evolutions.json",
            )
    finally:
        finalize_team_trace(team_name)


async def resume_evolution_if_requested(
    *,
    agent,
    session: Session,
    result: dict,
    approve: bool,
    evolution_log: Path,
) -> None:
    if result.get("result_type") != "interrupt":
        print("No approval interrupt was produced; the reviewer may have selected no_evolution.")
        return
    interrupt_ids = result.get("interrupt_ids") or []
    if not interrupt_ids:
        print("Evolution was interrupted, but no interrupt id was returned.")
        return

    tool_call_id = interrupt_ids[0]
    print("approval tool call id:", tool_call_id)
    print("approval state:", result.get("state"))
    if not approve:
        return

    # Resume the interrupted approval tool call in the original Session. The
    # evolution record is written only after the allow_once decision succeeds.
    interactive_input = InteractiveInput()
    interactive_input.update(tool_call_id, {"action": "allow_once", "feedback": ""})
    resumed = await Runner.run_agent(agent, {"query": interactive_input}, session=session)
    print("approval resume output:", resumed.get("output", resumed))
    print("approved evolution log:", evolution_log)


async def main() -> None:
    args = parse_args()
    configure_example_logging()
    workspace = prepare_workspace(args.workspace)
    model, model_name = build_model_from_env()
    # Acquire demand before retrieving the shared processor used by the Agent
    # and Team observability Rails as well as the evolution Rails.
    acquire_observability(ObservabilityConfig(exporter="console"))
    processor: TrajectorySpanProcessor = get_trajectory_span_processor()

    try:
        await Runner.start()
        print("workspace:", workspace)
        if args.scenario in {"create", "all"}:
            await run_creation_case(
                model=model,
                processor=processor,
                workspace=workspace,
                query=args.create_query,
            )
        if args.scenario in {"evolve", "all"}:
            await run_evolution_case(
                model=model,
                model_name=model_name,
                processor=processor,
                workspace=workspace,
                query=args.evolve_query,
                user_intent=args.user_intent,
                approve_record=args.approve_record,
            )
    finally:
        try:
            await Runner.stop()
        finally:
            # Runner shutdown drains detached callbacks; releasing earlier can
            # discard the spans needed to assemble the final trajectory.
            release_observability()


if __name__ == "__main__":
    asyncio.run(main())
