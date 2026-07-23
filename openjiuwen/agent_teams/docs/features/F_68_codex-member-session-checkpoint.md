# Codex resume id 迁移到成员 AgentSession

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-07-23 |
| 范围 | `spawn/external_cli_spawn.py`、`external/cli_agent/spawn.py`、`external/cli_agent/codex/runtime.py`、`runtime/metadata.py` |
| 测试基线 | external + runtime metadata 单测 151 passed；ruff / diff check 通过 |
| Refs | S_18，F_66，F_67 |

## 背景

F_67 让 Codex SDK 成员能跨 team pause/stop/start 恢复同一 thread，但把
`external_session_id` 写入 leader 的 per-team checkpoint namespace。该映射虽然按
team/member/backend 隔离，所有权却仍落在团队聚合状态上；实际 owning entity 是外层
`TeamAgent` 表示的那个成员。

`MemberRuntime.start(team_session=...)` 已经拿到当前 `AgentTeamSession`，
`AgentTeamSession.create_agent_session(agent_id=...)` 又能用相同 Jiuwen
`session_id` 和稳定成员 identity 派生、恢复成员 checkpoint，因此不需要 spawn 层继续代理
读写 Codex thread id。

## 数据结构

每个 Codex 成员以稳定 `member_agent_id = f"{team_name}_{member_name}"` 创建成员
`AgentSession`。该 session 的状态只保存 backend-native 恢复指针：

```python
{
    "external_runtime": {
        "backend": "codex",
        "external_session_id": "<Codex thread.id>",
    }
}
```

完整模型上下文仍由 Codex thread 持有；Jiuwen checkpoint 只保存恢复主键。

## 决策

1. **成员 session 持有 resume id**
   - `external_cli_spawn` 只生成稳定 `AgentCard.id` 并把它作为
     `member_agent_id` 传给 runtime 构建层。
   - `CodexSdkRuntime.start(team_session=...)` 派生并 `pre_run()` 成员
     `AgentSession`，不再依赖 leader session 回调。

2. **Runtime 内完成读写闭环**
   - 首次 `thread_start()` 后立即 `update_state()` + `commit()`，不等待 round 结束。
   - `stop()` 对成员 session 执行幂等 `post_run()`，完成最后一次 checkpoint 提交。
   - runtime 的 `thread.id` 持久化幂等判断以实际 id 为准，不用布尔值掩盖 ID 变化。

3. **严格恢复保持不变**
   - 仅 `resume_external_backend=True` 时要求成员 checkpoint 中存在合法、backend
     匹配的 id。
   - 缺失/损坏、`thread_resume()` 失败或返回不同 id 均直接报错；禁止创建替代 thread。
   - 普通新 spawn 忽略旧 checkpoint 中的 resume id，创建新 thread 后覆盖当前映射。

4. **team namespace 回归团队状态**
   - 删除 `runtime.metadata` 的 `external_sessions` 字段与读写 helper。
   - `TeamAgentSpec` 仍只保存静态启动配置，不保存任何 backend-native session id。

## 数据流

```text
external_cli_spawn
  -> stable member_agent_id
  -> build CodexSdkRuntime (thread 尚未启动)
  -> CodexSdkRuntime.start(team_session)
  -> team_session.create_agent_session(member_agent_id)
  -> member_session.pre_run()
  -> read state["external_runtime"]
  -> thread_start() or strict thread_resume()
  -> member_session.update_state(...) + commit()
```

## 拒绝的方案

- **继续放在 leader team-session namespace**：功能可用，但成员私有运行态归属错误，
  spawn 层还要承担 SDK resume id 的代理读写。
- **把完整 `AgentSession` 或 thread id 放进 Card/Spec**：Card/Spec 是可序列化静态身份与
  装配蓝图，不得持有运行时 Session 或特定 Jiuwen session 的恢复数据。
- **只保留 Runtime 内存**：无法穿越成员 runtime 销毁和冷重建。
- **从 Codex 全局列表猜 thread**：无法可靠绑定 team/session/member，可能恢复错误上下文。

## 验证

- fake SDK 验证首次 thread id 写入成员 session，恢复读取同一 id。
- fake member session 验证 backend/空 id 损坏状态被严格拒绝。
- spawn 单测验证只透传稳定 `member_agent_id`，不再读写 leader team checkpoint。
- metadata 单测删除已经失效的 team-level external session 契约。

## 已知遗留

- 默认 `InMemoryCheckpointer` 只支持同进程恢复；跨进程恢复要求宿主配置持久化
  Checkpointer。本特性不改变全局 checkpointer 选择策略。
- Codex SDK 仍没有 thread 删除 API接线；clean team 只清理由 Jiuwen 所有的映射和资源。
