# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Evaluator Pipeline Example

This example demonstrates how to use the evaluator pipeline with:
- JiuWenSwarm agent adapter
- SkillsBench benchmark adapter
- Single-run mode (evaluate without skill evolution)
- Evolution mode (self-evolving training)

Prerequisites:
1. Install dependencies: uv sync
2. Configure LLM API credentials via environment variables
3. Ensure Docker is running (for containerized execution)

Environment Variables:
- DASHSCOPE_API_KEY: Your DashScope API key
- OPENAI_API_KEY: Your OpenAI API key (alternative)
- API_BASE: API base URL (e.g., https://dashscope.aliyuncs.com/compatible-mode/v1)
- MODEL_NAME: Model name (e.g., glm-5, qwen-plus)
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from openjiuwen.agent_evolving.evaluator.evaluator_pipeline import (
    EvolutionPipeline,
    PipelineConfig,
)
from openjiuwen.core.common.logging import logger


# ====================
# Configuration
# ====================
def load_config() -> dict:
    """Load configuration from environment variables."""
    return {
        # LLM Configuration
        "api_key": os.getenv("DASHSCOPE_API_KEY", os.getenv("OPENAI_API_KEY", "")),
        "api_base": os.getenv("API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        "model_name": os.getenv("MODEL_NAME", "glm-5"),
        
        # SkillsBench Configuration
        "skillsbench_repo_url": os.getenv("SKILLSBENCH_REPO_URL", None),
        "skillsbench_tasks_dir": os.getenv("SKILLSBENCH_TASKS_DIR", "./tasks"),
        
        # Pipeline Configuration
        "output_dir": os.getenv("OUTPUT_DIR", "./eval_results"),
        "max_iterations": int(os.getenv("MAX_ITERATIONS", "3")),
    }


# ====================
# Single-Run Mode Example
# ====================
async def run_single_mode(config: dict) -> None:
    """Run evaluator pipeline in single-run mode (no skill evolution).
    
    This mode evaluates agent performance on benchmark tasks without
    performing skill evolution.
    """
    logger.info("\n" + "="*60)
    logger.info("Running Single-Run Mode")
    logger.info("="*60)

    # Configure pipeline
    pipeline_config = PipelineConfig(
        agent="jiuwenswarm",
        agent_config={
            "api_key": config["api_key"],
            "api_base": config["api_base"],
            "model_name": config["model_name"],
            "install_mode": "auto",  # auto, git, pypi, local
        },
        benchmark="skillsbench",
        bench_config={
            "repo_url": config["skillsbench_repo_url"],
            "tasks_dir": config["skillsbench_tasks_dir"],
            "skills_mode": "with_skills",  # with_skills, without_skills
            "workspace_dir": "/workspace",
        },
        evolution_mode=False,  # Disable evolution for single-run
        output_dir=config["output_dir"],
        task_ids=None,  # Run all tasks (or specify like ["task1", "task2"])
    )

    # Create and run pipeline
    pipeline = EvolutionPipeline(pipeline_config)
    results = await pipeline.run()

    # Print summary
    logger.info("\n" + "="*60)
    logger.info("Single-Run Results Summary")
    logger.info("="*60)
    for result in results:
        status = "✅ PASS" if result.convergence_achieved else "❌ FAIL"
        logger.info(f"Task: {result.task_id} | Status: {status}")
        if "error" in result.metrics:
            logger.info(f"  Error: {result.metrics['error']}")


# ====================
# Evolution Mode Example
# ====================
async def run_evolution_mode(config: dict) -> None:
    """Run evaluator pipeline in evolution mode (self-evolving training).
    
    This mode enables automatic skill evolution:
    1. Run agent on tasks
    2. Analyze failures
    3. Generate skill improvements
    4. Iterate until convergence
    """
    logger.info("\n" + "="*60)
    logger.info("Running Evolution Mode")
    logger.info("="*60)

    # Configure pipeline with evolution enabled
    pipeline_config = PipelineConfig(
        agent="jiuwenswarm",
        agent_config={
            "api_key": config["api_key"],
            "api_base": config["api_base"],
            "model_name": config["model_name"],
            "install_mode": "auto",
        },
        benchmark="skillsbench",
        bench_config={
            "repo_url": config["skillsbench_repo_url"],
            "tasks_dir": config["skillsbench_tasks_dir"],
            "skills_mode": "with_skills",
            "workspace_dir": "/workspace",
        },
        evolution_mode=True,  # Enable skill evolution
        max_iterations=config["max_iterations"],
        convergence_check=True,  # Stop early if convergence achieved
        convergence_threshold=0.9,  # 90% pass rate to converge
        output_dir=config["output_dir"],
        task_ids=None,
    )

    # Create and run pipeline
    pipeline = EvolutionPipeline(pipeline_config)
    results = await pipeline.run()

    # Print summary
    logger.info("\n" + "="*60)
    logger.info("Evolution Results Summary")
    logger.info("="*60)
    for result in results:
        status = "✅ CONVERGED" if result.convergence_achieved else "❌ NOT CONVERGED"
        logger.info(f"Task: {result.task_id}")
        logger.info(f"  Status: {status}")
        logger.info(f"  Iterations: {result.total_iterations}/{config['max_iterations']}")
        logger.info(f"  Convergence Type: {result.convergence_type}")
        if "pass_rate" in result.metrics:
            logger.info(f"  Final Pass Rate: {result.metrics['pass_rate']:.2%}")


# ====================
# Hermes Agent Example
# ====================
async def run_hermes_example(config: dict) -> None:
    """Run evaluator pipeline with Hermes agent adapter."""
    logger.info("\n" + "="*60)
    logger.info("Running Hermes Agent Example")
    logger.info("="*60)

    pipeline_config = PipelineConfig(
        agent="hermes",
        agent_config={
            "api_key": config["api_key"],
            "api_base": config["api_base"],
            "model_name": config["model_name"],
        },
        benchmark="skillsbench",
        bench_config={
            "repo_url": config["skillsbench_repo_url"],
            "tasks_dir": config["skillsbench_tasks_dir"],
            "skills_mode": "with_skills",
            "workspace_dir": "/workspace",
        },
        evolution_mode=False,
        output_dir=config["output_dir"],
    )

    pipeline = EvolutionPipeline(pipeline_config)
    results = await pipeline.run()

    logger.info("\n" + "="*60)
    logger.info("Hermes Results Summary")
    logger.info("="*60)
    for result in results:
        status = "✅ PASS" if result.convergence_achieved else "❌ FAIL"
        logger.info(f"Task: {result.task_id} | Status: {status}")


# ====================
# Main Entry Point
# ====================
def main():
    """Main entry point for evaluator pipeline example."""
    # Load configuration
    config = load_config()
    
    # Validate required configuration
    if not config["api_key"]:
        logger.error("ERROR: DASHSCOPE_API_KEY or OPENAI_API_KEY environment variable is required")
        return
    
    # Create output directory
    os.makedirs(config["output_dir"], exist_ok=True)

    # Run examples
    logger.info("\n" + "="*80)
    logger.info("Evaluator Pipeline Example")
    logger.info("="*80)
    logger.info("Configuration loaded:")
    logger.info(f"  Agent: JiuWenSwarm + Hermes")
    logger.info(f"  Benchmark: SkillsBench")
    logger.info(f"  Model: {config['model_name']}")
    logger.info(f"  Output: {config['output_dir']}")

    # Choose mode to run
    # Option 1: Single-run mode
    asyncio.run(run_single_mode(config))
    
    # Option 2: Evolution mode (uncomment to run)
    # asyncio.run(run_evolution_mode(config))


if __name__ == "__main__":
    main()
