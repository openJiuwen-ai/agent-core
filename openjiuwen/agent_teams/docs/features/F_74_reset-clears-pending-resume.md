# F_74: reset/release clears pending_resume (and the accessor fix that made it work)

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-19 |
| 范围 | `openjiuwen/agent_teams/runtime/metadata.py`、`openjiuwen/agent_teams/runtime/manager.py`、`openjiuwen/core/runner/team_runner.py`、`openjiuwen/agent_teams/kv_cache/kv_cache_team_actions.py`、`tests/unit_tests/agent_teams/runtime/test_metadata.py`、`tests/unit_tests/agent_teams/runtime/test_reset_session.py` |
| 测试基线 | `test_metadata.py` + `test_reset_session.py` 全绿；`runtime/` + `test_persistent_team.py` + `test_runner_team_runtime.py` 164 全绿；agent_teams 全量 2297 passed（2 个预存环境失败与本改动无关：Windows 路径分隔符 / DB 写锁时序 flake） |
| Refs | #<issue>（须向用户确认；无则删此行） |

## 背景

relay-claw 宿主里跑 team：query1=扫雷，query2=贪吃蛇，两不同任务、用户未说续跑。
按预期 query2 应清看板全新开始；实际 reset 确被调用并删了 2 行任务，但成员随后
打印 `resuming the paused round from the session checkpoint`——上一轮（扫雷）的
暂停回合被续跑了。

## 决策

根因有两层，第二层是实现期实测才发现、修正了原方案前提的：

1. **表层**：`manager.reset_session` / `release_session` 清任务/落表但不清团队级
   `pending_resume` 标记。reset 是"cold-recover 之前、语义为全新开始"的唯一入口
   （宿主 reset RPC 经 jiuwenswarm 直达这里），理应清标记；它没做，所以下次
   cold-recover 读到标记就续跑。修复：在 `reset_session`（清任务行后）/ 
   `release_session`（drop 表后）用 `clear_pending_resume` 清标记，best-effort，
   保留 checkpoint bucket（COLD_RECOVER，team memory/历史不丢）。

2. **深层（关键）**：canonical accessor `clear_pending_resume` **本身在真 `Session`
   上删不掉标记**。真 `Session.update_state` 经 `StateCollection`→`update_dict`
   做深合并，深合并不删 key，仅当入参 value 为 `None` 时才走 `delete_by_key`。
   而 `clear_pending_resume` 原写法是"读 teams、`bucket.pop`、整张 map 写回"——
   写回的 map 缺该 key，深合并却把旧 `pending_resume` 保留。`merge_pending_resume`
   （加 key）能持久化、`clear_pending_resume`（删 key）删不掉。`test_metadata`
   的 `_StubSession` 用浅 `dict.update` 掩盖了此 bug（假绿）。**因此 kernel 的
   `_clear_pending_resume` 在生产里也一直失效——标记从未被真正清过**，这才是
   续跑被反复触发的更深层根因。

修复顺序：先修 accessor（Decision 0），再让 reset/release/kernel 调它（自动受益）。

- **Decision 0（accessor 修复）**：`metadata.py` 的 `clear_pending_resume` 与同类
  `remove_team_namespace` 改用定向 `None`-delete 路径（`{TEAMS_KEY: {team_name:
  {TEAM_PENDING_RESUME_KEY: None}}}` / `{TEAMS_KEY: {team_name: None}}`），删前
  查存在性、幂等。`test_metadata.py` 的 `_StubSession.update_state` 由浅
  `dict.update` 改为 `update_dict`，忠实模拟真 Session 深合并。
- **Decision 1（reset/release 清标记）**：见上表层修复。`manager.py` 两处插入
  best-effort `try/except` + `team_logger.warning`，复刻
  `_clear_inprocess_members_inflight` 的 `# noqa: BLE001` 风格。session 经既有
  `TeamRuntimeManager._build_session(session_id)` + `pre_run` + `flush_checkpoint`
  做"读-改-写-落盘"（与 `resolve_team_session_release_info` :1104 同款）。
- **kernel 不动**：kernel 调 `clear_pending_resume`，accessor 修复后自动受益
  （warm/cold 续跑成功后能真正清标记，避免后续 cold-recover 重复续跑）。

## 拒绝的方案

- **回退到 reset 释放 checkpoint → NEW_TEAM_IN_SESSION**：会丢 team memory/历史，
  与 `5d48d0d8` 有意设计（保 bucket 走 COLD_RECOVER）相悖。
- **让 dispatch 读 `pending_resume`**：违反 `runtime/AGENTS.md` 契约（dispatch 是
  纯函数、7 路真值表，续跑发生在 kernel 层）。
- **在 `kernel.resume_paused_round` 判"该不该续跑"**：kernel 不知用户意图
  （是否续跑关键词），决策必须来自 reset 入口。
- **只补 reset/release 调用、不修 accessor**：调用是空操作——深合并不删，
  续跑仍发生。必须先修 accessor。
- **在 reset/release 里手写 None-delete、绕过 `clear_pending_resume`**：会让
  kernel 的 `_clear_pending_resume` 仍坏（生产里 cold-resume 后标记不清、下次
  cold-recover 仍续跑）。修 accessor 一处，所有消费者（kernel + reset + release）
  都对。

## 验证

- `test_metadata.py` 16 用例全绿（含 `test_clear_pending_resume_drops_only_that_key`
  忠实桩下验证：删 pending_resume、保留 spec/db_state）。
- `test_reset_session.py` 6 用例全绿：主路径（reset/release 后 `read_pending_resume`
  经 fresh session 读回 None）+ 无标记 no-op + 无 checkpoint 早返回 + 幂等 +
  flush 失败不破（`_build_session` patch 接缝，marker 存活断言）。
- 既有路径不回归：`test_pause_lifecycle`（F_61 续跑链）、`test_dispatch`（7 路
  真值表）、`test_manager`、`test_runner_team_runtime`（含 release_session 既有
  测试）、`test_persistent_team` 全绿。
- 跨仓零依赖：jiuwenswarm 仅 `Runner.reset_agent_team_session`→`manager.reset_session`
  触点（修复后正确清标记，宿主受益），不引用 `clear_pending_resume`/
  `remove_team_namespace` 内部、不依赖旧坏语义；relay-claw 零引用 `pending_resume`。

## 已知遗留

- 跨仓 `jiuwenswarm/agent_ws_server.py:4044-4045` 注释仍写"drop session tables +
  release checkpoint"，与代码（保 bucket 走 COLD_RECOVER）矛盾，需在 jiuwenswarm
  仓单独修（本方案不跨仓改）。
- `write_team_namespace` 在深合并下不是"真正覆写"而是 merge（既有未列待覆盖 key
  的场景未受影响、无测试失败、无生产报告）。本次未动，记为同类潜在项，留待后续。
- `remove_team_namespace` 无生产调用者（仅定义+导出+文档引用），本次随 accessor
  修复一并改 None-delete（桩忠实化后其测试会暴露同类 bug，必须同修），零行为风险。
