# Codex external session checkpoint 与冷续接

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-07-21 |
| 范围 | `runtime/metadata.py`、`spawn/external_cli_spawn.py`、`external/cli_agent/spawn.py`、`external/cli_agent/codex/runtime.py` |
| 测试基线 | 针对性单测 40 passed |
| Refs | S_18，F_61，F_66 |

## 背景

F_66 已经让一个活着的 `CodexSdkRuntime` 在多轮任务中复用同一 Codex thread，
也支持外部传入 `thread_id` 后调用 `thread_resume()`。但当团队 pause/stop 后发生冷重建，
spawn 路径没有地方读写该 id，新 runtime 只能再建一条 thread。

## 决策

1. **运行态归 team-session checkpoint 所有**
   - 存储路径是
     `state["teams"][team_name]["external_sessions"][member_name]`。
   - 每条记录包含 `backend` 和 `external_session_id`；Codex 对后者的解释是
     `thread.id`。
   - 不把运行态 id 写入 `TeamAgentSpec.external_cli_agents`，避免静态蓝图污染和跨
     Jiuwen session 泄漏。

2. **三级隔离**
   - team 由 per-team namespace 隔离。
   - member 由 `external_sessions[member_name]` 隔离。
   - backend 在读取时必须精确匹配，防止成员改用 Claude/其他 backend 后误用
     Codex thread id。

3. **新 thread 立即回写**
   - `CodexSdkRuntime` 在 `thread_start()` 取得 id 后调用异步 `on_thread_id`。
   - spawn 层将 id 合并进捕获的 leader team session，随后立即
     `flush_checkpoint()`，不等到整轮结束。
   - 回调仅能修改 spawn 时捕获且当前仍活跃的 session 对象；若团队已切换
     session，拒绝旧 runtime 回写。

4. **冷续接是显式语义**
   - 仅 `resume_external_backend=True` 时读 checkpoint 并向 Codex runtime 传
     `thread_id`。
   - 普通新 spawn 不使用旧 id，仍新建 thread。
   - strict resume 下旧 checkpoint 缺失、损坏或空值直接报错，不允许新建替代 thread。
   - `thread_resume()` 抛错或返回不同 id 同样报错；恢复路径不会调用 `thread_start()`。

## 数据流

```text
Codex thread_start
  -> CodexSdkRuntime.on_thread_id(thread.id)
  -> external_cli_spawn callback
  -> merge_external_session_id(...)
  -> team_session.flush_checkpoint()

cold rebuild
  -> read_external_session_id(team, member, backend)
  -> build_cli_runtime(external_session_id=...)
  -> CodexSdkRuntime(thread_id=...)
  -> AsyncCodex.thread_resume(thread_id)
```

## 拒绝的方案

- **存到 `TeamAgentSpec`**：spec 是静态启动配置，thread id 是某个 Jiuwen session 的运行态。
- **仅存在 runtime 内存**：只能保证同进程多 turn，无法穿越 stop/start。
- **按 backend 全局存一个 id**：会让 `codex-1` 和 `codex-2` 共享模型上下文。
- **无条件自动 resume**：会让一次真正的新 spawn 意外继承旧工作状态。

## 验证

- metadata 单测验证 team/member/backend 隔离、非法 checkpoint 容错、其他 team bucket 字段保留。
- fake Codex SDK 单测验证新 thread id 仅回调一次，已恢复 id 不重复改写。
- fake Codex SDK 单测验证 resume 失败时不启动 replacement thread。
- spawn 单测验证冷重建读取指定成员 id，新 id 写回后立即 flush。
- 针对性单测：40 passed。

## 剩余验收

- 真实 Codex SDK + team MCP 环境下跑 persistent team pause/stop/start E2E，确认日志中
  恢复后的 thread id 与首次启动一致。
- Codex SDK 仍只支持本地 app-server subprocess，SSH 不在本特性范围。
