# F_89：团队共享产物目录（outputs）与 .team 路由废止

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-09-01 |
| 范围 | `agent_teams/prompts/cn|en/*.md`（teammate_policy / leader_policy / leader_workflow / leader_workflow_hybrid / human_agent_policy / reviewer_inspector / reviewer_verifier）、`prompts/messages.py`（`build_team_info_text` + 标签表）、`team_context.TeamContextTracker`、`rails/team_policy_rail.py`、`rails/elements.py`（`TeamPolicyInput` / `TeamWorkspaceInput`）、`agent/agent_configurator.py`（去 `.team` mount、读 `team_outputs_dir`）、`harness/schema/build_context.py`（`BuildContext.team_outputs_dir` 基类字段）、`team_workspace/rails.py`（`TeamWorkspaceRail` 改拦 outputs 绝对前缀）、`team_workspace/manager.py`（docstring）、`spawn/external_cli_spawn.py`、`tools/locales/descs/cn|en/workspace/workspace_meta.md`、`tools/locales/cn.py|en.py` |
| 测试基线 | `tests/unit_tests/agent_teams/prompts/test_team_messages.py`、`test_team_policy_rail.py`、`prompts/test_member_system_prompt.py`（断言改 outputs / 去 .team）、`team_workspace/test_outputs_interception.py`（新）；配套 jiuwenswarm 测试见 [[jiuwenclaw-doc]] |
| Refs | 简报 `jiuwenclaw/doc/analysis/2026-09/2026-09-01-pr5702-team-adaptation-complete-brief.md`；spec `S_13_team-workspace.md`（修订）、`S_26_team-shared-deliverables.md`（新） |

## 背景

### 症状

无项目目录的团队成员产物散落三处：`team-workspace/.team/<team>/`、默认项目 `/.team/<team>/`、
member workspace 的 `.team` symlink。根因：① `_with_project_cwd` 无 project_dir 时 `agent_spec.cwd=None`；
② `agent_configurator member_cwd=None` 兜底默认项目；③ 提示词 `.team/` 相对路径 + cwd 错位 → 产物落 `.team` 实子目录。
PR 5702 把单 agent 的无项目场景拆成 `work/`（临时）+ `outputs/`（产物），但**未碰 team**：team member 走
`has_projectless_task=False` 的 else 分支（产物放项目内），无 project_dir 的 team member 拿不到 outputs 路径。

### 定位

team 适配 = 让无 project_dir 的 team member **走 PR 5702 已有的 `has_projectless_task=True` 分支**，
但不复用单 agent 的 Documents 根（那是单会话的、非 git 仓库 auto_commit 失效）。产物落
`team-workspace/artifacts/<YYYY-MM-DD>/chat-<n>/outputs/`：全 team 共享一个 chat-n、靠文件名区分成员产物；
outputs 在 team-workspace git 仓库范围内 → auto_commit / history 工作。`.team` 符号链接挂载点与
`.team/` 相对路径提示词全部废止，改为绝对路径访问。

## 决策（用户裁决）

1. **产物不进 Documents/outputs**：那是单会话的。移到 `team-workspace/artifacts/<date>/chat-<n>/outputs/`。
2. **引入 per-member work/ 子目录**：无 project_dir 的 member 的 cwd 移到
   `team-workspace/artifacts/<date>/chat-<n>/work/<member_slug>/`，临时文件 per-member 隔离、不散落在一起；
   prompt 的「临时工作目录」指向该 work 目录，最终产物指向共享 outputs。（最初方案 A 不建 work，
   后改为带 work 以隔离临时文件。）
3. **outputs 共享、work 隔离**：outputs 全 team 共享一个 chat-n、靠文件名区分；work per-member。
4. **有 project_dir** → 不建 outputs/work，产物写项目内，走 else 分支（对齐单 agent）。
5. **`.team` 路由全去掉**：symlink mount、相对路径提示词、TeamWorkspaceRail 的 `.team/` 前缀拦截。
6. **TeamWorkspaceRail 锁改拦 outputs** 绝对前缀；auto_commit/publish 走 outputs。
7. team-workspace 保持纯配置/内部数据目录（prompts/team.yaml/trajectories/skills-visibility/artifacts）。

## 数据流

```
assembly.enrich_team_spec_for_swarm
  └─ 无 project_dir → get_team_artifact_workspace(team_ws_root, session_id)
        → task_workspace_root / team_outputs_dir  ← per-team，塞进 SwarmBuildContext
SwarmBuildContext.to_seed → 跨序列化边界（spawned teammate / distributed remote）
  └─ from_seed 重建 context（team_outputs_dir 走基类 BuildContext 字段）
agent_configurator
  └─ team_outputs_dir = spec.build_context.team_outputs_dir → 传给 TEAM_POLICY RailSpec.params
  └─ member_cwd：worktree > project > resolve_member_work_dir()（无 project_dir 无 worktree 时
     cwd 移到 work/<member>/，临时文件 per-member 隔离）
elements.build_team_policy_rail
  └─ TeamPolicyInput.team_outputs_dir → TeamPolicyRail(team_outputs_dir=...)
        └─ TeamContextTracker(team_outputs_dir=...)
              └─ build_team_info_text(team_outputs_dir=...) → 团队信息块「最终产物目录」bullet（仅无 project_dir）
member_rails._build_runtime_prompt_rail
  └─ RuntimePromptInput.team_outputs_dir → resolve_member_work_dir(task_workspace_root, member_name)
        → set_runtime_paths(cwd=work_dir, task_work_dir=work_dir, task_outputs_dir=...)
        → RuntimePromptRail has_projectless_task=True 分支：三段路径（root/work<member>/outputs）+ 产物放 outputs
member_rails._build_team_workspace_report_path_rail
  └─ TeamWorkspaceReportPathInput.team_outputs_dir → rail(outputs_dir=...)
        → 提示：team-workspace 标配置目录、无 project_dir 产物指 outputs、中间文件留 cwd（work/<member>/）
elements.build_team_workspace_rail
  └─ TeamWorkspaceInput.team_outputs_dir → TeamWorkspaceRail(outputs_dir=...)
        → 拦 outputs 绝对路径 write：lock + auto_commit + publish
```

## 落盘

| 角色 | 目录 | 位置 |
|---|---|---|
| member 内部数据 | memory/skills/todo/IDENTITY | `~/.jiuwenswarm/.agent_teams/<t>/workspaces/<member>_workspace`（不动） |
| team 配置/内部数据 | prompts/team.yaml/trajectories/skills-visibility | `~/.jiuwenswarm/.agent_teams/<t>/team-workspace`（不动，绝对路径访问） |
| member cwd | bash 执行、临时文件 | 无 project_dir：`team-workspace/artifacts/<date>/chat-<n>/work/<member>/`（per-member 隔离，新）；有 project_dir：worktree 副本 |
| member 产物 | 最终交付物 | **无 project_dir：`team-workspace/artifacts/<date>/chat-<n>/outputs/`（全 team 共享，新）**；有 project_dir：project 内（不建 outputs） |

## 提示词变更（22 处）

`.team/` → 团队共享产物目录（见团队信息块「最终产物目录」绝对路径）：
- `prompts/cn|en/*.md`（teammate_policy/leader_policy/leader_workflow*/human_agent_policy/reviewer_*）16 处
- `prompts/messages.py`：删 `team_workspace_symlink_note`，加 `team_outputs_dir` 参数 + `team_outputs_dir`/`team_outputs_dir_purpose` 标签，`build_team_info_text` 去掉 mount 分支、加 outputs bullet
- `tools/locales/descs/cn|en/workspace/workspace_meta.md` 4 处、`tools/locales/cn.py|en.py` 2 处
- jiuwenswarm `team_workspace_report_path_rail.py`（见 jiuwenclaw 仓）

## 遗留 / 已知边界

- **`mount_into_workspace` / `mount_worktree`** 保留为可选 symlink 便利（worktree 隔离的代码型 team 仍会在 worktree 内建 `.team` symlink，但模型不再引用 `.team`，纯遗留，未删——见 [[jiuwenclaw-doc]] 第 10 项磁盘清理）。
- **`_materialize_team_deliverable`**（jiuwenswarm）：无 project_dir 时产物已在 outputs，原样交付不复制；有 project_dir 复制到项目（逻辑不变，docstring 更新）。
- 外部 CLI 成员（`teammate_policy_external.md`）无 `.team` 引用，走路径版，改提 outputs 即可（已确认无引用）。
