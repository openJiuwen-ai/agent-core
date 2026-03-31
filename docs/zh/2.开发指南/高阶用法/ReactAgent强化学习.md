# ReactAgent强化学习

`openjiuwen.dev_tools.agentrl` 模块提供基于 VERL 强化学习框架和 OpenYuanrong 分布式计算引擎的 ReactAgent 强化训练能力。

**运行环境：** 本教程中的示例**在昇腾（NPU）上运行**。请先参考 [verl 昇腾快速开始](https://verl.readthedocs.io/en/latest/ascend_tutorial/quick_start/ascend_quick_start.html) 安装 **verl** 昇腾版及相关依赖，包括 **torch**、**torch_npu**、CANN、**vllm**、**vllm-ascend** 等；完成后再结合下文「运行环境部署」与 `examples` 下的示例目录准备数据与启动训练。

本教程介绍如何使用 `agentrl` 模块训练一个借助计算器工具求解数学题的 ReactAgent；Agent 需按 `### ANSWER: <answer> ###` 格式输出最终答案。详细代码请参考 `examples/rl_calculator`。

## 运行环境部署

### 前置依赖

| 项目 | 推荐版本 |
|------|-----------------|
| 硬件 | Atlas **910B4**（NPU） |
| CANN | **8.3 RC1** |
| Python | **3.11.10** |
### 从源码安装vLLM、vllm-ascend和VERL

vLLM和vllm-ascend和VERL都需从源码进行安装，建议将三个源码放在同一个目录下，如rl_pkgs。

安装 vLLM

```bash
mkdir rl_pkgs
git clone https://github.com/vllm-project/vllm
cd vllm
git checkout v0.11.0
VLLM_TARGET_DEVICE=empty pip install -v -e .
cd ..
```

安装vllm-ascend

```bash
git clone https://github.com/vllm-project/vllm-ascend
cd vllm-ascend
git checkout v0.11.0rc1
pip install -v -e .
cd ..
```

安装VERL 0.7.0

```bash

git clone https://github.com/verl-project/verl
cd verl
git checkout v0.7.0
pip install -e .
cd ..
```

### pip安装 openJiuwen 与其它 Python 依赖


```bash
pip install openjiuwen
pip install triton-ascend==3.2.0rc4
pip install transformers==4.57.6
pip install uvicorn==0.40.0
pip install fastapi==0.128.0
pip install openai==2.15.0
```

### 安装元戎与ray_adapter
下载 [元戎 OpenYuanrong 0.7.0][yuanrong-wheel] 与 [ray_adapter 0.7.1][ray-adapter-wheel] 的 wheel 包,在conda环境中离线安装：
```bash
pip install openyuanrong-0.7.0-cp311-cp311-manylinux_2_34_aarch64.whl
pip install ray_adapter-0.7.1-py3-none-any.whl
```
下载[verl的元戎patch包][patch-url]，放在rl_pkgs目录下，将其转化为utf8格式：
```bash
iconv -f UTF-16 -t UTF-8 yr_v7.patch > yr_v7.patch.utf8
```
进入verl源码目录，安装patch文件
```bash
cd verl
patch -p1 < ../yr_v7.patch.utf8
```
[yuanrong-wheel]: https://build-logs.openeuler.openatom.cn:38080/temp-archived/openeuler/openYuanrong/yr_release/aarch64/0.7.0/openyuanrong-0.7.0-cp311-cp311-manylinux_2_34_aarch64.whl
[ray-adapter-wheel]: https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/ray_adapter/ray_adapter-0.7.1-py3-none-any.whl
[patch-url]: https://patch-url
## 整体流程

强化学习训练主要包含以下步骤：

1. **准备配置**：构建 `RLConfig`，涵盖训练、Rollout、运行时、持久化等参数。
2. **定义奖励函数**：实现并注册奖励函数，根据 Agent 输出与标准答案计算奖励。
3. **注册工具**：为 Agent 提供可调用的工具（如计算器工具，可进行简单的表达式运算和方程求解）。
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
| algorithm_adv_estimator | 算法类型 |
| save_freq / test_freq | 保存 checkpoint、验证的间隔步数 |
| project_name / experiment_name | 实验项目与名称 |
| save_path | 模型 checkpoint 保存路径 |
| whole_trajectory | 是否使用整条轨迹计算优势 |
| logger | 日志后端，如 `["tensorboard"]` |

**RolloutConfig（Rollout 配置）**

| 参数 | 说明 |
|------|------|
| rollout_n | 每个 prompt 的采样数量 |
| actor_optimizer_lr | Actor 模型学习率 |
| actor_clip_ratio_low / actor_clip_ratio_high | PPO clip 上下界 |

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
    """Evaluate a math expre术、方程求解等
    # ...ssion and return the result."""
    # 实现表达式求值逻辑，支持算
    return result
```

训练时通过 `optimizer.set_tools([calculator])` 注册工具。

## 数据准备

实现 `task_data_fn`，将数据集每行（如 parquet 中的一行）转换为query和ground_truth：

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

`query` 会作为用户输入传给 Agent，`ground_truth` 可在奖励函数中与 Agent 输出对比以获取奖励值。

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

可参考项目中的 `examples/rl_calculator` 完整示例以运行，训练日志会输出模型路径、数据路径、算法、epoch 等信息，若启用了 TensorBoard 可本地加载查看训练曲线。

更多 API 与配置细节见 [agentrl 模块文档](../../API文档/openjiuwen.dev_tools/agentrl.README.md)。
