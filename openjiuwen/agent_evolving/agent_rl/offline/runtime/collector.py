# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Run one offline agent invocation and return its canonical trajectory."""

from typing import Any, Dict, Optional

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionTriggerPoint


async def run_agent_and_collect_trajectory(
    agent: Any,
    inputs: Dict[str, Any],
    *,
    trajectory_span_processor: TrajectorySpanProcessor,
    session_id: str = "",
    source: str = "offline",
    case_id: Optional[str] = None,
) -> Optional[Trajectory]:
    """Run the agent and read its trajectory before unregistering the rail."""

    if not isinstance(trajectory_span_processor, TrajectorySpanProcessor):
        raise TypeError("trajectory_span_processor must be a TrajectorySpanProcessor")
    if not hasattr(agent, "register_rail"):
        raise ValueError(
            "Agent does not support rail-based trajectory collection. Use a DeepAgent with register_rail()."
        )

    effective_session_id = str(session_id or inputs.get("conversation_id") or "default")
    effective_case_id = case_id or inputs.get("conversation_id")

    from openjiuwen.agent_evolving.agent_rl.rl_rail import RLRail

    rail = RLRail(
        session_id=effective_session_id,
        source=source,
        case_id=effective_case_id,
        evolution_trigger=EvolutionTriggerPoint.NONE,
        trajectory_span_processor=trajectory_span_processor,
    )
    await agent.register_rail(rail)

    from openjiuwen.core.common.logging import logger
    from openjiuwen.core.session.agent import create_agent_session

    session = None
    session_ready = False
    trajectory: Trajectory | None = None
    try:
        session = create_agent_session(
            session_id=effective_session_id,
            card=agent.card if hasattr(agent, "card") else None,
        )
        await session.pre_run(inputs=inputs)
        session_ready = True
        try:
            if hasattr(agent, "invoke"):
                await agent.invoke(inputs, session=session)
            else:
                from openjiuwen.core.runner.runner import Runner

                await Runner.run_agent(agent=agent, inputs=inputs, session=session)
        except Exception as exc:
            logger.warning(
                "Agent invoke raised exception during trajectory collection, returning partial trajectory. error=%s",
                exc,
            )
    finally:
        # Read the clean window while its processor subscription is still
        # active, including the invoke-failure path.
        if session_ready:
            member_id = None
            get_agent_id = getattr(session, "get_agent_id", None)
            if callable(get_agent_id):
                try:
                    candidate = get_agent_id()
                except Exception:
                    candidate = None
                if isinstance(candidate, str) and candidate:
                    member_id = candidate
            try:
                trajectory = rail.get_trajectory(session_id=effective_session_id, member_id=member_id)
            except Exception as exc:
                logger.warning("Failed to read collected trajectory: %s", exc)

        if hasattr(agent, "unregister_rail"):
            try:
                await agent.unregister_rail(rail)
            except Exception as exc:
                logger.warning("Failed to unregister trajectory collection rail: %s", exc)
        if session is not None:
            try:
                await session.close_stream()
            except Exception as exc:
                logger.warning("Failed to close trajectory collection session: %s", exc)
            if session_ready:
                try:
                    await session.commit()
                except Exception as exc:
                    logger.warning("Failed to commit trajectory collection session: %s", exc)

    return trajectory


__all__ = ["run_agent_and_collect_trajectory"]
