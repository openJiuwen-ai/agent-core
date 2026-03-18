NL2SQL 强化学习示例
=====================

本示例基于 Spider NL2SQL 数据集，为 `agent_rl` 提供一个「自然语言 → SQL」的训练场景，结构与 `rl_calculator` 示例保持一致。

目录结构
--------

- `prepare_data.py`：将 Spider 的 JSON 数据（question / query / tables.json 等）转换为 RL 训练使用的 Parquet 数据。
- `sample_processing.py`：将一行 Parquet 数据转换为 agent 的输入（`query` + `ground_truth`）。
- `prompts.py`：NL2SQL 场景的系统提示词模板。
- `tools.py`：`execute_sql` 工具，直接在 SQLite 上执行 SQL，并返回详细结果 / 错误信息，供模型调试和纠错。
- `sql_eval.py`：SQL 执行匹配逻辑（执行 gold / pred 两条 SQL，比较结果集是否等价）。
- `reward.py`：奖励函数，调用 `sql_eval` 做 execution match，等价记 1 分，否则 0 分。
- `train.py`：训练入口，配置 `RLConfig`，注册 reward / tool / task_data_fn，启动 `RLOptimizer.train()`。

运行步骤（简要）
----------------

1. 安装依赖（在你的虚拟环境中）：

   ```bash
   pip install pandas pyarrow sqlparse
   # 如果还没安装本项目，可按需：
   # cd /path/to/agent-core
   # pip install -e .
   ```

2. 准备 Spider 数据（假设路径为 `SPIDER_DIR`）：

   ```bash
   SPIDER_DIR=/path/to/spider_data
   ```

3. 生成 Parquet 数据：

   ```bash
   cd examples/rl_nl2sql

   python prepare_data.py \
     --spider_dir "$SPIDER_DIR" \
     --output_dir /path/to/output
   ```

   会在输出目录下生成 `train.parquet` / `dev.parquet`（以及可选的 `test.parquet`）。

4. 配置 Spider 数据路径给工具和奖励函数使用：

   ```bash
   export SPIDER_DATA_DIR=$SPIDER_DIR
   ```

5. 启动 RL 训练：

   ```bash
   cd examples/rl_nl2sql
   python train.py
   ```

   如需调整模型路径 / GPU 数量 / 可见设备等，可直接修改 `train.py` 中的 `TrainingConfig` 配置。

