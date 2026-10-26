# Team teammate worktree 隔离对齐 AgentTool isolation

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-17 |
| 范围 | `openjiuwen/agent_teams/agent/{spawn_manager,agent_configurator}.py`、`openjiuwen/agent_teams/worktree/{lifecycle,naming}.py`、`openjiuwen/agent_teams/tools/{models,member_options,memory_database}.py`、`openjiuwen/agent_teams/tools/database/{engine,member_dao}.py`、`openjiuwen/agent_teams/schema/team.py`、`openjiuwen/agent_teams/rails/{elements,team_context,team_tool_rail}.py`、`openjiuwen/agent_teams/docs/specs/` |
| 测试基线 | `uv run pytest -q tests/unit_tests/agent_teams/test_worktree_naming.py tests/unit_tests/agent_teams/test_spawn_manager_worktree.py tests/unit_tests/agent_teams/agent/test_worktree_event_bridge.py` → `13 passed` |
| Refs | `docs/designs/team_worktree_isolation_agenttool_cn.md` |

## 背景

team 场景原来的 worktree 能力更像"成员起来以后，自己再决定要不要进一个
worktree"。这和 Claude Code 的 AgentTool `isolation="worktree"` 语义并不一致：

1. worktree 不是 spawn 前就准备好的运行时环境，而是 team rail 暴露出来的一组手动工具。
2. teammate 的 cwd / workspace 不是天然落在隔离目录里，leader 也拿不到稳定的收尾点。
3. worktree metadata、模型引用和成员扩展配置散落在独立列或运行时临时结构里，难以演进。

这次调整的目标是把 team teammate 的 worktree 隔离对齐到 Claude Code 的宿主模型：
leader 侧负责创建和收尾，teammate 只接收一个 `worktree_path` 并在其中工作。

## 决策

1. **worktree 只通过 spawn 时的 isolation 启用**  
   leader 侧 spawn 宿主解析 `isolation="worktree"` 后，才为该 teammate 建立隔离
   worktree。team 工具集不再向 teammate 暴露 `enter_worktree` / `exit_worktree`
   作为手动兜底。

2. **创建者始终是 leader 宿主，且复用通用 manager**  
   worktree 创建统一复用
   `WorktreeManager.create_owner_worktree(slug)`；teammate 自身不创建 worktree，
   也不直接接触 git worktree 生命周期。

3. **命名规则固定为 `agent-{team_name}-{member_name}-{hash8}`**  
   worktree 名不由 LLM 生成，不允许随意命名。命名逻辑收敛到
   `openjiuwen.agent_teams.worktree.naming`，确保 slug 合法、稳定、可追踪。

4. **worktree 延迟到真正 spawn 前再创建**  
   只有 `SpawnManager.build_context_from_db` 为成员构造运行时上下文时，才会检查
   `TeamMember.options.worktree.isolation` 并创建或复用 worktree。这样和日志里看到的
   "只有主工作区，成员 worktree 还未建立"保持一致，也避免 build team 阶段就提前开目录。

5. **teammate 运行时上下文只携带 `worktree_path`**  
   `TeamRuntimeContext` 只增加 `worktree_path`。`worktree_name` /
   `worktree_branch` / `head_commit` 留在 leader 宿主内存里用于 finalize 和后续集成，
   不进 child payload，不进 teammate runtime context。

6. **成员持久化配置统一收敛到 `options` JSON**  
   `TeamMember` 不再为 worktree 或 model ref 维护平铺列。当前约定：
   - `options.model_ref`
   - `options.worktree.isolation`
   - `options.worktree.path`

   旧库若还存在 `model_ref_json`，迁移时只做一次 backfill 到 `options.model_ref`，
   随后删除旧列；本次不为 `isolation` / `worktree_path` 维护兼容旧列，因为这两列从未作为旧库事实存在。

7. **worktree 清理采用 fail-closed**  
   teammate 结束后由 leader 宿主检查 worktree 变更：
   - 干净：`git worktree remove`，并清空 `options.worktree.path`
   - 有修改、宿主 metadata 缺失、或无法可靠判断：保留 worktree

   保留时由 leader 后续依据 `worktree_path` 和可用的 `worktree_branch`
   统一做集成与冲突处理，而不是让成员之间互相 merge / pull。

8. **把 team worktree 逻辑从 SpawnManager 杂糅代码中拆到独立 package**  
   命名、创建/复用、finalize 判定等逻辑收敛到 `openjiuwen.agent_teams.worktree/`，
   `SpawnManager` 只保留 orchestration 职责，避免 manager 继续膨胀成杂物间。

## 拒绝的方案

- **让 teammate 自己创建 worktree**：这样 child 既要懂 worktree backend，又要把路径和分支回传，
  还会把清理点分散到多个进程，不符合 Claude Code 的宿主语义。
- **继续暴露 `enter_worktree` / `exit_worktree` 作为主路径**：这会把"运行时隔离"退化成
  "工具级可选操作"，成员不一定真的在隔离 cwd 中工作。
- **把 `worktree_name` / `worktree_branch` / `head_commit` 也塞进 `TeamRuntimeContext` 或 DB 列**：
  这些字段只服务 leader 宿主的生命周期管理，不是 child 执行任务所必需的信息，持久化它们只会扩大 schema 面。
- **worktree 和 model_ref 继续各占一组平铺列**：会让成员扩展配置继续碎片化，后续再加字段还得继续改表。
  `options` 统一承载是更可扩展的形态。
- **发生冲突后由 teammate 自己 push / pull / 解冲突**：这会让成员之间形成分布式集成链路，
  冲突来源也更难定位。当前方案把集成责任收敛回 leader。

## 验证

- `uv run pytest -q tests/unit_tests/agent_teams/test_worktree_naming.py tests/unit_tests/agent_teams/test_spawn_manager_worktree.py tests/unit_tests/agent_teams/agent/test_worktree_event_bridge.py`
  → `13 passed`
- 关键行为基线：
  - worktree 名按 `agent-{team_name}-{member_name}-{hash8}` 生成；
  - spawn 前不预创建成员 worktree，真正 build member context 时才延迟创建；
  - teammate workspace root 会被 `ctx.worktree_path` 覆盖；
  - finalize 在干净 / 脏 / metadata 缺失三种情况下分别走 remove 或 keep。

## 已知遗留

- `human_agent` / `bridge_agent` / `external_cli` 目前不一定全部走同一套隔离策略；如果后续要扩展，
  应继续复用同一套宿主创建 + `worktree_path` 注入契约。
- 进程重启后如果 leader 宿主内存里的完整 worktree metadata 已丢失，系统会按 fail-closed 保留 worktree；
  这是有意选择，后续集成需要 leader 做显式处理。
