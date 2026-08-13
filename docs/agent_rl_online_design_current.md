# 特性设计：Online RL 训练闭环（POC版）

## 0. 关键限制（前置约束）

> **重要**：基于当前 POC 阶段的资源与技术现状，本特性存在以下关键限制，全篇设计均在此范围内展开。

| 限制项 | 说明 |
|--------|------|
| **模型范围** | POC 主要验证 **Qwen3-4B** 类模型的在线 RL 闭环；模型文件需要提前准备在部署机器上。其他模型可通过配置接入，但不作为当前验收重点。 |
| **部署形态** | 当前主流程按单机部署设计：Gateway、OnlineTrainingScheduler、JiuwenSwarm、vLLM/Judge、轨迹存储部署在同一台机器或同一内网可访问环境。代码通过 URL 配置具备跨进程/跨容器能力，但跨节点弱网和复杂鉴权不在 POC 范围内。 |
| **RL 使能权限** | RL Rail 默认关闭。管理员在启动 JiuwenSwarm/Agent 前通过环境变量 `USE_RL_ONLINE_RAIL=1` 开启。当前代码只提供环境变量开关和 `GATEWAY_API_KEY` Bearer 鉴权，尚未实现 UI/RBAC 权限模型。 |
| **训练调度** | 当前训练采用手动 task 触发：调用 `POST /v1/training/tasks` 创建训练任务。`threshold` 是 task 可消费样本的最小阈值，不建议描述为“轨迹达到阈值自动训练”。 |
| **LoRA 能力** | 当前代码已实现 LoRA 生成、版本管理、latest 查询、注册、删除、可用性设置，以及 latest LoRA 推理路由。直连 vLLM 时由本地 `RLOnlineRail.before_model_call` 查询 Gateway 的 effective LoRA 并临时改写本轮 model；走 Gateway 代理时 Gateway 也保留同等注入能力。实际热加载和推理生效依赖 vLLM/硬件后端能力。LoRA 质量评估和收益验证不在当前 POC 范围内。 |
| **轨迹存储** | 当前支持 Redis 和本地文件两种 store。通过 `TRAJECTORY_STORE_BACKEND=auto|redis|local` 选择；`auto` 在配置 Redis URL 时使用 Redis，否则使用本地 JSON store。 |
| **场景范围** | 当前文档只覆盖 Online RL。SFT 后训练流程、supervisor replay、数据集任务 rollouter 等能力应放在 SFT 设计文档中单独描述。 |

---

## 1. 特性概述

本特性实现 Online RL 训练基础闭环，覆盖从 RL 特性开启、JiuwenSwarm 轨迹采集、Gateway 接收和管理轨迹、手动触发 PPO 训练，到 LoRA 生成、版本查询和 latest LoRA 推理路由的完整流程。

当前架构重点验证流程的**可运行性**、**可控性**和**可复现性**：

- JiuwenSwarm 不需要修改业务代码，通过 DeepAgent 创建链路中的环境变量自动注入 `RLOnlineRail`。
- 轨迹由 `RLOnlineRail` 批量上传到 Gateway 的 `/v1/gateway/upload/batch`。
- Gateway 内部的 `GatewayTrajectoryRuntime` 承担轨迹接收、延迟 Judge、轨迹管理 API 和 store 写入能力。
- OnlineTrainingScheduler 消费同一 store 中的 pending 样本，并由 `/v1/training/tasks` 手动触发训练。
- 训练完成后发布 LoRA 到 `LoRARepository`。直连 vLLM 场景下，`RLOnlineRail` 在本地模型调用前查询 effective LoRA，并将本轮请求的 model 临时切到用户 LoRA 名称；Gateway 代理场景下，Gateway 也可按用户 latest LoRA 自动注入推理请求。

---

## 2. 核心流程设计（含问题解答）

### 2.1 服务启动与环境准备

| 步骤 | 操作 | 设计说明（含问题解答） |
|------|------|------------------------|
| 1 | 启动 RL 后端服务 | 拉起 Gateway 和 OnlineTrainingScheduler。当前没有独立 Trajectory Manager 进程，轨迹管理能力内置在 Gateway 的 `GatewayTrajectoryRuntime` 中；底层 store 可选 Redis 或本地文件。验证点：Gateway `/health` 可访问，Scheduler 日志显示已连接 store。 |
| 2 | 启动 vLLM/Judge | 启动推理 vLLM；Judge 可以复用推理 vLLM，也可以独立配置 `JUDGE_URL`/`JUDGE_MODEL`。推理服务如需 LoRA 热加载，需要启动 vLLM 时打开 LoRA 支持。 |
| 3 | 启用 Agent RL 能力 | 启动 JiuwenSwarm/Agent 前设置 `USE_RL_ONLINE_RAIL=1`。DeepAgent 创建时会调用 `build_rl_online_rail_from_env()` 自动注入 `RLOnlineRail`。默认不使能。 |
| 4 | 配置轨迹上传 URL | 设置 `TRAJECTORY_GATEWAY_URL=http://<gateway_ip>:<gateway_port>`。如果 Gateway 配置了 `GATEWAY_API_KEY`，Agent 侧还需要设置 `TRAJECTORY_GATEWAY_API_KEY`。 |
| 5 | 配置用户身份 | 多用户场景需要确保请求携带稳定 `x-user-id`。launcher 会基于 `RL_ONLINE_TENANT_ID` 或 `WEB_USER_ID` 写入 JiuwenSwarm 环境；未配置时当前 Gateway 默认回退到单用户 `jiuwenclaw-web`。 |

### 2.2 轨迹采集与存储

| 步骤 | 操作 | 设计说明（含问题解答） |
|------|------|------------------------|
| 6 | 触发 Agent 推理 | 用户通过 JiuwenSwarm 正常发起对话。推理请求可以直连 vLLM，不要求经过 Gateway；`RLOnlineRail` 在 Agent 本地模型调用链路采集轨迹并上传 Gateway。若部署选择走 Gateway `/v1/chat/completions` 代理，Gateway 也可透传上游 vLLM 并执行 latest LoRA 注入。 |
| 7 | Rail 采集轨迹 | `RLOnlineRail` 在 DeepAgent 执行链路上逐轮采集 prompt/response、token ids、logprobs、tool calls、session 等信息。 |
| 8 | 批量上传轨迹 | `RLOnlineRail` 按 `TRAJECTORY_BATCH_SIZE` 调用 Gateway `POST /v1/gateway/upload/batch` 上传 rail-v1 批次；失败时可写入 `TRAJECTORY_WAL_DIR`。 |
| 9 | 延迟 Judge 和入库 | Gateway 收到 rail-v1 数据后通过延迟奖励机制处理上一轮样本，并写入 Redis 或 local store。轨迹状态初始为 `pending`，供 Scheduler 后续消费。 |
| 10 | 轨迹查询和管理 | 可通过 `/v1/rl/trajectories`、`/v1/rl/trajectories/stats`、`/v1/rl/trajectories/{trajectory_id}` 查询和管理轨迹。 |

### 2.3 训练触发与 LoRA 生成

| 步骤 | 操作 | 设计说明（含问题解答） |
|------|------|------------------------|
| 11 | 手动触发 RL 训练 | 调用 Gateway `POST /v1/training/tasks` 创建训练任务。请求带 `user_id` 时只训练指定用户；不带 `user_id` 时训练所有达到阈值的用户，每个用户单独生成 LoRA。 |
| 12 | 查询训练状态 | 调用 `GET /v1/training/tasks` 或 `GET /v1/training/tasks/{task_id}` 查询状态。状态包括 `pending`、`running`、`stopping`、`succeeded`、`failed`、`canceled`。 |
| 13 | 停止训练 | 调用 `PATCH /v1/training/tasks/{task_id}`，body 为 `{"status":"stopping"}`。Scheduler 轮询到停止状态后取消后续训练，并把未消费样本恢复为 pending。 |
| 14 | 训练样本选择 | 默认每次训练取 `threshold` 条样本。若设置 `drain_pending_on_train=true`，则一次 claim 当前 pending 样本；可通过 `max_samples_per_run` 限制数量，通过 `ppo_samples_per_step` 控制单次 run 内的 PPO step 粒度。 |
| 15 | LoRA 发布 | 训练完成后，PPO executor 将 LoRA 发布到 `LORA_REPO_ROOT`。可通过 `GET /v1/rl/lora` 或 `GET /v1/rl/lora/latest?model_id=<id>` 查询。 |

### 2.4 可选：LoRA 热加载与 latest 推理路由

| 步骤 | 操作 | 设计说明（含问题解答） |
|------|------|------------------------|
| 16 | 设置默认 LoRA 策略 | 设置 `LORA_DEFAULT_POLICY=latest_by_user` 后，`RLOnlineRail.before_model_call` 会按当前 `user_id` 查询 Gateway 的 effective LoRA。 |
| 17 | 查询 effective LoRA | Rail 本地调用 `POST /v1/rl/lora/effective`，body 包含 `{"model_id":"<user_id>","ensure_loaded":true}`。Gateway 查询 latest LoRA，并在需要时调用上游 vLLM `/v1/load_lora_adapter`。 |
| 18 | latest LoRA 推理 | 直连 vLLM 场景下，如果 effective LoRA 可用，Rail 会在本轮模型调用前把本地 `config.model_name` 临时改为用户 LoRA 名称，`ReActAgent._railed_model_call()` 随后用该 model 发起真实 LLM 调用；`after_model_call` 再恢复原模型名。Gateway 代理场景下，Gateway 也会在 `/v1/chat/completions` 中执行等价的 model 字段注入。 |
| 19 | LoRA 下载 | 当前 `GET /v1/rl/lora/{lora_id}:download` 返回 LoRA 本地路径和元数据，不是文件流下载。如果外部系统要求真正下载文件，需要后续改为 `StreamingResponse` 或对象存储 signed URL。 |

---

## 3. 依赖需求（与设计对齐）

| 依赖项 | 状态 | 说明 |
|--------|------|------|
| DeepAgent 支持环境变量注入 Rail | **已支持** | `openjiuwen/harness/factory.py` 中会追加 env-configured online training rail。 |
| JiuwenSwarm 支持稳定用户 ID | **必须满足** | 多用户训练和 LoRA 路由依赖稳定 `user_id`。launcher 会通过 `WEB_USER_ID`/`RL_ONLINE_TENANT_ID` 配置；Rail 会用该 ID 上传轨迹，并在需要时补充 `x-user-id` 请求头。 |
| JiuwenSwarm 使用当前 agent-core | **必须满足** | 需要使用包含 `USE_RL_ONLINE_RAIL` 注入逻辑的 agent-core。 |
| Gateway 轨迹管理能力 | **已支持** | Gateway 内置 `GatewayTrajectoryRuntime`，对外提供轨迹上传、查询、状态管理 API。 |
| Redis 或本地文件 store | **已支持** | `TRAJECTORY_STORE_BACKEND=redis/local/auto`。Redis 适合分布式和大规模，本地 store 适合单机简化部署。 |
| 推理引擎支持 LoRA 热加载 | **受限** | 代码会调用 vLLM `/v1/load_lora_adapter`，但最终是否生效取决于 vLLM/硬件后端能力。 |
| verl/Ray PPO 训练依赖 | **必须满足** | OnlineTrainingScheduler 通过 PPO executor 复用 verl/Ray 训练链路。 |

---

## 4. 关键变动（与设计对齐）

| 组件 | 变动点 | 验证方式 |
|--------|--------|----------|
| **DeepAgent/JiuwenSwarm** | 通过 `USE_RL_ONLINE_RAIL=1` 自动注入 `RLOnlineRail`，无需修改 JiuwenSwarm 业务代码。Rail 负责本地轨迹采集、上传，以及 direct-vLLM 场景下的 latest LoRA 查询和本轮 model 临时切换。 | 启动日志显示 Rail ready；真实对话后 Gateway 收到 `/v1/gateway/upload/batch`；`latest_by_user` 策略下本轮模型调用使用用户 LoRA 名称。 |
| **Gateway** | 提供可选 `/v1/chat/completions` 代理、`/v1/gateway/upload/batch` Rail 上传、`/v1/rl/trajectories*` 轨迹管理、`/v1/training/tasks` 训练任务、`/v1/rl/lora*` LoRA 管理。Gateway 是 LoRA/control-plane 和轨迹管理中心，不是直连 vLLM 场景的必经推理转发点。 | 调用各 API 返回符合预期，轨迹状态可从 pending 变为 trained。 |
| **Trajectory Runtime** | 不再作为独立 TM 服务，而是 Gateway 内部模块；支持 Redis/local store。 | `/v1/gateway/stats` 返回 store backend 和样本统计。 |
| **Scheduler** | 使用 task-driven 训练模型。存在 active task 时消费 task；不再把“达到阈值”描述为直接自动训练。 | 创建 task 后 Scheduler claim 样本并启动 PPO；停止 task 可取消后续训练。 |
| **Store** | 支持 `RedisTrajectoryStore` 和 `LocalTrajectoryStore`，并统一 training task store。 | 分别设置 `TRAJECTORY_STORE_BACKEND=redis` 或 `local` 跑通同一流程。 |
| **LoRA Repository** | 支持 LoRA 发布、latest 管理、可用性设置、删除、查询 effective LoRA。 | 训练后 `/v1/rl/lora` 可看到版本；`latest_by_user` 策略下推理请求携带 LoRA 信息。 |

当前 API 对照：

| 能力 | 当前接口 |
|------|----------|
| Gateway 健康检查 | `GET /health` |
| RL 健康检查 | `GET /v1/rl/health` |
| Gateway 统计 | `GET /v1/gateway/stats` |
| Chat 代理（可选） | `POST /v1/chat/completions` |
| Rail 批量上传 | `POST /v1/gateway/upload/batch` |
| 通用轨迹导入 | `POST /v1/rl/trajectories:batchCreate` |
| 轨迹统计 | `GET /v1/rl/trajectories/stats` |
| 轨迹列表 | `GET /v1/rl/trajectories` |
| 轨迹详情 | `GET /v1/rl/trajectories/{trajectory_id}` |
| 更新轨迹 | `PATCH /v1/rl/trajectories/{trajectory_id}` |
| 删除轨迹 | `DELETE /v1/rl/trajectories/{trajectory_id}` |
| 创建训练任务 | `POST /v1/training/tasks` |
| 查询训练任务 | `GET /v1/training/tasks`、`GET /v1/training/tasks/{task_id}` |
| 停止训练任务 | `PATCH /v1/training/tasks/{task_id}`，body: `{"status":"stopping"}` |
| LoRA 列表 | `GET /v1/rl/lora` |
| 最新 LoRA | `GET /v1/rl/lora/latest?model_id=<id>` |
| Effective LoRA | `POST /v1/rl/lora/effective` |
| 注册 LoRA | `POST /v1/rl/lora` |
| LoRA 详情 | `GET /v1/rl/lora/{lora_id}` |
| LoRA 元数据下载 | `GET /v1/rl/lora/{lora_id}:download` |
| 设置 latest | `POST /v1/rl/lora/{lora_id}:setLatest` |
| 设置可用性 | `POST /v1/rl/lora/{lora_id}:setAvailability` |
| 删除 LoRA | `DELETE /v1/rl/lora/{lora_id}` |

---

## 5. 验收标准（含实际验收用例）

### 5.1 基础验收场景（必选）

| 用例ID | 场景 | 操作步骤 | 验收标准 |
|--------|------|----------|----------|
| **TC1** | RL 服务启动 | 1. 启动 vLLM/Judge。<br>2. 启动 Gateway。<br>3. 启动 OnlineTrainingScheduler。<br>4. 检查 `/health`、`/v1/rl/health`。 | 服务进程存活，端口监听正常，Gateway 返回 ready。 |
| **TC2** | Agent RL 使能 | 1. 设置 `USE_RL_ONLINE_RAIL=1`。<br>2. 设置 `TRAJECTORY_GATEWAY_URL`。<br>3. 启动 JiuwenSwarm。 | DeepAgent 自动加载 `RLOnlineRail`，无 JiuwenSwarm 代码修改。 |
| **TC3** | 轨迹采集 | 1. 发送至少两轮用户请求。<br>2. 等待 Rail 上传和延迟 Judge。<br>3. 查询 `/v1/rl/trajectories/stats`。 | Gateway 收到轨迹，store 中出现 pending 样本。 |
| **TC4** | 手动训练触发 | 1. 调用 `POST /v1/training/tasks`。<br>2. 查询 `GET /v1/training/tasks/{task_id}`。 | task 从 pending/running 进入 succeeded；样本从 pending/training 变为 trained。 |
| **TC5** | 训练停止 | 1. 创建训练任务。<br>2. 调用 `PATCH /v1/training/tasks/{task_id}`，body 为 `{"status":"stopping"}`。 | task 进入 canceled 或已完成状态；未消费样本恢复 pending。 |
| **TC6** | LoRA 生成与查询 | 1. 完整执行一次训练。<br>2. 调用 `GET /v1/rl/lora`。<br>3. 调用 `GET /v1/rl/lora/latest?model_id=<user_id>`。 | 返回包含新版本 LoRA，路径存在，版本递增。 |
| **TC7** | latest LoRA 推理路由 | 1. 设置 `LORA_DEFAULT_POLICY=latest_by_user`。<br>2. 启动带 `RLOnlineRail` 的 JiuwenSwarm。<br>3. 发起下一轮 Agent 对话，推理可直连 vLLM。 | Rail 能查询 effective LoRA；若 vLLM 支持热加载，则本轮真实模型调用使用用户 latest LoRA。 |
| **TC8** | 多用户训练 | 1. 分别为 user1/user2/user3 上传达到阈值的轨迹。<br>2. 调用 `POST /v1/training/tasks` 且不传 `user_id`。 | Scheduler 遍历所有 ready 用户，每个用户独立训练并生成 LoRA。 |

### 5.2 可选验收场景

| 用例ID | 场景 | 操作步骤 | 验收标准 |
|--------|------|----------|----------|
| **TC9** | 本地 store 部署 | 设置 `TRAJECTORY_STORE_BACKEND=local` 和 `LOCAL_TRAJECTORY_STORE_DIR`，重复 TC1-TC6。 | 不依赖 Redis，轨迹、task 状态和 LoRA 训练链路可跑通。 |
| **TC10** | Redis store 部署 | 设置 `TRAJECTORY_STORE_BACKEND=redis` 和 `REDIS_URL`，重复 TC1-TC6。 | Redis 中样本状态转换正常，Gateway/Scheduler 共享同一数据。 |
| **TC11** | drain pending 模式 | 设置 `drain_pending_on_train=true`，上传超过 threshold 的样本后触发训练。 | 单次训练 claim 当前 pending 样本；受 `max_samples_per_run` 和 `ppo_samples_per_step` 控制。 |
| **TC12** | LoRA 管理 API | 注册外部 LoRA、设置 latest、设置 availability、删除版本。 | `/v1/rl/lora*` API 状态码和返回内容符合预期。 |

---

# 测试用例细化：Online RL 训练闭环（POC版）

## 1. 测试预备知识

### 1.1 关键限制回顾

- **权限**：当前通过 `GATEWAY_API_KEY` 保护 Gateway API；Admin/RBAC 不在当前代码中强制实现。
- **资源**：POC 推荐单机部署，推理 vLLM、Judge、Gateway、Scheduler、JiuwenSwarm 需要互相可访问。
- **模型**：推荐使用已验证的 Qwen3-4B 类模型路径，具体由 `--model-path` 或 YAML 配置。
- **轨迹**：当前主链路由 `RLOnlineRail` 上传轨迹，Agent 推理可以直连 vLLM；Gateway chat 路径默认不直接采集轨迹。
- **训练**：通过 `/v1/training/tasks` 手动触发；`threshold` 控制最小可训练样本数。
- **LoRA**：代码支持生成、版本管理和热加载调用；推理是否真实生效依赖 vLLM/硬件后端。

### 1.2 测试环境配置

| 组件 | 配置项 | 示例值 |
| :--- | :--- | :--- |
| **vLLM** | 启动模型 | `/models/Qwen3-4B` |
| **vLLM** | LoRA 运行时 | `VLLM_ALLOW_RUNTIME_LORA_UPDATING=1`，启动参数包含 `--enable-lora` |
| **Gateway** | API 地址 | `http://<gateway_ip>:18080` |
| **Gateway** | store backend | `TRAJECTORY_STORE_BACKEND=local` 或 `redis` |
| **Gateway** | 本地 store | `LOCAL_TRAJECTORY_STORE_DIR=/path/to/local_store` |
| **Gateway** | Redis store | `REDIS_URL=redis://127.0.0.1:6379/0` |
| **Gateway** | LoRA 仓库 | `LORA_REPO_ROOT=/path/to/lora_repo` |
| **Gateway** | LoRA 默认策略 | `LORA_DEFAULT_POLICY=latest_by_user` 或 `disabled`，供 effective LoRA 查询和可选 Gateway 代理注入使用 |
| **JiuwenSwarm** | Rail 使能 | `USE_RL_ONLINE_RAIL=1` |
| **JiuwenSwarm** | 轨迹上传 | `TRAJECTORY_GATEWAY_URL=http://<gateway_ip>:18080` |
| **JiuwenSwarm** | 用户 ID | `RL_ONLINE_TENANT_ID=<user_id>` 或 `WEB_USER_ID=<user_id>` |
| **JiuwenSwarm** | LoRA 默认策略 | `LORA_DEFAULT_POLICY=latest_by_user` 或 `disabled`，供本地 Rail 在 `before_model_call` 查询 effective LoRA |
| **Scheduler** | 训练触发阈值 | `training.threshold` 或 `--threshold` |
| **Scheduler** | 训练 GPU | `training.gpu_ids` 或 `--train-gpu` |

---

## 2. 详细测试用例

### TC1: RL 服务启动与存活检查

| 项目 | 内容 |
| :--- | :--- |
| **用例名称** | 验证 RL 后端服务启动正常 |
| **前置条件** | 1. GPU/NPU 驱动正常。<br>2. 模型路径可访问。<br>3. 目标端口未被占用。 |
| **测试步骤** | 1. 启动推理 vLLM/Judge。<br>2. 启动 Gateway。<br>3. 启动 OnlineTrainingScheduler。<br>4. 执行 `curl http://<gateway_ip>:18080/health`。<br>5. 执行 `curl http://<gateway_ip>:18080/v1/rl/health`。 |
| **预期结果** | Gateway 返回 `{"status":"ok"}`；RL health 返回 ready；Scheduler 日志显示 store backend 和轮询配置。 |
| **验收标准** | 服务进程存活，端口监听正常，无 crash 日志。 |

---

### TC2: Agent RL 能力使能与轨迹上报

| 项目 | 内容 |
| :--- | :--- |
| **用例名称** | 验证 Agent 在使能 RL 后能通过 `RLOnlineRail` 上报轨迹 |
| **前置条件** | TC1 执行成功，JiuwenSwarm 使用当前 agent-core。 |
| **测试步骤** | 1. 设置 `USE_RL_ONLINE_RAIL=1`。<br>2. 设置 `TRAJECTORY_GATEWAY_URL=http://<gateway_ip>:18080`。<br>3. 如启用鉴权，设置 `TRAJECTORY_GATEWAY_API_KEY`。<br>4. 设置稳定 `RL_ONLINE_TENANT_ID` 或 `WEB_USER_ID`。<br>5. 启动 JiuwenSwarm 并发送至少两轮对话。<br>6. 查询 `/v1/rl/trajectories/stats`。 |
| **预期结果** | Agent 正常回复；Gateway 接收 Rail batch；store 中出现轨迹样本。 |
| **验收标准** | 推理流程正常，轨迹采集链路通畅，样本可通过 API 查询。 |

---

### TC3: 训练任务启动与资源占用

| 项目 | 内容 |
| :--- | :--- |
| **用例名称** | 验证通过 API 触发 RL 训练 |
| **前置条件** | TC2 执行成功，指定用户 pending 样本数达到 `threshold`。 |
| **测试步骤** | 1. 调用 `POST /v1/training/tasks` 创建训练任务。<br>2. 调用 `GET /v1/training/tasks/{task_id}` 查询状态。<br>3. 观察 GPU/NPU 资源占用。<br>4. 训练完成后再次查询任务状态。 |
| **预期结果** | task 被 Scheduler claim，状态进入 running；训练过程有资源占用；最终进入 succeeded 或 failed。 |
| **验收标准** | 训练可以被手动触发，任务状态可查询，成功时生成 LoRA。 |

示例：

```bash
curl -X POST "http://<gateway_ip>:18080/v1/training/tasks" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"user1"}'
```

---

### TC4: 训练任务停止

| 项目 | 内容 |
| :--- | :--- |
| **用例名称** | 验证训练任务可停止 |
| **前置条件** | 已创建 pending 或 running 训练任务。 |
| **测试步骤** | 1. 查询 task id。<br>2. 调用 `PATCH /v1/training/tasks/{task_id}`，body 为 `{"status":"stopping"}`。<br>3. 查询任务状态和轨迹状态。 |
| **预期结果** | pending task 直接 canceled；running task 进入 stopping 后由 Scheduler 转成 canceled 或完成当前批次。 |
| **验收标准** | stop API 可用，未训练样本不会丢失。 |

示例：

```bash
curl -X PATCH "http://<gateway_ip>:18080/v1/training/tasks/<task_id>" \
  -H "Content-Type: application/json" \
  -d '{"status":"stopping"}'
```

---

### TC5: LoRA 生成与查询

| 项目 | 内容 |
| :--- | :--- |
| **用例名称** | 验证训练后 LoRA 版本生成并可查询 |
| **前置条件** | TC3 训练任务 succeeded。 |
| **测试步骤** | 1. 调用 `GET /v1/rl/lora?model_id=<user_id>`。<br>2. 调用 `GET /v1/rl/lora/latest?model_id=<user_id>`。<br>3. 检查返回 path 对应本地目录。 |
| **预期结果** | 返回至少一个 LoRA 版本，latest 指向最新版本，路径存在。 |
| **验收标准** | LoRA 文件生成正常，版本元数据完整。 |

---

### TC6: latest LoRA 推理路由

| 项目 | 内容 |
| :--- | :--- |
| **用例名称** | 验证本地 Rail 使用用户 latest LoRA 路由推理 |
| **前置条件** | 已生成指定用户 LoRA；Gateway 配置 `LORA_REPO_ROOT`；JiuwenSwarm 配置 `USE_RL_ONLINE_RAIL=1`、`TRAJECTORY_GATEWAY_URL`、`LORA_DEFAULT_POLICY=latest_by_user`；vLLM 支持 LoRA 热加载。 |
| **测试步骤** | 1. 可先调用 `POST /v1/rl/lora/effective`，body 为 `{"model_id":"<user_id>","ensure_loaded":true}`，确认 Gateway 能返回 enabled LoRA。<br>2. 通过 JiuwenSwarm 发起下一轮 Agent 对话，推理可直连 vLLM。<br>3. 查看 `RLOnlineRail` 日志，确认 `using latest LoRA user=<user_id>`。 |
| **预期结果** | Rail 在 `before_model_call` 查询 effective LoRA，Gateway 找到 latest LoRA 并尝试加载；Rail 将本轮模型调用的 model 临时切到用户 LoRA 名称，调用结束后恢复。 |
| **验收标准** | direct-vLLM 场景下 latest LoRA 路由链路完整；若 vLLM 后端支持，LoRA 推理生效。 |

---

### TC7: 多用户无 user_id 训练

| 项目 | 内容 |
| :--- | :--- |
| **用例名称** | 验证不传 user_id 时训练所有 ready 用户 |
| **前置条件** | user1、user2、user3 均有不少于 threshold 的 pending 样本。 |
| **测试步骤** | 1. 调用 `POST /v1/training/tasks`，body 为 `{}`。<br>2. 查询任务状态。<br>3. 查询 `/v1/rl/lora`。 |
| **预期结果** | Scheduler 遍历所有 ready 用户，按用户分别训练；每个用户生成自己的 LoRA 版本。 |
| **验收标准** | 单个 task 能覆盖多个 ready 用户，但 LoRA 版本仍按用户隔离。 |

---

### TC8: 本地 store 部署

| 项目 | 内容 |
| :--- | :--- |
| **用例名称** | 验证不依赖 Redis 的本地轨迹服务 |
| **前置条件** | 本地目录可写。 |
| **测试步骤** | 1. 设置 `TRAJECTORY_STORE_BACKEND=local`。<br>2. 设置 `LOCAL_TRAJECTORY_STORE_DIR=/tmp/online_rl_store`。<br>3. 重复 TC1-TC5。 |
| **预期结果** | 本地 JSON store 中保存 trajectories/tasks/pending judge 状态；训练链路可跑通。 |
| **验收标准** | 无 Redis 环境下端到端流程可用。 |

---

### TC9: LoRA 元数据下载

| 项目 | 内容 |
| :--- | :--- |
| **用例名称** | 验证 LoRA download API 当前语义 |
| **前置条件** | 已生成 LoRA。 |
| **测试步骤** | 1. 获取 `lora_id`，格式为 `<model_id>:<version>`。<br>2. 调用 `GET /v1/rl/lora/{lora_id}:download`。 |
| **预期结果** | 返回 LoRA 元数据和本地 path。 |
| **验收标准** | 当前 API 可定位 LoRA 文件；注意它不是文件流下载。 |
