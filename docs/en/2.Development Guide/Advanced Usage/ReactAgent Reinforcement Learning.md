# ReactAgent Reinforcement Learning

openJiuwen provides reinforcement learning (RL) based Agent training capabilities. Through the `openjiuwen.dev_tools.agentrl` module, you can use algorithms such as GRPO and PPO to perform online reinforcement learning on Agents that use Tools, continuously improving Agent performance on specific tasks.

This tutorial uses `examples/rl_calculator` as an example to introduce how to use the reinforcement learning module to train an Agent that can correctly solve math problems with the help of a calculator tool.

## Overall Flow

Reinforcement learning training mainly includes the following steps:

1. **Prepare Configuration**: Build `RLConfig`, covering training, Rollout, runtime, persistence, and other parameters.
2. **Define Reward Function**: Implement and register a reward function to compute rewards based on Agent output and ground truth.
3. **Register Tools**: Provide callable tools for the Agent (e.g., a calculator).
4. **Prepare Data**: Implement `task_data_fn` to convert dataset rows into Agent input format.
5. **Start Training**: Create `RLOptimizer`, configure it, and call `train()` to start training.

## Configuration

### Building RLConfig

`RLConfig` is the top-level configuration for reinforcement learning training, including training, Rollout, runtime, and persistence sub-configurations:

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
    # Multi-round Ada: uncomment — ada=AdaConfig(rollout_max_round=2, final_keep_per_prompt=8),
)
```

### Parameter Descriptions

**TrainingConfig (Training Configuration)**

| Parameter | Description |
|-----------|-------------|
| model_path | Base model path |
| train_files / val_files | Training/validation data files (parquet format) |
| train_batch_size | Training batch size |
| total_epochs | Total training epochs |
| max_prompt_length / max_response_length | Max input/output token length |
| n_gpus_per_node / visible_device | GPU count and visible device IDs |
| algorithm_adv_estimator | Algorithm type, e.g. `grpo`, `ppo` |
| save_freq / test_freq | Checkpoint save and validation interval (steps) |
| project_name / experiment_name | Project and experiment names (for logging) |
| save_path | Model checkpoint save path |
| whole_trajectory | Whether to use full trajectory for advantage computation |
| logger | Logging backends, e.g. `["tensorboard"]` |

**RolloutConfig (Rollout Configuration)**

| Parameter | Description |
|-----------|-------------|
| rollout_n | Number of samples per prompt |
| actor_optimizer_lr | Actor model learning rate |
| actor_clip_ratio_low / actor_clip_ratio_high | PPO clip lower/upper bounds |

**AdaConfig (optional, enable via `RLConfig.ada`)**

| Parameter | Description |
|-----------|-------------|
| rollout_max_round | Max dialogue rounds when multi-round Ada rollout is enabled |
| final_keep_per_prompt | Number of samples kept per prompt under Ada sampling |

**AgentRuntimeConfig (Runtime Configuration)**

| Parameter | Description |
|-----------|-------------|
| system_prompt | Agent system prompt |
| temperature | Generation temperature, controls randomness |
| max_new_tokens | Max tokens per generation |

**PersistenceConfig (Persistence Configuration)**

| Parameter | Description |
|-----------|-------------|
| enabled | Whether to enable rollout persistence |
| save_path | Persistence file save directory |
| flush_interval | Disk write interval (steps) |
| save_rollouts / save_step_summaries | Whether to save rollout details and step summaries |

## System Prompt

The system prompt should clearly define the Agent's role, tool usage, and output format. For example, in the calculator scenario:

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

The Agent should use `### ANSWER: <answer> ###` format for final output to facilitate reward function parsing.

## Tool Definition

Use the `@tool` decorator to define tools callable by the Agent. For a calculator:

```python
from openjiuwen.core.foundation.tool import tool

@tool(
    name="calculator",
    description="Perform arithmetic calculations, simplify algebraic expressions, and solve equations.",
)
def calculator(expression: str) -> str:
    """Evaluate a math expression and return the result."""
    # Implement expression evaluation logic, support arithmetic, equation solving, etc.
    # ...
    return str(result)
```

Register tools during training via `optimizer.set_tools([calculator])`.

## Data Preparation

Implement `task_data_fn` to convert each dataset row (e.g., a row in parquet) into Agent input. The dataset typically contains `question`, `result`, and other columns:

```python
def task_data_fn(task_sample: dict) -> dict:
    """Convert dataset row to Agent input format.

    Dataset columns: question, result, chain, etc.
    Returns query (Agent input) and ground_truth (for reward computation).
    """
    return {
        "query": task_sample.get("question", ""),
        "ground_truth": task_sample.get("result", ""),
    }
```

`query` is passed as user input to the Agent; `ground_truth` can be compared with Agent output in the reward function.

## Reward Function

The reward function receives `RolloutMessage` and computes rewards based on the Agent's dialogue trajectory and output. The return value must include `reward_list` and `global_reward`:

```python
from openjiuwen.dev_tools.agentrl.coordinator.schemas import RolloutMessage

def calc_reward(msg: RolloutMessage) -> dict:
    """Return 1.0 if answer is correct, otherwise 0.0."""
    if not msg.rollout_info:
        return {"reward_list": [], "global_reward": 0.0}

    first_turn = msg.rollout_info[0]
    ground_truth = (first_turn.input_prompt or {}).get("ground_truth", "")

    last_turn = msg.rollout_info[-1]
    response = last_turn.output_response or {}
    content = response.get("content", "")

    # Parse answer in ### ANSWER: xxx ### format from content
    answer = _extract_answer(content)
    matched = _results_match(answer, ground_truth)
    global_reward = 1.0 if matched else 0.0
    reward_list = [global_reward] * len(msg.rollout_info)

    return {"reward_list": reward_list, "global_reward": global_reward}
```

- `rollout_info`: Input and output info for each dialogue turn.
- `ground_truth`: From `input_prompt` returned by `task_data_fn`.
- `reward_list`: Step-level rewards; `global_reward`: Task-level total reward.

## Starting Training

After assembling the above configuration and functions, create `RLOptimizer` and start training:

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

- `register_reward`: Register the reward function.
- `set_tools`: Register the tool list.
- `set_task_data_fn`: Set the data conversion function.
- `train()`: Start the training loop; `stop()`: Clean up resources.

## Running the Example

Refer to the complete example in `examples/rl_calculator`:

```bash
# Enter the example directory
cd examples/rl_calculator

# Ensure data files exist: train.parquet, test.parquet (with question, result, etc.)
# Run training
python train.py
```

Training logs will output model path, data path, algorithm, epoch, etc. If TensorBoard is enabled, use `logger=["tensorboard"]` to view training curves.

## Further Notes

- **Algorithms**: `algorithm_adv_estimator` supports `grpo`, `ppo`, etc.
- **Multi-turn (Ada)**: set `ada=AdaConfig(rollout_max_round=..., final_keep_per_prompt=...)` on `RLConfig`; see [config documentation](../../API%20Docs/openjiuwen.dev_tools/agentrl/config.md).
- **Persistence**: Use `PersistenceConfig` to save rollout data locally for debugging and analysis.

For more API and configuration details, see the [agentrl module documentation](../../API%20Docs/openjiuwen.dev_tools/agentrl.README.md).
