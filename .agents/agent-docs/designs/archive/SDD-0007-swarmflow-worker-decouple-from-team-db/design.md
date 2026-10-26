---
id: SDD-0007
title: SwarmFlow Worker 去团队成员化与会话存储解耦
slug: swarmflow-worker-decouple-from-team-db
status: shipped
stage: archive
owner: "@alan_cheng"
reviewers: ["@arch-team"]
created: 2026-06-23
updated: 2026-06-24
version: 1
domain: core
modules: [agent_teams/workflow]
tags: [swarmflow, worker, team-db, decouple, cleanup]
related: [SDD-0002, SDD-0005, SDD-0006]
approved-date: 2026-06-23
dev-start-date: 2026-06-23
archived-date: 2026-06-24
implementation:
  status: shipped
  repos:
    - https://gitcode.com/alan_cheng/agent-core
  prs: []
  commits:
    - alan_cheng/agent-core@e956586a
    - alan_cheng/agent-core@a15469dd
    - alan_cheng/agent-core@1d71d7af
---

# SwarmFlow Worker 去团队成员化与会话存储解耦

## 1. 背景与问题 (Why)

SwarmFlow 把每个 `agent()` 调用映射成一个**单轮、用完即弃的 worker**。它本质上不是团队成员——
不进协作循环、不订阅消息、不认领任务，只是借团队的 teammate 配置派生出能力、跑一次性任务。
`agent_session` / `human_session` 背后的 avatar 同理，只是保活多轮。

但旧实现里 `TeamWorkerBackend.run` 对每个 worker 都干了一件不该干的事：调 `spawn_member(role=WORKER,
status=BUSY)` 往团队成员表（DB roster）写一行，跑完再 `update_member_status(..., SHUTDOWN)`。这带来
两个问题：

1. **语义错了。** 团队成员表是协作成员的真相源——`list_members`、dispatcher、monitor 都读它。把一个
   临时执行器塞进去，是污染。worker 根本不是 teammate，不该出现在成员名册里。

2. **名册膨胀。** 每个 `agent()` 留一条 SHUTDOWN 死行且不删。一个大工作流会累积成百上千条无用成员
   行（这早被列为已知问题）。

进一步审查发现：把 DB 写入移除后，`team_backend` 在整条 worker 链路上只剩一行
`register_cleanup_path(ws_root)`——而**这行本来就是冗余的**：装配时 `agent_configurator` 已经把整个
`team_home` 登记进团队清理，worker 工作区是 `team_home/workspaces/{member}_workspace`（team_home 的
子目录），删除团队时 rmtree 父目录会连带删掉子目录。所以 worker 单独登记纯属重复——`team_backend`
在 worker 链路已无任何真实用途。

本特性做两件事：删掉 worker 的 DB 写入；并顺势把已无用途的 `team_backend` 从整条 worker 链路彻底
拔掉，让 worker 名副其实地"零团队 DB 耦合"。

## 2. 目标与非目标

**目标**

- 删除 worker 的 DB roster 写入（不再 `spawn_member` / `update_member_status`）。
- 把 `team_backend` 从 `tool_factory → SwarmflowTool → run_swarmflow → TeamWorkerBackend` 整条链路
  删除，worker 路径与团队 DB 彻底解耦。
- worker 工作区清理改为依赖装配时已登记的 `team_home` 整体清理。
- 借此消除 `run()` 里为 DB open/close 而存在的 `try/finally` 特殊结构（消除特殊情况）。

**非目标**

- 不动 avatar session——它本来就不写 DB。
- 不动 async-tool 溢写目录的 `register_cleanup_path`（那是另一回事，见 §4.4）。
- 不改 worktree 隔离路径自己的 per-worker 清理（与 team_home 整体清理互补）。

## 3. 术语与上下文

| 术语 | 含义 |
|---|---|
| WORKER 不变量 | worker 单轮、无状态、用完即弃、不进协作、无 DB 身份 |
| roster / 成员表 | 团队 DB 里记协作成员的表，`list_members`/dispatcher/monitor 读它 |
| `team_backend` | 团队 DB 后端句柄，旧实现透传给 worker 用来写 roster |
| `team_home` | 团队的文件系统根目录，装配时整体登记进清理 |

## 4. 方案设计 (What / How)

### 4.1 总体架构

这是一个"做减法"的特性。变化是：worker 链路上原本贯穿的 `team_backend` 注入被整条拔掉，DB 写入
消失，清理责任上移到团队级。

```
改动前（team_backend 贯穿整条链 + worker 写 DB）
  tool_factory(team_backend)
    → SwarmflowTool(_team_backend)
      → run_swarmflow(team_backend)
        → TeamWorkerBackend(team_backend)
          → run(): spawn_member 写 roster + update_member_status SHUTDOWN
                   + register_cleanup_path(ws)

改动后（整条链拔掉 team_backend，worker 零 DB 写入）
  tool_factory
    → SwarmflowTool
      → run_swarmflow
        → TeamWorkerBackend
          → run(): 纯进程内身份，零 DB 写入
                   工作区清理交给 team_home 整体登记
```

整条链路的每处签名都已不含 `team_backend`，类上也不再有 `_team_backend` 属性（单测靠"构造不传该
参数即不报错 + 无该属性"反证）。

### 4.2 数据流与时序

**`TeamWorkerBackend.run` 当前的真实控制流**（已无任何 DB 操作）：

```
run()
  │
  ├─ mint member_name = wf-{label-slug}-{n}（纯进程内运算，不落 DB）
  ├─ resolve model（model_resolver 回调，无 DB）
  ├─ ensure worktree（仅 isolation='worktree' 时建）
  ├─ _execute_worker： build(role=WORKER) → run_once → dispose
  ├─ 有 schema 则读结构化结果，否则取自由文本 → AgentResult
  └─ finally： worktrees.finalize（干净则删、有改动则保留）

  ✗ 全程无 spawn_member / update_member_status / 任何 team DB 写入
```

对比旧流程，消失的是：`spawn_member`、`update_member_status`、以及那对 open/close 之间包着主体逻辑
的 `try/finally`。现在是直白的顺序执行；唯一的 `try/finally` 只为 worktree 生命周期服务，与 DB 无关。

`member_name` 仍然 mint，但只作**纯进程内身份**，三个用途：worker card 的 name、能力管理器给
`structured_output` 工具 re-qualify owner id（并发不撞）、以及工作区目录名。它不落 DB，`run()` 结束
后不再被任何持久化结构引用。

### 4.3 接口与数据模型

**链路四处签名都已去掉 `team_backend`**（证据）：

```python
# tool_factory.py：构造 SwarmflowTool 时不传 team_backend
SwarmflowTool(parent_agent=..., messager=..., team_name=..., model_resolver=...,
              worker_base_spec=..., human_base_spec=..., t=..., language=...)

# tool_swarmflow.py：SwarmflowTool.__init__ 无 team_backend 形参，类无 _team_backend
# runner.py：run_swarmflow(...) 签名无 team_backend
# team_worker_backend.py：TeamWorkerBackend.__init__ 无 team_backend，类体无 self._team_backend
```

**WORKER 不变量（当前代码均有直接证据）：**

> **Requirement:** 一个 `agent()` 调用对应一个 worker；worker 单轮 `run_once` 执行、跑完 `dispose`、
> 上下文每次全新、不进协作循环、**无 DB roster 身份**。
> **Scenario:** WHEN worker 跑完 THEN 团队成员表里没有它的行；WHEN 大工作流跑了上千个 `agent()`
> THEN 成员表不膨胀。单测 `test_execute_worker_derives_teammate_spec_without_team_tools` 断言
> `role==WORKER`、`tools==[]`、`enable_task_loop/planning` 保留。

### 4.4 关键流程

- **worker 工作区清理（默认路径）**：worker 工作区落在 `team_home/workspaces/{member}_workspace`，是
  `team_home` 的子目录。装配时 `agent_configurator` 已把整个 `team_home` 登记进团队清理；删除团队
  时按深度排序处理重叠路径、rmtree 父目录连带删子目录。**所以 worker 不必单独登记**——这正是删掉
  那行 `register_cleanup_path(ws_root)` 的依据。
- **worktree 隔离路径的清理是另一套**：当 `isolation='worktree'` 时，worker 工作区是一个独立 git
  worktree，由 `run()` 的 `finally` 调 `worktrees.finalize` 做 per-worker 清理（干净则删、有改动 /
  状态不可确认则保留给后续集成）。它与 team_home 整体清理**互补而非替代**。
- **avatar session 本就不写 DB**：对 `AvatarSessionManager` 全文 grep `spawn_member` /
  `update_member` / `team_backend` / `db.` 零命中。本特性未动它，与其设计一致。
- **async-tool 溢写目录的 `register_cleanup_path` 保留**：`team_tool_rail._wire_async_spill` 里那处
  登记的是 NativeHarness 异步工具框架的溢写输出目录（见 SDD-0006），跑在 Leader harness 上，与
  swarmflow worker 是两回事，明确保留不动。

### 4.5 配置与开关

本特性是纯粹的内部解耦，无新增配置 / 开关。`enable_swarmflow` 注入 gate、worker 能力派生
（model / tools / skills / `enable_task_loop` / `enable_task_planning`）均保持不变。

## 5. 关键决策与权衡 (ADR)

### 5.1 删 worker 的 DB roster 写入
- **选择：** 移除 `_open_worker_row` / `_close_worker_row` 及 `run()` 里的 `spawn_member` /
  `update_member_status`。
- **为什么：** worker 不是 teammate，不该进成员表；顺带消除了为这对 open/close 而存在的特殊
  `try/finally` 结构，`run()` 变直白顺序执行。

### 5.2 彻底拔掉 `team_backend` 注入链
- **选择：** 从 tool_factory 到 TeamWorkerBackend 整条删 `team_backend`，清理改依赖 `team_home`
  整体登记。
- **为什么：** DB 写入移除后 `team_backend` 唯一剩的用途（worker 工作区 cleanup 登记）已被 team_home
  整体登记覆盖，留着就是"将来可能用"式的死注入，违背"新代码放最窄层、不提前泛化"。直接删。

### 5.3 被否决的备选与取舍理由
- **保留 `team_backend` 只做 cleanup 登记**：留一个已被 team_home 覆盖的冗余依赖，等于死注入。
- **保留 roster 行但完成即删行（而非标 SHUTDOWN）**：仍要为非成员写 DB、仍要每个 worker 两次 DB
  往返，治标不治本——根因是 worker 本就不该进成员表。
- **worker pool 复用以摊薄 roster 开销**：把"少写点行"当目标，回避了"worker 不是成员"的根本语义
  问题。

## 6. 影响面与兼容性

- **改动**：`workflow/backends/team_worker_backend.py`（删 DB 写入 + 去 team_backend）、
  `workflow/runner.py`、`workflow/tool_swarmflow.py`、`tools/tool_factory.py`（链路去 team_backend）。
- **未改**：`avatar_session_backend.py`（本就不写 DB）、`team_tool_rail.py` 的 async-tool 溢写登记
  （另一回事）、worktree 隔离路径的 per-worker 清理。
- **兼容性**：worker 能力派生（model/tools/skills/todo 规划）不变；用户 API 不变。无 BREAKING——
  这是把"本不该有的副作用"删掉。

## 7. 测试与验证

- `tests/unit_tests/agent_teams/workflow/` 全量 87 passed（含 `test_worker_backend.py` 6 例、
  `test_swarmflow_leader.py`）。测试里 worker / SwarmflowTool 构造均移除了 `team_backend` 入参。
- `test_team_tools.py` 104 passed, 14 skipped——`create_team_tools` 装配 SwarmflowTool 的工厂路径不
  回归。
- `test_execute_worker_derives_teammate_spec_without_team_tools`：断言 `role==WORKER`、`tools==[]`、
  `enable_task_loop/planning` 保留、worker system prompt 注入。
- 签名冒烟：`SwarmflowTool` / `TeamWorkerBackend` 构造后均无 `_team_backend` 属性（若传则 TypeError）。

## 8. 可观测性

- 团队成员表（`list_members` / monitor）不再混入 worker 行——成员表恢复为"真协作成员"的真相源。
- 大工作流跑完后成员表行数不膨胀，可作为回归观测点。

## 9. 风险与回滚

- **风险：** 若未来 worker / session 工作区布局移出 `team_home`，则"依赖 team_home 整体清理"的前提
  失效，需重新评估清理登记（已记入遗留）。
- **回滚**：本特性是删除副作用，回滚等于恢复 DB 写入与 team_backend 注入——不建议（重新引入污染与
  膨胀）。

## 10. 开放问题 (Open Questions)

- [x] worker 该不该进成员表？→ 不该，它不是 teammate。
- [x] 删 DB 后 team_backend 还留吗？→ 不留，唯一用途已被 team_home 整体清理覆盖。
- [ ] 若 worker / session 工作区将来移出 team_home，清理登记如何重新组织？

## 11. 评审记录 (Iteration Log)

| 轮次 | 日期 | 版本 | 评审人 | 结论 | 主要修改/讨论 |
|---|---|---|---|---|---|
| 1 | 2026-06-24 | 1 | @arch-team | approved | 回溯归档；worker 去成员化 + team_backend 解耦，内容以当前 HEAD 代码为准 |

## 12. 实现记录与偏差

### 实际实现
`TeamWorkerBackend.run` 已无任何 DB 操作（无 `spawn_member` / `update_member_status` /
`_open_worker_row` / `_close_worker_row`）；`member_name` 仍 mint 但只作纯进程内身份（card / owner /
工作区目录名）。`team_backend` 从 `tool_factory → SwarmflowTool → run_swarmflow → TeamWorkerBackend`
整条链路删除，worker 工作区清理依赖 `agent_configurator` 已登记的 `team_home` 整体 cleanup。

### 与设计的偏差
- 与 F_44 文档完全一致，无方向性偏差。
- 文档未充分展开的代码细节：`_setup_worker_workspace` 仍保留（移除的只是其中那行
  `register_cleanup_path(ws_root)`），worker 仍 mount 团队共享工作区到 `.team/{team_name}/`；worktree
  隔离路径有自己的 per-worker 清理（`worktrees.finalize`），与 team_home 整体清理互补。

### 链接
- 原始特性记录：`agent-core` 仓 `F_44_swarmflow-worker-not-teammate-no-db.md`，规约 `S_18`「WORKER
  不变量」段。本特性 footer 用 `Refs: #1047`。
- 关键代码：`workflow/backends/team_worker_backend.py`（`run` / `_next_member_name`）、
  `workflow/{runner,tool_swarmflow}.py`、`tools/tool_factory.py`。
- commits 见 frontmatter `implementation.commits`。

### 遗留与后续
- `docs/designs/` 下若干未跟踪草稿仍提"worker 占 DB 行 / roster 膨胀"，那是工作草稿、非正式
  S_/F_ 文档，未同步刷新。
- avatar session 工作区清理同样依赖 team_home 整体 cleanup；若未来工作区布局移出 team_home，需重新
  评估清理登记。

## 13. 参考与关联设计

- SDD-0002（有状态会话与人机交互）：worker 与会话共享成员派生逻辑；avatar session 同样不写 DB。
- SDD-0005（脚本定义增强）：worker 的 `isolation='worktree'` 隔离路径与本设计的工作区清理边界相关。
- SDD-0006（异步工具管控框架）：`team_tool_rail` 里保留的 async-tool 溢写目录登记与 worker 清理是
  两回事。
- 原始特性记录：`agent-core` 仓 `F_44` / `S_18`。
