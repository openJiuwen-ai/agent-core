# JiuwenClaw 在线强化学习闭环

基于真实用户交互的在线 RL 闭环系统。用户与 JiuwenClaw Agent 正常对话，JiuwenClaw 端的 **`RLOnlineRail`** 在 agent 链路上**逐轮**抓取轨迹（含 `token_ids` + `logprobs`），按批 POST 到 Gateway 的 `/v1/gateway/upload/batch`；Gateway 使用**延迟奖励**机制——当下一轮用户消息到来时，将上一轮的 `user(N) + assistant(N) + user(N+1)` 三元组发送给 Judge 模型做多维度评分。样本写入 **RedisTrajectoryStore** 后，由 OnlineTrainingScheduler 轮询拉取，累积达阈值自动触发 **Ray PPO 训练**（复用 agent-core 离线 PPO 栈），训练完成后热加载 LoRA 到 vLLM 立即生效。

## 架构总览

```
                          用户 (浏览器)
                               │
                               │ http://localhost:5173 (Web 前端，默认本机访问)
                               ▼
                   ┌──────────────────────┐
                   │  JiuwenClaw 全栈服务  │
                   │  app  (ws://127.0.0.1:19000)│
                   │  web  (http://127.0.0.1:5173)│
                   └──────────┬───────────┘
                              │ POST /v1/chat/completions
                              ▼
                   ┌──────────────────────┐
                   │   Gateway :18080     │─────────────────┐
                   │   透传 chat + LoRA   │                  │ 延迟触发
                   │   注入；接收 Rail    │                  │ (下一轮到来时)
                   │   batch 上传         │                  ▼
                   │                      │       ┌──────────────────┐
                   └──────┬───────────────┘       │ vLLM Judge:18003│
                          │                       │ TP=2 (默认复用)  │
                          ▼                       │ GPU 2,3          │
                   ┌──────────────┐               └────────┬─────────┘
                   │ vLLM :18002  │                        │ 四维度评分
                   │ Qwen3-4B    │                        ▼
                   │ TP=2, LoRA  │             ┌────────────────────┐
                   │ GPU 0,1     │             │ RedisTrajectoryStore│
                   └──────┬──────┘             │  redis://127.0.0.1 │
                          ↑                    └────────┬───────────┘
                          │  热加载 LoRA               │ poll (每 N 秒)
                          │                   ┌────────▼───────────┐
                          └───────────────────│ OnlineTraining     │
                                              │ Scheduler          │
                                              │ → Ray PPO + LoRA   │
                                              │   GPU 4,5          │
                                              └────────────────────┘
```

**数据流：** 用户对话 → Gateway 透传 chat 到 vLLM + 注入最新 LoRA → JiuwenClaw 端 `RLOnlineRail` 在 agent 执行链路上抓取 token_ids/logprobs，按 `TRAJECTORY_BATCH_SIZE` 批量 POST 到 Gateway `/v1/gateway/upload/batch` → 下一轮到来时触发 Judge 延迟打分 → 写入 **RedisTrajectoryStore** → OnlineTrainingScheduler 轮询拉取 → 累积达 `threshold` → `VerlDataProtoConverter` 转为 DataProto → Ray PPO `train_step`（actor + ref，无 critic）→ LoRA 导出 → vLLM 热加载 → 后续请求自动使用新 LoRA

### JiuwenClaw 端：RLOnlineRail（rail-v1）

在线轨迹由 DeepAgent 上的 **`RLOnlineRail`** 上送到 Gateway（`openjiuwen.agent_evolving.agent_rl.online.rail`），与离线 Rail 采集语义对齐。配置 **`TRAJECTORY_GATEWAY_URL`** / **`TRAJECTORY_GATEWAY_API_KEY`**（若 Gateway 启用了 key）以及 **`USE_RL_ONLINE_RAIL=1`**。

**示例（JiuwenClaw 与 agent-core 同机、已起 Gateway）：**

```bash
export USE_RL_ONLINE_RAIL=1
export TRAJECTORY_GATEWAY_URL=http://127.0.0.1:18080
# export TRAJECTORY_GATEWAY_API_KEY=...  # 若 gateway 配了 API key
```

### JiuwenSwarm Gateway 采集模式

真实 JiuwenSwarm 运行时由 `jiuwenswarm-agentserver` 和 `jiuwenswarm-gateway` 两个进程组成。Gateway 采集模式
不安装 `RLOnlineRail`，调用方在任务开始前通过 `/v1/gateway/collection/sessions` 创建 session，并在 JiuwenSwarm
默认模型配置中将 `api_base` 指向 agent-core Gateway，同时提供稳定的 `x-user-id` 和 `x-session-id`：

```yaml
models:
  defaults:
    - model_client_config:
        api_base: http://127.0.0.1:18080/v1
        api_key: EMPTY
        model_name: Qwen3-0.6B
        client_provider: OpenAI
        custom_headers:
          x-user-id: online-user
          x-session-id: collection-session-42
```

JiuwenSwarm 可通过 `jiuwenswarm-start app` 启动，也可以分别启动两个进程。任务成功后调用 finalize 和
task-reward；失败或取消时调用 abort。采集 session 生命周期由调用方管理，不由 JiuwenSwarm conversation
session 自动管理。

真实进程 E2E 默认不在 CI 中运行。准备专用 Redis 和本地模型后执行：

```bash
RUN_JIUWENSWARM_E2E=1 \
JIUWENSWARM_E2E_REDIS_URL=redis://127.0.0.1:16379/0 \
JIUWENSWARM_E2E_MODEL_PATH=/path/to/Qwen3-0.6B \
JIUWENSWARM_REPO=/path/to/jiuwenswarm \
pytest -s tests/system_tests/agent_evolving/agent_rl/online/test_gateway_mode_online_loop_e2e.py
```

Gateway collection 每次成功 commit 立即生成一个与 Rail 兼容的 per-call sample：sample identity 使用
`trajectory_id:step_index`，prompt/response token IDs、response mask、logprobs 保持真实 rollout 值。
样本进入现有 Redis pending-judge 管线；下一次用户消息作为 follow-up feedback 评分，成功 session finalize
按现有 session-done 语义 flush 剩余样本，abort 则丢弃该 session 的 pending 样本。评分后的样本继续由现有
scheduler 和 `VerlDataProtoConverter` 消费。

Gateway collection 同样支持调用方请求 `stream=true`。Gateway 仍由现有 Forwarder 一次性获取完整 upstream
response，先使用完整 token IDs / logprobs 完成 capture commit 或 drop，再把该 response 合成为 SSE。每个调用
最多产生一个 per-call sample，不按 SSE chunk 拆分。capture 失败只记录 drop，不阻止正常 SSE 返回；为 capture
强制请求的 token 字段不会泄漏给未请求这些字段的调用方，因此 SSE chunk 顺序、usage 和 finish semantics 保持
不变。Rail mode 的 streaming 路径不参与 gateway capture，行为保持不变。

Gateway collection 对外同时支持 OpenAI `POST /v1/chat/completions` 与 Anthropic `POST /v1/messages`。Anthropic
请求、响应、SSE 和错误 envelope 由 Gateway 内置协议模块转换为 OpenAI-compatible 请求，内部继续由
Forwarder 调用 vLLM，以取得真实 token IDs 和 logprobs。

### Web 侧 `x-user-id` 要求

在线训练按 `user_id` 聚合样本，因此 Gateway 会强制要求 `/v1/chat/completions` 和 `/v1/messages` 请求携带稳定的
`x-user-id` header。

- 走 `run_online_rl.py` 启动时，launcher 会自动向 JiuwenClaw 工作区写入 `WEB_USER_ID="local-web-user"`，并由 JiuwenClaw 出站请求自动补成 `x-user-id`。
- 如果你本地要区分不同操作者，启动前可覆盖：

```bash
export WEB_USER_ID=my_name
python run_online_rl.py
```

- 如果是手工启动 JiuwenClaw，而不是走 launcher，也需要自己提供稳定 `WEB_USER_ID`，否则 Web 对话会报：

```text
missing x-user-id header; online training requires a stable user id
```

## 训练架构

在线 PPO 训练**完全复用 agent-core 离线 PPO 栈**（`VerlTrainingExecutor` + verl），通过 `OnlineTaskRunner` Ray actor 驱动：

- **Actor + Ref Workers**：加载基座模型（FSDP + LoRA），计算 old_log_probs、ref_log_probs、advantages
- **无 Critic**：奖励直接来自 Judge 评分，无需价值网络
- **无 Rollout Engine**：rollout 数据由 Gateway 在线采集，不启动 vLLM rollout
- **优势估计**：`reinforce++`，带 KL penalty（锚定 ref policy）
- **FSDP Offload**：actor 和 ref 均启用 `param_offload` + `optimizer_offload`，节省 GPU 显存

```
Gateway 样本 (token_ids, logprobs, judge_score)
     │
     ▼  VerlDataProtoConverter
DataProto (input_ids, attention_mask, position_ids, responses, response_mask, rewards)
     │
     ▼  OnlineTaskRunner.train_on_batch()
VerlTrainingExecutor.train_step():
  1. compute_old_log_prob    (actor workers)
  2. compute_ref_log_prob    (ref workers)
  3. compute_advantages      (reinforce++, KL penalty)
  4. update_actor            (PPO clip + gradient step)
     │
     ▼  export_lora()
PEFT LoRA adapter → lora_repo → vLLM 热加载
```

## 核心机制

### 延迟奖励（Delayed Reward）

传统方式在 assistant 回复后立即打分，缺乏用户反馈信号。本系统使用延迟奖励：

```
Turn N:   user(N) → assistant(N)     →  暂存为 pending_judge
Turn N+1: user(N+1) 到来时            →  取出 pending，组装评分输入：
                                          user(N) + assistant(N) + user(N+1)
                                          ↓
                                        Judge 打分（user(N+1) 作为隐式反馈）
                                          ↓
                                        记录带分数的样本
```

这样 Judge 能看到用户的后续反应（如"回答得很好"或"答案错了"），给出更准确的奖励信号。

### 逐轮数据粒度

每一轮交互作为独立样本记录，包含：

| 字段 | 说明 |
|------|------|
| `prompt_ids` | 该轮 prompt 的 token ID 序列 |
| `response_ids` | 模型回复的 token ID 序列 |
| `response_mask` | response token 的训练 mask；canonical trajectory 中含 prompt 前缀 `0` |
| `response_logprobs` | 每个 response token 的 log probability |
| `routed_experts` | 可选 MoE 路由，shape 为 `[prompt + response tokens, layers, topk]` |
| `judge.score` | 归一化奖励 ∈ [0, 1] |
| `judge.details` | 四维度原始分（task_completion, response_quality, tool_usage, coherence） |

`logprobs` 数据在 PPO 训练中作为 behavior policy 的参考（实际训练时由 actor workers 重新计算 `old_log_probs`）。

Capture/Rail 上送的 response-only mask 长度必须等于 `response_ids`；缺失时每个 response token 默认取 `1`。
Gateway canonical trajectory 会在前面补齐与 prompt 等长的 `0`，因此最终 mask 长度等于 `input_ids`；长度不匹配会
拒绝转换。response 被截断时，trainer 会同步截断 mask。

`routed_experts` 存在时必须与 canonical `input_ids` 严格逐 token 对齐，并保持非空、规则的
`[tokens, layers, topk]` 三维结构。`VerlDataProtoConverter` 对 prompt 保留尾部、对 response 保留头部时，
会同步裁剪对应路由；batch tensor shape 为 `[batch, padded_input_tokens, layers, topk]`，padding 值为 `0`。
只有 batch 内每个样本都提供路由时才生成该 tensor；不同样本的 `layers/topk` shape 不一致会拒绝转换。

### Gateway 行为

`rail` 启动模式会关闭 Gateway 内的 trajectory collector，即 chat 路径上**不**直接抓轨迹，而是：

- 接收 `/v1/chat/completions` 或 `/v1/messages`，统一调用上游 OpenAI-compatible vLLM，并按 `x-user-id` 注入最新 LoRA
- 经 `/v1/gateway/upload/batch` 接收 JiuwenClaw 端 `RLOnlineRail` 上送的逐轮样本
- 在样本入队前调用 Judge 做延迟打分（取上一轮 pending + 本轮 user 触发）
- 写入 RedisTrajectoryStore，供 OnlineTrainingScheduler 消费

旧 `mode` / `rollout_batch_size` 等 Gateway 配置已删除。

## GPU 分配（默认 6 卡，RTX 3090/4090 24GB；复用 Judge 时 4 卡即可）

| GPU | 用途 | 备注 |
|-----|------|------|
| 0, 1 | vLLM 推理 (Qwen3-4B, TP=2, port 18002) | 含 LoRA 热加载；默认 Judge 也复用此服务 |
| 2, 3 | vLLM Judge (TP=2, port 18003) | 仅当 Judge 模型与推理模型不同时单独占用 |
| 4, 5 | Ray PPO 训练 | Actor + Ref (FSDP offload) |

> GPU 不够？保持 `judge.reuse_inference_if_same_model: true` 让 Judge 复用推理 vLLM；或用更小模型 / 外部 API（`--judge-url`）。

## 前置准备

### 1. 安装依赖

```bash
# agent-core (openjiuwen SDK)
cd agent-core
make install

# jiuwenclaw 应用（可选，提供 Web 前端）
cd jiuwenclaw
pip install -e ".[dev]"

# vLLM、VERL、Ray。agent-core 在线 RL 当前固定使用 VERL 0.8.0；
# 由 VERL 的 vllm extra 选择兼容的 vLLM，不要使用 uni-agent 源码内的 editable VERL。
pip install "verl[vllm]==0.8.0" ray
```

### 2. 确认模型路径

脚本默认使用以下路径，可通过参数覆盖：

```
/path/to/your/model    # 推理模型（默认复用为 Judge，请按本机实际路径修改）
```

当 `--judge-model-name` 与 `--model-name` 不同时，会启动独立 Judge vLLM；否则 Judge 复用推理 vLLM。

### 3. 编译 Web 前端（可选）

```bash
cd jiuwenclaw/jiuwenclaw/web
npm install
npm run build
```

编译产出 `dist/` 目录后，启动脚本会默认在 `http://127.0.0.1:5173` 提供 Web UI。如需从远程机器访问，建议通过 SSH 隧道转发到本地浏览器。

### 4. 确认 GPU 空闲

```bash
nvidia-smi --query-gpu=index,memory.used --format=csv,noheader
# 确保目标 GPU 的 memory.used 接近 0 MiB
```

启动器会检查端口并在冲突时停止启动，不会清理其他用户的进程。

## 启动

```bash
cd agent-core
python examples/jiuwenrl_online/run_online_rl.py \
  --config ./my_online_rl.yaml
```

推荐把以下内容放到 YAML 中维护，而不是依赖 CLI：

- 模型路径与模型名（`inference.model_path` / `judge.model_path`）
- vLLM / Judge 的 GPU、端口、TP
- vLLM `extra_args`，例如 `--max-model-len`、`--gpu-memory-utilization`
- Gateway / 训练阈值 / JiuwenClaw 端口
- `trajectory.batch_size` / `trajectory.mode`

`run_online_rl.py` 会依次拉起 5 个服务：

1. **vLLM 推理** — `inference.model_name`（默认 Qwen3-4B-Thinking-2507），TP=2，GPU 0,1，port 18002
2. **vLLM Judge** — 当 `judge.reuse_inference_if_same_model=true` 且 model_name 与推理一致时复用推理 vLLM；否则独立启动 TP=2，GPU 2,3，port 18003
3. **Gateway** — port 18080，透传 chat + LoRA 注入 + 接收 Rail batch 上传 + 延迟 Judge 打分 + 写 Redis
4. **OnlineTrainingScheduler** — 后台线程，轮询 **RedisTrajectoryStore** 触发 Ray PPO 训练
5. **JiuwenClaw** — app (ws://127.0.0.1:19000) + web (http://127.0.0.1:5173，默认仅本机访问)

启动完成后会打印类似以下信息（来自 `print_launch_summary`，字段以代码为准）：

```
============================================================
  JiuwenClaw online RL loop started (v2: per-turn + Judge)

  Config file:      .../online_config.py
  Web frontend:    http://127.0.0.1:5173
  JiuwenClaw WS:   ws://127.0.0.1:19000/ws
  vLLM Inference:  http://127.0.0.1:18002
  vLLM Judge:      http://127.0.0.1:18002 (reuse inference)
  Gateway proxy:   http://127.0.0.1:18080
  Redis store:     redis://127.0.0.1:6379/0
  Trajectory mode: feedback_level
  Trajectory log:  records/ (JSONL, per-turn)
  LoRA repo:       .../lora_repo
  Train threshold: 4 samples
  Collect batch:   4
  Scan interval:   30s
  Training mode:   PPO (Ray)
  Train GPUs:      [4,5]

  Press Ctrl+C to stop all services.
============================================================
```

### 常用启动模式

```bash
# 演示标记（兼容遗留脚本，不改变默认参数；要降低阈值请直接用 --threshold/--scan-interval）
python run_online_rl.py --demo

# 使用独立 YAML 管理整套启动参数
python run_online_rl.py --config ./my_online_rl.yaml

# 推理 vLLM 已在运行（跳过启动）
python run_online_rl.py --inference-url http://127.0.0.1:18002

# Judge vLLM 也已在运行（跳过两个 vLLM 的启动，秒级启动）
python run_online_rl.py \
  --inference-url http://127.0.0.1:18002 \
  --judge-url http://127.0.0.1:18003

# 自定义 GPU 分配
python run_online_rl.py \
  --vllm-gpu 0,1 \
  --judge-gpu 2,3 \
  --train-gpu 4,5

# 自定义 PPO 训练配置
python run_online_rl.py --ppo-config /path/to/my_ppo_config.yaml

# 跳过 JiuwenClaw 启动（仅 Gateway + 训练）
python run_online_rl.py --skip-jiuwen
```

### 远程访问

```bash
# SSH 隧道转发（在本地机器执行）
ssh -L 5173:127.0.0.1:5173 -L 19000:127.0.0.1:19000 user@HOST
```

然后在本地浏览器打开 `http://localhost:5173`。

### 停止服务

按 `Ctrl+C` 会优雅关闭所有子进程和 Ray 集群。如果 launcher 异常退出后端口/GPU 还有残留，可手动清理：

```bash
pkill -f vllm.entrypoints.openai.api_server
pkill -f openjiuwen.agent_evolving.agent_rl.online.gateway
pkill -f jiuwenclaw.app
ray stop --force
```

## 全部参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--config` | — | 可选启动配置 YAML；若提供，则在 launcher schema 默认值之上叠加 |
| **推理 vLLM** | | |
| `--model-path` | `/path/to/your/model` | 推理基座模型路径 |
| `--model-name` | `Qwen3-4B-Thinking-2507` | vLLM 注册的模型名 |
| `--vllm-gpu` | `0,1` | 推理 GPU（逗号分隔） |
| `--vllm-tp` | `2` | 推理 Tensor Parallel 大小 |
| `--vllm-port` | — | 推理服务端口；直接运行 launcher 时必填 |
| `--inference-url` | — | 设置后跳过推理 vLLM 启动 |
| **Judge vLLM** | | |
| `--judge-model-path` | `/path/to/your/model` | Judge 模型路径 |
| `--judge-model-name` | `Qwen3-4B-Thinking-2507` | Judge 模型名 |
| `--judge-gpu` | `2,3` | Judge GPU（逗号分隔） |
| `--judge-tp` | `2` | Judge Tensor Parallel 大小 |
| `--judge-port` | — | Judge 服务端口；直接运行 launcher 时必填 |
| `--judge-url` | — | 设置后跳过 Judge vLLM 启动 |
| **Gateway** | | |
| `--gateway-port` | — | Gateway 代理端口；直接运行 launcher 时必填 |
| `--redis-url` | — | RedisTrajectoryStore 地址；直接运行 launcher 时必填 |
| **PPO 训练 & 调度** | | |
| `--threshold` | `4` | RedisTrajectoryStore 触发训练的样本数阈值 |
| `--scan-interval` | `30` | OnlineTrainingScheduler 扫描间隔（秒） |
| `--train-gpu` | `4,5` | PPO 训练 GPU（逗号分隔） |
| `--ppo-config` | — | 自定义 PPO 配置 YAML（默认使用内置 `ppo_online_trainer.yaml`） |
| `--lora-repo` | `./lora_repo` | LoRA adapter 版本化存储目录 |
| `--trajectory-batch-size` | `4` | 写入 workspace/JiuwenClaw 的轨迹相关 env（如 `TRAJECTORY_BATCH_SIZE`） |
| **其他** | | |
| `--demo` | — | 演示模式兼容标记（不改变运行逻辑） |
| `--skip-jiuwen` | — | 跳过 JiuwenClaw app/web 启动 |
| `--jiuwen-agent-server-port` | — | JiuwenClaw AgentServer 端口；启用 JiuwenClaw 时必填 |
| `--jiuwen-ws-port` | — | JiuwenClaw WebSocket 端口；启用 JiuwenClaw 时必填 |
| `--jiuwen-web-host` | `127.0.0.1` | JiuwenClaw Web 前端监听地址 |
| `--jiuwen-web-port` | — | JiuwenClaw Web 前端端口；启用 JiuwenClaw 时必填 |

说明：`run_online_rl.py` 本身不再为端口和 `redis_url` 提供内置默认值；直接运行 launcher 时，需要通过 `--config` 或 CLI 显式提供。示例 `ctl_online_rl.sh` / `ctl_online_rl_local.sh` 会为本地开发补齐一组常用参数：`--vllm-port 18002`、`--judge-port 18003`、`--gateway-port 18080`、`--redis-url redis://127.0.0.1:6379/0`、`--jiuwen-agent-server-port 18092`、`--jiuwen-ws-port 19000`、`--jiuwen-web-port 5173`。

## 在线 PPO 闭环原理

```
Turn N: JiuwenClaw user(N) → Gateway → vLLM → assistant(N) 回传
                   ↓
         RLOnlineRail 在 agent 链路上抓取 prompt_ids / response_ids / logprobs
         按 TRAJECTORY_BATCH_SIZE 批量 POST /v1/gateway/upload/batch
                   ↓
         Gateway 暂存为 pending_judge
                   ↓
Turn N+1: user(N+1) 到来
                   ↓
         Gateway 取出 pending → 发送 Judge 打分
         user(N) + assistant(N) + user(N+1) → Judge 四维度评分
                   ↓
         写入 RedisTrajectoryStore (redis://127.0.0.1:6379/0)
                   ↓
OnlineTrainingScheduler (每 N 秒轮询 Redis)
  │  拉取样本，累积 >= threshold?
  ▼
VerlDataProtoConverter: 样本 → DataProto
  │  input_ids, attention_mask, position_ids,
  │  responses, response_mask, rewards, data_id_list
  ▼
OnlineTaskRunner (Ray actor, GPU 4,5):
  │  1. compute_old_log_prob   (actor FSDP workers)
  │  2. compute_ref_log_prob   (ref FSDP workers)
  │  3. compute_advantages     (reinforce++, KL penalty)
  │  4. update_actor           (PPO clip + gradient step)
  │  5. save_checkpoint        (FSDP → PEFT LoRA adapter)
  │  6. 发布到 lora_repo/online/<version>/
  │  7. 通知 vLLM 热加载
  ▼
后续请求 → Gateway 按 x-user-id 注入对应 LoRA → vLLM 使用新 LoRA 推理
```

## 运行时产物

| 路径 | 说明 |
|------|------|
| `logs/inference_vllm.log` | 推理 vLLM 日志 |
| `logs/judge_vllm.log` | Judge vLLM 日志 |
| `logs/gateway.log` | Gateway 日志 |
| `logs/online_rl_*.log` | 完整闭环日志（restart 脚本产生） |
| `records/trajectories.jsonl` | 逐轮轨迹记录（含 token_ids、logprobs、Judge 评分） |
| `lora_repo/online/<version>/` | PPO 训练产出的 LoRA adapter（PEFT 格式） |

### 查看轨迹

```bash
python3 -c "
import json
with open('records/trajectories.jsonl') as f:
    recs = [json.loads(l) for l in f if l.strip()]
for r in recs[-5:]:
    j = r.get('judge', {})
    t = r.get('trajectory', {})
    print(f\"session={r['session_id']} turn={r['turn_num']} \"
          f\"score={j.get('score','?')} overall={j.get('overall_raw','?')} \"
          f\"prompt_ids={len(t.get('prompt_ids',[]))} response_ids={len(t.get('response_ids',[]))}\")
    print(f\"  reason: {j.get('details',{}).get('reason','')[:80]}\")
"
```

### 查看 Gateway 实时状态

```bash
curl -s http://127.0.0.1:18080/v1/gateway/stats | python3 -m json.tool
```

| 字段 | 说明 |
|------|------|
| `total_requests` | 累计处理的请求数 |
| `total_samples` | 已完成 Judge 打分的样本数 |
| `trajectory_store_total` | 已写入轨迹存储的样本总数 |
| `trajectory_store_pending` | 当前等待训练消费的样本数 |

## Gateway API

Gateway 兼容 OpenAI API，同时提供管理端点：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/chat/completions` | POST | 透传上游 vLLM + LoRA 注入（支持 stream，需 `x-user-id`） |
| `/v1/gateway/upload/batch` | POST | 接收 JiuwenClaw `RLOnlineRail` 上送的逐轮样本批次 |
| `/v1/gateway/stats` | GET | Gateway 实时统计（请求数、样本数、轨迹存储 pending） |
| `/health` | GET | 健康检查；返回当前 `instance_id` |
| `/{path}` | * | 其余路径透传到上游（如 `/v1/models`） |

`x-user-id` 用于按用户聚合轨迹与注入对应 LoRA；`x-session-id`/`x-request-id` 仅用作 trace。Gateway 自身不在 chat 路径上记录会话状态，逐轮状态由上送方（Rail）维护。

启用 gateway collection session 模式时，session 身份和生命周期保存在 Redis，不存在需要恢复的进程内
trajectory 状态。Gateway 重启后，未结束 session 可继续 capture、finalize 或 abort；已提交样本和 pending
reward 同样保留。

## Judge 评分机制

Gateway 使用**延迟触发**的 LLM-as-Judge 评分：

1. Turn N 完成后，Gateway 将 `user(N) + assistant(N)` 暂存为 pending
2. Turn N+1 的用户消息到来时，取出 pending 样本，组装完整评分输入
3. 发送给 Judge 模型，使用以下评分维度：

| 维度 | 分数范围 | 说明 |
|------|----------|------|
| `task_completion` | 0-10 | Agent 是否完成了用户意图 |
| `response_quality` | 0-10 | 回答是否准确、有帮助、简洁 |
| `tool_usage` | 0-10 | 工具调用是否必要且正确 |
| `coherence` | 0-10 | 多轮对话是否自然流畅 |
| `overall` | 0-10 | 综合评分 |

- **归一化**：`score = (overall - 5) / 5`，映射到 [-1, 1]
- **容错**：Judge 调用失败时 score 默认 0.0（中性），不阻塞流程
- **投票**：支持多次调用取中位数（`num_votes`），提高评分稳定性

## PPO 训练配置

内置配置文件 `openjiuwen/agent_evolving/agent_rl/online/yaml/ppo_online_trainer.yaml`，核心参数：

| 参数 | 值 | 说明 |
|------|-----|------|
| `adv_estimator` | `reinforce_plus_plus` | 优势估计方法 |
| `use_kl_in_reward` | `true` | 在奖励中加入 KL 惩罚 |
| `kl_coef` | `0.001` | KL 惩罚系数 |
| `clip_ratio` | `0.2` | PPO clip 范围 |
| `ppo_epochs` | `1` | PPO 更新轮数 |
| `actor.fsdp_config.param_offload` | `true` | Actor FSDP 参数卸载到 CPU |
| `actor.fsdp_config.optimizer_offload` | `true` | Actor 优化器卸载到 CPU |
| `ref.fsdp_config.param_offload` | `true` | Ref policy 参数卸载到 CPU |
| `lora.r` | `16` | LoRA rank |
| `lora.lora_alpha` | `32` | LoRA alpha |

可通过 `--ppo-config` 参数覆盖为自定义配置。

## 组件依赖

| 组件 | 说明 |
|------|------|
| `agent_rl/online/gateway/` | Gateway 核心：上游透传、LoRA 注入、Rail batch 接收、延迟 Judge、写训练队列 |
| `agent_rl/online/rail/` | JiuwenClaw 端 `RLOnlineRail`：抓取逐轮 token_ids/logprobs，按批 POST 到 Gateway |
| `agent_rl/online/launcher/` | 启动配置 schema/loader/cli 与服务编排 runner（split by b8e6ac4） |
| `agent_rl/online/scheduler/` | OnlineTrainingScheduler（轮询 RedisTrajectoryStore，触发 PPO） |
| `agent_rl/online/inference/` | vLLM 热加载通知 |
| `agent_rl/online/judge/` | LLM-as-Judge 评分服务端 + 客户端 |
| `agent_rl/online/yaml/ppo_online_trainer.yaml` | PPO 训练配置 |
| `agent_rl/config/online_config.py` | launcher 配置模型、Python 默认值和 PPO overlay |
| `agent_rl/optimizer/task_runner.py` | OnlineTaskRunner（Ray actor，PPO 训练 + LoRA 导出） |
| `agent_rl/rl_trainer/verl_executor.py` | VerlTrainingExecutor（PPO 训练核心，离线/在线共用） |
| `agent_rl/rl_trainer/verl_converter.py` | 样本 → DataProto 转换器 |
| `agent_rl/storage/` | LoRA 仓库管理、轨迹样本状态机、RedisTrajectoryStore |
| `jiuwenclaw/` | AgentServer + Web 前端（`.env` 由 launcher 自动写入指向 Gateway） |
| `vLLM` | 推理服务（`--enable-lora`）+ Judge 服务 |
| `verl` | PPO 训练后端 |
| `Ray` | 分布式训练编排 |

## 发布前 collection-mode 回归

无网络闭环回归覆盖 gateway session 创建、OpenAI/Anthropic 调用、delayed/terminal reward、显式 finalize、
trainer conversion 和 Rail 兼容路径。失败分类和重启恢复由相应单元测试覆盖：

```bash
# agent-core
pytest -q \
  tests/system_tests/agent_evolving/agent_rl/online/test_gateway_mode_online_loop_e2e.py \
  tests/unit_tests/agent_evolving/agent_rl/online/gateway/test_gateway_collector.py \
  tests/unit_tests/agent_evolving/agent_rl/online/gateway/test_anthropic_protocol.py \
  -m "not level1"
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `run_online_rl.py` | 在线 RL 闭环启动脚本（一键拉起全部服务） |
| `ARCHITECTURE.md` | 详细架构与数据流说明 |

> 早期版本附带的 `ctl_online_rl.sh` / `ctl_online_rl_local.sh` / `check_env.sh` / `test_online_ppo.py` 已不再随仓库分发（commit b8e6ac4 起 ctl 脚本本地维护）。如有需要可在本地自行维护。

## 常见问题

**Q: 训练时报 GPU OOM？**
PPO 训练默认启用 FSDP `param_offload` + `optimizer_offload`，将参数和优化器状态卸载到 CPU。如果仍然 OOM，可以减少 `ppo_mini_batch_size` 或增加训练 GPU 数量。

**Q: Judge vLLM 启动时报 CUDA OOM？**
降低 `--judge-tp` 或使用更小的 Judge 模型。内置 Python 配置已为 Judge 设置 `--max-model-len 8192 --max-num-seqs 16 --gpu-memory-utilization 0.85` 来适配 24GB GPU（参见 `online_config.py` 的 `JudgeConfig.extra_args`）。

**Q: Gateway 启动超时（tokenizer 下载失败）？**
Gateway 需要加载 tokenizer。确保 `--model-path` 指向本地模型目录。

**Q: 样本采集了但 Judge 没打分？**
Judge 使用延迟奖励机制，需要等待**下一轮**用户消息才会触发打分。可结合轨迹文件与 Gateway stats 一起排查。

**Q: 样本打分了但没触发训练？**
检查以下几点：
1. Gateway 是否正常写入 RedisTrajectoryStore
2. Redis 是否已启动且可连接（`redis-cli ping` 应返回 `PONG`）
3. OnlineTrainingScheduler 累积的样本数是否达到 `threshold`（默认 4）
4. 查看 OnlineTrainingScheduler 日志确认轮询状态

**Q: PPO 训练日志显示 "Data alignment: N -> 0 samples"？**
这是 verl 的 PPO data alignment 机制，当同一 group 只有 1 个样本时会被过滤。增加有效样本量，或在 PPO 配置中设置 `algorithm.filter_groups: false`。

**Q: 如何只启动 Gateway + 训练而不启动 vLLM？**
```bash
python run_online_rl.py \
  --inference-url http://127.0.0.1:18002 \
  --judge-url http://127.0.0.1:18003
```

**Q: 远程机器如何访问 Web 前端？**
默认只监听 `127.0.0.1`。请使用 SSH 隧道：`ssh -L 5173:127.0.0.1:5173 -L 19000:127.0.0.1:19000 user@HOST`，然后浏览器打开 `http://localhost:5173`。

**Q: 如何查看历史轨迹的详细 Judge 评分？**
```bash
python3 -c "
import json
with open('records/trajectories.jsonl') as f:
    for line in f:
        r = json.loads(line)
        j = r.get('judge', {})
        d = j.get('details', {})
        print(f\"turn={r['turn_num']} score={j.get('score',0):.2f} \"
              f\"tc={d.get('task_completion')} rq={d.get('response_quality')} \"
              f\"tu={d.get('tool_usage')} co={d.get('coherence')}\")
        print(f\"  reason: {d.get('reason','')[:100]}\")
"
```
