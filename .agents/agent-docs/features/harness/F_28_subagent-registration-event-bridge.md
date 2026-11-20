# agent.register 启动 SubAgent 登记桥接

## 元信息

| 项 | 值 |
|---|---|
| 类型 | feature |
| 日期 | 2026-11-12 |
| 范围 | `DeepAgentConfig`、factory、`DeepAgent`、`DeepAgentSpec` |
| Core 契约 | [F_10 `agent.register`](../../core/events/docs/features/F_10_ev10-agent-register-ops.md) |
| 关联特性 | [F_27 `tool.register`](F_27_tool-registration-event-bridge.md) |

## 设计决策

`SubAgentConfig` 是默认子 Agent 的 canonical 配置。`DeepAgentConfig.subagent_registration_mode`
控制启动配置的登记路径：

| 模式 | SubAgentConfig | bus 要求 |
|---|---|---|
| `legacy`（默认） | 直接进入静态 `deep_config.subagents` | 无 |
| `event` | 进入 FIFO 队列，经 `agent.register` 事件登记 | 必须有 live `ExtensionEventBus` |

`DeepAgentSpec.event_subagent_types` 是 provider 类型边界：未选中的配置继续走 legacy，
从而允许宿主在滚动升级期间混合两种路径。

## 调用链

```text
DeepAgentConfig / DeepAgentSpec
  → event SubAgentConfig FIFO
  → parent rails init 完成
  → Event("agent.register", AgentRegisterInput.from_subagent_config(config))
  → AgentRegisterTriggerContext(agent=DeepAgent)
  → USER → BUILTIN → CORE
  → DeepAgent.load_subagent_config()
  → Extension Binder：subagents、AgentCard、SubagentRail
```

启动阶段没有用户 `SessionContext`，因此使用只对 CORE 可见的
`AgentRegisterTriggerContext`；公开 `ops.agent.register` 仍然要求会话。父 Rail 全部初始化后
才 flush，确保宿主自定义 `SubagentRail`（例如 BrowserTaskPromptRail）被 Binder 复用，且新
类型的工具描述和 allowed types 在下一次模型请求前刷新。

## 失败与兼容

- 队列按顺序消费，事件成功后才移除队首；USER / BUILTIN / CORE 或 Binder 失败不会静默回退到 legacy。
- event 模式没有 bus 时明确失败；宿主只有在能力探测通过且 bus 已连接时才应选择 event。
- 老 agent-core 不具备新字段、触发上下文或队列方法时，JiuwenSwarm 不传事件专用参数，保持 legacy。
- 注册阶段只绑定配置和父 Agent 的发现面，不创建子 Agent 实例；tools、mcps、rails、model、
  workspace、sys_operation 和 factory 字段在实际 spawn 时由 `SubAgentConfig` 生效。

## 验证

`test_agent_register_ops.py` 覆盖 canonical 配置、可变容器快照、无 session 的内部上下文和
DeepAgent 队列；旧 card + prompt、USER 短路、deny 和未绑定 runtime 路径继续回归。
