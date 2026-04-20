# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
TrajectoryCollector
-------------------

Collects agent execution trajectory for RL training.

Uses EvolutionRail-based RLRail which provides:
- Automatic trajectory collection via EvolutionRail base class
- RL-specific state tracking (LLM step counts, case_id)

Usage::

    collector = TrajectoryCollector()
    trajectory = await collector.collect(agent, inputs={"query": "..."})
"""

from typing import Any, Dict, List, Optional

from openjiuwen.agent_evolving.trajectory import InMemoryTrajectoryStore, Trajectory


class TrajectoryCollector:
    """Run an agent and collect trajectory data.

    Returns a Trajectory object assembled by TrajectoryBuilder.

    Usage::

        collector = TrajectoryCollector()
        trajectory = await collector.collect(agent, inputs={"query": "..."})
    """

    async def collect(
        self,
        agent: Any,
        inputs: Dict[str, Any],
        *,
        session_id: str = "",
        source: str = "offline",
        case_id: Optional[str] = None,
    ):
        """Run agent and return a Trajectory object.

        Args:
            agent: A DeepAgent (or any agent supporting register_rail).
            inputs: Agent input dict (must contain 'query').
            session_id: Session identifier for the Trajectory.
            source: Source type - "online" or "offline".
            case_id: Optional case ID for offline scenarios.

        Returns:
            Trajectory object collected during the run.
        """
        if not hasattr(agent, "register_rail"):
            raise ValueError(
                "Agent does not support rail-based trajectory collection. "
                "Use a DeepAgent with register_rail()."
            )

        effective_session_id = session_id or inputs.get("conversation_id", "")
        effective_case_id = case_id or inputs.get("conversation_id", None)

        # Import here to avoid circular dependency
        from openjiuwen.agent_evolving.agent_rl.rl_rail import RLRail

        # Create a store to capture the trajectory (base class will save to it)
        store = InMemoryTrajectoryStore()

        rail = RLRail(
            session_id=effective_session_id,
            source=source,
            case_id=effective_case_id,
            trajectory_store=store,
        )
        await agent.register_rail(rail)

        # Create session for the agent
        session = None
        from openjiuwen.core.session.agent import create_agent_session

        session = create_agent_session(
            session_id=effective_session_id
            or inputs.get("conversation_id", "default"),
            card=agent.card if hasattr(agent, "card") else None,
        )
        await session.pre_run(inputs=inputs)

        try:
            if hasattr(agent, "invoke"):
                await agent.invoke(inputs, session=session)
            else:
                from openjiuwen.core.runner.runner import Runner
                await Runner.run_agent(agent=agent, inputs=inputs, session=session)
        except Exception as e:
            from openjiuwen.core.common.logging import logger

            logger.warning(
                "Agent invoke raised exception during trajectory collection, "
                "returning partial trajectory. error=%s",
                e,
            )
        finally:
            if hasattr(agent, "unregister_rail"):
                await agent.unregister_rail(rail)
            if session is not None:
                await session.post_run()

        # Retrieve trajectory from store (base class after_invoke() saves it there)
        trajectories: List[Trajectory] = store.query()
        if not trajectories:
            return None

        # Return the last trajectory (most recent by insertion order)
        # InMemoryTrajectoryStore stores in dict, so we get the last saved one
        trajectory = trajectories[-1]

        # Enhance with RL-specific metadata if available
        trajectory.source = source
        trajectory.session_id = effective_session_id
        trajectory.case_id = effective_case_id

        return trajectory
