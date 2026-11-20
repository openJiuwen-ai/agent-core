---
title: 集群模式teammate支持swarmflow工具（agent-core 侧实现）
author: '@xiajing'
id: F_20261030_2012_teammate-swarmflow-orchestration
type: F
modules:
- openjiuwen/agent_teams
files:
- openjiuwen/agent_teams/workflow/swarmflow_infra.py
- openjiuwen/agent_teams/workflow/tool_swarmflow.py
designs:
- SDD-0031
design_note: 设计依据见 SDD-0031
author_email: xiajing22@huawei.com
---

# 集群模式teammate支持swarmflow工具（agent-core 侧实现）

## 元信息

| 项 | 值 |
|---|---|
| 范围 | 工具开放与归因：workflow/swarmflow_infra.py、workflow/tool_swarmflow.py、agent/infra.py、agent/agent_configurator.py、agent/team_agent.py、agent/coordination/handlers/workflow.py；运行与生命周期：spawn/inprocess_spawn.py、harness/team_harness.py、runtime/background_task_controller.py、schema/events.py、schema/blueprint.py、tools/tool_factory.py、tools/tool_permissions.py；workflow 引擎：workflow/backends/avatar_session_backend.py、workflow/engine/primitives.py、workflow/engine/progress.py、workflow/engine/runner.py、workflow/runner.py（共 17 文件；frontmatter files 只登记工具开放核心） |
| 测试基线 | `tests/unit_tests/agent_teams/` 13 件 153 passed：test_swarmflow_infra、test_teammate_controller_attach、test_swarmflow_owner、test_swarmflow_leader、test_swarmflow_locales、test_tool_factory、test_reply_buffer、test_engine_retryable、test_pause_resume、test_runner、test_swarmflow_invoke_control、test_background_task_controller、test_shutdown_stops_owned_runs |
| 关联设计 | SDD-0031 |

## 实施要点

| 主题 | 实际做法 |
|---|---|
| 共享设施 | `SwarmflowInfra` team 级单例（governor/预算 ledger/resolver/specs），leader 构建、`TeamInfra` 挂载、teammate configure 前按引用收养；无注入实例时 resolver gate 剔除工具 |
| controller attach | teammate session controller attach 到成员循环；run 跨轮存活（stop_by_owner dispose-only）；relaunch 走发起工具票面；inline script 按 run 物化 |
| shutdown 先停 run | `shutdown_self` 在流收尾前经 session 级 controller 调 `stop_by_owner(include_paused=True)` 停掉名下 run；否则 native.stop 裸 cancel 抢先摘票，run 无终态事件无 journal，卡永挂；dispose 处的调用保留作幂等兜底 |
| 预算 rail | 挂载条件从 leader 角色放宽为持有账本即挂；成员 run 与成员自身普通轮都计入同一 session ledger，与 leader 共池同约束；session 总额是部署配置（TeamAgentSpec），单 run 限额由脚本 META 声明 |
| executor 归因 | 事件 schema 单字段 `executor`；工具从 build_context 按当前宿主现算（leader 也填名）。叙述、注入、run 卡徽章、恢复转交全部读它：谁执行就进谁的上下文 |
| 叙述边界 | run 的 started/phase/agent 叙述进当前宿主上下文；executor 为 None（无团队上下文）时 leader 兜底；paused/resumed 不叙述；终态不叙述（async-tool 注入回宿主） |
| human 硬化 | timeout 只计等人（分类/格式化与其余 agent 轮一致不设逐调用超时，挂死由 pause/stop 终结）；重试复用已获回复；HUMAN_REPLY_SKIP 哨兵；seal/pause WAL cancel-shielded |
| 三方成员 | claude/codex 成员经 `ExternalSwarmflowHost` 持有 `swarmflow` 工具（发起语义与本进程成员一致），方案见 SDD-0031 §8.1 |
| 与灯的边界 | run 宿主在成员 runtime + 团队 controller，不占对话轮，本特性不改灯；灯由任务在飞与 run 活跃两道 hold 撑住，teammate 的 run 通常包在任务里 |

## 验证结论

UT 153 passed 全绿。
端到端（wire 链路、恢复转交 ST、human 往返 ST）在嵌入层仓库验证，见该仓
F_20261030_2018_teammate-swarmflow-adaptation。