# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import sys
import os
import io

# Fix stdout/stderr encoding to handle Unicode characters on Windows with GBK console
# This must be done BEFORE any logging imports to prevent UnicodeEncodeError
if sys.stdout.encoding and sys.stdout.encoding.lower() in ('gbk', 'cp936', 'ascii'):
    # Wrap stdout to handle encoding errors gracefully
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer,
        encoding='utf-8',
        errors='replace',
        line_buffering=True
    )
if sys.stderr.encoding and sys.stderr.encoding.lower() in ('gbk', 'cp936', 'ascii'):
    # Wrap stderr to handle encoding errors gracefully
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer,
        encoding='utf-8',
        errors='replace',
        line_buffering=True
    )

#import logging
#logger = logging.getLogger(__name__)
from openjiuwen.core.common.logging import context_engine_logger as logger

# =============================================================================
# STEP 0: Environment Setup
# =============================================================================
# For development, use one of these approaches:
# 1. Set PYTHONPATH environment variable: 
#    export PYTHONPATH="${PYTHONPATH}:/path/to/agent-core"
# 2. Install in development mode:
#    cd /path/to/agent-core && pip install -e .
# 3. Run with PYTHONPATH:
#    PYTHONPATH=/path/to/agent-core python examples/context_evolver/quickstart.py
#
# The code below ensures the imports work even without explicit setup.
if os.environ.get("PYTHONPATH") is None:
    # Only add to sys.path if not already configured via PYTHONPATH
    agent_core_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if agent_core_root not in sys.path:
        sys.path.append(agent_core_root)

# Import configuration
from openjiuwen.extensions.context_evolver.core import config as app_config

# Import agent_core components
from openjiuwen.core.single_agent import AgentCard, Session

# Import context_evolver components
from openjiuwen.extensions.context_evolver import (
    TaskMemoryService,
    AddMemoryRequest,
    ContextEvolvingReActAgent,
    create_memory_agent_config,
    MemoryAgentConfigInput,
    SummarizeTrajectoriesInput,
    wikipedia_tool,
)


async def main():
    """Main function demonstrating ContextEvolvingReActAgent usage."""

    logger.info("=" * 60)
    logger.info("ContextEvolvingReActAgent Quick Start Guide")
    logger.info("=" * 60)

    # =========================================================================
    # STEP 1: Check Configuration
    # =========================================================================
    logger.info("\n[Step 1] Checking configuration...")

    api_key = app_config.get("API_KEY")
    if not api_key:
        logger.error("ERROR: API_KEY not found in config.yaml")
        logger.error("Please add your OpenAI API key to config.yaml:")
        logger.error('  API_KEY: "your-api-key-here"')
        return

    api_base = app_config.get("API_BASE", "https://api.openai.com/v1")
    model_name = app_config.get("MODEL_NAME", "gpt-5.2")

    logger.info("  API Base: %s", api_base)
    logger.info("  Model: %s", model_name)
    logger.info("  Configuration OK!")
    
    # =========================================================================
    # STEP 2: Create Memory Service
    # =========================================================================
    logger.info("\n[Step 2] Creating TaskMemoryService...")

    # The memory service handles storing and retrieving memories
    memory_service = TaskMemoryService()

    logger.info("  Retrieval Algorithm: %s", memory_service.retrieval_algorithm)
    logger.info("  Summary Algorithm: %s", memory_service.summary_algorithm)
    logger.info("  Memory service created!")

    # =========================================================================
    # STEP 3: Add Some Memories
    # =========================================================================
    logger.info("\n[Step 3] Adding memories to knowledge base...")

    # Add memories based on the configured algorithm
    # ACE uses: content, section
    # ReasoningBank uses: title, description, content
    # ReMe uses: when_to_use, content

    if memory_service.summary_algorithm == "ReasoningBank":
        # ReasoningBank format
        await memory_service.add_memory(
            user_id="demo_user",
            request=AddMemoryRequest(
                title="Python Best Practices",
                description="Guidelines for writing clean Python code",
                content="Use meaningful variable names. Follow PEP 8 style guide. "
                        "Write docstrings for functions. Use type hints for clarity. "
                        "Prefer list comprehensions over loops when appropriate.",
            ),
        )
        logger.info("  Added: Python Best Practices (ReasoningBank format)")
    elif memory_service.summary_algorithm == "ReMe":
        # ReMe format
        await memory_service.add_memory(
            user_id="demo_user",
            request=AddMemoryRequest(
                when_to_use="When writing Python code and need best practices for clean, maintainable code",
                content="Use meaningful variable names. Follow PEP 8 style guide. "
                        "Write docstrings for functions. Use type hints for clarity. "
                        "Prefer list comprehensions over loops when appropriate.",
            ),
        )
        logger.info("  Added: Python Best Practices (ReMe format)")
    else:
        # ACE format (default)
        await memory_service.add_memory(
            user_id="demo_user",
            request=AddMemoryRequest(
                content="Use meaningful variable names. Follow PEP 8 style guide. "
                        "Write docstrings for functions. Use type hints for clarity. "
                        "Prefer list comprehensions over loops when appropriate.",
                section="python"
            ),
        )
        logger.info("  Added: Python Best Practices (ACE format)")

    # =========================================================================
    # STEP 4: Create the Memory-Augmented Agent
    # =========================================================================
    logger.info("\n[Step 4] Creating ContextEvolvingReActAgent agent...")

    # Create an agent card (identifies the agent)
    agent_card = AgentCard(
        id="my-memory-agent",
        name="my-memory-agent",
        description="A helpful assistant with memory capabilities"
    )

    # Create the memory-augmented agent
    agent = ContextEvolvingReActAgent(
        card=agent_card,
        user_id="demo_user",           # Links to the memories we added
        memory_service=memory_service,  # Use our memory service
        inject_memories_in_context=True # Automatically add memories to prompts
    )

    # Configure the agent with model settings
    agent_config = create_memory_agent_config(
        MemoryAgentConfigInput(
            model_provider="OpenAI",
            api_key=api_key,
            api_base=api_base,
            model_name=model_name,
            system_prompt="You are a helpful programming assistant. "
                         "Use any provided memory context to give better answers.",
        )
    )
    agent.configure(agent_config)

    logger.info("  Agent created and configured!")

    # =========================================================================
    # STEP 5: Query the Agent
    # =========================================================================
    logger.info("\n[Step 5] Querying the agent...")
    logger.info("  Query: 'What are some Python best practices?'")
    logger.info("  (The agent will automatically retrieve relevant memories)")

    result = await agent.invoke({
        "query": "What are some Python best practices?"
    })

    output = result.get("output", "No output")
    memories_used = result.get("memories_used", 0)

    logger.info("  Memories retrieved: %s", memories_used)
    logger.info("\n  Response:\n  %s", '-' * 50)
    # Handle potential Unicode issues on Windows
    try:
        logger.info("  %s", output)
    except UnicodeEncodeError:
        safe_output = output.encode('ascii', 'replace').decode('ascii')
        logger.info("  %s", safe_output)
    logger.info("  %s", '-' * 50)

    # =========================================================================
    # STEP 6: Summarize Trajectories (Learning from Interaction)
    # =========================================================================
    logger.info("\n[Step 6] Summarizing trajectories (learning from interaction)...")
    logger.info("  This extracts learnings from the interaction and saves as memories.")

    query2 = "How do I write a Python function with type hints?"

    # Query the agent first
    result2 = await agent.invoke({"query": query2})
    output2 = result2.get("output", "No output")

    # Now summarize the trajectory with feedback
    summary_result = await agent.summarize_trajectories(
        SummarizeTrajectoriesInput(
            query=query2,
            trajectory=output2,
            feedback="helpful",  # Can be: 'helpful', 'harmful', 'neutral', True/False
            matts_mode="none"    # Options: "none", "parallel", "sequential"
        )
    )

    if summary_result:
        memories_added = len(summary_result.get('memory', []))
        logger.info("  Query: '%s'", query2)
        logger.info("  Feedback: helpful")
        logger.info("  Memories extracted: %s", memories_added)
        logger.info("  [PASS] Trajectory summarization completed!")
    else:
        logger.warning("  [WARN] Summarization may have issues")
    
    # =========================================================================
    # STEP 7: Context Evolver Use Case Example on HotpotQA
    # =========================================================================
    logger.info("\n[Step 7] Retrieve-Generate-Summarize Loop (HotpotQA Example)")
    logger.info("  Using Algorithm: ReasoningBank")
    logger.info("  Using MATTS: Parallel (K=3)")

    log_filename = "quickstart.log"
    logger.reconfigure({
        "level": "INFO",
        "output": ["file"],
        "log_file": log_filename,
        "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    })
    
    # Note: Newlines in log messages will be escaped by the logger's control character sanitization.
    # If you need to preserve newlines, consider using a custom formatter or modifying the logger
    # configuration through its public API instead of accessing protected members.
        
    logger.info(f"  Logging detailed execution to {log_filename}")

    # Create a dedicated agent for HotpotQA loop
    logger.info("  Creating new HotpotQA Agent...")
    
    # Configure Memory Service to use ReasoningBank
    app_config.set_value("RETRIEVAL_ALGO", "RB")
    app_config.set_value("SUMMARY_ALGO", "RB")
    app_config.set_value("MANAGEMENT_ALGO", "RB")
    
    memory_service = TaskMemoryService()

    hotpot_card = AgentCard(
        id="hotpotqa-demo-agent",
        name="HotpotQA Demo Agent",
        description="Agent for HotpotQA quickstart demo"
    )
    
    # We reuse the memory_service from earlier for continuity in this script
    hotpot_agent = ContextEvolvingReActAgent(
        card=hotpot_card,
        user_id="demo_user_hotpot",
        memory_service=memory_service,
        inject_memories_in_context=True
    )
    
    # Configure the agent
    hotpot_config = create_memory_agent_config(
        MemoryAgentConfigInput(
            model_provider="OpenAI",
            api_key=app_config.get("API_KEY"),
            api_base=app_config.get("API_BASE"),
            model_name=app_config.get("MODEL_NAME"),
        )
    )
    hotpot_agent.configure(hotpot_config)

    # Add Wikipedia tool for HotpotQA
    hotpot_agent.add_tool(wikipedia_tool)

    # Questions and Ground Truths
    hotpot_questions = [
        {
            "question": "Which magazine was started first Arthur's Magazine or First for Women?",
            "answer": "Arthur's Magazine"
        },
        {
            "question": "Which tennis player won more Grand Slam titles, Henri Leconte or Jonathan Stark?",
            "answer": "Jonathan Stark"
        },
        {
            "question": "Were Pavel Urysohn and Leonid Levin known for the same type of work?",
            "answer": "no"
        }
    ]

    matts_k = 3

    for i, item in enumerate(hotpot_questions):
        question = item["question"]
        ground_truth = item["answer"]
        
        logger.info("Processing Q%s: %s", i + 1, question)
        
        trajectories = []
        
        # Parallel Generation (Simulated sequential execution for demo)
        for run_id in range(matts_k):
            logger.info("    Running trial %s/%s...", run_id + 1, matts_k)
            
            # Create a new session for context isolation
            session = Session(card=hotpot_agent.card)
            
            try:
                # Invoke agent
                result = await hotpot_agent.invoke({"query": question}, session=session)
                output = result.get("output", "")
                
                # Check correctness (simple string inclusion check for demo)
                is_correct = ground_truth.lower() in output.lower()
                status = "SUCCESS" if is_correct else "FAILURE"
                logger.info(f"      Result: {status} (Output: {output[:50]}...)")
                
                # Format trajectory using the agent's helper
                # Retrieve context from engine
                context = hotpot_agent.context_engine.get_context(
                    session_id=session.get_session_id(), 
                    context_id="default_context_id"
                )
                
                if context:
                    messages = context.get_messages()
                    trajectory = hotpot_agent.format_trajectory(messages)
                else:
                    trajectory = f"USER: {question}\nASSISTANT: {output}"
                
                # Log the clean trajectory
                logger.info(f"Trajectory {run_id+1} for Q{i + 1}:\n{trajectory}")
                trajectories.append(trajectory)
                
            except Exception as e:
                logger.error(f"Trial failed: {e}")
                trajectories.append(None)

        summary_result = await hotpot_agent.summarize_trajectories(
            SummarizeTrajectoriesInput(
                query=question,
                trajectory=trajectories,
                matts_mode="parallel"
            )
        )

    # Reconfigure logger to output summary to console cleanly
    logger.reconfigure({
        "level": "DEBUG",
        "output": ["console"],
        "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    })
    # =========================================================================
    # DONE!
    # =========================================================================
    logger.info("\n" + "=" * 60)
    logger.info("Quick Start Complete!")
    logger.info("=" * 60)
    logger.info("\nNext steps:")
    logger.info("  1. Add your own memories using memory_service.add_memory()")
    logger.info("  2. Query the agent with agent.invoke({'query': '...'})")
    logger.info("  3. Use agent.summarize_trajectories() to learn from interactions")
    logger.info("  4. See readme.md for more advanced usage")
    


if __name__ == "__main__":
    asyncio.run(main())
