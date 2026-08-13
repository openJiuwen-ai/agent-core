# 特性设计：第三方 Agent 接入 Online RL 训练闭环（POC版）

## 0. 关键限制（前置约束）

> **重要**：第三方 Agent 接入与原生 JiuwenSwarm/Rail 接入共用 Gateway、Scheduler、轨迹 store、PPO 训练和 LoRA 管理能力；差异只在轨迹采集入口。第三方 Agent 无法注入本地 `RLOnlineRail`，因此必须通过 Gateway 代理推理请求，由 Gateway 在转发链路上采集轨迹。

| 限制项 | 说明 |
|--------|------|
| **Agent 范围** | POC 面向可配置 Base URL 的第三方 Agent。OpenAI-compatible `/v1/chat/completions` 客户端可直接接入 Gateway；Claude Code 等 Anthropic-compatible 客户端需要 Gateway 具备协议适配能力，或接入 Anthropic-compatible 上游服务。 |
| **接入方式** | 第三方 Agent 不修改 Agent Loop，不注入 `RLOnlineRail`。必须把 Agent 的 LLM Base URL 指向 Gateway，通过 Gateway 代理到真实 vLLM/Judge 后端。若第三方 Agent 直连 vLLM，Gateway 无法看到请求，也无法采集轨迹。 |
| **部署形态** | 当前主流程按单机或同一内网部署设计：Gateway、OnlineTrainingScheduler、第三方 Agent、vLLM/Judge、轨迹存储在同一台机器或可互通网络中。跨节点弱网、复杂鉴权和企业级租户隔离不在 POC 范围内。 |
| **训练调度** | 训练采用手动 task 触发：调用 `POST /v1/training/tasks` 创建训练任务。`threshold` 表示 task 可消费样本的最小阈值，不应描述为“轨迹达到阈值自动训练”。 |
| **LoRA 能力** | Gateway 代理路径负责 latest LoRA 注入。设置 `LORA_DEFAULT_POLICY=latest_by_user` 后，Gateway 会按 `x-user-id` 查询 latest LoRA，确保加载后将本轮请求的 `model` 改写为用户 LoRA 名称。实际热加载和推理生效依赖 vLLM/硬件后端能力。 |
| **用户身份** | 多用户训练依赖稳定 `x-user-id`。第三方 Agent 如果不能自定义 header，需要通过 Gateway API key、请求上下文或部署侧映射补齐稳定用户 ID；不能长期依赖单用户默认值。 |
| **轨迹存储** | 当前支持 Redis 和本地文件两种 store。通过 `TRAJECTORY_STORE_BACKEND=auto|redis|local` 选择；`auto` 在配置 Redis URL 时使用 Redis，否则使用本地 JSON store。 |
| **场景范围** | 当前文档只覆盖第三方 Agent 接入 Online RL。SFT 后训练、supervisor replay、数据集 task rollouter 等能力应放在 SFT 设计文档中单独描述。 |

---

## 1. 特性概述

本特性实现第三方 Agent 接入 Online RL 训练闭环，覆盖从第三方 Agent 配置 Gateway Base URL、Gateway 代理推理并采集轨迹、手动触发 PPO 训练，到 LoRA 生成、版本查询和 latest LoRA 推理路由的完整流程。

当前架构重点验证流程的**无侵入接入**、**可控训练**和**与原生 Online RL 主链路复用**：

- 第三方 Agent 不需要修改业务代码，只需要把 LLM Base URL 指向 Gateway，并提供稳定 `x-user-id` 或等价用户映射。
- Gateway 的 `/v1/chat/completions` 是第三方 Agent 的主入口，负责鉴权、用户识别、请求字段清理、latest LoRA 注入、上游转发和响应返回。
- 第三方 Agent 轨迹由 Gateway 在代理链路采集，归一化为与 Rail 上传一致的 online RL sample，再写入 Redis 或 local store。
- OnlineTrainingScheduler 消费同一 store 中的 pending 样本，并由 `/v1/training/tasks` 手动触发训练。
- 训练完成后发布 LoRA 到 `LoRARepository`。第三方 Agent 后续请求仍经过 Gateway，由 Gateway 按用户 latest LoRA 自动注入推理请求。

与原生 JiuwenSwarm/Rail 方案的核心差异：

| 方案 | 推理链路 | 轨迹采集位置 | latest LoRA 注入位置 |
|------|----------|--------------|----------------------|
| 原生 JiuwenSwarm/Rail | 可以直连 vLLM，也可以走 Gateway | Agent 本地 `RLOnlineRail` 上传 `/v1/gateway/upload/batch` | 直连时由 Rail 本地注入；走 Gateway 时由 Gateway 注入 |
| 第三方 Agent | 必须走 Gateway 代理 | Gateway `/v1/chat/completions` 代理链路 | Gateway 代理链路注入 |

---

## 2. 核心流程设计（含问题解答）

### 2.1 服务启动与环境准备

| 步骤 | 操作 | 设计说明（含问题解答） |
|------|------|------------------------|
| 1 | 启动 RL 后端服务 | 拉起 Gateway 和 OnlineTrainingScheduler。当前没有独立 Trajectory Manager 进程，轨迹管理能力内置在 Gateway 的 `GatewayTrajectoryRuntime` 中；底层 store 可选 Redis 或本地文件。验证点：Gateway `/health` 可访问，Scheduler 日志显示已连接 store。 |
| 2 | 启动 vLLM/Judge | 启动推理 vLLM；Judge 可以复用推理 vLLM，也可以独立配置 `JUDGE_URL`/`JUDGE_MODEL`。推理服务如需 LoRA 热加载，需要启动 vLLM 时打开 LoRA 支持。 |
| 3 | 启动 Gateway 代理 | Gateway 配置上游 `INFERENCE_URL`/`LLM_URL`、`MODEL_ID`、`GATEWAY_API_KEY`、`LORA_REPO_ROOT`、`TRAJECTORY_STORE_BACKEND` 等环境变量。第三方 Agent 的所有 LLM 请求都应进入 Gateway。 |
| 4 | 配置第三方 Agent Base URL | OpenAI-compatible 客户端设置 `OPENAI_BASE_URL=http://<gateway_ip>:<gateway_port>/v1` 或等价配置。Claude Code 可设置 `ANTHROPIC_BASE_URL=http://<gateway_ip>:<gateway_port>`，但需要 Gateway 支持 Anthropic 协议适配或上游支持该协议。 |
| 5 | 配置鉴权和用户身份 | 如 Gateway 配置 `GATEWAY_API_KEY`，第三方 Agent 需要携带 Bearer token。多用户场景需要携带 `x-user-id`；如果 Agent 不支持自定义 header，需要在 Gateway 接入层建立 API key 到 user_id 的映射。 |

### 2.2 轨迹采集与存储

| 步骤 | 操作 | 设计说明（含问题解答） |
|------|------|------------------------|
| 6 | 触发第三方 Agent 推理 | 用户在第三方 Agent 中正常发起任务。与原生 JiuwenSwarm 不同，第三方 Agent 不能直连 vLLM，否则不会产生 Gateway 轨迹。 |
| 7 | Gateway 代理推理 | Gateway `/v1/chat/completions` 接收请求，解析 `messages`、`tools`、`model`、`stream`、`x-user-id` 等信息；通过 `Forwarder` 清理内部或非上游字段后转发到 vLLM。 |
| 8 | Gateway 采集轨迹 | Gateway 在代理链路中记录 prompt、response、tool calls、usage、finish_reason、session/user/model 等信息，构造 online RL sample。若 `DISABLE_GATEWAY_TRAJECTORY_COLLECTION=true`，Gateway 只做转发，不写入训练样本。 |
| 9 | 延迟 Judge 和入库 | Gateway 复用 `GatewayTrajectoryRuntime` 的样本记录与延迟 Judge 能力，将样本写入 Redis 或 local store。轨迹状态初始为 `pending`，供 Scheduler 后续消费。 |
| 10 | 轨迹查询和管理 | 可通过 `/v1/rl/trajectories`、`/v1/rl/trajectories/stats`、`/v1/rl/trajectories/{trajectory_id}` 查询和管理轨迹。第三方样本应在 `source` 或 `mode` 中标记为 Gateway proxy 来源，便于和 Rail 样本区分。 |

### 2.3 训练触发与 LoRA 生成

| 步骤 | 操作 | 设计说明（含问题解答） |
|------|------|------------------------|
| 11 | 手动触发 RL 训练 | 调用 Gateway `POST /v1/training/tasks` 创建训练任务。请求带 `user_id` 时只训练指定用户；不带 `user_id` 时训练所有达到阈值的用户，每个用户单独生成 LoRA。 |
| 12 | 查询训练状态 | 调用 `GET /v1/training/tasks` 或 `GET /v1/training/tasks/{task_id}` 查询状态。状态包括 `pending`、`running`、`stopping`、`succeeded`、`failed`、`canceled`。 |
| 13 | 停止训练 | 调用 `PATCH /v1/training/tasks/{task_id}`，body 为 `{"status":"stopping"}`。Scheduler 轮询到停止状态后取消后续训练，并把未消费样本恢复为 pending。 |
| 14 | 训练样本选择 | 默认每次训练取 `threshold` 条样本。若设置 `drain_pending_on_train=true`，则一次 claim 当前 pending 样本；可通过 `max_samples_per_run` 限制数量，通过 `ppo_samples_per_step` 控制单次 run 内的 PPO step 粒度。 |
| 15 | LoRA 发布 | 训练完成后，PPO executor 将 LoRA 发布到 `LORA_REPO_ROOT`。可通过 `GET /v1/rl/lora` 或 `GET /v1/rl/lora/latest?model_id=<id>` 查询。 |

### 2.4 LoRA 热加载与 latest 推理路由

| 步骤 | 操作 | 设计说明（含问题解答） |
|------|------|------------------------|
| 16 | 设置默认 LoRA 策略 | 设置 `LORA_DEFAULT_POLICY=latest_by_user` 后，Gateway 在第三方 Agent 的代理请求中按当前 `x-user-id` 查询 latest LoRA。 |
| 17 | 确保 LoRA 已加载 | Gateway 调用上游 vLLM `/v1/load_lora_adapter` 加载 LoRA，`lora_name` 使用用户 ID。加载状态可通过 `POST /v1/rl/lora/effective` 验证。 |
| 18 | latest LoRA 推理 | Gateway 将本轮请求 body 的 `model` 改写为用户 LoRA 名称，并移除冲突的 `extra_body.lora_name`。第三方 Agent 无需感知 LoRA 路由细节。 |
| 19 | LoRA 下载 | 当前 `GET /v1/rl/lora/{lora_id}:download` 返回 LoRA 本地路径和元数据，不是文件流下载。如果外部系统要求真正下载文件，需要后续改为 `StreamingResponse` 或对象存储 signed URL。 |

---

## 3. 依赖需求（与设计对齐）

| 依赖项 | 状态 | 说明 |
|--------|------|------|
| 第三方 Agent 支持 Base URL 配置 | **必须满足** | Agent 需要能把推理请求导向 Gateway。OpenAI-compatible 客户端可直接指向 `/v1`；Anthropic-compatible 客户端需要协议适配。 |
| Gateway 代理路径采集轨迹 | **必须满足** | 这是第三方接入与原生 Rail 接入的核心差异。验收时必须确认 `/v1/chat/completions` 请求能生成 pending 样本，而不是只完成上游转发。 |
| Gateway 字段清理与协议适配 | **必须满足** | Gateway 需要在转发前移除内部控制字段，如 `session_id`、`session_done`、`turn_type`、`memory_scope`、`user_id`、`workspace_id`，避免污染 vLLM 请求。 |
| 稳定用户 ID | **必须满足** | 多用户训练、LoRA latest 查询和推理路由都依赖 `x-user-id`。单用户默认值只适合 POC 单租户调试。 |
| Gateway 轨迹管理能力 | **已支持** | Gateway 内置 `GatewayTrajectoryRuntime`，对外提供轨迹上传、查询、状态管理 API。第三方代理样本应复用同一 runtime。 |
| Redis 或本地文件 store | **已支持** | `TRAJECTORY_STORE_BACKEND=redis/local/auto`。Redis 适合分布式和大规模，本地 store 适合单机简化部署。 |
| 推理引擎支持 LoRA 热加载 | **受限** | 代码会调用 vLLM `/v1/load_lora_adapter`，但最终是否生效取决于 vLLM/硬件后端能力。 |
| verl/Ray PPO 训练依赖 | **必须满足** | OnlineTrainingScheduler 通过 PPO executor 复用 verl/Ray 训练链路。 |

---

## 4. 关键变动（与设计对齐）

| 组件 | 变动点 | 验证方式 |
|--------|--------|----------|
| **第三方 Agent** | 不注入 `RLOnlineRail`，只通过 Base URL 和 header 接入 Gateway。第三方 Agent 业务代码不需要改动。 | 第三方 Agent 正常完成推理；请求日志显示进入 Gateway `/v1/chat/completions`。 |
| **Gateway** | 第三方路径中 Gateway 是必经推理转发点，负责鉴权、用户识别、字段清理、上游转发、代理轨迹采集、latest LoRA 注入、轨迹和 LoRA 管理 API。 | 调用 `/v1/chat/completions` 后，上游 vLLM 正常响应，`/v1/gateway/stats` 和 `/v1/rl/trajectories` 可看到样本增长。 |
| **Trajectory Runtime** | 不再作为独立 TM 服务，而是 Gateway 内部模块；第三方代理样本和 Rail 上传样本写入同一 Redis/local store。 | `/v1/gateway/stats` 返回 store backend 和样本统计；第三方样本状态从 pending 变为 trained。 |
| **Scheduler** | 使用 task-driven 训练模型。存在 active task 时消费 task；不再把“达到阈值”描述为直接自动训练。 | 创建 task 后 Scheduler claim 样本并启动 PPO；停止 task 可取消后续训练。 |
| **Store** | 支持 `RedisTrajectoryStore` 和 `LocalTrajectoryStore`，并统一 training task store。 | 分别设置 `TRAJECTORY_STORE_BACKEND=redis` 或 `local` 跑通同一流程。 |
| **LoRA Repository** | 支持 LoRA 发布、latest 管理、可用性设置、删除、查询 effective LoRA。第三方代理请求由 Gateway 按用户 latest LoRA 改写 model。 | 训练后 `/v1/rl/lora` 可看到版本；`latest_by_user` 策略下 Gateway 日志显示 applied LoRA adapter。 |

当前 API 对照：

| 能力 | 当前接口 |
|------|----------|
| Gateway 健康检查 | `GET /health` |
| RL 健康检查 | `GET /v1/rl/health` |
| Gateway 统计 | `GET /v1/gateway/stats` |
| 第三方 Agent Chat 代理 | `POST /v1/chat/completions` |
| Rail 批量上传（原生 Agent） | `POST /v1/gateway/upload/batch` |
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

| 验收项 | 要求 |
|--------|------|
| 服务可启动 | Gateway、Scheduler、vLLM/Judge 正常启动；`GET /health`、`GET /v1/rl/health` 可访问。 |
| 第三方 Agent 可接入 | 第三方 Agent Base URL 指向 Gateway 后，正常完成一次推理请求；无需修改 Agent Loop。 |
| Gateway 代理采集生效 | `/v1/chat/completions` 请求后，Gateway store 中新增 pending 轨迹；样本包含 `user_id`、`session_id`、`model`、messages、response、tool_calls、usage 等核心字段。 |
| 训练可手动触发 | `POST /v1/training/tasks` 创建 task，Scheduler 消费 pending 样本并生成 LoRA。 |
| 训练可停止 | `PATCH /v1/training/tasks/{task_id}` 设置 `stopping` 后，Scheduler 能停止后续训练并恢复未消费样本。 |
| LoRA 可管理 | `/v1/rl/lora*` 可查询 latest、详情、可用性和删除状态。 |
| latest LoRA 代理生效 | 第三方 Agent 后续请求仍走 Gateway，Gateway 按 `x-user-id` 注入 latest LoRA；日志和响应中的 `rl_lora` 可观测。 |
| 直连对比明确 | 第三方 Agent 直连 vLLM 时只能验证上游模型能力，不应产生 Gateway 轨迹，也不能依赖 Gateway latest LoRA 注入。 |

---

# 测试用例细化：第三方 Agent 接入 Online RL（POC版）

## 1. 测试预备知识

### 1.1 关键限制回顾

- **Agent**：第三方 Agent 通过 Base URL 接入 Gateway；不修改 Agent Loop，不注入 `RLOnlineRail`。
- **轨迹采集**：第三方 Agent 必须通过 Gateway 代理推理，由 Gateway 在代理链路采集轨迹。
- **训练触发**：通过 `POST /v1/training/tasks` 手动触发，不依赖“达到阈值自动训练”。
- **服务形态**：Gateway 内置轨迹管理能力，没有独立 Trajectory Manager 进程。
- **验证方式**：POC 主要依赖 API、日志和 store 数据验证，无 UI 依赖。

### 1.2 环境配置

| 组件 | 配置项 | 值 |
| :--- | :--- | :--- |
| **第三方 Agent** | Base URL | OpenAI-compatible: `http://<gateway_ip>:<gateway_port>/v1`；Anthropic-compatible: `http://<gateway_ip>:<gateway_port>` |
| **第三方 Agent** | 用户身份 | 推荐携带 `x-user-id: <stable_user_id>`；不支持 header 时需部署侧映射 |
| **第三方 Agent** | API Key | Gateway 启用 `GATEWAY_API_KEY` 时携带 Bearer token |
| **Gateway** | `INFERENCE_URL`/`LLM_URL` | `http://<vllm_ip>:<vllm_port>` |
| **Gateway** | `TRAJECTORY_STORE_BACKEND` | `auto`、`redis` 或 `local` |
| **Gateway** | `LORA_DEFAULT_POLICY` | `disabled` 或 `latest_by_user` |
| **Gateway** | `LORA_REPO_ROOT` | LoRA 版本仓库目录 |
| **vLLM** | 启动参数 | 如需 LoRA 热加载，需要启用 vLLM LoRA 能力 |

---

## 2. 详细测试用例

### TC1: RL 服务手工拉起与存活

| 项目 | 内容 |
| :--- | :--- |
| **用例名称** | 验证 RL 相关服务手工启动正常 |
| **前置条件** | 服务器环境干净，模型和端口已准备。 |
| **测试步骤** | 1. 启动 vLLM/Judge。<br>2. 启动 Gateway。<br>3. 启动 OnlineTrainingScheduler。<br>4. 调用 `GET /health`、`GET /v1/rl/health`。 |
| **预期结果** | Gateway 和 Scheduler 均正常运行，Gateway health 返回 ready/ok 信息。 |
| **验收标准** | 服务手工拉起成功，无崩溃；不要求独立 Trajectory Manager 进程。 |

### TC2: 第三方 Agent 接入配置与连通性

| 项目 | 内容 |
| :--- | :--- |
| **用例名称** | 验证第三方 Agent 通过 Base URL 接入 RL Gateway |
| **前置条件** | TC1 执行成功。 |
| **测试步骤** | 1. 将第三方 Agent Base URL 设置为 Gateway。<br>2. 配置 API Key 和 `x-user-id`。<br>3. 发起一次简单任务，例如 `Explain this code: print("hello")`。<br>4. 观察 Agent 输出和 Gateway 日志。 |
| **预期结果** | Agent 正常输出结果；Gateway 日志显示请求进入 `/v1/chat/completions` 并转发到上游 vLLM。 |
| **验收标准** | 第三方 Agent 通过 Gateway 代理正常推理。 |

### TC3: Gateway 字段剥离与轨迹采集验证

| 项目 | 内容 |
| :--- | :--- |
| **用例名称** | 验证 Gateway 剥离扩展字段并生成 online RL sample |
| **前置条件** | TC2 执行成功。 |
| **测试步骤** | 1. 发送包含 messages/tools 的第三方 Agent 请求。<br>2. 查看 Gateway 日志，确认代理转发成功。<br>3. 调用 `GET /v1/gateway/stats`。<br>4. 调用 `GET /v1/rl/trajectories?user_id=<stable_user_id>&limit=10`。 |
| **预期结果** | Gateway stats 中样本数增长；轨迹列表出现第三方代理来源样本，且包含 prompt、response、tool_calls、usage、user_id、model 等字段。 |
| **验收标准** | 满足“不改造 Agent Loop”前提下的轨迹采集。 |

### TC4: 训练触发与状态查询（无 UI 场景）

| 项目 | 内容 |
| :--- | :--- |
| **用例名称** | 验证无界面情况下通过 API 触发并查询训练 |
| **前置条件** | TC3 已产生足够 pending 轨迹。 |
| **测试步骤** | 1. 调用 `POST /v1/training/tasks`，body 可包含 `{"user_id":"<stable_user_id>","threshold":4}`。<br>2. 调用 `GET /v1/training/tasks`。<br>3. 调用 `GET /v1/training/tasks/{task_id}`。<br>4. 查看 Scheduler 日志。 |
| **预期结果** | 创建 task 返回 `task_id`；状态从 `pending/running` 变化到 `succeeded` 或可解释的失败状态；Scheduler 日志显示 claim 样本和训练流程。 |
| **验收标准** | 训练在后台启动，状态可通过 API 追踪。 |

### TC5: 停止训练任务

| 项目 | 内容 |
| :--- | :--- |
| **用例名称** | 验证训练任务可手动停止 |
| **前置条件** | TC4 中存在运行中 task。 |
| **测试步骤** | 1. 调用 `PATCH /v1/training/tasks/{task_id}`，body 为 `{"status":"stopping"}`。<br>2. 查询 task 状态。<br>3. 查看未消费轨迹状态。 |
| **预期结果** | task 进入 `stopping/canceled` 或最终停止状态；未消费样本恢复为 pending。 |
| **验收标准** | 训练控制不依赖 UI，可通过 API 停止。 |

### TC6: LoRA 生成、查询与元数据下载

| 项目 | 内容 |
| :--- | :--- |
| **用例名称** | 验证 LoRA 文件生命周期 |
| **前置条件** | TC4 训练完成。 |
| **测试步骤** | 1. 调用 `GET /v1/rl/lora?model_id=<stable_user_id>`。<br>2. 调用 `GET /v1/rl/lora/latest?model_id=<stable_user_id>`。<br>3. 调用 `GET /v1/rl/lora/{lora_id}:download`。 |
| **预期结果** | LoRA 列表中有新版本；latest 可查询；download 接口返回本地路径和元数据。 |
| **验收标准** | LoRA 产物可查询、可定位。 |

### TC7: latest LoRA 代理注入验证

| 项目 | 内容 |
| :--- | :--- |
| **用例名称** | 验证第三方 Agent 后续请求自动使用 latest LoRA |
| **前置条件** | TC6 已生成 latest LoRA，Gateway 设置 `LORA_DEFAULT_POLICY=latest_by_user`。 |
| **测试步骤** | 1. 第三方 Agent 继续发送同一用户请求。<br>2. 查看 Gateway 日志。<br>3. 查看响应中的 `rl_lora` 字段或上游请求 model。 |
| **预期结果** | Gateway 日志显示 applied LoRA adapter；本轮请求的 model 被改写为用户 LoRA 名称。 |
| **验收标准** | 第三方 Agent 无需感知 LoRA，Gateway 代理链路完成 latest 注入。 |

---

## 3. 异常与边界测试

### TC8: 直连 vLLM 对比测试

| 项目 | 内容 |
| :--- | :--- |
| **测试步骤** | 1. 将第三方 Agent Base URL 改为直连 vLLM。<br>2. 发送相同请求。<br>3. 对比通过 Gateway 转发的结果和 Gateway 轨迹统计。 |
| **预期结果** | 推理结果应基本一致；直连 vLLM 不会产生 Gateway 轨迹，也不会触发 Gateway latest LoRA 注入。 |

### TC9: 重复触发训练防护

| 项目 | 内容 |
| :--- | :--- |
| **测试步骤** | 训练 A 运行中，再次调用 `POST /v1/training/tasks`。 |
| **预期结果** | 当前单 active task 模式下返回 409 Conflict 或明确的 active task 冲突信息。 |

### TC10: 缺失 user_id

| 项目 | 内容 |
| :--- | :--- |
| **测试步骤** | 第三方 Agent 请求不携带 `x-user-id`，并关闭单用户默认回退。 |
| **预期结果** | Gateway 拒绝采样或拒绝请求，提示缺少稳定用户 ID。 |

### TC11: 关闭 Gateway 代理采集

| 项目 | 内容 |
| :--- | :--- |
| **测试步骤** | 设置 `DISABLE_GATEWAY_TRAJECTORY_COLLECTION=true` 后重启 Gateway，再通过第三方 Agent 发起请求。 |
| **预期结果** | 推理仍可转发成功，但 `/v1/rl/trajectories` 不新增第三方代理样本。 |

---

## 4. 验收总结对照表

| 需求目标 | 对应 TC | 验证结论 |
| :--- | :--- | :--- |
| **第三方 Agent 无侵入接入** | TC2 | 手工配置 Base URL 和鉴权即可接入，无需修改 Agent Loop。 |
| **Gateway 代理采集轨迹** | TC3 | 第三方 Agent 请求经 Gateway 转发，并生成 pending 轨迹。 |
| **训练可控（无 UI）** | TC4、TC5、TC9 | 通过 training task API 触发、查询、停止，并具备重复触发保护。 |
| **LoRA 管理** | TC6 | LoRA 版本可查，latest 可查，元数据可获取。 |
| **latest LoRA 推理生效** | TC7 | Gateway 代理链路按用户 latest LoRA 自动改写 model。 |
| **直连差异清晰** | TC8 | 直连 vLLM 只验证模型推理，不采集 Gateway 轨迹。 |

---

## 5. 与初版需求的差异说明

| 初版描述 | 本期实际 | 差异原因 |
| :--- | :--- | :--- |
| 仅支持 Claude Code | 扩展为“可配置 Base URL 的第三方 Agent”；OpenAI-compatible 优先，Claude Code 需要 Anthropic 协议适配 | 当前 Gateway 主代理入口是 OpenAI-compatible `/v1/chat/completions`，第三方接入不应绑定单一 CLI。 |
| 独立 Trajectory Manager | Gateway 内置 `GatewayTrajectoryRuntime` | 当前代码已把轨迹接收、查询、状态管理和 store 写入统一到 Gateway。 |
| `/api/v1/train/start`、`/api/v1/train/status` | `POST /v1/training/tasks`、`GET /v1/training/tasks/{task_id}` | 当前训练控制已统一为 task API。 |
| `/api/v1/lora/list`、`/api/v1/lora/download?version=latest` | `/v1/rl/lora*` 系列接口 | 当前 LoRA 管理支持 latest、详情、可用性、删除和 metadata download。 |
| Gateway 只做字段剥离和转发 | Gateway 是第三方接入的采集入口和 LoRA 注入入口 | 第三方 Agent 不能注入本地 Rail，所以 Gateway 代理链路必须承担轨迹采集。 |
| 客户界面感知 | 无界面，API 查询 | POC 阶段简化前端开发。 |
| 守护进程看护 | 手工拉起 | 运维体系尚未集成。 |
