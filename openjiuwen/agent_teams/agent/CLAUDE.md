# Agent Teams Agent

`TeamAgent` 运行时主骨架。`TeamAgent` 是单一实现——同一个类同时承担 leader 和 teammate 两个角色，通过 `TeamRole` 切换行为，内部组合一个 `DeepAgent` 跑 LLM。本目录把 TeamAgent 的内部结构按"四象限"+ 协作层组织。

## 四象限分解

`TeamAgent` 的字段被刻意分到四个文件，避免一个类塞 100+ 字段：

| 象限 | 文件 | 含义 |
|---|---|---|
| **静态数据** | `blueprint.py` | `TeamAgentBlueprint`，frozen dataclass。构造时确定的配置，整个生命周期不变（spec、role、member_name、persona 等） |
| **运行时可变状态** | `state.py` | `TeamAgentState`。**只放跨 operator 的字段**——operator 内部状态（spawn_manager 的 spawned_handles、coordination 的 subscribed_topics 等）留在 operator 自己里 |
| **每实例资源** | `resources.py` | `PrivateAgentResources`。这个 TeamAgent 独占的资源（DeepAgent、worktree manager、memory manager 等）。每个 member 一份 |
| **每进程基础设施** | `infra.py` | `TeamInfra`。**进程内**所有 member 共享的资源（messager、db、team_backend 等）。leader 和 teammate 在不同进程，所以 "共享" 是 per-process 而不是跨实例单例 |

新增字段前先想：跨 operator 吗？跨实例吗？跨进程吗？放错象限就会出现"这个状态我看到 3 处都在改"的代码烂味。

## Manager / 协作层

| 文件 | 类 | 职责 |
|---|---|---|
| `team_agent.py` | `TeamAgent(BaseAgent)` | 唯一对外类。leader / teammate 都是它，行为由 `blueprint.role` 切 |
| `agent_configurator.py` | `AgentConfigurator` | DeepAgent 装配，挂 prompts/ 与 rails/ 子模块（spawn 时复用 `models/` 的 allocator 回调）；`_resolve_team_mode` 在这里 |
| `member.py` | `TeamMember` | 成员状态机封装 |
| `member_factory.py` | `create_member_handle(...)` | 集中 TeamMember 构造，leader / teammate 路径共用一份实现 |
| `payload.py` | `SpawnPayloadBuilder` | spawn teammate 时的**跨进程 wire 格式**。输出键是 `TeamAgent.from_spawn_payload` 的公共契约——改这里的字段要同步改子进程入口 |
| `spawn_manager.py` | `SpawnManager` | teammate 进程生命周期：拉起 / 心跳 / 重启 / 取消 |
| `recovery_manager.py` | `RecoveryManager` | 团队级容错：成员崩溃恢复、状态对齐 |
| `session_manager.py` | `SessionManager` | session checkpoint 读写、生命周期 |
| `stream_controller.py` | `StreamController` | DeepAgent 的 stream 队列、round 状态、pending input、interrupt 收纳；自动给 chunk 升级为 `TeamOutputSchema` 并通过 `add_chunk_observer` 对外 fan-out |

## coordination/ — 唤醒循环

事件驱动的 wake-up 层：`EventBus` 收事件 → `EventDispatcher` 粗筛 → `AsyncCallbackFramework` 分发到 6 个场景 handler（lifecycle / member / message / task_board / stale_task / team_completion）。**自身不做决策**，handler 走三类 narrow protocol 触发行为：`AgentRoundController` 驱动 TeamHarness（round 控制），`TeamLifecycleController` 触发 TeamAgent 级生命周期（shutdown），`PollController` 直达 EventBus（poll 暂停/恢复）。详见 [`coordination/CLAUDE.md`](coordination/CLAUDE.md)。

## 跨文件协作的几个关键点

- **同一 team 的 leader 和 teammate 不在同一进程**：`infra.py` 的 "per-process" 语义就来自这里。要让两边都看到的状态走 db / messager，不要走对象引用。
- **`payload.py` 的 wire 格式是公共契约**：`build_spawn_payload(...)` 的所有输出键 = `TeamAgent.from_spawn_payload` 的所有读取键，改一边必须改另一边。
- **stream_controller 不直接驱动 DeepAgent**：它管理 stream queue 和 round 状态；驱动 DeepAgent 的入口是 `team_agent.py` 的 `start_agent / steer / follow_up / deliver_input`。
- **stream chunk 跨成员 fan-out**：每个 `StreamController` 在 chunk 进 queue 之前用 `_tag_chunk` 把 chunk 升级为 `TeamOutputSchema`（带 `source_member` + `role`），再 fan-out 给注册在 `_chunk_observers` 上的回调。inprocess 模式下，`SpawnManager._wire_inprocess_chunk_forward` 会把每个 spawn 出来的 teammate `StreamController` 上挂一个 forward observer——把 chunk 转投到 leader 的 `stream_queue`，让 `Runner.run_agent_team_streaming` 对外流出全成员 chunk。observer 抛错自动 detach 不阻塞主流；teardown 时由 `cleanup_teammate` 反注册。subprocess 模式不挂 observer（不同进程不共享对象），扩展点已留好（messager-driven observer）。

## 跟其他子目录的边界

- 真正干活的 LLM 在 `harness/deep_agent.py`，本目录只组装 + 调度。
- 跨 team 的对象池 / 派发 / 并发门禁在 `runtime/`（leader 进 pool 的唯一公共路径是 spec → `manager.activate`）。
- 三视角交互（GodView / Operator / HumanAgent）通过 `interaction/` 进 coordination；mention 解析在 `interaction/router.py` 不在 dispatcher。
