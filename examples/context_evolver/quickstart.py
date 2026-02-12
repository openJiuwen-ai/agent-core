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
    # STEP 0.5: Setup .env file if needed
    # =========================================================================
    context_evolver_root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "openjiuwen", "extensions", "context_evolver"
    )
    env_path = os.path.join(context_evolver_root, ".env")
    env_example_path = os.path.join(context_evolver_root, ".env.example")

    # Check if running in interactive mode
    is_interactive = sys.stdin.isatty()

    # Check if .env exists
    env_exists = os.path.exists(env_path)
    should_recreate = False

    if env_exists and is_interactive:
        # .env exists - ask user if they want to reconfigure
        logger.info("\n" + "=" * 60)
        logger.info(f"Found existing .env file at: {env_path}")
        logger.info("=" * 60)

        # Load and display current configuration
        try:
            with open(env_path, 'r', encoding='utf-8') as f:
                current_config = {}
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        current_config[key.strip()] = value.strip()

            logger.info("\nCurrent configuration:")
            api_key_value = current_config.get('API_KEY', 'Not set')
            api_key_display = f"{api_key_value[:20]}..." if len(api_key_value) > 20 else api_key_value
            logger.info(f"  API_KEY: {api_key_display}")
            logger.info(f"  API_BASE: {current_config.get('API_BASE', 'Not set')}")
            logger.info(f"  MODEL_NAME: {current_config.get('MODEL_NAME', 'Not set')}")
            logger.info(f"  EMBEDDING_MODEL: {current_config.get('EMBEDDING_MODEL', 'Not set')}")
            logger.info(f"  MODEL_PROVIDER: {current_config.get('MODEL_PROVIDER', 'Not set')}")
        except (OSError, UnicodeDecodeError) as e:
            current_config = {}
            logger.info(f"\n(Unable to read current configuration: {e})")

        response = input("\nDo you want to reconfigure? (y/N): ").strip().lower()
        should_recreate = response in ['y', 'yes']

        if not should_recreate:
            logger.info("Using existing configuration.")
    elif not env_exists:
        logger.info("\n[Step 0.5] .env file not found. Creating one...")
        should_recreate = True

    if should_recreate:
        # Read .env.example as template
        if os.path.exists(env_example_path):
            with open(env_example_path, 'r', encoding='utf-8') as f:
                env_template = f.read()
        else:
            # Fallback template if .env.example doesn't exist
            env_template = """# API Configuration
API_KEY=your-api-key-here
API_BASE=https://api.openai.com/v1

# Model Configuration  #gpt-5.2 gpt-4
MODEL_NAME=gpt-5.2
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=2560
MODEL_PROVIDER=OpenAI

# Optional: LLM Parameters
LLM_TEMPERATURE=0.7
LLM_SEED=42
LLM_SSL_VERIFY=false
"""

        if is_interactive:
            # Prompt user for configuration
            logger.info("\n" + "=" * 60)
            logger.info("Configuration setup: Please provide your settings")
            logger.info("=" * 60)

            # Load current values if updating
            default_values = {}
            if env_exists:
                try:
                    with open(env_path, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#") and "=" in line:
                                key, value = line.split("=", 1)
                                default_values[key.strip()] = value.strip()
                except (OSError, UnicodeDecodeError) as e:
                    logger.warning(f"Unable to load existing config values: {e}")

            # Get API_KEY
            current_api_key = default_values.get('API_KEY', '')
            is_valid_key = (current_api_key and
                           current_api_key != 'your-api-key-here' and
                           not current_api_key.startswith('sk-proj-xxx'))

            if is_valid_key:
                api_key_display = (f"{current_api_key[:20]}..."
                                  if len(current_api_key) > 20 else current_api_key)
                prompt = f"\nAPI_KEY [current: {api_key_display}] (press Enter to keep): "
                api_key = input(prompt).strip()
                if not api_key:
                    api_key = current_api_key
            else:
                api_key = input("\nAPI_KEY (required): ").strip()

            if not api_key:
                logger.error("ERROR: API_KEY is required!")
                return

            # Prompt for optional parameters with defaults
            logger.info("\nOptional parameters (press Enter to use defaults):")

            default_api_base = default_values.get('API_BASE', 'https://api.openai.com/v1')
            api_base = input(f"API_BASE [{default_api_base}]: ").strip() or default_api_base

            default_model = default_values.get('MODEL_NAME', 'gpt-5.2')
            model_name = input(f"MODEL_NAME [{default_model}]: ").strip() or default_model

            default_embed = default_values.get('EMBEDDING_MODEL', 'text-embedding-3-small')
            embedding_model = input(f"EMBEDDING_MODEL [{default_embed}]: ").strip() or default_embed

            default_dims = default_values.get('EMBEDDING_DIMENSIONS', '2560')
            embedding_dims = input(f"EMBEDDING_DIMENSIONS [{default_dims}]: ").strip() or default_dims

            default_provider = default_values.get('MODEL_PROVIDER', 'OpenAI')
            model_provider = input(f"MODEL_PROVIDER [{default_provider}]: ").strip() or default_provider

            default_temp = default_values.get('LLM_TEMPERATURE', '0.7')
            llm_temp = input(f"LLM_TEMPERATURE [{default_temp}]: ").strip() or default_temp

            default_seed = default_values.get('LLM_SEED', '42')
            llm_seed = input(f"LLM_SEED [{default_seed}]: ").strip() or default_seed

            default_ssl = default_values.get('LLM_SSL_VERIFY', 'false')
            llm_ssl_verify = input(f"LLM_SSL_VERIFY [{default_ssl}]: ").strip() or default_ssl
        else:
            # Non-interactive mode: read from stdin or environment
            logger.info("  Running in non-interactive mode.")
            logger.info("  Reading API_KEY from environment or stdin...")

            # Try to read API_KEY from environment first
            api_key = os.environ.get("API_KEY", "").strip()

            # If not in environment, try to read from stdin
            if not api_key:
                try:
                    api_key = sys.stdin.readline().strip()
                except (OSError, EOFError) as e:
                    logger.warning(f"Unable to read API_KEY from stdin: {e}")

            if not api_key:
                logger.error("ERROR: API_KEY is required!")
                logger.error("Please provide API_KEY via environment variable or stdin")
                return

            # Use defaults for all other parameters
            api_base = os.environ.get("API_BASE", "https://api.openai.com/v1")
            model_name = os.environ.get("MODEL_NAME", "gpt-5.2")
            embedding_model = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
            embedding_dims = os.environ.get("EMBEDDING_DIMENSIONS", "2560")
            model_provider = os.environ.get("MODEL_PROVIDER", "OpenAI")
            llm_temp = os.environ.get("LLM_TEMPERATURE", "0.7")
            llm_seed = os.environ.get("LLM_SEED", "42")
            llm_ssl_verify = os.environ.get("LLM_SSL_VERIFY", "false")

        # Create .env content
        env_content = f"""# API Configuration
API_KEY={api_key}
API_BASE={api_base}

# Model Configuration  #gpt-5.2 gpt-4
MODEL_NAME={model_name}
EMBEDDING_MODEL={embedding_model}
EMBEDDING_DIMENSIONS={embedding_dims}
MODEL_PROVIDER={model_provider}

# Optional: LLM Parameters
LLM_TEMPERATURE={llm_temp}
LLM_SEED={llm_seed}
LLM_SSL_VERIFY={llm_ssl_verify}
"""

        # Write .env file
        with open(env_path, 'w', encoding='utf-8') as f:
            f.write(env_content)

        logger.info(f"\n✓ .env file {'updated' if env_exists else 'created'} at: {env_path}")
        logger.info("  You can edit this file later to update configuration.")

        # Reload config to pick up new .env
        app_config.reload()

    # =========================================================================
    # STEP 1: Check Configuration
    # =========================================================================
    logger.info("\n[Step 1] Checking configuration...")

    api_key = app_config.get("API_KEY")
    if not api_key or api_key == "your-api-key-here" or api_key.startswith("sk-proj-xxx"):
        logger.error("ERROR: Valid API_KEY not found in .env file")
        logger.error(f"Please edit {env_path} and add your OpenAI API key")
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
