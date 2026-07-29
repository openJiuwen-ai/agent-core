# SwarmFlow agent 支持 worktree isolation

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-22 |
| 范围 | `openjiuwen/agent_teams/workflow/engine/{facade,provider,primitives,seam}.py`、`openjiuwen/agent_teams/workflow/backends/team_worker_backend.py`、`openjiuwen/agent_teams/workflow/{runner,tool_swarmflow,worktree}.py`、`openjiuwen/agent_teams/{rails/team_context,workspace_layout}.py`、`openjiuwen/agent_teams/agent/agent_configurator.py`、`openjiuwen/agent_teams/prompts/{cn,en}/leader_swarmflow.md`、`openjiuwen/agent_teams/tools/locales/descs/{cn,en}/swarmflow.md`、`tests/unit_tests/agent_teams/workflow/test_worker_backend.py` |
| 测试基线 | `uv run pytest -q tests/unit_tests/agent_teams/workflow/test_worker_backend.py` → `11 passed`；`uv run pytest -q tests/unit_tests/agent_teams/test_worktree_naming.py tests/unit_tests/agent_teams/test_spawn_manager_worktree.py tests/unit_tests/agent_teams/agent/test_worktree_event_bridge.py` → `13 passed` |
| Refs | F_38_team-teammate-worktree-isolation-agenttool.md |

## 背景

SwarmFlow 已经可以通过脚本让 agent 使用 git worktree，但此前做法是让某个 worker
在 prompt 中执行 `git checkout` / `git worktree add` / `git merge` 等命令。这个路径
能跑通，却不是框架级隔离：

- workflow 脚本需要知道 git worktree 创建细节；
- worker 是否真的在隔离 cwd 中工作，取决于 prompt 是否正确 `cd`；
- 清理 / 保留 / 冲突集成逻辑散落在脚本里；
- 与 team teammate 已经落地的 `isolation="worktree"` 语义不一致。

本次改动把 worktree 隔离提升为 SwarmFlow `agent()` 的正式选项：

```python
await agent("write code", label="sum-agent", isolation="worktree")
```

脚本只声明该 agent 需要隔离，具体 worktree 创建、workspace root 覆盖、收尾判定由
`TeamWorkerBackend` 托管。

## 决策

1. **`agent()` 增加 `isolation` 参数**  
   `facade` / `Provider` / `EngineProvider` / `primitives` 全链路透传
   `isolation`。当前只接受 `None` 或 `"worktree"`，未知值立即抛
   `WorkflowError`，避免脚本误写后静默退化为普通 workspace。

2. **engine 只传 option，不创建 worktree**  
   `workflow/engine` 仍不 import team 业务模块；它只把 `isolation` 放进
   backend opts。真正的 worktree 创建、change detection、remove/keep 决策都在
   `TeamWorkerBackend`。

3. **worker worktree 创建复用 owner-scoped manager**  
   `TeamWorkerBackend` 在看到 `opts["isolation"] == "worktree"` 时调用
   `WorktreeManager.create_owner_worktree(slug)`。slug 复用 team worktree 命名规则：
   `agent-{team_name}-{worker_member_name}-{hash8}`。

4. **worker 从启动开始就在 worktree workspace 中运行**  
   worker spec 派生时，`WorkspaceSpec.root_path` 被覆盖为 `worktree_path`，
   `stable_base=False`，且不登记到 `TeamBackend.cleanup_path`，避免 team cleanup
   绕过 `git worktree remove` 直接删除目录。

5. **收尾策略对齐 teammate worktree fail-closed**  
   worker 单轮结束后检查 worktree：
   - 无未提交修改且无新增提交：自动 `remove_worktree`
   - 有修改、有提交、hook-based、无法确认状态、无法解析 repo root：保留 worktree

   保留的 worktree 交由 leader 后续集成；不让 worker 之间互相 push / pull / merge。

6. **worktree manager 通过 BuildContext.extras 透传**  
   `AgentConfigurator` 复用 team 既有不可序列化对象传递方式，把 owner-scoped
   `WorktreeManager` 写入 `BuildContext.extras`。`SwarmflowTool` 只把
   `build_context` 传给 `run_swarmflow` / `TeamWorkerBackend`，backend 再通过
   `get_worktree_manager(build_context)` 读取 manager；不在 `run_swarmflow` 或
   `TeamWorkerBackend` 上新增单独的 `worktree_manager` 参数。
7. **脚本生成约束放在 swarmskill-creator**  
   `leader_swarmflow` 只负责路由：用户要求 worktree / 分支隔离时，把隔离诉求转交给
   `swarmskill-creator`，不在 leader prompt 中展开 `agent()` 代码示例或 worker 语义。
   具体脚本 authoring 规则由 `swarmskill-creator` 的 Stage 3b 与
   `workflow.py.template` 承担：代码修改 agent 在 `agent()` 调用上传
   `isolation="worktree"`，不要把 `git worktree add` 写进写代码 agent 的 prompt；
   集成阶段由明确的 merge agent 基于真实项目仓库的 git 状态、`git worktree list`、
   分支和提交信息完成提交、合并和冲突处理。

8. **不把 worktree metadata 注入脚本返回值**  
   backend 收尾只负责生命周期：干净 worktree 删除；有修改 / 有提交 / 状态不可确认
   则保留给后续集成。`agent()` 返回值保持 worker 原始输出，不追加
   `worktree.path` / `worktree.branch`。后续 merge phase 由 merge agent 自己检查
   真实仓库状态和 worktree / branch / commit 信息。

## 拒绝的方案

- **继续让脚本手写 git worktree 命令**：会让每个 workflow 重复实现创建、cwd、清理、
  merge 规则，也无法保证 worker 真正在隔离 workspace 中执行。
- **把 isolation 放进 prompt 而不是函数参数**：LLM 只能“尽量遵守”，框架无法验证、
  无法复用统一清理策略。
- **让 worker 调 `enter_worktree` / `exit_worktree` 工具**：这会回到手动进入模型；
  当前目标是 worker 启动时 cwd 已经是 worktree。
- **未知 isolation 值忽略不报错**：脚本拼错时会产生危险假象，必须 fail-fast。

## 验证

- `test_agent_isolation_option_is_forwarded_to_backend`：验证 DSL 参数能从
  `from swarmflow import agent` 的 facade 进入 backend opts。
- `test_agent_rejects_unknown_isolation`：验证未知 isolation 值抛 `WorkflowError`。
- `test_worktree_isolation_sets_worker_workspace_and_removes_clean_worktree`：
  验证 worker worktree 创建、workspace root 覆盖、干净 worktree 自动 remove。
- `test_worktree_isolation_keeps_changed_worktree_without_return_metadata`：
  验证有修改的 worktree 会被保留，但不会把 path / branch metadata 返回给脚本。
- 回归 teammate worktree 相关测试，确认 team teammate 隔离路径未受影响。

## 已知遗留

- worktree 冲突合并仍由 leader / 上层 workflow 负责，本次只解决 worker 的隔离创建与收尾。
