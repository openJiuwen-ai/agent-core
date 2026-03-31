Calculator 强化学习示例
=======================

请先阅读仓库文档 **[ReactAgent 强化学习](../../docs/zh/2.开发指南/高阶用法/ReactAgent强化学习.md)**，其中说明了详细的 **verl**、**vLLM / vllm-ascend**、OpenYuanrong 等环境与依赖部署，以及通用训练流程。


本示例演示如何使用 openjiuwen 的 `agentrl` 模块训练一个借助计算器工具求解数学题的 ReactAgent。

目录结构
--------

- `sample_processing.py`：将 Parquet 行转换为 agent 输入（`query` + `ground_truth`）。
- `prompts.py`：计算器场景的系统提示词模板。
- `tools.py`：计算器工具，支持算术、代数化简和方程求解。
- `reward.py`：奖励函数，答案正确记 1 分，否则 0 分。
- `train.py`：训练入口，配置 `RLConfig`，注册 reward / tool / task_data_fn，启动 `RLOptimizer.train()以开启训练`。

运行步骤
--------

1. 下载 Calc-X 数据：从 [calc-x-data.zip](https://drive.google.com/file/d/1FQMyKLLd6hP9dw9rfZn1EZOWNvKaDsqw/view) 下载压缩包并解压。解压后应得到包含 `train.parquet` 与 `test.parquet` 的目录（Parquet 含 `question`、`result` 列，分别对应题目与标准答案）。

2. 编辑并启动训练：

   打开 `examples/rl_calculator/train.py`，至少配置好以下内容：

   - **Parquet 训练/验证数据**：修改文件顶部的 `DATA_DIR`，使其指向第 1 步解压后的目录（目录中应有 `train.parquet`、`test.parquet`）。
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
配置环境变量
   ```bash
export HCCL_SOCKET_IFNAME=eth0
export GLOO_SOCKET_IFNAME=eth0
export VLLM_DP_MASTER_IP=XX.XX.XX.XX
export DISTRIBUTED_BACKEND=yr 
export HCCL_EXEC_TIMEOUT=3600
export HCCL_CONNECT_TIMEOUT=3600
export HCCL_IF_BASE_PORT=48890
export TASK_QUEUE_ENABLE=1
   ```

启动训练：
   ```bash
   cd examples/rl_calculator
   python train.py
   ```

训练结束或重启训练服务时关闭元戎：
   ```bash
   yr stop
   ```