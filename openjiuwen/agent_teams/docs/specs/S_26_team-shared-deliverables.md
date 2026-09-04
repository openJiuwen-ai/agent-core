# 团队共享产物目录（Team Shared Deliverables）

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/team_workspace/`、`agent_teams/rails/`、`agent_teams/agent/agent_configurator.py`、`harness/schema/build_context.py`；平台侧 `jiuwenswarm/agents/swarm/`、`jiuwenswarm/common/team_artifacts.py` |
| 最近一次修订日期 | 2026-09-01 |
| 关联 feature | `F_89_team-shared-deliverables-outputs.md` · `S_13_team-workspace.md`（修订：.team 路由废止） |

## 范围 / 边界

本 spec 描述团队**最终产物目录**：无项目目录的团队成员把最终交付物写到哪里、如何被锁与版本控制、
提示词如何告知模型。是对 [[S_13]]（team-workspace）的补充与局部修订：[[S_13]] 的 `.team/` 挂载点
路由废止，改为按绝对路径访问 `team-workspace/artifacts/` 子树。

### 管什么

- 无 project_dir 的团队成员共享一个最终产物目录 `team-workspace/artifacts/<YYYY-MM-DD>/chat-<n>/outputs/`。
- 无 project_dir 的成员各自的临时工作目录 `.../chat-<n>/work/<member_slug>/`（cwd，临时文件 per-member 隔离）。
- 该目录的版本（git auto-commit、history 查询）：outputs 在 team-workspace git 仓库范围内，auto_commit 生效。
- 该目录下文件的写锁（in-memory，按文件路径粒度，带 timeout）。
- 团队信息块告知模型该目录的绝对路径（仅无 project_dir 时注入）。
- 运行时提示词的 `has_projectless_task` 分支（PR 5702）对 team member 生效。

### 不管什么

- **不管单 agent projectless**：单 agent 的 `Documents/JiuwenSwarm/<date>/chat-<n>/`（含 work/outputs）
  归 `jiuwenswarm/common/projectless_workspace.py`，team 不复用其根（非 git 仓库、单会话）。
- **不管 member cwd**：无 project_dir 时 cwd 留 member workspace（不引入 work/）；有 project_dir 时
  cwd = worktree 副本。outputs 是产物落点，不是 cwd。
- **不管 team-workspace 其他内部数据**：prompts/team.yaml/trajectories/skills-visibility 仍是配置目录，
  按绝对路径访问，与 outputs 同处 team-workspace 但角色不同。

## 目录形态

```
<team-workspace>/                          # 配置/内部数据目录（绝对路径访问）
├── prompts/{system,identity,tool}/
├── .team-meta/team.yaml
├── trajectories/
├── skills-visibility.json
└── artifacts/                             # 产物根（本 spec）
    └── <YYYY-MM-DD>/
        └── chat-<n>/                      # 全 team 共享一个 chat-n（按 session 复用）
            ├── metadata.json              # chat_id/session_id/title（原 query）
            ├── .session_id                # 分配碰撞探针
            ├── work/                      # per-member 临时工作目录
            │   └── <member_slug>/         # 每个成员的 cwd（临时文件隔离）
            └── outputs/                  # 共享最终产物（成员靠文件名区分）
```

- **chat-n 复用**：同一 team session_id 的所有成员共享同一 chat-n 目录（registry 在
  `artifacts/.team_artifacts/<safe_session>.json`，记录 `root_dir`）。
- **outputs 共享、work per-member**：outputs 全 team 共享一个、靠文件名区分；work 按成员 slug 隔离，
  临时文件不散落在一起。member 的 cwd = `work/<member_slug>/`（无 project_dir 时）。
- **按天布局**：复用单 agent projectless 的 `_allocate_task_root` 思路（`<date>/chat-<n>/`），
  根植于 team-workspace（平台侧 `jiuwenswarm/common/team_artifacts.py`，`resolve_member_work_dir`）。

## 注入路径（BuildContext → 提示词 → rail）

- `BuildContext.team_outputs_dir`（基类字段，平台填充）：无 project_dir 时 =
  `team-workspace/artifacts/<date>/chat-<n>/outputs`；有 project_dir = None。
- `BuildContext.resolve_member_work_dir()`（基类方法，平台覆盖）：无 project_dir 时 =
  `team-workspace/artifacts/<date>/chat-<n>/work/<member_slug>/`；基类返回 None。per-member，不入 seed。
- `SwarmBuildContext.task_workspace_root`（per-team，seed 序列化）：产物根（chat-n 目录）。
- `agent_configurator`：`team_outputs_dir = spec.build_context.team_outputs_dir` → 传 `TEAM_POLICY`
  RailSpec.params；`member_cwd = worktree > project > resolve_member_work_dir()`（无 project_dir 无 worktree
  时 cwd 移到 `work/<member>/`，临时文件 per-member 隔离）。
- `TeamPolicyRail` → `TeamContextTracker(team_outputs_dir=...)` → `build_team_info_text(team_outputs_dir=...)`。
- `member_rails._build_runtime_prompt_rail`：无 project_dir 时 `resolve_member_work_dir()` 得 work_dir，
  `set_runtime_paths(cwd=work_dir, task_workspace_root=..., task_work_dir=work_dir, task_outputs_dir=...)`
  → `RuntimePromptRail` 走 `has_projectless_task=True` 分支（PR 5702 已有，team 适配只负责传参）。
- `TeamWorkspaceRail(outputs_dir=...)`：拦 outputs 绝对路径 write，lock + auto_commit + publish。

## 锁与版本控制

- `TeamWorkspaceRail` 不再拦 `.team/` 前缀，改拦 `outputs_dir` 绝对前缀（`os.path.commonpath` 判定）。
- 无 outputs_dir（有 project_dir 的 member）→ rail 不拦截（产物在项目内，不经 workspace 版本控制）。
- `auto_commit` / `history`（workspace_meta 工具）：outputs 在 team-workspace git 仓库范围内，能工作。
- `set_team_workspace(workspace_path)`（CwdState）：仍存 team-workspace 根（内部目录可信判定用）。

## .team 路由废止清单

- `agent_configurator`：去掉 `team_workspace_mount` 计算与 `mount_into_workspace` 调用。
- `team_policy_rail` / `elements.TeamPolicyInput`：`team_workspace_mount` 参数 → `team_outputs_dir`。
- `team_context.TeamContextTracker`：`team_workspace_mount` → `team_outputs_dir`，删 `team_workspace_symlink_note`。
- `prompts/messages.py`：`build_team_info_text(team_workspace_mount=...)` → `(team_outputs_dir=...)`。
- `team_workspace/rails.py`：`TEAM_PREFIX=".team/"` 删除，改绝对路径 outputs 判定。
- 提示词模板（16 处）+ 工具描述（6 处）：`.team/` → 团队共享产物目录（指 team 信息块绝对路径）。
- 保留：`mount_into_workspace` / `mount_worktree` 作为可选 symlink 便利（worktree 代码型 team 遗留，模型不再引用 `.team`）。

## 测试基线

- `tests/unit_tests/agent_teams/team_workspace/test_outputs_interception.py`（新）：rail 拦 outputs、commit、resolve。
- `tests/unit_tests/agent_teams/prompts/test_team_messages.py`：`build_team_info_text` 新签名 + outputs bullet。
- `tests/unit_tests/agent_teams/test_team_policy_rail.py`：`team_outputs_dir` 传参 + 团队信息块渲染。
- `tests/unit_tests/agent_teams/prompts/test_member_system_prompt.py`：去 `.team` 措辞断言。
- 平台侧 `jiuwenswarm/tests/unit_tests/common/test_team_artifacts.py`（新）：按天布局、复用、无 work、registry。
