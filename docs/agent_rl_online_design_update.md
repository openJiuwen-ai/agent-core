# Online RL 设计文档更新对齐

本文基于远端旧文档 `agent-os/rl-design.md`，按当前 `agent-core`
分支实现更新环境变量与 API 接口，并列出旧文档和当前代码的缺口。

参考旧文档：
https://gitee.com/liulili-huawei/agent-rl-learn-history/blob/master/agent-os/rl-design.md

当前代码参考：

- `openjiuwen/harness/factory.py`
- `openjiuwen/agent_evolving/agent_rl/online/core/rail_factory.py`
- `openjiuwen/agent_evolving/agent_rl/online/gateway/app/server.py`
- `openjiuwen/agent_evolving/agent_rl/online/gateway/app/bootstrap.py`
- `openjiuwen/agent_evolving/agent_rl/online/scheduler/online_training_scheduler.py`
- `openjiuwen/agent_evolving/agent_rl/config/online_config.py`

## 1. 旧文档需要替换的关键项

| 旧文档写法 | 当前实现 | 说明 |
| --- | --- | --- |
| `ENABLE_ONLINE_RL=true` | `USE_RL_ONLINE_RAIL=1` | DeepAgent 创建时通过环境变量自动注入 `RLOnlineRail`。 |
| `TRAJECTORY_UPLOAD_URL` | `TRAJECTORY_GATEWAY_URL` | Rail 上传 Gateway 基地址，例如 `http://127.0.0.1:18080`。 |
| `POST /api/v1/train/start` | `POST /v1/training/tasks` | 训练统一抽象为 task；当前同时只允许一个 active task。 |
| `GET /api/v1/train/status` | `GET /v1/training/tasks` 或 `GET /v1/training/tasks/{task_id}` | 通过 task store 查询训练任务状态。 |
| `POST /api/v1/train/stop` | `PATCH /v1/training/tasks/{task_id}`，body: `{"status":"stopping"}` | 停止训练通过修改 task 状态触发。 |
| `GET /api/v1/lora/list` | `GET /v1/rl/lora` | 支持按 `model_id` 查询，也支持列出所有用户 LoRA。 |
| `GET /api/v1/lora/download?version=latest` | `GET /v1/rl/lora/{lora_id}:download` | 当前返回本地路径元数据，不是文件流下载。 |
| 独立 `Trajectory Manager` 服务 | Gateway 内部 `GatewayTrajectoryRuntime` + store | 当前没有单独 TM 进程；Gateway 接收、管理、统计轨迹，Scheduler 消费同一 store。 |
| 轨迹存储目录文件 | Redis 或本地 JSON store | 通过 `TRAJECTORY_STORE_BACKEND=redis/local/auto` 选择。 |

## 2. 当前环境变量

### 2.1 Agent/JiuwenSwarm 侧

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `USE_RL_ONLINE_RAIL` | 空 | truthy 时在 DeepAgent rail 链路自动注入 `RLOnlineRail`。支持 `1/true/yes/on`。 |
| `TRAJECTORY_GATEWAY_URL` | `http://127.0.0.1:18080` | `RLOnlineRail` 上传轨迹与查询 effective LoRA 的 Gateway 基地址。 |
| `TRAJECTORY_GATEWAY_API_KEY` | 空 | Gateway 启用 `GATEWAY_API_KEY` 时，Rail 请求使用的 Bearer token。 |
| `TRAJECTORY_WAL_DIR` | `records/rail_v1_wal` | Rail 异步上传失败时的本地 WAL 目录。 |
| `RL_ONLINE_TENANT_ID` | 空 | 轨迹归属用户/租户；用于按用户训练和 LoRA 路由。launcher 未配置时会回退到 `WEB_USER_ID`。 |
| `LORA_DEFAULT_POLICY` | `disabled` | `latest_by_user` 时，直连 vLLM 场景由 Rail 本地查询 effective LoRA 并临时切换本轮 model；Gateway 代理场景由 Gateway 注入该用户最新 LoRA。 |
| `WEB_USER_ID` | `local-web-user` | launcher 启动 JiuwenClaw 时写入稳定用户 ID，并补充 `x-user-id` header。 |
| `TRAJECTORY_BATCH_SIZE` | launcher 配置，默认 `4` | JiuwenClaw 侧批量上传 Rail 轨迹的批大小。 |
| `TRAJECTORY_TOKENIZER_PATH` | 模型路径 | 轨迹采集侧 tokenizer 路径。 |
| `TRAJECTORY_MODE` | `feedback_level` | 轨迹采集模式标记。 |
| `ENABLE_TRAJECTORY_COLLECTION` | `false` | launcher 写入工作区，避免 JiuwenClaw 旧采集链路和 `RLOnlineRail` 重复采集。 |

### 2.2 Gateway 侧

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `GATEWAY_HOST` | `127.0.0.1` | Gateway 监听 host。 |
| `GATEWAY_PORT` | 必填 | Gateway 监听端口。 |
| `INFERENCE_URL` / `LLM_URL` | `http://127.0.0.1:18000` | 上游 vLLM/OpenAI 兼容服务地址；`INFERENCE_URL` 优先。 |
| `JUDGE_URL` | 等于 inference url | Judge 服务地址；为空/复用时指向推理服务。 |
| `MODEL_ID` / `SERVED_MODEL_NAME` | 空 | Gateway 当前服务模型名；`MODEL_ID` 优先。 |
| `MODEL_PATH` | launcher 传入 | 基座模型路径，主要用于服务启动上下文。 |
| `JUDGE_MODEL` | 空 | Judge 使用的模型名；为空时用 `MODEL_ID`。 |
| `REQUEST_TIMEOUT` | `120` | Gateway 请求上游 HTTP 超时。 |
| `LLM_API_KEY` | 空 | Gateway 转发到上游 LLM 时注入的 Bearer token。 |
| `JUDGE_API_KEY` | `EMPTY` | Gateway 调 Judge 时使用的 token。 |
| `GATEWAY_API_KEY` | 空 | Gateway 自身 API 鉴权 token；为空则不鉴权。 |
| `RECORD_DIR` | `records` | samples JSONL 和本地 store 默认根目录。 |
| `LOG_LEVEL` | `INFO` | Gateway 日志级别。 |
| `DUMP_TOKEN_IDS` | false | 是否把 token ids 写入调试记录。 |
| `LORA_REPO_ROOT` | 空 | LoRA repository 根目录；为空时 LoRA API 不可用。 |
| `LORA_DEFAULT_POLICY` | `disabled` | `latest_by_user` 时 Gateway 代理路径按 `x-user-id` 注入最新 LoRA；直连 vLLM 路径由 Rail 本地处理。 |
| `REDIS_URL` | 空 | Redis store 地址。 |
| `TRAJECTORY_STORE_BACKEND` | `auto` | `auto` 有 Redis URL 时用 Redis，否则用 local；也可显式 `redis`/`local`。 |
| `LOCAL_TRAJECTORY_STORE_DIR` | 空 | local store 目录；为空时使用 `${RECORD_DIR}/local_store`。 |
| `UPSTREAM_MAX_RETRIES` | `2` | Gateway 调上游重试次数。 |
| `UPSTREAM_RETRY_BACKOFF_SEC` | `0.2` | 上游重试初始退避秒数。 |
| `UPSTREAM_RETRY_MAX_BACKOFF_SEC` | `2.0` | 上游重试最大退避秒数。 |
| `DISABLE_GATEWAY_TRAJECTORY_COLLECTION` | false | 当前 chat 路径不直接采集轨迹时设置为 true，轨迹由 Rail 上传。 |

### 2.3 Scheduler/PPO 训练侧

| 配置/环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `training.threshold` / `--threshold` | `4` | 用户 pending 样本达到阈值后才可被任务消费。 |
| `training.scan_interval` / `--scan-interval` | `30` | Scheduler 轮询 task/store 间隔。 |
| `training.gpu_ids` / `--train-gpu` | `4,5` | PPO 训练使用 GPU 列表。 |
| `training.ppo_config` / `--ppo-config` | 空 | 自定义 verl Hydra PPO 配置。 |
| `training.lora_repo` / `--lora-repo` | 运行目录下 `lora_repo` | LoRA 输出仓库。 |
| `training.drain_pending_on_train` / `--drain-pending-on-train` | false | 训练触发后 claim 当前所有 pending 样本，而不是固定 `threshold` 条。 |
| `training.max_samples_per_run` / `--max-samples-per-run` | `0` | 单次训练最多 claim 的样本数；0 表示不限制。 |
| `training.ppo_samples_per_step` / `--ppo-samples-per-step` | `0` | 一个训练 run 内每个 PPO step 使用多少样本；0 表示一次性训练全部样本。 |
| `training.allow_partial_last_step` / `--allow-partial-last-step` | true | `ppo_samples_per_step` 生效时，是否允许最后不足 step size 的尾批参与训练。 |
| `ONLINE_RL_INIT_LORA_ADAPTER_PATH` | 空 | PPO 初始化时加载已有 LoRA adapter。 |
| `ONLINE_RL_DEVICE_BACKEND` | 空 | `ascend/npu` 时将 verl trainer device 设置为 `npu`。 |
| `ONLINE_RL_VISIBLE_DEVICES_ENV` | `CUDA_VISIBLE_DEVICES` | PPO executor 设置可见设备用的环境变量；NPU 可设为 `ASCEND_RT_VISIBLE_DEVICES`。 |
| `ONLINE_RL_DETERMINISTIC_SEED` | 空 | 设置 data/rollout/trainer seed。 |
| `ONLINE_RL_FSDP_MODEL_DTYPE` | 空 | 覆盖 actor/ref FSDP dtype。 |
| `ONLINE_RL_MAX_PROMPT_LENGTH` | 空 | 覆盖 verl `data.max_prompt_length`。 |
| `ONLINE_RL_MAX_RESPONSE_LENGTH` | 空 | 覆盖 verl `data.max_response_length`。 |
| `ONLINE_RL_TRAIN_BATCH_SIZE` | 空 | 覆盖 verl `data.train_batch_size`。 |
| `ONLINE_RL_PPO_MINI_BATCH_SIZE` | 空 | 覆盖 actor PPO mini batch。 |
| `ONLINE_RL_PPO_MICRO_BATCH_SIZE_PER_GPU` | 空 | 覆盖 actor micro batch/GPU。 |
| `ONLINE_RL_SEQUENCE_PARALLEL_SIZE` | 空 | 同时覆盖 actor/ref ulysses SP、rollout TP。 |
| `ONLINE_RL_ACTOR_PPO_MAX_TOKEN_LEN_PER_GPU` | 空 | 覆盖 actor `ppo_max_token_len_per_gpu`。 |
| `ONLINE_RL_LOG_PROB_MICRO_BATCH_SIZE_PER_GPU` | 空 | 覆盖 ref/rollout logprob micro batch/GPU。 |
| `ONLINE_RL_REF_LOG_PROB_MAX_TOKEN_LEN_PER_GPU` | 空 | 覆盖 ref logprob token 上限。 |
| `ONLINE_RL_ROLLOUT_LOG_PROB_MAX_TOKEN_LEN_PER_GPU` | 空 | 覆盖 rollout logprob token 上限。 |
| `ONLINE_RL_ROLLOUT_MAX_MODEL_LEN` | 空 | 覆盖 rollout `max_model_len`。 |
| `ONLINE_RL_ROLLOUT_MAX_NUM_BATCHED_TOKENS` | 空 | 覆盖 rollout `max_num_batched_tokens`。 |
| `ONLINE_RL_ROLLOUT_GPU_MEMORY_UTILIZATION` | 空 | 覆盖 rollout 显存利用率。 |

### 2.4 Launcher CLI

`examples/jiuwenrl_online/run_online_rl.py` 支持通过 YAML + CLI 覆盖启动配置。
主要 CLI 参数：

| 参数 | 对应配置 |
| --- | --- |
| `--model-path`, `--model-name` | `inference.model_path/name` |
| `--vllm-gpu`, `--vllm-tp`, `--vllm-port`, `--inference-url` | 推理 vLLM 资源或复用已有服务 |
| `--judge-model-path`, `--judge-model-name`, `--judge-gpu`, `--judge-tp`, `--judge-port`, `--judge-url` | Judge 服务配置 |
| `--gateway-port`, `--redis-url`, `--trajectory-store-backend`, `--local-trajectory-store-dir` | Gateway/store 配置 |
| `--lora-default-policy`, `--lora-repo` | LoRA 策略和仓库路径 |
| `--threshold`, `--scan-interval`, `--train-gpu`, `--ppo-config` | Scheduler/PPO 配置 |
| `--drain-pending-on-train`, `--max-samples-per-run`, `--ppo-samples-per-step`, `--allow-partial-last-step` | 手动训练批次控制 |
| `--trajectory-batch-size` | JiuwenClaw/Rail 上传批大小 |
| `--skip-jiuwen`, `--jiuwen-agent-server-port`, `--jiuwen-ws-port`, `--jiuwen-web-host`, `--jiuwen-web-port` | JiuwenClaw 启动控制 |

## 3. 当前 API 接口

除 `/health` 外，Gateway 路由都会在 `GATEWAY_API_KEY` 非空时校验
`Authorization: Bearer <GATEWAY_API_KEY>`。`/v1/chat/completions` 会读取
`x-user-id` 做用户维度训练和 LoRA 路由；当前默认开启单用户回退，未传时使用
`jiuwenclaw-web`，多用户场景必须显式传稳定 `x-user-id`。

### 3.1 健康和统计

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/health` | Gateway 进程健康检查。 |
| `GET` | `/v1/rl/health` | RL Gateway 聚合健康信息。 |
| `GET` | `/v1/gateway/stats` | Gateway 请求数、轨迹 store 类型、样本统计。 |

### 3.2 Chat 代理和 Rail 上传

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/v1/chat/completions` | 可选 OpenAI chat 兼容代理入口；透传上游 vLLM，并按策略注入最新 LoRA。Agent 直连 vLLM 时不经过该接口。 |
| `POST` | `/v1/gateway/upload/batch` | `RLOnlineRail` 的 rail-v1 批量上传入口。 |
| `*` | `/{path:path}` | 其他请求透传到上游 LLM 服务。 |

### 3.3 训练任务

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/v1/training/tasks` | 创建训练任务。带 `user_id` 时训练指定用户；不带 `user_id` 时训练所有达到阈值的用户，每个用户各生成一个 LoRA。 |
| `GET` | `/v1/training/tasks?limit=20` | 查询训练任务列表。 |
| `GET` | `/v1/training/tasks/{task_id}` | 查询单个训练任务状态。 |
| `PATCH` | `/v1/training/tasks/{task_id}` | 修改任务状态；`{"status":"stopping"}` 用于请求停止，支持 `canceled/succeeded/failed`。 |

典型触发：

```bash
curl -X POST "$GATEWAY/v1/training/tasks" \
  -H "Authorization: Bearer $GATEWAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"user1"}'
```

训练所有 ready 用户：

```bash
curl -X POST "$GATEWAY/v1/training/tasks" \
  -H "Authorization: Bearer $GATEWAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

### 3.4 轨迹管理

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/v1/rl/trajectories:batchCreate` | 通用轨迹批量导入；支持 `rail-v1`、`agent-rollout-v1`、`online-rl-sample-v1`。 |
| `GET` | `/v1/rl/trajectories/stats?model_id=&user_id=` | 轨迹管理统计。 |
| `GET` | `/v1/rl/trajectories?model_id=&status=&user_id=&session_id=&task_id=&source=&policy_version=&limit=100` | 查询轨迹列表。 |
| `GET` | `/v1/rl/trajectories/{trajectory_id}` | 查询单条轨迹详情。 |
| `PATCH` | `/v1/rl/trajectories/{trajectory_id}` | 更新轨迹状态/奖励/metadata/source/policy。 |
| `DELETE` | `/v1/rl/trajectories/{trajectory_id}?force=false` | 删除轨迹；training 状态默认不可删，需 `force=true`。 |

### 3.5 LoRA 管理

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/v1/rl/lora?model_id=&limit=20` | 查询 LoRA 版本列表。 |
| `GET` | `/v1/rl/lora/latest?model_id=<user_or_model>` | 查询某用户/模型的最新 LoRA。 |
| `POST` | `/v1/rl/lora/effective` | 查询并可确保加载当前策略下的 effective LoRA；body 包含 `model_id`、`ensure_loaded`。 |
| `POST` | `/v1/rl/lora` | 注册外部 LoRA 路径并发布到 repo。 |
| `GET` | `/v1/rl/lora/{lora_id}` | 查询指定 LoRA。`lora_id` 格式为 `<model_id>:<version>`。 |
| `GET` | `/v1/rl/lora/{lora_id}:download` | 当前返回 LoRA 本地路径元数据，不做文件流传输。 |
| `POST` | `/v1/rl/lora/{lora_id}:setLatest` | 将指定版本设为 latest。 |
| `POST` | `/v1/rl/lora/{lora_id}:setAvailability` | 设置可用性。 |
| `DELETE` | `/v1/rl/lora/{lora_id}?force=false` | 删除指定 LoRA 版本。 |

### 3.6 Judge 服务

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/healthz` | Judge 服务健康检查。 |
| `POST` | `/score` | 对延迟奖励输入打分。 |

## 4. 当前实现对旧设计的主要差异

1. 当前是 task 驱动训练，不再建议使用“轨迹达到阈值自动触发训练”的文档表述。
   实际实现中 Scheduler 拿到 `training_task_store` 后只消费 pending training task；
   `threshold` 是“task 可消费的最少样本数”，不是直接自动触发开关。

2. 轨迹采集入口不是 Gateway chat 路径内置采集，而是 DeepAgent 注入
   `RLOnlineRail` 后异步上传到 `/v1/gateway/upload/batch`。Gateway chat 仍负责
   LLM 代理、用户身份校验和 LoRA 注入。

3. 当前支持 Redis 和本地文件 store。旧文档只描述“本地文件/Trajectory Manager”
   或单一 Redis 都不准确，应改成 `TRAJECTORY_STORE_BACKEND=auto|redis|local`。

4. 当前 LoRA 已实现 repo 版本管理、latest 查询、注册、删除、可用性设置、上游
   vLLM `/v1/load_lora_adapter` 热加载尝试，以及 latest LoRA 推理路由。直连
   vLLM 时由 `RLOnlineRail.before_model_call` 查询 `/v1/rl/lora/effective` 并临时
   切换本轮 model；Gateway 代理路径也保留 `latest_by_user` 自动注入。旧文档中
   “仅验证生成，不涉及热加载生效”的限制需要更新为：代码链路已具备热加载和
   latest LoRA 推理路由，但实际依赖 vLLM/硬件后端支持。

5. 旧文档的 download 语义和当前实现不同。当前 `:download` 返回路径/元数据，不是
   二进制文件流或签名 URL。若验收要求“真正下载权重文件”，当前实现缺少文件流下载。

6. 当前多用户训练策略已经存在：`POST /v1/training/tasks` 不传 `user_id` 时，
   Scheduler 会遍历所有达到阈值的用户，并按用户分别训练和发布 LoRA。

7. 当前文档中的权限“仅 Admin 用户可通过环境变量启用”不是代码强约束。
   代码只提供 `GATEWAY_API_KEY` Bearer 鉴权和本机环境变量控制，尚未实现 UI/Admin
   权限模型。

## 5. 对比后发现的缺少项

| 缺少项 | 当前状态 | 建议 |
| --- | --- | --- |
| 真正的 LoRA 文件下载 | `:download` 只返回本地路径元数据 | 如果对外 API 需要下载，应改成 `StreamingResponse` 或对象存储 signed URL。 |
| 明确的 Admin/RBAC 权限 | 只有 `GATEWAY_API_KEY` | POC 可以接受；产品化需接入用户身份和权限。 |
| API 文档中的训练 task payload schema | 代码接受开放 dict | 建议补 Pydantic request/response model，避免调用方误用。 |
| 训练任务并发/队列 | 当前 single-active-task | 如果后续要多任务并发，需要扩展 task store 状态机和资源隔离。 |
| 轨迹列表分页 cursor | 当前 `next_cursor=None`，limit 截断 | 数据量大时需要 cursor 或按索引分页。 |
| LoRA 质量/收益评估 | 训练后只发布 LoRA | 若验收要质量指标，需要接评测或线上 A/B 指标。 |
| 跨节点部署说明 | 代码通过 URL 可配，但文档未覆盖网络边界 | 补充 Gateway、Scheduler、JiuwenSwarm、vLLM 的可访问地址和鉴权要求。 |
| SFT 后训练流程 | 当前文档是 Online RL；SFT flow 在其他分支/文档 | 不应混入本 RL POC 文档，建议单独维护 SFT 设计。 |

## 6. 建议替换后的核心验收口径

1. 使用 `USE_RL_ONLINE_RAIL=1` 和 `TRAJECTORY_GATEWAY_URL` 启动 JiuwenSwarm，
   确认 DeepAgent 自动加载 `RLOnlineRail`，无需修改 JiuwenSwarm 代码。

2. 通过真实 JiuwenSwarm 对话产生至少两轮交互，等待延迟 Judge 后，
   调用 `/v1/rl/trajectories/stats` 和 `/v1/rl/trajectories` 确认轨迹进入
   `pending`。

3. 调用 `POST /v1/training/tasks` 手动触发训练。指定 `user_id` 时只训练该用户；
   不指定时训练所有达到阈值的用户。

4. 训练完成后通过 `/v1/training/tasks/{task_id}` 确认 `succeeded`，通过
   `/v1/rl/lora` 查询 LoRA 版本。直连 vLLM 场景通过带 `RLOnlineRail` 的
   JiuwenSwarm 下一轮对话确认 Rail 查询 effective LoRA 并临时切换本轮 model；
   Gateway 代理场景可通过 `/v1/chat/completions` 确认 latest LoRA 推理路由。
