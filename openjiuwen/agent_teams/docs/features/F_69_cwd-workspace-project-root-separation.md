# cwd / workspace / project_root 三层解绑

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-07-28 |
| 范围 | `harness/schema/config.py` + `harness/schema/deep_agent_spec.py`（`DeepAgentConfig` / `DeepAgentSpec` 新增 `cwd` / `project_root`）；`harness/deep_agent.py`（`init_cwd` 三值分开，门放宽为 `workspace or cwd`）；`core/sys_operation/local/{fs_operation,shell_operation}.py`（sandbox 白名单加 `get_cwd()`）；`agent_teams/agent/agent_configurator.py`（worktree 只改 cwd、workspace 恒为成员目录 + `None` 兜底、cleanup 无条件注册）；`agent_teams/workflow/backends/team_worker_backend.py`（worker 同款解绑，`_setup_worker_workspace` 返回 `(spec, cwd)`）；`docs/specs/S_05`（不变量 7 重写）、`S_12`（WorkspaceSpec 段）、`S_13`（`.team` 挂载点）、`S_18`（worker 工作区）；测试 `agent/test_worktree_context_isolation.py`、`workflow/test_worker_backend.py`。**关联仓库 jiuwenswarm**：`agents/swarm/assembly.py`（`_with_project_workspace`→`_with_project_cwd`，删 `_worktree_enabled`）、`agents/swarm/providers/member_rails.py`（`RuntimePromptRail` 如实报 cwd + 成员 workspace）、`agents/harness/common/rails/runtime_prompt_rail.py`（`set_runtime_paths(workspace_dir=...)`）、`tests/agents/swarm/test_swarm_assembly.py` |
| 测试基线 | agent-core `tests/unit_tests/{agent_teams,harness,core/sys_operation}` 4685 passed / 32 skipped（唯一失败 `test_local_and_aio_providers_coexist` 为改动前既有失败，已用 `git stash` 验证）；jiuwenswarm `tests/agents/swarm/` 121 passed + `RuntimePromptRail` 相关 3 个测试文件 90 passed |
| Refs | #751 |

## 背景

`core/sys_operation/cwd.py` 从设计之初就是三层模型，注释写得很明确：

> Auxiliary workspace locations (**not part of the cwd fallback chain**) ...
> they record related paths used by tools, **not where shell commands run**.

即 `project_root`（项目身份）/ `cwd`（shell 执行点、相对路径基准）/ `workspace`
（本 agent 的产物目录）三者独立。但 `DeepAgent._ensure_initialized` 把它们并成了
一个值：

```python
init_root = self._deep_config.workspace.root_path or os.getcwd()
init_cwd(init_root, workspace=init_root)      # cwd == workspace
```

于是"想让成员的 shell 在某个目录里跑"的唯一手段变成"改写它的 workspace"。三处覆盖
因此堆在同一个 `WorkspaceSpec.root_path` 字段上，按优先级互相顶替：

| 顺序 | 位置 | 行为 |
|---|---|---|
| 1 | `agent_configurator.py` | 有 `worktree_path` → `root_path = worktree_path`，`stable_base=False` |
| 2 | jiuwenswarm `assembly.py` | 有 `project_dir`（**且开了 worktree**）→ `root_path = project_dir` |
| 3 | `agent_configurator.py` | `stable_base=True` → `root_path = ensure_team_member_workspace_link(...)` |

实际后果（三种组合都不对，且错法各不相同）：

- **开 worktree**：workspace 变成 worktree。成员的 memory、skills 视图、`.team` 挂载点
  全落在一个临时 checkout 里，`git worktree remove` 一执行就没了。
- **有 project 且开 worktree**：workspace 先被改成项目目录，再被 worktree 顶掉。中途那一步
  还会让 `build_member_skill_toolkit` 的 `Path(workspace_root) / "skills"` 落到**用户项目根**下，
  在别人的 repo 里创建 `skills/`。
- **有 project 未开 worktree**（`_with_project_workspace` 被 `if _worktree_enabled(spec)` 门控，
  这条路径根本不触发）：workspace 保持成员目录、真实 cwd 也是成员目录，而
  `RuntimePromptRail` 却在提示词里宣告 `当前工作目录：<项目目录>`——模型按项目目录理解
  相对路径，工具却在成员目录解析。两个目录都真实存在，写出去的相对路径静默落错位置。
- **无 project**：`RuntimePromptRail` 的 cwd 三级兜底掉到 `get_agent_workspace_dir()`
  （进程级全局目录），同样与真实 cwd 不符。

根因只有一条：**cwd 没有独立的表达方式，只能借道 workspace**。

## 决策

### D1：`cwd` / `project_root` 上升为一等配置项
- `DeepAgentConfig` / `DeepAgentSpec` 各加两个字段。缺省语义 `cwd → workspace.root_path`、
  `project_root → cwd`，所以**单 agent / subagent 路径行为逐字不变**。
- `_ensure_initialized` 改为 `init_cwd(cwd_root, project_root=..., workspace=workspace_root)`。
  同时把入口条件从 `if config.workspace` 放宽成 `if config.workspace or config.cwd`——
  否则一个只配了 cwd、没有 workspace 的 agent 会整段跳过 cwd 初始化，静默退回
  `os.getcwd()`。

### D2：workspace 恒为"这个成员自己的目录"
- `agent_configurator`：worktree 只写 `member_cwd`，`WorkspaceSpec.root_path` 始终经
  `ensure_team_member_workspace_link` 解析；`ws_spec` 为 `None` 时兜底
  `WorkspaceSpec(stable_base=True)`（team 成员恒有 workspace，D1 的 cwd 初始化以它为锚）。
- 由此 workspace **恒为团队自己创建的目录**，`register_cleanup_path` 不再需要
  `not workspace_is_worktree` 这个条件——那个条件当初存在，只是因为 workspace 可能是 worktree。
- swarmflow `TeamWorkerBackend` 同款处理，`_setup_worker_workspace` 从返回 `WorkspaceSpec`
  改为返回 `(WorkspaceSpec, cwd)`，`stable_base` 恒 `True`。

### D3：jiuwenswarm 只设 cwd，不碰 workspace
- `_with_project_workspace` → `_with_project_cwd`，只写 `cwd` / `project_root`。
- 顺带**去掉 worktree 门控**：原来只在开隔离时才应用，导致"有项目但没开 worktree"的最常见
  组合反而没人管 cwd。现在有 `project_dir` 就设，worktree 场景再由 `agent_configurator`
  用 worktree 路径覆盖 cwd。`_worktree_enabled` 随之无调用方，删除。

### D4：sandbox 白名单补上 cwd
`fs_operation` / `shell_operation` 的默认根从 `[workspace, project_root]` 改为
`[workspace, project_root, cwd]`。三层独立之后，cwd 不再必然落在另外两个根里
（worktree 通常在 repo 之外），不补就会把成员锁在自己的工作目录之外。

### D5：`RuntimePromptRail` 如实报告
- 无条件 `set_runtime_paths(cwd=project_dir or member_workspace_root, project_dir=..., workspace_dir=member_workspace_root)`。
- 新增 `workspace_dir` 参数（显式 keyword，不走 kwargs），让 `Agent 内部数据目录`
  那行显示**成员自己的 workspace** 而非进程级全局目录；未传时（单 agent / code 路径）
  仍回落 `get_agent_workspace_dir()`，行为不变。

## 拒绝的方案
- **把相对路径基准从 `get_workspace()` 改成 `get_cwd()`**（本次一度确认要做，查证后撤回）：
  那四处 `base_dir = get_workspace() or get_cwd()` 全部位于 `_build_history_path`，决定的是
  工具历史 `.agent_history/` 落盘到哪，用 workspace 恰恰正确——改成 cwd 会把历史文件写进
  用户项目。真正的相对路径解析在 `filesystem.py` 的 `work_dir = get_cwd()` 与
  `bash/_tool.py` 的 `resolved_cwd = p.workdir or current_cwd`，**本来就是 cwd**，无需改动。
- **让 worktree 继续充当 workspace，只额外记一个"稳定产物目录"**：等于承认一个 agent 有两个
  workspace，`get_workspace()` 的语义立刻分叉；且 skills 视图 / `.team` 挂载点要挂哪个仍然
  没有答案。
- **保留 `stable_base` 作为开关**：改完之后 team 成员与 swarmflow worker 两条路径都恒传
  `True`，它已退化成常真值。本次没删（`WorkspaceSpec` 是公共 schema，删字段要单独评估
  外部调用方），但它已经不承载分支语义。
- **把 jiuwenswarm 的 `_with_project_cwd` 继续用 worktree 门控**：那个门控正是"有项目但没开
  worktree 时 cwd 无人设置"的来源。

## 已知遗留
- `mount_into_worktree`（`team_workspace/manager.py`）在两个仓库里都**无调用方**，是死代码。
  `.team` 一直是经 `mount_into_workspace` 挂在 workspace 上的，解绑后这一点自动正确。
- `TeamHarness.init_cwd_for_round()` / `MemberRuntime.init_cwd_for_round` 同样无调用方。它做的
  正是"把 cwd 设成 workspace.root_path"，没暴露成故障是因为 `DeepAgent` 基类兜住了同样的事。
- `RuntimePromptRail` 的 `# 运行时目录上下文` / `# 工作目录策略` 两段仍走
  `system_prompt_builder.add_section`，里面的 cwd 与 workspace 都是 per-member 值，会各占一份
  前缀 KV cache（违反 [[F_68]] 立的 S_09 不变量 6a）。用户明确要求本次先只修正取值，
  attachment 化留待后续。
- `stable_base` 的最终清理（连同 `WorkspaceSpec` 字段本身）未做。
