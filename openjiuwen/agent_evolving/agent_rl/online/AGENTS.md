# Agent Evolving Online RL

在线样本采集、Gateway 转发、judge、存储、训练调度与服务装配。端口、核心编排和具体 backend
分层维护，避免生产路由直接依赖训练实现。

## 模块地图

| 路径 | 职责 |
|---|---|
| `abstract/` | LoRA、Rail、rollouter、store、trainer、workflow 端口 |
| `core/` | factory、Rail、scheduler、uploader、training process 与运行时装配 |
| `backends/rl/` | RL collector、converter、store、trainer 和 Rail 实现 |
| `backends/sft/` | SFT sample、converter、store、trainer、rollouter 实现 |
| `backends/rollouter/` | Docker 和任务 rollout backend |
| `gateway/` | OpenAI/Anthropic 请求入口、上游转发、采集和 trajectory persistence |
| `judge/` | evaluator、scorer 和 judge server |
| `scheduler/` | 在线训练 scheduler、plugin 与 PPO executor |
| `launcher/` | CLI、workspace 和多服务启动编排 |
| `inference/` | 推理侧版本通知 |

根部的 `lora_runtime.py`、`training_process.py` 是兼容现有导入路径的 wrapper，不在其中
积累第二套逻辑；`task_orchestrator.py` 负责单个外部 Agent 在线 RL task attempt 的 collection、
environment、evaluation、reward 和 cleanup 顺序。

## 分层铁律

- `abstract/` 只定义端口和共享值，不导入 `core/` 或具体 backend。
- `core/` 依赖 abstract 并负责编排；backend 实现端口。不要让 core 根据 backend 私有字段
  分支，也不要让 backend 反向控制全局 scheduler 生命周期。
- Gateway route 只处理协议、校验和请求上下文；上游转发归 `gateway/upstream/`，采集与持久化
  分别归 `gateway/collector/` 和 `gateway/trajectory/`，judge 和训练不能复制进 route handler。
- 生产入口是 uvicorn factory
  `openjiuwen.agent_evolving.agent_rl.online.gateway.app.proxy:create_app`。保持 app 构造可重复，
  不在模块 import 时创建网络连接、线程或后台 task。

## 样本与训练生命周期

```text
client request → upstream response → trajectory/sample normalization
→ persistence → judge/reward → training scheduler → trainer
→ LoRA artifact → inference notification
```

- 请求成功、样本落库、judge 完成、训练完成和模型发布是独立状态；每一步使用可审计的状态或
  ID，不能因前一步成功推断后一步完成。
- Gateway 对外响应不能等待非必要训练工作；后台失败应保留样本和错误事实，并按组件契约重试。
- canonical message/tool-call/reward 转换集中在 converter 或 message utilities；OpenAI 与
  Anthropic route 只做协议适配，不能产生语义不同的训练样本。
- Redis/local/in-memory store 的一致性由 abstract contract 约束。测试替代实现不能泄漏为
  生产默认，也不能依赖进程内对象身份表达持久状态。
- scheduler 负责样本门槛、active training task 和状态推进；trainer 执行训练，notifier 负责
  将产物加载到推理服务。修改停止、重启或通知重试时，必须覆盖样本状态和资源清理。

## 配置与安全

- 配置只在各自的 config、factory 或 launcher 边界解析；route 和 backend 不重复读取环境变量。
- API key、上游凭据和 Redis 连接信息不得进入日志、样本或异常正文。转发时只传协议需要的
  headers，并保持超时、断连和流式取消语义。
- launcher 创建的 workspace、端口和子进程都要有明确 owner 与清理路径；单元测试不得启动
  用户环境中的真实服务。

## 修改与测试

- Gateway：运行 `uv run pytest tests/unit_tests/agent_evolving/agent_rl/online/gateway -q`。
- core/backend/scheduler：运行对应 online unit test 子目录，覆盖空批次、重复事件、失败重试、
  重启恢复和并发门禁。
- 协议变化同时覆盖 OpenAI/Anthropic 非流式、流式、错误映射和取消；涉及 Redis、容器、真实
  judge/trainer 或 LoRA 发布时，将 unit evidence 与 integration/system evidence 分开报告。
