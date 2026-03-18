Calculator 强化学习示例
=======================

本示例演示如何使用 `agentrl` 训练一个借助计算器工具求解数学题的 Agent。Agent 需按 `### ANSWER: <answer> ###` 格式输出最终答案。

目录结构
--------

- `sample_processing.py`：将 Parquet 行转换为 agent 输入（`query` + `ground_truth`）。
- `prompts.py`：计算器场景的系统提示词模板。
- `tools.py`：计算器工具，支持算术、代数化简和方程求解。
- `reward.py`：奖励函数，答案正确记 1 分，否则 0 分。
- `train.py`：训练入口，配置 `RLConfig`，注册 reward / tool / task_data_fn，启动 `RLOptimizer.train()`。

运行步骤
--------

1. 准备数据：Parquet 文件需包含 `question`、`result` 列，分别对应题目与标准答案。

2. 在 `train.py` 中配置数据路径，例如：

   ```python
   DATA_DIR = "/path/to/data"
   ```

3. 安装依赖并启动训练：

   ```bash
   pip install sympy  # 计算器工具依赖
   cd examples/rl_calculator
   python train.py
   ```

   如需调整模型路径、GPU 数量等，直接修改 `train.py` 中的 `TrainingConfig`。
