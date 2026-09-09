# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regular Skill creation and evolution examples with DeepAgent.

The creation case demonstrates ``SkillCreateRail`` detecting a reusable
workflow and asking the Agent to propose Skill creation. The evolution case
mirrors a host ``/evolve`` command for an existing Skill.

Run both cases with:

    uv sync --extra observability
    uv run --extra observability python -m \
      examples.agent_evolving.skill_evolution_example --scenario all
"""

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from openjiuwen.agent_evolving.trajectory import TrajectorySpanProcessor
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session.agent import Session
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.demand import get_trajectory_span_processor
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.observability import (
    AgentObservabilityRail,
    acquire_observability,
    close_agent_run_span,
    install_subagent_observability_hook,
    open_agent_run_span,
    release_observability,
)
from openjiuwen.harness.rails import SkillCreateRail, SkillEvolutionRail, configure_skill_evolution
from openjiuwen.harness.rails.evolution import build_evolve_review_command_prompt


SKILL_NAME = "research-helper"
DEFAULT_CREATE_QUERY = (
    "请调用 read_file 分别读取 source-a.md 和 source-b.md，再调用 write_file 把两份资料整理到 brief.md。"
    "必须实际调用工具，并在最后说明你采用的整理流程。"
)
DEFAULT_EVOLVE_QUERY = (
    "请先调用 skill_tool 使用 research-helper，然后整理一份三点式的 AI 行业简报。每一点都要包含结论和核验建议。"
)
DEFAULT_USER_INTENT = "增加一条在总结前检查每个结论是否包含核验建议的可复用规则。"


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


async def run_agent_with_observability(
    agent: Any,
    inputs: dict[str, Any],
    *,
    session: Session,
    mode: str,
) -> dict[str, Any]:
    """Run one Agent turn under the current single-Agent observability API."""
    session_id = session.get_session_id()
    root_span = open_agent_run_span(session_id=session_id, mode=mode)
    try:
        result = await Runner.run_agent(agent, inputs, session=session)
    except BaseException as exc:
        close_agent_run_span(root_span, session_id=session_id, exception=exc)
        raise
    close_agent_run_span(
        root_span,
        session_id=session_id,
        output=result.get("output", result),
    )
    return result


def prepare_workspace(workspace: str | None) -> Path:
    root = (
        Path(workspace).expanduser().resolve()
        if workspace
        else Path(tempfile.mkdtemp(prefix="skill_evolution_example_")).resolve()
    )
    root.mkdir(parents=True, exist_ok=True)
    (root / "skills").mkdir(exist_ok=True)
    (root / "source-a.md").write_text("模型能力：工具调用稳定性持续提升。\n", encoding="utf-8")
    (root / "source-b.md").write_text("工程建议：结论应附带可复核的证据路径。\n", encoding="utf-8")
    return root


def write_demo_skill(workspace: Path) -> Path:
    # Evolution reviews an existing Skill. Seed it independently from the
    # creation case so either CLI scenario can run on its own.
    skill_dir = workspace / "skills" / SKILL_NAME
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {SKILL_NAME}\n"
        "description: Produce concise research briefs with verification guidance.\n"
        "---\n\n"
        "# Workflow\n\n"
        "1. Identify the three most relevant conclusions.\n"
        "2. State one concrete verification suggestion for each conclusion.\n"
        "3. Keep the final brief concise.\n",
        encoding="utf-8",
    )
    return skill_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Regular Skill creation and evolution example")
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
    # SkillCreateRail observes the completed tool workflow and injects the
    # creation self-check. It deliberately stops at the user confirmation
    # boundary; skill-creator owns file generation after confirmation.
    creation_rail = SkillCreateRail(
        skills_dir=str(workspace / "skills"),
        trajectory_span_processor=processor,
        language="cn",
    )
    agent = create_deep_agent(
        model=model,
        system_prompt=(
            "你是资料整理助手。必须按用户要求实际调用文件工具。"
            "任务完成后，遵循系统中的 Skill 创建自检规则判断流程是否值得复用。"
        ),
        rails=[creation_rail, AgentObservabilityRail()],
        enable_task_loop=True,
        max_iterations=8,
        trajectory_span_processor=processor,
        workspace=str(workspace),
        language="cn",
    )
    session_id = f"skill_create_{uuid.uuid4().hex}"
    session = Session(session_id=session_id, card=agent.card)
    result = await run_agent_with_observability(
        agent,
        {"query": query},
        session=session,
        mode="skill.create",
    )

    print("\n=== Regular Skill creation case ===")
    print("output:", result.get("output", result))
    print(
        "If the execution met the reuse threshold, the final response asks for confirmation. "
        "After confirmation, the Agent hands off to skill-creator to create and validate the Skill."
    )


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
    skill_dir = write_demo_skill(workspace)
    agent = create_deep_agent(
        model=model,
        system_prompt="Use an explicitly requested Skill before completing the task.",
        skills=[SKILL_NAME],
        rails=[AgentObservabilityRail()],
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
        signal_trigger=False,
        review_trigger=False,
        async_evolution=False,
        auto_save=False,
        language="cn",
    )
    # Passive triggers are disabled above because this example mirrors an
    # explicit host /evolve command. Keep the concrete regular-Skill Rail;
    # team evolution uses a separate Rail and subject kind.
    evolution_rail = next(
        rail for rail in agent.find_rails_by_type((SkillEvolutionRail,)) if rail.__class__ is SkillEvolutionRail
    )
    session_id = f"skill_evolve_{uuid.uuid4().hex}"
    session = Session(session_id=session_id, card=agent.card)

    print("\n=== Regular Skill evolution case ===")
    initial_result = await run_agent_with_observability(
        agent,
        {"query": query},
        session=session,
        mode="skill.use",
    )
    print("initial output:", initial_result.get("output", initial_result))

    # The host resolves the requested Skill and turns /evolve plus the user's
    # intent into an Agent prompt. Reusing the Session lets the review consume
    # the trajectory produced by the preceding Skill invocation.
    subject = evolution_rail.store.resolve_subject_payload(SKILL_NAME)
    if subject is None:
        raise RuntimeError(f"Skill subject not found: {SKILL_NAME}")
    followup_prompt = build_evolve_review_command_prompt(
        subject=subject,
        user_intent=user_intent,
        language="cn",
    )
    evolution_result = await run_agent_with_observability(
        agent,
        {"query": followup_prompt},
        session=session,
        mode="skill.evolve",
    )
    print("evolution output:", evolution_result.get("output", evolution_result))
    await resume_evolution_if_requested(
        agent=agent,
        session=session,
        result=evolution_result,
        approve=approve_record,
        evolution_log=skill_dir / "evolutions.json",
    )


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

    # Approval resumes the interrupted tool call in the same Session. The
    # allow_once decision permits this proposal only; the Rail persists the
    # record after the resumed call completes.
    interactive_input = InteractiveInput()
    interactive_input.update(tool_call_id, {"action": "allow_once", "feedback": ""})
    resumed = await run_agent_with_observability(
        agent,
        {"query": interactive_input},
        session=session,
        mode="skill.evolve.resume",
    )
    print("approval resume output:", resumed.get("output", resumed))
    print("approved evolution log:", evolution_log)


async def main() -> None:
    args = parse_args()
    workspace = prepare_workspace(args.workspace)
    model, model_name = build_model_from_env()
    # Observability demand must be acquired before retrieving the process-wide
    # processor shared by AgentObservabilityRail and the evolution Rail.
    acquire_observability(ObservabilityConfig(exporter="console"))
    install_subagent_observability_hook()
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
            # Detached span callbacks may finish during Runner shutdown, so
            # release the shared observability runtime only afterward.
            release_observability()


if __name__ == "__main__":
    asyncio.run(main())
