# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


from dataclasses import dataclass
from typing import Dict, Any, Optional
from openjiuwen.core.common.logging import context_engine_logger as logger

from openjiuwen.core.single_agent import ReActAgent, ReActAgentConfig, AgentCard
from openjiuwen.core.runner import Runner

from .service import TaskMemoryService
from .core import config as memory_config
from .tool.wikipedia_tool import wikipedia_tool
from .service.trajectory_generator import (
    RunTrialsInput,
    SummarizeTrajectoriesInput,
    format_trajectory,
    summarize_trajectories,
    run_trials,
)


@dataclass
class MemoryAgentConfigInput:
    """Input parameters for create_memory_agent_config function."""
    model_provider: str
    api_key: str
    api_base: str
    model_name: str
    system_prompt: Optional[str] = None
    max_iterations: int = 5


class ContextEvolvingReActAgent(ReActAgent):
    """ReActAgent with integrated memory retrieval capabilities.

    This agent automatically retrieves relevant memories before invoking
    the base ReActAgent, augmenting the input with contextual knowledge.

    Attributes:
        memory_service: TaskMemoryService instance for memory operations
        user_id: User identifier for memory retrieval
        inject_memories_in_context: Whether to inject memories into system prompt
    """

    def __init__(
        self,
        card: AgentCard,
        user_id: str,
        memory_service: Optional[TaskMemoryService] = None,
        inject_memories_in_context: bool = True,
        persist_type: Optional[str] = None,
        persist_path: str = "./memories/{algo_name}/{user_id}.json",
        milvus_host: str = "localhost",
        milvus_port: int = 19530,
        milvus_collection: str = "vector_nodes",
        auto_summarize: bool = False,
        auto_summarize_matts_mode: str = "none",
    ):
        """Initialize ContextEvolvingReActAgent.

        Args:
            card: Agent card (required)
            user_id: User identifier for memory retrieval
            memory_service: Optional pre-configured TaskMemoryService.
                           If not provided, a new instance will be created.
            inject_memories_in_context: If True, inject retrieved memories
                                       into the system context. If False,
                                       include them in the query augmentation.
            persist_type: Persistence backend — ``None`` (off), ``"auto"``,
                          ``"json"``, or ``"milvus"``.  Passed to
                          :class:`TaskMemoryService` when creating an internal
                          instance, and used directly for loading memories at
                          start-up.
            persist_path: File-path template for the JSON backend.
                          Default: ``"./memories/{algo_name}/{user_id}.json"``.
            milvus_host: Milvus server hostname (default: ``"localhost"``).
            milvus_port: Milvus gRPC port (default: ``19530``).
            milvus_collection: Milvus collection name (default: ``"vector_nodes"``).
            auto_summarize: When True, automatically extracts the session
                           trajectory after each invoke and calls
                           summarize_trajectories. Default: False.
            auto_summarize_matts_mode: MaTTS mode forwarded to
                                      summarize_trajectories during auto-summarize.
                                      One of "none", "parallel", "sequential",
                                      or "combined". Default: "none".
        """
        super().__init__(card)

        self.user_id = user_id
        self.inject_memories_in_context = inject_memories_in_context
        self.auto_summarize = auto_summarize
        self.auto_summarize_matts_mode = auto_summarize_matts_mode

        # Build or reuse TaskMemoryService, forwarding persist params
        if memory_service is not None:
            self.memory_service = memory_service
        else:
            self.memory_service = TaskMemoryService(
                persist_type=persist_type,
                persist_path=persist_path,
                milvus_host=milvus_host,
                milvus_port=milvus_port,
                milvus_collection=milvus_collection,
            )

        # Load existing memories from the persistence backend into the vector store
        self.memory_service.load_memories(self.user_id)

        # Simple cache for memory retrieval to support efficient parallel/repeated calls
        self._last_retrieved_query: Optional[str] = None
        self._last_retrieval_result: Optional[Dict[str, Any]] = None

        # Auto-configure from .env settings
        self._auto_configure()

        logger.info(
            "ContextEvolvingReActAgent initialized for user=%s, inject_in_context=%s, "
            "auto_summarize=%s, persist_type=%s",
            user_id, inject_memories_in_context, auto_summarize, persist_type,
        )

    def _auto_configure(self) -> None:
        """Configure the agent from .env settings via memory_config.

        Reads API_KEY, API_BASE, MODEL_NAME, and MODEL_PROVIDER from the
        loaded .env configuration and builds a ReActAgentConfig directly.
        Skips silently when API_KEY is absent (e.g. during unit tests that
        do not load a real .env).
        """
        api_key = memory_config.get("API_KEY", "")
        if not api_key:
            return
        default_system_prompt = (
            "You are a helpful assistant with access to a memory system. "
            "When relevant memories are provided in your context, use them to inform "
            "your responses. Always provide accurate, helpful answers based on both "
            "your knowledge and any retrieved memories."
        )
        config = ReActAgentConfig()
        config.configure_model_client(
            provider=memory_config.get("MODEL_PROVIDER", "OpenAI"),
            api_key=api_key,
            api_base=memory_config.get("API_BASE", "https://api.openai.com/v1"),
            model_name=memory_config.get("MODEL_NAME", "gpt-4"),
        )
        config.configure_prompt_template([{"role": "system", "content": default_system_prompt}])
        config.configure_max_iterations(5)
        self.configure(config)

    async def _invoke_with_memory(self, inputs: Any, session=None) -> Dict[str, Any]:
        """Retrieve memories, augment the query, and call the base ReActAgent.

        This is the inner single-trial invoke used by both the direct path and
        by ``run_trials`` (which calls this method directly to avoid re-entering
        the routing logic in :meth:`invoke`).
        No summarization is performed here.
        """
        if isinstance(inputs, dict):
            query = inputs.get("query", "")
        elif isinstance(inputs, str):
            query = inputs
            inputs = {"query": query}
        else:
            query = ""

        retrieval_query = inputs.get("retrieval_query", query) if isinstance(inputs, dict) else query
        logger.debug("Retrieving memories for query: %s", retrieval_query)

        try:
            if self._last_retrieved_query == retrieval_query and self._last_retrieval_result:
                memory_result = self._last_retrieval_result
                logger.info("Reusing cached memory retrieval result")
            else:
                memory_result = await self.memory_service.retrieve(
                    user_id=self.user_id,
                    query=retrieval_query,
                )
                self._last_retrieved_query = retrieval_query
                self._last_retrieval_result = memory_result
                logger.info("Retrieved %s memories for query", len(memory_result.get("retrieved_memory", [])))

            memory_string = memory_result.get("memory_string", "")
            memories_used = len(memory_result.get("retrieved_memory", []))
        except Exception as e:
            logger.error("Failed to retrieve memories: %s", e)
            memory_string = ""
            memories_used = 0

        augmented_input = inputs.copy() if isinstance(inputs, dict) else {"query": inputs}
        if memories_used > 0 and memory_string:
            if self.inject_memories_in_context:
                memory_context = (
                    f"Some Related Experience to help you complete the task:\n"
                    f"{memory_string}\n"
                )
                augmented_input["query"] = f"{memory_context}\n\n{query}"
            else:
                augmented_input["memory_context"] = memory_string
                augmented_input["memories_used"] = memories_used

        result = await super().invoke(augmented_input, session)
        if isinstance(result, dict):
            result["memories_used"] = memories_used
        return result

    async def invoke(self, inputs: Any, session=None) -> Dict[str, Any]:
        """Invoke the agent with memory retrieval and optional summarization.

        If ``matts_mode`` is explicitly provided in the inputs (even as ``"none"``),
        it delegates to :func:`run_trials` which runs the MaTTS pipeline and returns
        the summary result.

        If no ``matts_mode`` is provided, it performs a standard memory-augmented
        call using :meth:`_invoke_with_memory` and returns the generated agent output.
        If ``auto_summarize`` is enabled, it automatically summarizes the interaction
        as a side effect before returning.
        """
        # Normalize inputs
        if isinstance(inputs, dict):
            query = inputs.get("query", "")
        elif isinstance(inputs, str):
            query = inputs
            inputs = {"query": query}
        else:
            query = ""

        if not query:
            logger.warning("No query provided in inputs")
            return await super().invoke(inputs, session)

        # Delegate to run_trials when matts_mode is EXPLICTLY provided.
        matts_mode = inputs.get("matts_mode") if isinstance(inputs, dict) else None

        if matts_mode is not None:
            ground_truth = inputs.get("ground_truth", "") if isinstance(inputs, dict) else ""
            matts_k = inputs.get("matts_k", None) if isinstance(inputs, dict) else None
            return await run_trials(
                agent=self,
                params=RunTrialsInput(
                    memory_service=self.memory_service,
                    user_id=self.user_id,
                    question=query,
                    ground_truth=ground_truth,
                    matts_k=matts_k,
                    matts_mode=matts_mode,
                ),
            )

        # retrieval + query augmentation + invoke without summarization
        return await self._invoke_with_memory(inputs, session)

    def add_tool(self, tool):
        """Add a tool to this agent.

        Args:
            tool: A LocalFunction tool to add
        """

        self.ability_manager.add(tool.card)
        Runner.resource_mgr.add_tool(tool)

    def add_tools(self, tools):
        """Add multiple tools to this agent.

        Args:
            tools: List of LocalFunction tools to add
        """
        for tool in tools:
            self.add_tool(tool)

