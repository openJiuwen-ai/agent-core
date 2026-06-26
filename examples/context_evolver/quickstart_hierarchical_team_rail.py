# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Software Development Hierarchical Team with TeamContextEvolutionRail

Team structure (Hierarchical pattern):
    tech_lead (leader)   — orchestrates specialists; synthesizes shared store after each task
    ├── backend_dev      — handles API, database, and server-side issues
    └── frontend_dev     — handles UI, UX, and React issues

Memory layout:
    memories/dev_team/tech_lead.json     — leader personal store    (ACE — leader's own orchestration experience)
    memories/dev_team/backend_dev.json   — backend personal store   (ACE)
    memories/dev_team/frontend_dev.json  — frontend personal store  (ACE)
    memories/dev_team/shared.json        — shared team store        (ReasoningBank)

What the demo shows:
    2 mixed queries → tech_lead delegates to specialists → every team member
    (specialists AND tech_lead) writes its own personal store the same way and
    hands off its distilled insight to an in-memory TeamInsightBuffer (no member
    writes the shared store directly) → after each team task completes,
    tech_lead additionally drains the buffer (including its own entry) and is
    the shared store's sole writer.

Run with:
    uv run python examples/context_evolver/quickstart_hierarchical_team_rail.py
"""

import io
import os
import sys
import anyio

# ---------------------------------------------------------------------------
# Windows console encoding fix
# ---------------------------------------------------------------------------
if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "cp936", "ascii"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
if sys.stderr.encoding and sys.stderr.encoding.lower() in ("gbk", "cp936", "ascii"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# ---------------------------------------------------------------------------
# Project root on sys.path
# ---------------------------------------------------------------------------
_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _root not in sys.path:
    sys.path.insert(0, _root)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
from openjiuwen.core.common.logging import context_engine_logger as logger  # noqa: E402

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
from openjiuwen.extensions.context_evolver.core import config as app_config  # noqa: E402
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig  # noqa: E402
from openjiuwen.core.single_agent.schema.agent_card import AgentCard  # noqa: E402
from openjiuwen.core.multi_agent.schema.team_card import TeamCard  # noqa: E402
from openjiuwen.core.multi_agent.teams.hierarchical_tools import (  # noqa: E402
    HierarchicalTeam,
    HierarchicalTeamConfig,
)
from openjiuwen.harness import create_deep_agent  # noqa: E402
from openjiuwen.core.runner import Runner  # noqa: E402
from openjiuwen.extensions.context_evolver import TaskMemoryService  # noqa: E402
from openjiuwen.harness.rails import TeamContextEvolutionRail, TeamInsightBuffer  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_DEFAULTS = {
    "API_KEY": "your_api_key_here",
    "API_BASE": "https://api.deepseek.com/v1",
    "MODEL_NAME": "deepseek-v4-flash",
    "MODEL_PROVIDER": "DeepSeek",
    "LLM_TEMPERATURE": 1.0,
    "LLM_SEED": 42,
    "LLM_SSL_VERIFY": False,
    "EMBEDDING_MODEL": "text-embedding-3-small",
    "EMBEDDING_DIMENSIONS": 2560,
    "EMBEDDING_API_KEY": "your_embedding_api_key_here",
    "EMBEDDING_API_BASE": "https://api.openai.com/v1",
}
for _k, _v in _DEFAULTS.items():
    app_config.set_value(_k, _v)

logger.reconfigure({
    "level": "INFO",
    "output": ["console"],
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
})
logger.logger().propagate = False

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
DIVIDER = "=" * 65
SUBDIV = "-" * 55


def _banner(title: str) -> None:
    logger.info("")
    logger.info(DIVIDER)
    logger.info(title)
    logger.info(DIVIDER)


def _section(label: str) -> None:
    logger.info("")
    logger.info("  %s", label)
    logger.info("  %s", SUBDIV)


def _make_card(agent_id: str, description: str, input_params: dict | None = None) -> AgentCard:
    kwargs: dict = {"id": agent_id, "name": agent_id, "description": description}
    if input_params is not None:
        kwargs["input_params"] = input_params
    return AgentCard(**kwargs)


def _log_result(result, label: str) -> None:
    if isinstance(result, dict):
        out = result.get("output", str(result))
        mem = result.get("memories_used", 0)
    else:
        out = str(result or "")
        mem = 0
    logger.info("  [%s] memories_used : %d", label, mem)
    logger.info("  [%s] response      :", label)
    for line in str(out).splitlines()[:6]:
        logger.info("    %s", line)
    if len(str(out).splitlines()) > 6:
        logger.info("    ... (truncated)")


# =============================================================================
# MAIN
# =============================================================================

async def main() -> None:

    _banner("Software Development Hierarchical Team with TeamContextEvolutionRail")
    logger.info("tech_lead    — team leader (orchestrates + synthesizes shared store)")
    logger.info("backend_dev  — specialist: API, database, server-side issues")
    logger.info("frontend_dev — specialist: UI, UX, React issues")
    logger.info("Specialists write personal (ACE) store; insights hand off to an in-memory buffer.")
    logger.info("tech_lead drains the buffer and is the shared (ReasoningBank) store's sole writer.")

    # -------------------------------------------------------------------------
    # Step 0 — Configuration check
    # -------------------------------------------------------------------------
    _section("[Step 0] Checking configuration")

    api_key = app_config.get("API_KEY", "")
    api_base = app_config.get("API_BASE", "https://api.openai.com/v1")
    model_name = app_config.get("MODEL_NAME", "gpt-4o")
    model_provider = app_config.get("MODEL_PROVIDER", "OpenAI")

    if not api_key or api_key == "your_api_key_here":
        logger.error("API key not configured. Edit API_KEY in _DEFAULTS.")
        return

    logger.info("  API Base : %s", api_base)
    logger.info("  Model    : %s / %s", model_provider, model_name)

    await Runner.start()
    try:
        model = Model(
            model_client_config=ModelClientConfig(
                client_provider=model_provider,
                api_key=api_key,
                api_base=api_base,
                verify_ssl=False,
            ),
            model_config=ModelRequestConfig(model=model_name),
        )

        # -------------------------------------------------------------------------
        # Step 1 — Create memory services
        # -------------------------------------------------------------------------
        _section("[Step 1] Creating memory services")

        TEAM_ID = "dev_team"
        mem_dir = os.path.join(_root, "memories")

        # Optional override: set TEAM_ALGO to apply the same retrieval/summary
        # algorithm to every store (shared + all personal) for compatibility
        # testing. Unset keeps the demo's normal ACE-personal / ReasoningBank-shared split.
        _shared_algo = "COGNITION"
        _personal_algo = "REME"

        # Shared team store — all specialists distil insights here; leader synthesizes from here.
        shared_svc = TaskMemoryService(
            persist_type="json",
            persist_path=os.path.join(mem_dir, TEAM_ID, "shared.json"),
            retrieval_algo=_shared_algo,
            summary_algo=_shared_algo,
        )

        # Personal stores — raw task experiences per specialist.
        personal_svc_backend = TaskMemoryService(
            persist_type="json",
            persist_path=os.path.join(mem_dir, TEAM_ID, "backend_dev.json"),
            retrieval_algo=_personal_algo,
            summary_algo=_personal_algo,
        )
        personal_svc_frontend = TaskMemoryService(
            persist_type="json",
            persist_path=os.path.join(mem_dir, TEAM_ID, "frontend_dev.json"),
            retrieval_algo=_personal_algo,
            summary_algo=_personal_algo,
        )
        # Leader personal store — tech_lead writes its own orchestration trajectory
        # here exactly like a specialist, in addition to its shared-store synthesis.
        personal_svc_leader = TaskMemoryService(
            persist_type="json",
            persist_path=os.path.join(mem_dir, TEAM_ID, "tech_lead.json"),
            retrieval_algo=_personal_algo,
            summary_algo=_personal_algo,
        )

        # Unify all services onto one vector store for consistent counts across session.
        from openjiuwen.extensions.context_evolver.core.context import ServiceContext as _SC
        _unified_vs = _SC().get_service("vector_store")
        shared_svc.vector_store = _unified_vs
        personal_svc_backend.vector_store = _unified_vs
        personal_svc_frontend.vector_store = _unified_vs
        # personal_svc_leader already uses _unified_vs (last created)

        def _count(ws: str) -> int:
            return len(_unified_vs.get_all(metadata_filter={"workspace_id": ws}))

        logger.info("  shared store     : %s/%s/shared.json", mem_dir, TEAM_ID)
        logger.info("  backend personal : %s/%s/backend_dev.json", mem_dir, TEAM_ID)
        logger.info("  frontend personal: %s/%s/frontend_dev.json", mem_dir, TEAM_ID)
        logger.info("  leader personal  : %s/%s/tech_lead.json", mem_dir, TEAM_ID)

        # -------------------------------------------------------------------------
        # Step 2 — Create TeamContextEvolutionRails
        # -------------------------------------------------------------------------
        _section("[Step 2] Creating TeamContextEvolutionRails")

        # Shared by every rail in the team — specialists hand off distilled insights
        # here; leader_rail drains it and is the shared store's sole writer.
        insight_buffer = TeamInsightBuffer()

        backend_rail = TeamContextEvolutionRail(
            team_id=TEAM_ID,
            agent_role="backend_dev",
            personal_service=personal_svc_backend,
            shared_service=shared_svc,
            insight_buffer=insight_buffer,
            is_team_leader=False,
            auto_summarize=True,
        )

        frontend_rail = TeamContextEvolutionRail(
            team_id=TEAM_ID,
            agent_role="frontend_dev",
            personal_service=personal_svc_frontend,
            shared_service=shared_svc,
            insight_buffer=insight_buffer,
            is_team_leader=False,
            auto_summarize=True,
        )

        # Attached to leader_agent — after_task_iteration writes tech_lead's own
        # orchestration trajectory to its personal store (same as a specialist),
        # then drains insight_buffer and synthesizes once the whole team task completes.
        leader_rail = TeamContextEvolutionRail(
            team_id=TEAM_ID,
            agent_role="tech_lead",
            personal_service=personal_svc_leader,
            shared_service=shared_svc,
            insight_buffer=insight_buffer,
            is_team_leader=True,
            auto_summarize=True,
        )

        logger.info("  backend_rail  ready (ns=%s:backend_dev,  leader=False)", TEAM_ID)
        logger.info("  frontend_rail ready (ns=%s:frontend_dev, leader=False)", TEAM_ID)
        logger.info("  leader_rail   ready (ns=%s:tech_lead,    leader=True)", TEAM_ID)

        # -------------------------------------------------------------------------
        # Step 3 — Create agents
        # -------------------------------------------------------------------------
        _section("[Step 3] Building agents")

        # Specialist cards expose a "query" input so the leader can call them as tools.
        backend_card = _make_card(
            "backend_dev",
            "Backend developer specialising in REST API design, database optimisation, and server-side performance issues",
            input_params={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The backend development task or question to handle"},
                },
                "required": ["query"],
            },
        )
        frontend_card = _make_card(
            "frontend_dev",
            "Frontend developer specialising in React components, UI/UX improvements, and client-side performance",
            input_params={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The frontend development task or question to handle"},
                },
                "required": ["query"],
            },
        )
        leader_card = _make_card(
            "tech_lead",
            "Technical lead who orchestrates the development team and synthesises team knowledge",
        )

        backend_agent = create_deep_agent(
            model=model,
            card=backend_card,
            system_prompt=(
                "You are a backend developer specialising in REST API design, database "
                "optimisation, and server-side performance. "
                "When a [MEMORY CONTEXT] block appears in your context, draw on those "
                "memories to provide experience-based technical solutions. "
                "Give concrete, actionable recommendations with code examples where relevant."
            ),
            enable_task_loop=True,
            max_iterations=5,
            rails=[backend_rail],
        )

        frontend_agent = create_deep_agent(
            model=model,
            card=frontend_card,
            system_prompt=(
                "You are a frontend developer specialising in React, UI/UX design, and "
                "client-side performance optimisation. "
                "When a [MEMORY CONTEXT] block appears in your context, draw on those "
                "memories to provide experience-based technical solutions. "
                "Give concrete, actionable recommendations with code examples where relevant."
            ),
            enable_task_loop=True,
            max_iterations=5,
            rails=[frontend_rail],
        )

        leader_agent = create_deep_agent(
            model=model,
            card=leader_card,
            system_prompt=(
                "You are the Technical Lead of a software development team. "
                "Your team members are:\n"
                "  - backend_dev: handles API design, database queries, and server-side issues\n"
                "  - frontend_dev: handles React components, UI/UX, and client-side issues\n\n"
                "For each task:\n"
                "1. Identify which parts need backend work and which need frontend work.\n"
                "2. Delegate backend tasks to backend_dev and frontend tasks to frontend_dev.\n"
                "3. Compile their responses into a comprehensive, unified solution.\n\n"
                "When a [MEMORY CONTEXT] block appears, use those team insights to guide "
                "your delegation strategy and final synthesis."
            ),
            enable_task_loop=True,
            max_iterations=10,
            rails=[leader_rail],
        )

        # -------------------------------------------------------------------------
        # Step 4 — Build HierarchicalTeam
        # -------------------------------------------------------------------------
        _section("[Step 4] Building HierarchicalTeam")

        team_card = TeamCard(
            id="dev_hierarchical_team",
            name="dev_hierarchical_team",
            description="Software development hierarchical team with context evolution",
        )

        team_config = HierarchicalTeamConfig(root_agent=leader_card, message_timeout=600.0)
        team = HierarchicalTeam(card=team_card, config=team_config)

        # Root: tech_lead
        team.add_agent(leader_card, lambda: leader_agent)
        # Children of tech_lead: specialists
        team.add_agent(backend_card, lambda: backend_agent, parent_agent_id="tech_lead")
        team.add_agent(frontend_card, lambda: frontend_agent, parent_agent_id="tech_lead")

        logger.info("  HierarchicalTeam: tech_lead → backend_dev | frontend_dev")

        # =========================================================================
        # Round 1 — Build experience (2 mixed queries)
        # =========================================================================
        _banner("Round 1 — Building team experience (2 queries)")
        logger.info("tech_lead delegates to specialists; each specialist writes to personal + shared.")
        logger.info("After each task, tech_lead synthesizes specialist insights into shared store.")

        round1_cases = [
            {
                "id": "T-1",
                "query": (
                    "Our REST API endpoint /users/profile is taking over 3 seconds to respond "
                    "due to N+1 database queries. The frontend React component also re-renders "
                    "on every keystroke causing UI lag. We need fixes for both issues."
                ),
            },
            {
                "id": "T-2",
                "query": (
                    "We need to add JWT refresh token rotation to the authentication service. "
                    "On the frontend, the login form does not show field-level validation errors "
                    "and submits even when fields are empty. Fix both."
                ),
            },
        ]

        for case in round1_cases:
            _section(f"[{case['id']}] {case['query'][:65]}...")
            logger.info("  Query: %s", case["query"])

            backend_ns = f"{TEAM_ID}:backend_dev"
            frontend_ns = f"{TEAM_ID}:frontend_dev"

            shared_before = _count(TEAM_ID)
            backend_before = _count(backend_ns)
            frontend_before = _count(frontend_ns)

            # Pre-configure task_status for all rails before team.invoke().
            # Specialist rails fire internally when leader calls them as tools;
            # leader rail fires after the full team task completes.
            backend_rail.set_task_status("resolved")
            frontend_rail.set_task_status("resolved")
            leader_rail.set_task_status("resolved")

            result = await team.invoke({"query": case["query"]})
            _log_result(result, case["id"])

            logger.info(
                "  Store Δ → backend: %d→%d  frontend: %d→%d  shared: %d→%d",
                backend_before, _count(backend_ns),
                frontend_before, _count(frontend_ns),
                shared_before, _count(TEAM_ID),
            )

        # =========================================================================
        # Final summary
        # =========================================================================
        _section("Final Summary")
        leader_ns = f"{TEAM_ID}:tech_lead"
        logger.info("  backend personal : %d node(s)  ws=%s", _count(backend_ns), backend_ns)
        logger.info("  frontend personal: %d node(s)  ws=%s", _count(frontend_ns), frontend_ns)
        logger.info("  leader personal  : %d node(s)  ws=%s", _count(leader_ns), leader_ns)
        logger.info("  shared (team)    : %d node(s)  ws=%s", _count(TEAM_ID), TEAM_ID)
        logger.info("  total nodes      : %d", len(_unified_vs.get_all()))
        logger.info("")
        logger.info("  Built specialist experience across 2 resolved tasks.")
        logger.info("  After each task, tech_lead synthesized specialist insights in shared store.")

        _banner("Demo Complete!")

    finally:
        await Runner.stop()


if __name__ == "__main__":
    anyio.run(main)
