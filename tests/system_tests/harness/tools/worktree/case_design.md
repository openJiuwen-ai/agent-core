# DeepAgent worktree 工具集成测试用例设计

## 1. 目标与范围

验证 `DeepAgent` 在装载 `EnterWorktreeTool` / `ExitWorktreeTool` 后，
能否驱动一个完整的 git worktree 隔离生命周期：

- 工具是否被正确注册并由 LLM 触发；
- `WorktreeManager` 与 `cwd` ContextVar 是否正确联动；
- 工具失败路径（已在会话中、参数非法、未提交变更）的 ToolOutput 是否
  按预期返回，并且不污染外层会话。

**不在范围内**：worktree 的 `WorktreeRail` / `cleanup` 行为、跨进程
spawn、远端 backend——这些已有专门的单元测试覆盖。

## 2. 公共环境

| 资产 | 说明 |
|---|---|
| 临时 git 仓库 | `pytest tmp_path/<repo>`，含一个 README.md 初始 commit；origin/main 指向 HEAD（满足 `GitBackend._resolve_base`） |
| Workspace | `repo/<wkspc>` 子目录；让 `find_canonical_git_root(workspace)` 能向上找到仓库 |
| Mock LLM | `MockLLMModel` 预置工具调用序列，固定每个用例的执行路径 |
| Tool Trace Rail | `AgentRail` 子类，`after_tool_call` 钩子里捕获 `(tool_name, tool_args, tool_result)`，供断言 |
| WorktreeManager | 使用真实 `GitBackend`，`workspace_root=workspace`，开启 `enabled=True` |

每个用例都跑在独立 `tmp_path` 下，并在结束时清理 worktree session
ContextVar、cwd ContextVar，避免互相污染。

## 3. 用例清单

### TC-01 happy path：enter → 写文件 → exit(keep)

**步骤**

1. LLM 第 1 轮调用 `enter_worktree({"name": "wt-happy"})`。
2. LLM 第 2 轮调用 `write_file(...)` 在 worktree 内写入新文件。
3. LLM 第 3 轮调用 `exit_worktree({"action": "keep"})`。
4. LLM 第 4 轮返回最终文本回答。

**断言**

- 第 1、3 步 `ToolOutput.success=True`。
- worktree 目录存在；分支名形如 `worktree-<slug>`；
  `<workspace>/.worktree/<slug>` 软链存在。
- `<worktree>/notes.md`（写入文件）确实在 worktree 路径下，
  仓库主路径下对应文件不存在。
- 退出后 `get_current_session() is None`，
  `get_cwd()` 已经回到 enter 之前的 cwd。

### TC-02 enter → 写文件 → exit(remove, discard)

**步骤**

1. `enter_worktree`。
2. `write_file` 在 worktree 内写入。
3. `exit_worktree({"action": "remove", "discard_changes": true})`。
4. text answer。

**断言**

- 第 3 步 `ToolOutput.success=True`，且 `data.discarded_files >= 1`。
- worktree 目录已被删除。
- 主仓库 `git status` 干净，没有遗留的临时分支引用 worktree HEAD。
- `<workspace>/.worktree/<slug>` 软链已被清理。

### TC-03 两阶段确认：remove 拒绝未提交变更，discard=true 重试成功

**步骤**

1. `enter_worktree`。
2. `write_file` 写入 → 制造未提交变更。
3. `exit_worktree({"action": "remove"})` —— 期望被工具拒绝。
4. `exit_worktree({"action": "remove", "discard_changes": true})` —— 重试成功。
5. text answer。

**断言**

- 第 3 步 `ToolOutput.success=False`，error 中含 "uncommitted"。
- 第 3 步**不会**改动 worktree（目录、分支、文件都还在）。
- 第 4 步 `success=True`，worktree 已删除。

### TC-04 重复 enter 在已激活会话中被拒绝

**步骤**

1. `enter_worktree({"name": "wt-first"})`。
2. `enter_worktree({"name": "wt-second"})` —— 期望被工具拒绝。
3. `exit_worktree({"action": "remove", "discard_changes": true})`。
4. text answer。

**断言**

- 第 2 步 `success=False`，error 含 "Already in worktree" 和 "wt-first"。
- 第 1 步创建的 worktree 没有被改名/重建，路径不变。
- 第 3 步可以正常退出（说明 session 状态没被第 2 步破坏）。

### TC-05 没有会话时调用 exit 报错并能恢复

**步骤**

1. `exit_worktree({"action": "keep"})` —— 期望被工具拒绝。
2. `enter_worktree`。
3. `exit_worktree({"action": "keep"})`。
4. text answer。

**断言**

- 第 1 步 `success=False`，error 含 "No active worktree session"。
- 第 1 步**不会**改变当前 cwd。
- 第 2、3 步正常完成；第 3 步后 `get_current_session() is None`。

### TC-06 非法 slug 被拒绝，纠正后能进入

**步骤**

1. `enter_worktree({"name": "../escape"})` —— 期望被工具拒绝。
2. `enter_worktree({"name": "safe-name"})`。
3. `exit_worktree({"action": "remove", "discard_changes": true})`。
4. text answer。

**断言**

- 第 1 步 `success=False`，error 含 "Invalid worktree name"。
- 第 1 步不会创建任何目录，`<workspace>/.worktrees` 下无遗留。
- 第 2 步成功，目录名为 `safe-name`。

## 4. 断言策略

- **结构断言**优先：通过 `ToolTraceRail` 拿 `ToolCallInputs.tool_result`
  直接判断每一步成功/失败；不依赖 LLM 文本输出做断言。
- **文件系统断言**：`pathlib.Path` 直接看真实路径，不要 mock。
- **会话/CWD 断言**：用 `get_current_session()` / `get_cwd()` 验证
  ContextVar 状态，确认副作用被正确清理。

## 5. 隔离与清理

- 每个用例使用独立 `tmp_path`，git 仓库一次性。
- `autouse` fixture 在 setup/teardown 重置：
  - `set_current_session(None)`
  - `_cwd_state.set(CwdState())`
- `Runner.start()` / `Runner.stop()` 包住每个用例，避免共享 `resource_mgr`
  状态泄漏。

## 6. 不覆盖项与后续

- 真实 LLM 端到端（依赖外部 API）—— 可在另一个文件中以
  `@pytest.mark.skip("requires real credentials")` 形式补，模型自由生成
  工具调用，比 mock 更接近线上但不稳定。
- 多 agent 并发使用同一 workspace 创建 worktree 的竞争场景。
- `WorktreeRail` 介入下的 hook 行为（已有单测覆盖）。
