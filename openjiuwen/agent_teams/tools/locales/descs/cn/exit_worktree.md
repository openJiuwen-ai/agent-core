退出由 enter_worktree 创建的 worktree 会话，将工作目录恢复到原始位置。

## 作用范围

仅操作当前会话中由 enter_worktree 创建的 worktree。不会触碰：
- 手动通过 `git worktree add` 创建的 worktree
- 其他成员的 worktree
- 从未调用 enter_worktree 时当前所在的目录

在 enter_worktree 会话之外调用为空操作（no-op）。

## 何时使用

- 任务完成，需要退出 worktree
- 需要切换到其他工作上下文

## 参数

- `action`（必填）：`"keep"` 或 `"remove"`
  - `"keep"` -- 保留 worktree 目录和分支在磁盘上，后续可再次进入
  - `"remove"` -- 删除 worktree 目录及其分支，适用于工作已完成或已放弃
- `discard_changes`（可选，默认 false）：仅在 `action="remove"` 时有意义。当 worktree 有未提交文件或未合并提交时，工具会拒绝删除并列出变更，需设为 true 确认丢弃

## 行为

- 恢复会话工作目录到 enter_worktree 之前的位置
- action=remove 时，先检测未提交变更和新提交，有变更则拒绝（除非 discard_changes=true）
- 退出后可再次调用 enter_worktree 创建新的 worktree
