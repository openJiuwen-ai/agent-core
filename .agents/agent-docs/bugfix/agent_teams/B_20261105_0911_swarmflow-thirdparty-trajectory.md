---
title: Swarmflow 三方 Agent 轨迹未绑定成员身份
author: wangcan103
author_email: wangcan103@huawei.com
id: B_20261105_0911_swarmflow-thirdparty-trajectory
type: B
modules:
- openjiuwen/agent_teams
files:
- openjiuwen/agent_teams/external/member_runtime.py
- openjiuwen/agent_teams/spawn/external_cli_spawn.py
- openjiuwen/agent_teams/workflow/backends/team_worker_backend.py
- openjiuwen/agent_teams/workflow/backends/avatar_session_backend.py
- tests/unit_tests/agent_teams/external/test_member_runtime.py
- tests/unit_tests/agent_teams/workflow/test_worker_backend.py
- tests/unit_tests/agent_teams/workflow/test_avatar_session_backend.py
designs: []
design_note: 小范围补齐既有 HarnessTrajectoryRecorder 的 Swarmflow 接线，不新增协议或公开 API。
---

# Swarmflow 三方 Agent 轨迹未绑定成员身份

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-11-05 |
| 严重程度 | 低：不影响 Agent 执行，但缺少模型与工具轨迹 |
| 范围 | `openjiuwen/agent_teams/external/`、`workflow/backends/`、对应单测 |
| 关联 spec | `S_14_monitor-and-observability.md`、`S_18_swarmflow-engine-and-worker.md` |
| 关联历史实现 | `F_112_harness-protocol-trajectory-observation.md`、`F_114_swarmflow-thirdparty-agent.md` |
| Refs | #2228 |

## 现象

Swarmflow 使用 `agent(..., options={"agent_type": ...})` 或
`agent_session(..., options={"agent_type": ...})` 时，三方 runtime 能正常执行，但轨迹界面缺少该成员的
turn、模型请求与工具事件。

## 根因

`ExternalHarnessMemberRuntime` 只有在宿主调用 `bind_trajectory_recorder()` 后，才会请求 provider 提供
模型观测事件并把协议事件交给 `HarnessTrajectoryRecorder`。普通团队成员的
`external_cli_spawn` 已绑定 recorder；Swarmflow 的单轮 worker 和有状态 session 则直接构建 external
runtime，没有做绑定。Native worker 的 `execution_subject_scope()` 不能替代这一步，因为 external
轨迹来自 provider 的协议事件流，不是当前 Python context 中的模型回调。

## 决策

- 在现有 `ExternalHarnessMemberRuntime` 模块增加共享内部绑定 helper，统一创建 recorder、写入 Team/member/backend 属性，并复用于普通团队三方成员。
- Swarmflow 单轮 worker 在 `ExternalMemberTurns.start()` 前绑定；`agent_session(agent_type=...)` 在构建
  `ExternalMemberTurns` 时绑定，确保首次 start 前完成，后续 turn 复用。
- subject 用外层 Team session id、team name 和唯一 `member_name`；operator `label` 只用于显示。这样相同
  label 的并发成员仍有不同身份，且查询归属在 Team 会话下。
- 保持 recorder 的既有模型：每个 member turn 独立 trace root，不承诺成为 Team root 的子 span。观测未启用
  时 recorder 为 `None`，不改变业务执行；provider 未提供 `ModelRequestEvent` 时不伪造模型 span。

## 拒绝的方案

- 只在单轮 worker 绑定：会留下 `agent_session(agent_type=...)` 的同类缺口，因此两个 external Swarmflow
  入口同时接线。
- 用 `execution_subject_scope` 包住 `send_turn`：它只设置当前 SDK 执行上下文，不能替 external runtime
  请求 observation capability 或消费 provider 的模型事件。
- 用 `label` 或 avatar child session id 做轨迹身份：重复 label 会合并成员；child session id 会让轨迹脱离
  外层 Team session 的查询范围。

## 验证

定向单测覆盖共享 recorder 的 subject / attributes、缺少 session 与 observability 未启用行为、单轮 worker
在 runtime 启动前绑定、有状态 session 首次绑定及跨 turn 复用，以及普通 Team 三方成员的 spawn 路径。
2026-11-05 使用仓库 `.venv` 执行 `test_member_runtime.py`、`workflow/test_worker_backend.py`、
`workflow/test_avatar_session_backend.py`、`test_external_cli_spawn.py` 和
`test_external_cli_spawn_wrapper.py`：178 passed。出现 2 条既有 Pydantic deprecated warnings；无失败或跳过。

## 已知遗留

轨迹可见内容取决于所用 provider 是否实现 `MODEL_REQUEST_OBSERVATION` 并发出 `ModelRequestEvent`；该修复不
改变 provider 能力，也不增加 Team root 与 external member turn 之间的 span parent link。
