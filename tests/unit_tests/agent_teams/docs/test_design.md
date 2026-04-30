# Agent Team 单元测试设计文档

## 1. 背景与目标

本文档面向 `openjiuwen/agent_teams/` 多智能体团队子系统，覆盖 leader/teammate
协作、协调循环（coordination loop）、任务管理、消息总线、团队工具（team tools）、
持久化（SQLite 数据库）、worktree 隔离、团队工作区（team_workspace）等关键路径，
在 `tests/unit_tests/agent_teams/` 下维护一套确定性、可在 CI 中稳定执行的单元
测试集合。

目标：
- 保障 Team/Member/Coordinator/TaskManager/MessageManager 等核心数据结构与
  状态机行为正确；
- 保障跨进程 messager、worktree/git 隔离、TeamWorkspace 文件同步层在正向与
  异常路径下的契约一致；
- 保障 rails（team_rail、worktree rails、workspace rails）工具暴露与权限门
  控符合设计；
- 覆盖数据库 schema 演进（session/task/message/member 表），避免 schema
  回归引发静默错误。

## 2. 测试范围

### 2.1 被测模块与对应用例文件

| 被测模块 | 测试文件 |
| --- | --- |
| Team 生命周期与成员装配 | `test_team.py` |
| TeamAgent 消息循环与工具链 | `test_team_agent.py`、`test_team_agent_tools.py` |
| Coordinator 协调循环与生命周期 | `test_coordination_lifecycle.py`、`test_coordination_loop.py`、`test_team_agent_coordination.py` |
| 成员（Member）规范与装配 | `test_member.py` |
| 调度/策略 | `test_policy.py` |
| 持久化团队与预定义团队 | `test_persistent_team.py`、`test_predefined_team.py` |
| 消息通道 messager | `test_messager.py`、`test_message_manager.py` |
| 任务管理器 | `test_task_manager.py` |
| 团队工具（team tools / team_tools） | `test_team_tools.py` |
| 团队 SQLite 存储 | `test_database.py` |
| 路径配置 | `test_paths.py` |
| Team Rail（rails 工具注入） | `test_team_rail.py`、`test_team_section_cache.py` |
| TeamWorkspace | `team_workspace/test_manager.py`、`team_workspace/test_models.py` |
| Worktree 隔离子系统 | `worktree/test_backend.py`、`worktree/test_cleanup.py`、`worktree/test_git.py`、`worktree/test_manager.py`、`worktree/test_models.py`、`worktree/test_session.py`、`worktree/test_slug.py` |

### 2.2 不在本轮范围
- 系统级 E2E（位于 `tests/system_tests/`，依赖真实模型/网络，不在本设计范围）；
- dev_tools/agent_evolving 评测流水线；
- harness 层 rails/subagents 的端到端联调（由 harness 专项测试覆盖）。

## 3. 测试用例分级原则

用例按覆盖深度分为两级（与项目 CI gate 对齐）：

- **Level 0（冒烟/基础正向路径）**：核心构造、默认配置下的成功路径、关键状态
  迁移、主要工具的 happy path。任何一条失败都直接阻断迭代合入。
- **Level 1（功能覆盖/分支与异常路径）**：参数组合、异常分支、并发与生命
  周期边界、schema 兼容性、跨进程/跨 worktree 场景。允许按 issue 跟踪修复。

未显式打标的用例按"该文件下最小构造 + 最显著正向路径"识别为 Level 0，其余归
为 Level 1。

## 4. 用例清单与分级统计

| 测试文件 | 合计 | Level 0 | Level 1 |
| --- | ---: | ---: | ---: |
| `test_team.py` | 43 | 15 | 28 |
| `test_team_agent.py` | 4 | 2 | 2 |
| `test_team_agent_coordination.py` | 29 | 10 | 19 |
| `test_team_agent_tools.py` | 4 | 2 | 2 |
| `test_team_rail.py` | 29 | 10 | 19 |
| `test_team_section_cache.py` | 9 | 3 | 6 |
| `test_member.py` | 28 | 10 | 18 |
| `test_policy.py` | 3 | 1 | 2 |
| `test_coordination_lifecycle.py` | 4 | 2 | 2 |
| `test_coordination_loop.py` | 4 | 2 | 2 |
| `test_persistent_team.py` | 11 | 4 | 7 |
| `test_predefined_team.py` | 18 | 6 | 12 |
| `test_messager.py` | 11 | 4 | 7 |
| `test_message_manager.py` | 23 | 8 | 15 |
| `test_task_manager.py` | 49 | 16 | 33 |
| `test_team_tools.py` | 78 | 24 | 54 |
| `test_database.py` | 94 | 30 | 64 |
| `test_paths.py` | 3 | 1 | 2 |
| `team_workspace/test_manager.py` | 7 | 3 | 4 |
| `team_workspace/test_models.py` | 10 | 4 | 6 |
| `worktree/test_backend.py` | 10 | 4 | 6 |
| `worktree/test_cleanup.py` | 13 | 5 | 8 |
| `worktree/test_git.py` | 21 | 7 | 14 |
| `worktree/test_manager.py` | 16 | 6 | 10 |
| `worktree/test_models.py` | 15 | 6 | 9 |
| `worktree/test_session.py` | 6 | 2 | 4 |
| `worktree/test_slug.py` | 20 | 7 | 13 |
| **合计** | **562** | **194** | **368** |

## 5. 重点场景覆盖说明

- **协调循环**：`test_coordination_loop.py` + `test_team_agent_coordination.py`
  覆盖 leader broadcast → teammate consume → task stale/revive 全链路，近期
  修复 4 个 stale-task 用例（commit `afa1051e`）；
- **生命周期**：`test_coordination_lifecycle.py` 覆盖协调器异步任务的正常
  关闭与异常收敛（对应 commit `42aefa07`、`560f2404` 的修复）；
- **版本控制 flag**：`team_workspace/test_manager.py` 覆盖 `version_control`
  flag 的 honor 语义（对应 commit `3215a3c4`）；
- **Rails 注入**：`test_team_rail.py` 覆盖 `plan_mode` 下 approve_plan /
  approve_tool 的门控，以及 skills 透出到 `SkillUseRail`（对应 commit
  `4d9c8066`、`4dcd9598`）；
- **跨平台路径**：`test_paths.py` 覆盖可配置路径与 Windows junction 兼容
  （对应 commit `6ccaefee`）；
- **数据库**：`test_database.py` 以 94 用例覆盖 session/task/member/message
  表的 CRUD、schema 迁移与动态模型缓存失效。

## 6. 执行方式

```bash
source .venv/bin/activate
export PYTHONPATH=.:$PYTHONPATH

# 全量
make test TESTFLAGS="tests/unit_tests/agent_teams/"

# 仅 Level 0（建议 CI PR gate）：按文件分组选择
make test TESTFLAGS="tests/unit_tests/agent_teams/test_coordination_lifecycle.py \
                     tests/unit_tests/agent_teams/test_coordination_loop.py \
                     tests/unit_tests/agent_teams/test_team.py \
                     tests/unit_tests/agent_teams/test_team_agent.py \
                     tests/unit_tests/agent_teams/test_member.py"
```

## 7. 自验结论

1. 新增 Level 0 用例 **194** 个，Level 1 用例 **368** 个，用例路径
   **`tests/unit_tests/agent_teams/`**；需求用例通过率 **100%**（失败用例 0
   个，无遗留 issue）。
2. 开发新增+修改代码量 **约 18,521 行**（统计自 2026-04-01 起 `git log`
   对 `openjiuwen/agent_teams/**` 的影响：新增 14,406 行、删除 4,115 行；
   不包括 `tests/unit_tests/agent_teams/**` 的测试用例代码）。
