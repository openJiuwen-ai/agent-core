Calculator 强化学习示例
=======================

**运行环境：** 本示例**在昇腾（NPU）上运行**。请先参考 [verl 昇腾快速开始](https://verl.readthedocs.io/en/latest/ascend_tutorial/quick_start/ascend_quick_start.html) 安装 **verl** 昇腾版及相关依赖，包括 **torch**、**torch_npu**、CANN、**vllm**、**vllm-ascend** 等；完成后再按下文准备数据与启动训练。

本示例演示如何使用openjiuwen的 `agentrl` 模块训练一个借助计算器工具求解数学题的 ReactAgent。Agent 需按 `### ANSWER: <answer> ###` 格式输出最终答案。

目录结构
--------

- `sample_processing.py`：将 Parquet 行转换为 agent 输入（`query` + `ground_truth`）。
- `prompts.py`：计算器场景的系统提示词模板。
- `tools.py`：计算器工具，支持算术、代数化简和方程求解。
- `reward.py`：奖励函数，答案正确记 1 分，否则 0 分。
- `train.py`：训练入口，配置 `RLConfig`，注册 reward / tool / task_data_fn，启动 `RLOptimizer.train()以开启训练`。

运行步骤
--------

1. 下载 Calc-X 数据：从 [calc-x-data.zip](https://drive.google.com/file/d/1FQMyKLLd6hP9dw9rfZn1EZOWNvKaDsqw/view) 下载压缩包并解压。解压后应得到包含 `train.parquet` 与 `test.parquet` 的目录（Parquet 需含 `question`、`result` 列，分别对应题目与标准答案）。

2. 编辑并启动训练：

   打开 `examples/rl_calculator/train.py`，至少配置好以下路径与资源后再执行下方命令：

   - **Parquet 训练/验证数据**：修改文件顶部的 `DATA_DIR`，使其指向第 1 步解压后的目录（目录中应有 `train.parquet`、`test.parquet`）。也可不改 `DATA_DIR`，在 `TrainingConfig` 中直接填写 `train_files`、`val_files` 的绝对路径。
   - **基座模型**：在 `TrainingConfig` 中设置 `model_path`，指向本机 Hugging Face 格式的模型目录。
   - **其它**：按需调整 `save_path`（checkpoint 输出）、`n_gpus_per_node`、`visible_device` 等。

   ```bash
   cd examples/rl_calculator
   python train.py
   ```
