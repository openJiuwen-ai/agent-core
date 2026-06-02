# evaluator_pipeline Module Overview

`openjiuwen.agent_evolving.evaluator.evaluator_pipeline` is the **Skill Evaluation and Evolution Pipeline module** in openJiuwen, responsible for coordinating Agents and Benchmarks to execute evaluation tasks.

## Module Structure

This module contains the following sub-modules:

| Sub-module | Description | Documentation File |
|------------|-------------|-------------------|
| `base` | Abstract adapter interfaces for Agent and Benchmark | [base.md](base.md) |
| `config` | Pipeline configuration class | [config.md](config.md) |
| `docker_env` | Docker container environment management | [docker_env.md](docker_env.md) |
| `models` | Core data model definitions | [models.md](models.md) |
| `pipeline` | Core evaluation pipeline implementation | [pipeline.md](pipeline.md) |
| `skill_manager` | Skill management and evolution tracking | [skill_manager.md](skill_manager.md) |

## Core Components

### 1. EvolutionPipeline
Core execution class that supports two modes:
- **Single-run Mode**: Execute one evaluation and finish
- **Evolution Mode**: Multiple iterations with automatic skill optimization until convergence

### 2. BaseAgentAdapter
Abstract base class for Agent adapters. Developers can inherit this class to implement custom Agents.

### 3. BaseBenchAdapter
Abstract base class for Benchmark adapters. Developers can inherit this class to implement custom benchmarks.

### 4. SkillManager
Skill manager responsible for loading, saving, and version evolution of skills.

### 5. DockerEnvironment
Docker environment wrapper providing container build, start, execute, and stop operations.

## Data Flow

```
PipelineConfig → EvolutionPipeline → Task → AgentContext → AgentRunResult → EvalResult → PipelineResult
```

## Quick Start

```python
import asyncio
from openjiuwen.agent_evolving.evaluator.evaluator_pipeline import (
    EvolutionPipeline,
    PipelineConfig,
)

# Create configuration
config = PipelineConfig(
    agent="jiuwenswarm",
    benchmark="skillsbench",
    evolution_mode=True,
    max_iterations=5,
    agent_config={"model_name": "glm-5"},
    bench_config={"tasks_dir": "./tasks"},
)

# Create and run pipeline
pipeline = EvolutionPipeline(config)
results = asyncio.run(pipeline.run())
```