NL2SQL 强化学习示例
=====================

**运行环境：** 本示例**在昇腾（NPU）上运行**。请先参考 [verl 昇腾快速开始](https://verl.readthedocs.io/en/latest/ascend_tutorial/quick_start/ascend_quick_start.html) 安装 **verl** 昇腾版及相关依赖，包括 **torch**、**torch_npu**、CANN、**vllm**、**vllm-ascend** 等；完成后再按下文准备数据与启动训练。

本示例基于 Spider NL2SQL 数据集，利用openjiuwen的 `agentrl` 模块提供一个「自然语言 → SQL」的训练场景。

目录结构
--------

- `prepare_data.py`：将 Spider 的 JSON 数据（question / query / tables.json 等）转换为 RL 训练使用的 Parquet 数据。
- `sample_processing.py`：把 Parquet 中的每一行映射为 **`query`**（由库标识、Schema 文本与自然语言问题拼成的任务输入）和 **`ground_truth`**（含 gold SQL 等标注的 JSON 字符串，reward 据此打分）。
- `prompts.py`：NL2SQL 场景的系统提示词模板。
- `tools.py`：`execute_sql` 工具，直接在 SQLite 上执行 SQL，并返回详细结果 / 错误信息，供模型调试和纠错。
- `sql_eval.py`：SQL 执行匹配逻辑（执行 gold / pred 两条 SQL，比较结果集是否等价）。
- `reward.py`：奖励函数，调用 `sql_eval` 做 execution match，等价记 1 分，否则 0 分。
- `train.py`：训练入口，配置 `RLConfig`，注册 reward / tool / task_data_fn，启动 `RLOptimizer.train()`开启训练。

运行步骤
--------

1. 安装依赖（在你的python虚拟环境中）：

   ```bash
   pip install openjiuwen
   pip install sqlparse
   ```

2. 下载 Spider 数据：从 [spider_data.zip](https://drive.google.com/file/d/1403EGqzIDoHMdQF4c9Bkyl7dZLZ5Wt6J/view?pli=1) 下载压缩包并解压，得到包含 `database/`、`train.json` 等文件的 Spider 数据根目录（下文记为 `SPIDER_DIR`）。

3. 准备数据库环境变量：

   ```bash
   SPIDER_DIR=/path/to/spider_data
   ```

4. 生成 Parquet 数据：

   ```bash
   cd examples/rl_nl2sql

   python prepare_data.py \
     --spider_dir "$SPIDER_DIR" \
     --output_dir /path/to/output
   ```

   会在输出目录下生成 `train.parquet` / `dev.parquet`（以及可选的 `test.parquet`）。

5. 配置 Spider 数据路径给工具和奖励函数使用：

   ```bash
   export SPIDER_DATA_DIR=$SPIDER_DIR
   ```

6. 编辑并启动训练：

   打开 `examples/rl_nl2sql/train.py`，至少配置好以下路径与资源后再执行下方命令：

   - **Parquet 训练/验证数据**：修改文件顶部的 `DATA_DIR`，使其指向第 4 步 `prepare_data.py` 的 `--output_dir`（目录中应有 `train.parquet`、`dev.parquet`）。也可不改 `DATA_DIR`，在 `TrainingConfig` 中直接填写 `train_files`、`val_files` 的绝对路径。
   - **基座模型**：在 `TrainingConfig` 中设置 `model_path`，指向本机 Hugging Face 格式的模型目录。
   - **其它**：按需调整 `save_path`（checkpoint 输出）、`n_gpus_per_node`、`visible_device` 等。

   ```bash
   cd examples/rl_nl2sql
   python train.py
   ```

