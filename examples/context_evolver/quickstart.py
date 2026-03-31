# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import io
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
from openjiuwen.core.single_agent import AgentCard, ReActAgentConfig
from openjiuwen.extensions.context_evolver import (
    TaskMemoryService,
    ContextEvolvingReActAgent,
    wikipedia_tool,
)

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


# =============================================================================
# MAIN
# =============================================================================

async def main() -> None:

    _banner("ContextEvolvingReActAgent — HotpotQA RefCon Demo")

    # -------------------------------------------------------------------------
    # Step 0 — Configuration
    # -------------------------------------------------------------------------
    _section("[Step 0] Checking configuration")

    api_key = app_config.get("API_KEY", "")
    api_base = app_config.get("API_BASE", "https://api.openai.com/v1")
    model_name = app_config.get("MODEL_NAME", "gpt-5.2")
    model_provider = app_config.get("MODEL_PROVIDER", "OpenAI")

    if not api_key or api_key == "your_api_key_here":
        logger.error("API key not configured. Edit API_KEY in _DEFAULTS")
        return

    logger.info("  API Base : %s", api_base)
    logger.info("  Model    : %s", model_name)
    logger.info("  Provider : %s", model_provider)

    # =========================================================================
    # STEP 1: TRAJECTORIES GENERATION — HotpotQA (REFCON Sequential)
    # =========================================================================
    _banner("TRAJECTORIES GENERATION — HotpotQA (REFCON Sequential)")
    logger.info("  Using Algorithm: REFCON")

    app_config.set_value("RETRIEVAL_ALGO", "REFCON")
    app_config.set_value("SUMMARY_ALGO", "REFCON")
    app_config.set_value("MANAGEMENT_ALGO", "REFCON")

    memory_service = TaskMemoryService()

    hotpot_card = AgentCard(
        id="demo-agent-refcon",
        name="Demo Agent REFCON",
        description="Agent for HotpotQA quickstart demo using REFCON sequential"
    )

    agent = ContextEvolvingReActAgent(
        card=hotpot_card,
        user_id="demo_user_hotpot_refcon",
        memory_service=memory_service,
        inject_memories_in_context=True,
        persist_type="json",
    )

    agent_config = ReActAgentConfig()
    agent_config.configure_model_client(
        provider=model_provider,
        api_key=api_key,
        api_base=api_base,
        model_name=model_name,
    )
    agent_config.configure_max_iterations(5)
    agent.configure(agent_config)

    agent.add_tool(wikipedia_tool)

    question = "Which magazine was started first Arthur's Magazine or First for Women?"
    #ground_truth = "Arthur's Magazine" # uncomment this for ground truth evaluation
    _section(f"[Invoke] {question}")
    result = await agent.invoke({
        "query": question,
        #"ground_truth": ground_truth, # uncomment this for ground truth evaluation
        #"matts_mode": "combined", # uncomment this for ground truth evaluation
    })
    if result:
        logger.info("  Summary result :")
        for k, v in result.items():
            v_str = str(v)
            logger.info("    %-20s: %s", k, v_str[:120])
    else:
        logger.info("  No summary result returned.")

    _banner("Demo Complete!")


if __name__ == "__main__":
    anyio.run(main)
