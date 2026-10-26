# Swarmflow Worker 不是 Teammate：移除 DB roster 写入与 team_backend 注入

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-06-23 |
| 范围 | `openjiuwen/agent_teams/workflow/backends/team_worker_backend.py`、`workflow/runner.py`、`workflow/tool_swarmflow.py`、`tools/tool_factory.py` |
| 测试基线 | `tests/unit_tests/agent_teams/workflow/` 87 passed；`test_team_tools.py` 104 passed, 14 skipped |
| Refs | #1047 |

## 背景

swarmflow 把每个 `agent()` 调用映射成一个单轮、用完即弃的 WORKER；`agent_session` /
`human_session` 则是保活多轮的 avatar。它们都**不是团队成员**——不进 coordination 循环、
不订阅消息、不认领任务，只是借 team 的 teammate spec 派生能力跑一次性任务。

但 `TeamWorkerBackend` 之前在 `run()` 里对每个 worker 调
`spawn_member(role=WORKER, status=BUSY)` 往 team DB 写一条 member roster row，完成时再
`update_member_status(..., SHUTDOWN)`。这带来两个问题：

1. **语义错误**：worker 不是 teammate，却出现在团队成员表里。团队成员表是协作成员的真相源
   （`list_members` / dispatcher / monitor 都读它），临时执行器混进去是污染。
2. **roster 膨胀**：每个 `agent()` 留一条 SHUTDOWN 死行且不删，大工作流会累积成百上千条无用
   member row（设计文档早已把这列为已知问题）。

进一步审查发现：DB 写入移除后，`team_backend` 在整条 worker 链路上只剩
`_setup_worker_workspace` 里一行 `register_cleanup_path(ws_root)`——而这行本就是**冗余**的：
`agent_configurator` 已把整个 `team_home(team_name)` 登记进团队 cleanup，worker 工作区是
`team_home/workspaces/{member}_workspace`（team_home 的子目录），`clean_team` 的
`_remove_cleanup_paths` 按深度排序、专门处理 overlapping 路径，rmtree 父目录时子目录一并删。
所以 worker 单独登记纯属重复，`team_backend` 在 worker 链路已无任何真实用途。

## 决策

1. **删除 worker 的 DB roster 写入**：移除 `TeamWorkerBackend._open_worker_row` /
   `_close_worker_row` 及 `run()` 里的调用。worker 仍 mint 一个 `member_name`
   （`wf-<label-slug>-<n>`），但只作纯进程内身份（worker card / owner id / 工作区目录名），
   不落 DB。顺带消除了 `run()` 里为这对 open/close 而存在的 `try/finally` 特殊结构——现在是
   直白的顺序执行（消除特殊情况）。avatar session（`AvatarSessionManager`）本就不写 DB，无需改动。

2. **彻底删除 `team_backend` 注入链**：既然 worker 路径不再需要 team DB，把
   `tool_factory → SwarmflowTool → run_swarmflow → TeamWorkerBackend` 整条
   `team_backend` 透传删掉。worker 路径与 team DB 彻底解耦，名副其实地"零团队耦合"。
   worker 工作区的清理改为依赖 `agent_configurator` 已登记的 `team_home` 整体 cleanup。

3. **不动 async-tool 溢写目录登记**：`team_tool_rail.py` 里另一处
   `register_cleanup_path(out_dir)` 是 NativeHarness 异步工具框架的溢写输出目录登记，与
   swarmflow worker 无关，保持不变。

## 拒绝的方案

- **保留 `team_backend` 只做 cleanup 登记**：留一个唯一用途已被 `team_home` 整体登记覆盖的
  冗余依赖，等于"将来可能用"式的死注入，违背"新代码放最窄层、不提前泛化"。直接删。
- **保留 roster row 但完成即删 row（而非标 SHUTDOWN）**：仍要为非成员写 DB、仍要每个 worker
  两次 DB 往返，治标不治本——根因是 worker 本就不该进成员表。
- **worker pool 复用以摊薄 roster 开销**：把"少写点 row"当目标，回避了"worker 不是成员"这个
  根本语义问题。

## 验证

- `tests/unit_tests/agent_teams/workflow/` 全量 87 passed（含 `test_worker_backend.py` 6 例、
  `test_swarmflow_leader.py`）。测试里 worker / SwarmflowTool 构造移除了 `team_backend=None`
  入参。
- `tests/unit_tests/agent_teams/test_team_tools.py` 104 passed, 14 skipped——`create_team_tools`
  装配 SwarmflowTool 的工厂路径不回归。
- 签名冒烟：`SwarmflowTool` / `TeamWorkerBackend` 构造后均无 `_team_backend` 属性。

## 已知遗留

- `docs/designs/` 下若干未跟踪草稿（`swarmflow_detailed_design.md` / `swarmflow_vs_claude_code.md`
  等）仍提到"worker 占 DB row / roster 膨胀"，那是工作草稿、非正式 S_/F_ 文档，未同步刷新。
- avatar session 的工作区清理同样依赖 `team_home` 整体 cleanup；若未来 worker / session 工作区
  布局移出 `team_home`，需重新评估清理登记。
