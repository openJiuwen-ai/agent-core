# DeepAgent Reinforcement Learning

The `openjiuwen.agent_evolving.agent_rl` module provides reinforcement learning training capabilities based on the VERL reinforcement learning framework and the OpenYuanrong distributed computing engine. This tutorial walks through detailed training environment setup and briefly explains how to use this module to train a **Harness `DeepAgent`** (`openjiuwen.harness.deep_agent.DeepAgent`) that solves math problems with a calculator tool.

**Runtime note**: Standard rollout uses **`openjiuwen.harness.DeepAgent`** as the outer runtime. [`AgentFactory`](../API%20Docs/openjiuwen.agent_evolving/agent_rl/offline/runtime.md) builds and configures each task's agent with **`DeepAgentConfig`** (single-round rollout: **`enable_task_loop=False`**); tools attach to **`DeepAgent`**. Trajectories are collected with **`RLRail`** via **`register_rail`** on that agent; the inner model loop enables token IDs/logprobs for RL. If you substitute a custom `agent_factory`, the built-in **`TrajectoryCollector`** still expects something that exposes **`register_rail`** / **`invoke`** (for example **`DeepAgent`**).

## Environment Setup

### Prerequisites

| Item | Recommended version |
|------|---------------------|
| Hardware | Atlas **910B4** (NPU) |
| CANN | **8.3 RC1** |
| Python | **3.11.10** |

### Install vLLM, vllm-ascend, and VERL from source

vLLM, vllm-ascend, and VERL should all be installed from source. It is recommended to place the three source trees in the same directory, for example `rl_pkgs`.

```bash
mkdir rl_pkgs
cd rl_pkgs
```

Install vLLM v0.11.0:

```bash
mkdir rl_pkgs
git clone https://github.com/vllm-project/vllm
cd vllm
git checkout v0.11.0
VLLM_TARGET_DEVICE=empty pip install -v -e .
cd ..
```

Install vllm-ascend v0.11.0rc1:

```bash
git clone https://github.com/vllm-project/vllm-ascend
cd vllm-ascend
git checkout v0.11.0rc1
pip install -v -e .
cd ..
```

Install VERL 0.7.0:

```bash
git clone https://github.com/verl-project/verl
cd verl
git checkout v0.7.0
pip install -e .
cd ..
```

### Install openJiuwen and other Python dependencies


```bash
pip install openjiuwen
pip install triton-ascend==3.2.0rc4
pip install transformers==4.57.6
pip install uvicorn==0.40.0
pip install fastapi==0.128.0
pip install openai==2.15.0
```

### Install OpenYuanrong and ray_adapter

Download the [OpenYuanrong 0.7.0][yuanrong-wheel] and [ray_adapter 0.7.1][ray-adapter-wheel] wheel packages and install them offline in your conda environment:

```bash
pip install openyuanrong-0.7.0-cp311-cp311-manylinux_2_34_aarch64.whl
pip install ray_adapter-0.7.1-py3-none-any.whl
```

Download the [VERL OpenYuanrong patch package][patch-url], place it under `rl_pkgs`, and convert it to UTF-8:

```bash
iconv -f UTF-16 -t UTF-8 yr_v7.patch > yr_v7.patch.utf8
```

Apply the patch in the VERL source tree:

```bash
cd verl
patch -p1 < ../yr_v7.patch.utf8
```

[yuanrong-wheel]: https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/tmp/202603311854/openyuanrong-0.7.1-cp311-cp311-manylinux_2_34_aarch64.whl
[ray-adapter-wheel]: https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/ray_adapter/ray_adapter-0.7.1-py3-none-any.whl
[patch-url]: https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/tmp/verl-070-use-yuanrong-as-distributed-backend.patch

## Overall Flow

Reinforcement learning training mainly includes the following steps:

1. **Prepare Configuration**: Build `RLConfig`, covering training, Rollout, runtime, persistence, and other parameters.
2. **Define Reward Function**: Implement and register a reward function to compute rewards based on Agent output and ground truth.
3. **Register Tools**: Provide callable tools for the Agent (e.g., a calculator tool for simple expression evaluation and equation solving).
4. **Prepare Data**: Implement `task_data_fn` to convert dataset rows into Agent input format.
5. **Start Training**: Create `OfflineRLOptimizer`, configure it, and call `train()` to start training.

## Configuration

### Building RLConfig

`RLConfig` is the top-level configuration for reinforcement learning training, including training, Rollout, runtime, and persistence sub-configurations:

```python
from openjiuwen.agent_evolving.agent_rl import RLConfig
from openjiuwen.agent_evolving.agent_rl.config import (
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
| algorithm_adv_estimator | Algorithm type |
| save_freq / test_freq | Checkpoint save and validation interval (steps) |
| project_name / experiment_name | Project and experiment names |
| save_path | Model checkpoint save path |
| whole_trajectory | Whether to use full trajectory for advantage computation |
| logger | Logging backends, e.g. `["tensorboard"]` |

**RolloutConfig (Rollout Configuration)**

| Parameter | Description |
|-----------|-------------|
| rollout_n | Number of samples per prompt |
| actor_optimizer_lr | Actor model learning rate |
| actor_clip_ratio_low / actor_clip_ratio_high | PPO clip lower/upper bounds |

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

You can require the Agent to use the `### ANSWER: <answer> ###` format in the final output so the reward function can parse it.

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
    # Implement expression evaluation logic; support arithmetic, equation solving, etc.
    # ...
    return result
```

Register tools during training via `optimizer.set_tools([calculator])`.

## Data Preparation

Implement `task_data_fn` to convert each dataset row (e.g., a row in parquet) into `query` and `ground_truth`:

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

`query` is passed as user input to the Agent; `ground_truth` can be compared with Agent output in the reward function to obtain the reward value.

## Reward Function

The reward function receives `RolloutMessage` and computes rewards based on the Agent's dialogue trajectory and output. The return value must include `reward_list` and `global_reward`:

```python
from openjiuwen.agent_evolving.agent_rl.schemas import RolloutMessage

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
- `ground_truth`: From `ground_truth` in the `input_prompt` produced from `task_data_fn`.
- `reward_list`: Step-level rewards; `global_reward`: Task-level total reward.

## Starting Training

After assembling the above configuration and functions, create `OfflineRLOptimizer` and start training:

```python
from openjiuwen.core.common.logging import logger
from openjiuwen.agent_evolving.agent_rl import RLConfig, OfflineRLOptimizer

def main():
    optimizer = OfflineRLOptimizer(config)
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

See the [complete examples/rl_calculator example](../../../../examples/rl_calculator/README.md) in the repository to run training. Logs will print the model path, data paths, algorithm, epoch, and related information; if TensorBoard is enabled, load it locally to view training curves.

For more API and configuration details, see the [agent_rl module documentation](../API%20Docs/openjiuwen.agent_evolving/agent_rl/agent_rl.README.md).
