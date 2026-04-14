创建一个隔离的 git worktree 并将当前会话切换到其中。

## 何时使用

- 需要在独立副本中修改代码，避免与其他成员的分支冲突和文件竞争
- 需要在不影响主仓库的前提下进行实验性修改

## 何时不使用

- 仅需要创建分支或切换分支 -- 使用 git 命令
- 不涉及并行修改同一仓库的场景

## 前置条件

- 当前必须在一个 git 仓库中
- 不能已经在一个 worktree 会话中（需先 exit_worktree）

## 行为

- 在 `.agent_teams/worktrees/` 下基于 HEAD 创建新分支和 worktree
- 将会话的工作目录（CWD）切换到新 worktree
- 所有后续文件操作和 shell 命令在 worktree 内执行，不影响主仓库
- 使用 exit_worktree 离开（keep 保留或 remove 删除）

## 参数

- `name`（可选）：worktree 名称。不提供则自动生成随机名称。
