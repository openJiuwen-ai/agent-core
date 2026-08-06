# 团队 Fork 上下文继承

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-08-06 |
| 范围 | `agent_teams/fork.py`, `fork_compact.py`, `tools/tool_member.py`, `tools/team.py`, `agent/team_agent.py`, `spawn/inprocess_spawn.py`, `tools/tool_factory.py`, `tools/tool_permissions.py`, `tools/locales/*`, `tools/locales/descs/*/checkpoint.md`, `tools/locales/descs/*/spawn_teammate.md` |
| 测试基线 | `tests/unit_tests/agent_teams/test_fork.py` — 25/25 通过 |
| Refs | — |

## 背景

团队中多个并行的执行者需要对同一个代码仓库有共同的理解（如基类接口、项目架构）。
每个执行者独立启动时需要重新读取文件、搜索代码、分析结构——重复的 IO 和 token 消耗。
需要一个机制让已经理解代码的代理将上下文直接继承给新成员，省去重复探索。

## 决策

### 1. ForkContext — 上下文载体
- `ForkContext.from_agent(agent, checkpoint=N)` 捕获代理当前全部对话历史
- `checkpoint=N` 时截断到消息索引 N 之前；`checkpoint=None` 时返回全量
- 自动剥离所有 `SystemMessage`，防止源代理角色身份泄漏到目标

### 2. CheckpointTool — 命名快照
- `checkpoint(name="code-ready")` 保存当前 `len(messages)` 到共享字典
- 存储的是位置索引，不绑定语义方向 —— 由 `spawn_teammate` 的 `fork`/`compact` 参数决定使用方式
- 快照字典通过 `inprocess_spawn` 共享给进程内所有成员，以 leader 的 `_named_checkpoints` 为唯一切换依据

### 3. Fork Compaction — 上下文压缩
- `compact_context(agent, split_at=N)` 在 fork 注入后压缩旧消息
- `checkpoint` 位置之前的消息通过一次模型调用压缩为摘要，之后的全量保留
- 仅当 `spawn_teammate(fork="ckpt", compact=true)` 时触发
- 压缩在 `inprocess_spawn` 中、`Runner.run_agent_team` 之前完成，对子代理透明

### 4. Fork 语义
仅保留三个有实用价值的组合：

| fork | compact | 行为 |
|------|---------|------|
| `true` | — | 全量注入 |
| `"ckpt"` | false | 截断到 ckpt 之前 |
| `"ckpt"` | true | ckpt 为分界：之前压缩为摘要，之后全量保留 |

### 5. 触发时机
fork 决策在 `_on_teammate_created` 中执行——`spawn_teammate` 工具仅标记意图，
实际上下文注入发生在成员初始化完成的异步时刻。

### 6. 校验
对非法组合在日志中打 warning 并降级处理：
- `compact=true` 未配合 checkpoint fork → 忽略 compact
- checkpoint 不存在 → 回退为全量

## 拒绝的方案

### since_checkpoint 独立模式
- 仅取 checkpoint 之后的消息（增量）—— 增量缺少文件原文作依据，信息不完整
- 与 compact 组合时语义重叠 —— compact 已通过 checkpoint 决定分界
- 已砍掉，从 API 中移除

### fork=true + compact=true
- 全量上下文无需主动压缩，超出 token 限制时系统内置 `FullCompactProcessor` 自动兜底
- 保留会增加 API 复杂度但无实际收益

### fork="ckpt" 时 compact 截断后再压缩
- 截断到 ckpt 前再压缩全部 —— 语义不清，不如直接用 ckpt + compact（全量注入，ckpt 分界）
- compact 语义收敛为"分界压缩"，只在 checkpoint fork 时生效

### 第三方 CLI 成员 fork
- 外部 CLI（claude/codex）的对话存在于外部进程中，无程序化访问入口
- 无 DeepAgent / ContextEngine，无法取上下文或注入

## 验证

四轮 live 测试覆盖全部三种模式：

| 模式 | 测试场景 | 验证特征 |
|------|---------|---------|
| Live fork | `fork=true` | 全量注入，`compact_split=None` |
| Checkpoint fork | `fork="ckpt"` | 截断到 ckpt 位置之前 |
| Compaction fork | `fork="ckpt" compact=true` | 日志含 `compact_context: compressing X, keeping Y` → `done — Z messages` |

## 已知遗留

- **Checkpoint 非持久化**：存储在 leader 的内存字典中，进程重启后丢失。后续可接入 session state 持久化
- **subprocess spawn fork**：跨进程 fork 的 payload 未接线，当前仅支持 `spawn_mode="inprocess"`
