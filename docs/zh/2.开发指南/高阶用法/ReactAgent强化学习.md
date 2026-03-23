# ReactAgent强化学习

openJiuwen 提供基于强化学习（RL）的 Agent 训练能力。通过 `openjiuwen.dev_tools.agentrl` 模块，你可以使用 GRPO、PPO 等算法对使用工具（Tools）的 Agent 进行在线强化学习训练，使 Agent 在特定任务上的表现持续提升。

本教程以 `examples/rl_calculator` 为例，介绍如何使用强化学习模块训练一个能够借助计算器工具正确求解数学题的 Agent。

## 整体流程

强化学习训练主要包含以下步骤：

1. **准备配置**：构建 `RLConfig`，涵盖训练、Rollout、运行时、持久化等参数。
2. **定义奖励函数**：实现并注册奖励函数，根据 Agent 输出与标准答案计算奖励。
3. **注册工具**：为 Agent 提供可调用的工具（如计算器）。
4. **准备数据**：实现 `task_data_fn`，将数据集行转换为 Agent 输入格式。
5. **启动训练**：创建 `RLOptimizer`，配置完成后调用 `train()` 启动训练。

## 配置

### 构建 RLConfig

`RLConfig` 是强化学习训练的顶层配置，包含训练、Rollout、运行时和持久化等子配置：

```python
from openjiuwen.dev_tools.agentrl import RLConfig
from openjiuwen.dev_tools.agentrl.config.schemas import (
    AdaConfig,
    AgentRuntimeConfig,
    PersistenceConfig,
    RolloutConfig,
    TrainingConfig,
)

config = RLConfig(
    training=TrainingConfig(
        model_path="~/model/Qwen2.5-1.5B-Instruct",
        train_files="/path/to/train.parquet",
        val_files="/path/to/test.parquet",
        train_batch_size=32,
        total_epochs=2,
        max_prompt_length=3072,
        max_response_length=3072,
        n_gpus_per_node=4,
        visible_device="0,1,2,3",
        save_freq=200,
        test_freq=20,
        project_name="CalcX",
        experiment_name="calc_x_grpo",
        algorithm_adv_estimator="grpo",
        whole_trajectory=True,
        logger=["tensorboard"],
        val_before_train=False,
        save_path="~/checkpoint/calx"
    ),
    rollout=RolloutConfig(
        rollout_n=4,
        actor_optimizer_lr=1e-6,
        actor_clip_ratio_low=0.2,
        actor_clip_ratio_high=0.3,
    ),
    runtime=AgentRuntimeConfig(
        system_prompt=system_prompt,
        temperature=0.7,
        max_new_tokens=512,
    ),
    persistence=PersistenceConfig(
        enabled=True,
        save_path="./rollout_output",
        flush_interval=100,
        save_rollouts=True,
        save_step_summaries=True,
    ),
    # 多轮 + Ada 采样时取消注释：ada=AdaConfig(rollout_max_round=2, final_keep_per_prompt=8),
)
```

### 参数说明

**TrainingConfig（训练配置）**

| 参数 | 说明 |
|------|------|
| model_path | 基座模型路径 |
| train_files / val_files | 训练/验证数据文件（parquet 格式） |
| train_batch_size | 训练批次大小 |
| total_epochs | 训练总轮数 |
| max_prompt_length / max_response_length | 输入/输出最大 token 长度 |
| n_gpus_per_node / visible_device | GPU 数量与可见设备 ID |
| algorithm_adv_estimator | 算法类型，如 `grpo`、`ppo` |
| save_freq / test_freq | 保存 checkpoint、验证的间隔步数 |
| project_name / experiment_name | 实验项目与名称（用于日志） |
| save_path | 模型 checkpoint 保存路径 |
| whole_trajectory | 是否使用整条轨迹计算优势 |
| logger | 日志后端，如 `["tensorboard"]` |

**RolloutConfig（Rollout 配置）**

| 参数 | 说明 |
|------|------|
| rollout_n | 每个 prompt 的采样数量 |
| actor_optimizer_lr | Actor 模型学习率 |
| actor_clip_ratio_low / actor_clip_ratio_high | PPO clip 上下界 |

**AdaConfig（可选，通过 `RLConfig.ada` 启用）**

| 参数 | 说明 |
|------|------|
| rollout_max_round | 多轮 rollout 时 Agent 与工具交互的最大轮数（与默认单轮路径不同，需启用 Ada） |
| final_keep_per_prompt | Ada 采样下每个 prompt 最终保留条数 |

**AgentRuntimeConfig（运行时配置）**

| 参数 | 说明 |
|------|------|
| system_prompt | Agent 系统提示词 |
| temperature | 生成温度，控制随机性 |
| max_new_tokens | 单次生成最大 token 数 |

**PersistenceConfig（持久化配置）**

| 参数 | 说明 |
|------|------|
| enabled | 是否开启 rollout 持久化 |
| save_path | 持久化文件保存目录 |
| flush_interval | 写入磁盘的间隔步数 |
| save_rollouts / save_step_summaries | 是否保存 rollout 详情、步级摘要 |

## 系统提示词

系统提示词需要明确 Agent 的角色、工具用法及输出格式。例如在计算器场景下：

```python
from openjiuwen.core.foundation.prompt import PromptTemplate

CALCULATOR_SYSTEM_PROMPT = PromptTemplate(
    name="calculator_system",
    content=(
        "You are a {{role}}. Use the {{tool_name}} tool to solve "
        "{{task_type}} problems step by step.\n"
        "Output the answer when you are ready. "
        "The answer should be surrounded by three sharps (`###`), "
        "in the form of {{answer_format}}."
    ),
)

system_prompt = CALCULATOR_SYSTEM_PROMPT.format(
    keywords={
        "role": "calculator assistant",
        "tool_name": "calculator",
        "task_type": "math",
        "answer_format": "### ANSWER: <answer> ###",
    }
).content
```

要求 Agent 在最终输出时使用 `### ANSWER: <answer> ###` 格式，便于奖励函数解析。

## 工具定义

使用 `@tool` 装饰器定义 Agent 可调用的工具。以计算器为例：

```python
from openjiuwen.core.foundation.tool import tool

@tool(
    name="calculator",
    description="Perform arithmetic calculations, simplify algebraic expressions, and solve equations.",
)
def calculator(expression: str) -> str:
    """Evaluate a math expression and return the result."""
    # 实现表达式求值逻辑，支持算术、方程求解等
    # ...
    return str(result)
```

训练时通过 `optimizer.set_tools([calculator])` 注册工具。

## 数据准备

实现 `task_data_fn`，将数据集每行（如 parquet 中的一行）转换为 Agent 输入。数据集通常包含 `question`、`result` 等列：

```python
def task_data_fn(task_sample: dict) -> dict:
    """将数据集行转为 Agent 输入格式。

    数据集列：question, result, chain 等。
    返回 query（Agent 输入）和 ground_truth（用于奖励计算）。
    """
    return {
        "query": task_sample.get("question", ""),
        "ground_truth": task_sample.get("result", ""),
    }
```

`query` 会作为用户输入传给 Agent，`ground_truth` 可在奖励函数中与 Agent 输出对比。

## 奖励函数

奖励函数接收 `RolloutMessage`，根据 Agent 的对话轨迹和输出计算奖励。返回值需包含 `reward_list` 和 `global_reward`：

```python
from openjiuwen.dev_tools.agentrl.coordinator.schemas import RolloutMessage

def calc_reward(msg: RolloutMessage) -> dict:
    """答案正确返回 1.0，否则返回 0.0。"""
    if not msg.rollout_info:
        return {"reward_list": [], "global_reward": 0.0}

    first_turn = msg.rollout_info[0]
    ground_truth = (first_turn.input_prompt or {}).get("ground_truth", "")

    last_turn = msg.rollout_info[-1]
    response = last_turn.output_response or {}
    content = response.get("content", "")

    # 从 content 中解析 ### ANSWER: xxx ### 格式的答案
    answer = _extract_answer(content)
    matched = _results_match(answer, ground_truth)
    global_reward = 1.0 if matched else 0.0
    reward_list = [global_reward] * len(msg.rollout_info)

    return {"reward_list": reward_list, "global_reward": global_reward}
```

- `rollout_info`：各轮对话的输入、输出信息。
- `ground_truth`：来自 `task_data_fn` 返回的 `input_prompt`。
- `reward_list`：各步的步级奖励；`global_reward`：任务级总奖励。

## 启动训练

组装以上配置与函数后，创建 `RLOptimizer` 并启动训练：

```python
from openjiuwen.core.common.logging import logger
from openjiuwen.dev_tools.agentrl import RLConfig, RLOptimizer

def main():
    optimizer = RLOptimizer(config)
    optimizer.register_reward(calc_reward, name="calc_reward")
    optimizer.set_tools([calculator])
    optimizer.set_task_data_fn(task_data_fn)

    logger.info("=== Starting Calculator RL Training ===")
    logger.info("Model: %s", config.training.model_path)
    logger.info("Train files: %s", config.training.train_files)
    logger.info("Algorithm: %s", config.training.algorithm_adv_estimator)
    logger.info("Epochs: %d", config.training.total_epochs)

    try:
        optimizer.train()
    except KeyboardInterrupt:
        logger.info("Training interrupted by user")
    finally:
        optimizer.stop()
        logger.info("Training complete")


if __name__ == "__main__":
    main()
```

- `register_reward`：注册奖励函数。
- `set_tools`：注册工具列表。
- `set_task_data_fn`：设置数据转换函数。
- `train()`：启动训练循环；`stop()`：清理资源。

## 运行示例

可参考项目中的 `examples/rl_calculator` 完整示例：

```bash
# 进入示例目录
cd examples/rl_calculator

# 确保数据文件存在：train.parquet、test.parquet（包含 question、result 等列）
# 运行训练
python train.py
```

训练日志会输出模型路径、数据路径、算法、epoch 等信息。若启用了 TensorBoard，可使用 `logger=["tensorboard"]` 查看训练曲线。

## 进一步说明

- **算法**：`algorithm_adv_estimator` 支持 `grpo`、`ppo` 等。
- **多轮对话（Ada）**：在 `RLConfig` 上设置 `ada=AdaConfig(rollout_max_round=..., final_keep_per_prompt=...)`；详见 [config 文档](../../API文档/openjiuwen.dev_tools/agentrl/config.md)。
- **持久化**：通过 `PersistenceConfig` 可将 rollout 数据保存到本地，便于调试与分析。

更多 API 与配置细节见 [agentrl 模块文档](../../API文档/openjiuwen.dev_tools/agentrl.README.md)。
