# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import io
import json
import os
import sys
import anyio
# ---------------------------------------------------------------------------
# Windows console encoding fix — MUST be before any logging import
# ---------------------------------------------------------------------------
if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "cp936", "ascii"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
if sys.stderr.encoding and sys.stderr.encoding.lower() in ("gbk", "cp936", "ascii"):
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

# ---------------------------------------------------------------------------
# Project root on sys.path — MUST be before any openjiuwen import
# ---------------------------------------------------------------------------
_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _root not in sys.path:
    sys.path.append(_root)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

from openjiuwen.core.common.logging import context_engine_logger as logger

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
from openjiuwen.extensions.context_evolver.core import config as app_config
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.deepagents import create_deep_agent
from openjiuwen.core.runner import Runner
from openjiuwen.extensions.context_evolver import (
    TaskMemoryService,
    AddMemoryRequest,
)
from openjiuwen.deepagents.rails import TaskMemoryRail

# ---------------------------------------------------------------------------
# Environment variables — edit these if you don't have a .env file
# ---------------------------------------------------------------------------
_DEFAULTS = {
    "API_KEY": "your_api_key_here",
    "API_BASE": "https://api.openai.com/v1",
    "MODEL_NAME": "gpt-5.2",
    "MODEL_PROVIDER": "OpenAI",
    "EMBEDDING_MODEL": "text-embedding-3-small",
    "EMBEDDING_DIMENSIONS": 2560,
    "LLM_TEMPERATURE": 0.7,
    "LLM_SEED": 42,
    "LLM_SSL_VERIFY": False,
}
for _k, _v in _DEFAULTS.items():
    app_config.set_value(_k, _v)

logger.reconfigure({
    "level": "INFO",
    "output": ["console"],
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
})
logger.logger().propagate = False

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DIVIDER = "=" * 60
SUBDIV = "-" * 50


def _banner(title: str) -> None:
    logger.info("")
    logger.info(DIVIDER)
    logger.info(title)
    logger.info(DIVIDER)


def _section(label: str) -> None:
    logger.info("")
    logger.info("  %s", label)
    logger.info("  %s", SUBDIV)


def _make_card(agent_id: str, name: str) -> AgentCard:
    return AgentCard(id=agent_id, name=name, description=name)


# =============================================================================
# MAIN
# =============================================================================


async def main() -> None:  # noqa: C901

    _banner("TaskMemoryRail — Abilities Demo")
    logger.info("Demonstrates A1-A4 plus a WITHOUT vs. WITH comparison.")

    # -------------------------------------------------------------------------
    # Step 0 — Configuration
    # -------------------------------------------------------------------------
    _section("[Step 0] Checking configuration")

    api_key = app_config.get("API_KEY", "")
    api_base = app_config.get("API_BASE", "https://api.openai.com/v1")
    model_name = app_config.get("MODEL_NAME", "gpt-4o")
    model_provider = app_config.get("MODEL_PROVIDER", "OpenAI")

    if not api_key or api_key == "your_api_key_here":
        logger.error("API key not configured. Edit API_KEY in _DEFAULTS ")
        return

    logger.info("  API Base : %s", api_base)
    logger.info("  Model    : %s", model_name)
    logger.info("  Provider : %s", model_provider)

    await Runner.start()
    try:
        # Build Model instance for DeepAgent
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
        # Create TaskMemoryService and seed a memory
        # -------------------------------------------------------------------------
        _section("Creating TaskMemoryService and seeding a memory")

        user_id = "demo_user"
        mem_dir = os.path.join(_root, "memory_files")

        memory_service = TaskMemoryService(
            persist_type="json",
            persist_path=os.path.join(mem_dir, "{algo_name}", "{user_id}.json"),
            retrieval_algo="refcon",
            summary_algo="refcon"
        )
        content1 = (
            "When debugging Python code, prefer the built-in debugger pdb over "
            "print statements. Use pdb.set_trace() to pause execution and inspect "
            "variables interactively. For async code use asyncio debug mode "
            "(PYTHONASYNCIODEBUG=1). Always check the full traceback before "
            "adding any print statements."
        )
        mem_req1 = AddMemoryRequest(
            when_to_use="When asked how to debug Python code or find bugs",
            content=content1,
        )
        await memory_service.add_memory(user_id=user_id, request=mem_req1)
        logger.info("  Memory seeded (topic: Python debugging).")

        content2 = (
            "When writing unit tests in Python, prefer pytest over unittest. "
            "Use fixtures for reusable setup and teardown. Mock external dependencies "
            "with unittest.mock.patch to keep tests fast and isolated. "
            "Run tests with pytest -v for verbose output and pytest --tb=short to "
            "see concise tracebacks on failure."
        )
        mem_req2 = AddMemoryRequest(
            when_to_use="When asked how to write or structure Python unit tests",
            content=content2,
        )
        await memory_service.add_memory(user_id=user_id, request=mem_req2)
        logger.info("  Memory seeded (topic: Python unit testing).")

        # =========================================================================
        # A1 — TaskMemoryRail: Demo
        # =========================================================================
        _banner("TaskMemoryRail — Demo")

        hook = TaskMemoryRail(
            user_id=user_id,
            memory_service=memory_service,
        )

        _section("rail (DeepAgent + TaskMemoryRail, auto_summarize=True)")
        agent_mem = create_deep_agent(
            model=model,
            card=_make_card("mem_agent", "Memory-augmented DeepAgent"),
            system_prompt=(
                "You are a helpful assistant with access to a memory system. "
                "When relevant memories are provided in your context, use them to inform "
                "your responses. Always provide accurate, helpful answers based on both "
                "your knowledge and any retrieved memories."
            ),
            enable_task_loop=True,
            max_iterations=5,
            rails=[hook],
        )

        # -------------------------------------------------------------------------
        # Helper: log invoke result
        # -------------------------------------------------------------------------
        def _log_result(result: dict) -> None:
            out = result.get("output", "")
            mem = result.get("memories_used", 0)
            logger.info("  memories_used : %d", mem)
            logger.info("  Response      :")
            for line in out.splitlines()[:6]:
                logger.info("    %s", line)
            if len(out.splitlines()) > 6:
                logger.info("    ... (truncated)")

        # =========================================================================
        # Invoke 1 — debugging question (uses seeded memory)
        # =========================================================================
        _section("[Invoke 1] How should I debug my Python code?")
        query1 = "How should I debug my Python code?"
        logger.info("  Query: '%s'", query1)
        result1 = await Runner.run_agent(agent_mem, {"query": query1}, session="demo_session_1")
        _log_result(result1)
        logger.info("  Nodes in store after invoke 1 : %d", len(memory_service.vector_store.get_all()))

        # =========================================================================
        # Invoke 2 — unit-testing question (retrieves summarised memories)
        # =========================================================================
        _section("[Invoke 2] How do I write unit tests for my Python code?")
        query2 = "How do I write unit tests for my Python code?"
        logger.info("  Query: '%s'", query2)
        result2 = await Runner.run_agent(agent_mem, {"query": query2}, session="demo_session_2")
        _log_result(result2)
        logger.info("  Nodes in store after invoke 2 : %d", len(memory_service.vector_store.get_all()))

        # =========================================================================
        # Invoke 3 — async-specific question (retrieves summarised memories)
        # =========================================================================
        _section("[Invoke 3] My async Python code is throwing an unexpected exception.")
        query3 = "My async Python code is throwing an unexpected exception. How do I investigate it?"
        logger.info("  Query: '%s'", query3)
        logger.info("  Vector store : %d node(s) available", len(memory_service.vector_store.get_all()))
        result3 = await Runner.run_agent(agent_mem, {"query": query3}, session="demo_session_3")
        _log_result(result3)

        # =========================================================================
        # Summary
        # =========================================================================
        _section("Memory growth summary")
        logger.info("  Invoke 1 memories_used : %d", result1.get("memories_used", 0))
        logger.info("  Invoke 2 memories_used : %d", result2.get("memories_used", 0))
        logger.info("  Invoke 3 memories_used : %d", result3.get("memories_used", 0))
        logger.info("  Total nodes in store   : %d", len(memory_service.vector_store.get_all()))

        _banner("Demo Complete!")
    finally:
        await Runner.stop()


if __name__ == "__main__":
    anyio.run(main)
