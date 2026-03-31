# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations
from typing import Any, Dict, List, Optional
from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.single_agent.rail import AgentCallbackContext
from openjiuwen.deepagents.rails.base import DeepAgentRail
from openjiuwen.extensions.context_evolver.service import (
    TaskMemoryService,
    SummarizeTrajectoriesInput,
    format_trajectory,
    summarize_trajectories as _summarize_trajectories,
    evaluate_trial as _evaluate_trial,
)

# ---------------------------------------------------------------------------
# TaskMemoryRail
# ---------------------------------------------------------------------------


class TaskMemoryRail(DeepAgentRail):

    priority: int = 50  # run before user-defined callbacks (default priority = 100)

    def __init__(
        self,
        user_id: str,
        memory_service: Optional[TaskMemoryService] = None,
        inject_memories_in_context: Optional[bool] = True,
        auto_summarize: Optional[bool] = True,
        auto_summarize_matts_mode: Optional[str] = "none",
    ) -> None:
        super().__init__()
        self.user_id = user_id
        self.inject_memories_in_context = inject_memories_in_context
        self.auto_summarize = auto_summarize
        self.auto_summarize_matts_mode = auto_summarize_matts_mode

        self.memory_service = memory_service if memory_service is not None else TaskMemoryService()

        # Per-iteration state – reset at the start of each before_task_iteration
        self.memories_used: int = 0
        self.original_prompt_template: Optional[List[Dict]] = None

        # Simple retrieval cache
        self.last_retrieved_query: Optional[str] = None
        self.last_retrieval_result: Optional[Dict[str, Any]] = None

        # Agent reference
        self._agent: Optional[Any] = None

        # Tool management
        self._pending_tools: List[Any] = []
        self._tools_applied: bool = False

        # Current query saved in before_task_iteration for use in after_task_iteration
        self._current_query: str = ""



        self.memory_service.load_memories(self.user_id)

        logger.info(
            "TaskMemoryRail initialised for user=%s, inject_in_context=%s, "
            "auto_summarize=%s",
            user_id,
            inject_memories_in_context,
            auto_summarize,
        )

    # ------------------------------------------------------------------
    # Public read-only state accessors
    # ------------------------------------------------------------------

    @property
    def pending_tools(self) -> List[Any]:
        """Tools queued for registration on the next iteration."""
        return self._pending_tools

    @property
    def tools_applied(self) -> bool:
        """``True`` once pending tools have been flushed to the agent."""
        return self._tools_applied

    @property
    def agent(self) -> Optional[Any]:
        """The agent this rail is bound to, or ``None`` before binding."""
        return self._agent

    @property
    def current_query(self) -> str:
        """The query captured in the most recent ``before_task_iteration``."""
        return self._current_query


    # ------------------------------------------------------------------
    # BEFORE_TASK_ITERATION hook
    # ------------------------------------------------------------------


    async def before_task_iteration(self, ctx: AgentCallbackContext) -> None:
        # Reset per-iteration state
        self.memories_used = 0
        self.original_prompt_template = None

        # Capture agent reference on first iteration
        if self._agent is None:
            self._agent = ctx.agent

        # TaskIterationInputs carries .query directly
        query = getattr(ctx.inputs, "query", None) or ""
        retrieval_query = getattr(ctx.inputs, "retrieval_query", None) or query

        if not query:
            return

        # Save for use in after_task_iteration auto-summarize
        self._current_query = query

        # --- retrieve ---
        memory_string = ""
        try:
            if (
                self.last_retrieved_query == retrieval_query
                and self.last_retrieval_result is not None
            ):
                memory_result = self.last_retrieval_result
                logger.info("Reusing cached memory retrieval result")
            else:
                memory_result = await self.memory_service.retrieve(
                    user_id=self.user_id,
                    query=retrieval_query,
                )
                self.last_retrieved_query = retrieval_query
                self.last_retrieval_result = memory_result

            memory_string = memory_result.get("memory_string", "")
            retrieved_memory = memory_result.get("retrieved_memory", [])
            self.memories_used = len(retrieved_memory)
            logger.info("Retrieved %s memories for query", self.memories_used)

        except Exception as exc:
            logger.error("Failed to retrieve memories: %s", exc)
            return

        if not (self.memories_used > 0 and memory_string and self.inject_memories_in_context):
            return

        # --- inject into system prompt ---
        # For DeepAgent the prompt_template lives on the inner react_agent
        agent = ctx.agent
        inner_agent = getattr(agent, "react_agent", agent)
        if not (
            inner_agent is not None
            and hasattr(inner_agent, "config")
            and hasattr(inner_agent.config, "prompt_template")
        ):
            logger.warning(
                "Agent has no config.prompt_template – skipping memory injection"
            )
            return

        # Deep-copy current template so after_task_iteration can restore it exactly
        self.original_prompt_template = [
            dict(msg) for msg in inner_agent.config.prompt_template
        ]

        memory_block = (
            f"Some Related Experience to help you complete the task:\n"
            f"{memory_string}\n"
        )

        new_template: List[Dict] = []
        for msg in inner_agent.config.prompt_template:
            if msg.get("role") == "system":
                new_template.append({
                    "role": "system",
                    "content": (
                        (msg.get("content", "") + f"\n\n{memory_block}").strip()
                    ),
                })
            else:
                new_template.append(dict(msg))

        inner_agent.config.prompt_template = new_template
        logger.debug("Injected memory context into agent system prompt")

    # ------------------------------------------------------------------
    # AFTER_TASK_ITERATION hook
    # ------------------------------------------------------------------

    async def after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        agent = ctx.agent
        inner_agent = getattr(agent, "react_agent", agent)

        # Restore the original prompt template
        if self.original_prompt_template is not None:
            if (
                inner_agent is not None
                and hasattr(inner_agent, "config")
                and hasattr(inner_agent.config, "prompt_template")
            ):
                inner_agent.config.prompt_template = self.original_prompt_template
            self.original_prompt_template = None
            logger.debug("Restored original agent system prompt")

        # Attach memories_used to the result dict returned by invoke()
        result = getattr(ctx.inputs, "result", None)
        if isinstance(result, dict):
            result["memories_used"] = self.memories_used

        # Auto-summarize: only support matts_mode = "none", because other matts_mode need to call multiple invoke
        # To use other matts_mode, please manually use the trajectory_generator in the memory service
        if self.auto_summarize and self._current_query:
            try:
                trajectory = self.extract_trajectory(ctx)
                if trajectory:
                    # use invoke_result.get("output", "") instead of trajectory
                    # for simple questions and trajectory for complex questions
                    # use ground_truth if available for evaluate_trial
                    # modify evaluate_trial based on use case on trajectory_generator.py
                    feedback, score = _evaluate_trial(self._current_query, trajectory)
                    logger.info("Running auto-summarize for current trajectory")
                    await _summarize_trajectories(
                        self.memory_service,
                        self.user_id,
                        SummarizeTrajectoriesInput(
                            query=self._current_query,
                            trajectory=[trajectory],
                            matts_mode="none",
                            feedback=[feedback],
                            score=[score],
                        ),
                    )
            except Exception as exc:
                logger.error("Auto-summarize in after_task_iteration failed: %s", exc)

    # ------------------------------------------------------------------
    # Trajectory helpers
    # ------------------------------------------------------------------

    def extract_trajectory(self, ctx: AgentCallbackContext) -> Optional[str]:
        try:
            agent = ctx.agent
            session = ctx.session
            if session is None:
                return None
            # For DeepAgent, context_engine lives on the inner react_agent
            inner_agent = getattr(agent, "react_agent", agent)
            if inner_agent is None or not hasattr(inner_agent, "context_engine"):
                return None
            context = inner_agent.context_engine.get_context(
                session_id=session.get_session_id(),
                context_id="default_context_id",
            )
            if context:
                return format_trajectory(context.get_messages())
        except Exception as exc:
            logger.warning("Failed to extract trajectory: %s", exc)
        return None


__all__ = [
    "TaskMemoryRail",
    "SummarizeTrajectoriesInput",
]
