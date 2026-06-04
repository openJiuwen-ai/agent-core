# evaluator_pipeline 模块概述

`openjiuwen.agent_evolving.evaluator.evaluator_pipeline` 是 openJiuwen 中**技能评估与进化流水线模块**，负责协调 Agent 和 Benchmark 执行评估任务。

## 模块结构

该模块包含以下子模块：

| 子模块 | 说明 | 文档文件 |
|--------|------|----------|
| `base` | Agent 和 Benchmark 的抽象适配器接口 | [base.md](base.md) |
| `config` | 流水线配置类 | [config.md](config.md) |
| `docker_env` | Docker 容器环境管理 | [docker_env.md](docker_env.md) |
| `models` | 核心数据模型定义 | [models.md](models.md) |
| `pipeline` | 评估流水线核心实现 | [pipeline.md](pipeline.md) |
| `skill_manager` | 技能管理与进化追踪 | [skill_manager.md](skill_manager.md) |

## 核心组件

### 1. EvolutionPipeline
核心执行类，支持两种运行模式：
- **单轮模式**：执行一次评估后结束
- **进化模式**：多轮迭代，自动优化技能直到收敛

### 2. BaseAgentAdapter
Agent 适配器抽象基类，开发者可继承实现自定义 Agent。

### 3. BaseBenchAdapter
基准测试适配器抽象基类，开发者可继承实现自定义基准测试。

### 4. SkillManager
技能管理器，负责技能的加载、保存和版本演进。

### 5. DockerEnvironment
Docker 环境封装，提供容器的构建、启动、执行和停止操作。

## 数据流转

```
PipelineConfig → EvolutionPipeline → Task → AgentContext → AgentRunResult → EvalResult → PipelineResult
```

## 快速开始

```python
import asyncio
from openjiuwen.agent_evolving.evaluator.evaluator_pipeline import (
    EvolutionPipeline,
    PipelineConfig,
)

# 创建配置
config = PipelineConfig(
    agent="jiuwenswarm",
    benchmark="skillsbench",
    evolution_mode=True,
    max_iterations=5,
    agent_config={"model_name": "glm-5"},
    bench_config={"tasks_dir": "./tasks"},
)

# 创建并运行流水线
pipeline = EvolutionPipeline(config)
results = asyncio.run(pipeline.run())
```