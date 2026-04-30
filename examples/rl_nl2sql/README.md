NL2SQL 强化学习示例
===================

请先阅读仓库文档 **[DeepAgent 强化学习](../../docs/zh/2.开发指南/高阶用法/DeepAgent强化学习.md)**，其中说明了详细的 **verl**、**vLLM / vllm-ascend**、OpenYuanrong 等环境与依赖部署，以及通用训练流程。

本示例基于 Spider NL2SQL 数据集，利用 openjiuwen 的 `openjiuwen.agent_evolving.agent_rl` 模块提供一个自然语言转化为 SQL 的 DeepAgent（Harness 运行时）。

目录结构
--------

- `prepare_data.py`：将原始 Spider 的 JSON 数据（question / query / tables.json 等）转换为 RL 训练使用的 Parquet 数据。
- `sample_processing.py`：把 Parquet 中的每一行映射为 **`query`**（由库标识、Schema 文本与自然语言问题拼成的任务输入）和 **`ground_truth`**（含 gold SQL 等标注的字符串，reward 据此打分）。
- `prompts.py`：NL2SQL 场景的系统提示词模板。
- `tools.py`：`execute_sql` 工具，直接在 SQLite 上执行 SQL，并返回详细结果 / 错误信息，供模型调试和纠错。
- `sql_eval.py`：SQL 执行匹配逻辑（执行 gold / pred 两条 SQL，比较结果集是否等价）。
- `reward.py`：奖励函数，调用 `sql_eval` 做执行结果匹配，等价记 1 分，否则 0 分。
- `train.py`：训练入口，配置 `RLConfig`，注册 reward / tool / task_data_fn，创建 `OfflineRLOptimizer` 并调用 `train()` 开启训练。

运行步骤
--------

1. 安装SQL依赖：

   ```bash
   pip install sqlparse
   ```

2. 下载 Spider 数据：从 [spider_data.zip](https://drive.google.com/file/d/1uHoxsz3yaalgv3QMfq910UqdvQu3-KwM/view?usp=drive_link) 下载压缩包并解压，得到包含 `database/`、`train.json` 等文件的 Spider 数据根目录（下文记为 `SPIDER_DIR`）。

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

5. 配置 Spider 数据路径供工具和奖励函数使用：

   ```bash
   export SPIDER_DATA_DIR=$SPIDER_DIR
   ```

6. 编辑并启动训练：

   打开 `examples/rl_nl2sql/train.py`，至少配置好以下：

   - **Parquet 训练/验证数据**：修改文件顶部的 `DATA_DIR`，使其指向第 4 步 `prepare_data.py` 的 `--output_dir`（目录中应有 `train.parquet`、`dev.parquet`）。
   - **基座模型**：在 `TrainingConfig` 中设置 `model_path`，指向本机 Hugging Face 格式的模型目录。
   - **其它**：按需调整 `save_path`（checkpoint 输出）、`n_gpus_per_node`、`visible_device` 等。


启动元戎分布式计算框架，其中log_path为日志输出的路径：
   ```bash
   yr start --master -l DEBUG -d log_path
   ```

配置环境变量，终端中输入ifconfig查看ifname和ip：
   ```bash
eth0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1442
        inet XX.XX.XX.XX  netmask 255.255.0.0  broadcast 0.0.0.0
        ether 02:55:ac:10:00:6f  txqueuelen 1000  (Ethernet)
        RX packets 2296835  bytes 4487448162 (4.1 GiB)
        RX errors 0  dropped 0  overruns 0  frame 0
        TX packets 1850832  bytes 685011490 (653.2 MiB)
        TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0

lo: flags=73<UP,LOOPBACK,RUNNING>  mtu 65536
        inet 127.0.0.1  netmask 255.0.0.0
        loop  txqueuelen 1000  (Local Loopback)
        RX packets 21306806  bytes 3990489575 (3.7 GiB)
        RX errors 0  dropped 0  overruns 0  frame 0
        TX packets 21306806  bytes 3990489575 (3.7 GiB)
        TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0**
   ```
配置环境变量：
   ```bash
export HCCL_SOCKET_IFNAME=eth0
export GLOO_SOCKET_IFNAME=eth0
export VLLM_DP_MASTER_IP=XX.XX.XX.XX
export DISTRIBUTED_BACKEND=yr 
export HCCL_EXEC_TIMEOUT=3600
export HCCL_CONNECT_TIMEOUT=3600
export HCCL_IF_BASE_PORT=48890
export TASK_QUEUE_ENABLE=1
export VLLM_ASCEND_ENABLE_NZ=0
export VLLM_USE_V1=1
   ```

启动训练：
   ```bash
   cd examples/rl_nl2sql
   python train.py
   ```

训练结束或重启训练服务时关闭元戎：
   ```bash
   yr stop
   ```