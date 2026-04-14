查看团队任务信息。

## 使用场景

### action=list（默认，零参数调用）
- 查看所有任务的整体进度
- 识别被阻塞的任务和瓶颈
- 完成任务后检查是否有新解锁的工作
- 分配任务前了解当前任务状态

### action=get（需要 task_id）
- 开始工作前获取任务的完整要求和验收标准
- 理解任务的依赖关系（blocked_by: 被什么阻塞，blocks: 阻塞了什么）
- 成员汇报进度后核实任务状态

### action=claimable
- 快速获取所有可认领的 pending 任务
- 完成当前任务后寻找下一个可执行任务

## 输出

### list / claimable
每个任务的摘要：task_id, title, status, assignee, blocked_by。
不含 content — 需要详情请用 action=get 查看单个任务。

### get
单个任务完整详情：含 content, blocked_by（前置依赖）, blocks（下游依赖）。

## Tips

- list 不返回 content，token 开销低。需要详情时再 get 单个任务。
- blocked_by 非空的任务不可认领 — 先完成其前置任务。
- 优先处理 ID 序号靠前的任务，前序任务通常为后续任务建立上下文。

## 成员工作流

1. 完成当前任务后，调用 view_task（默认 list）查看可用工作
2. 找到 status=pending、assignee 为空、blocked_by 为空的任务
3. 用 claim_task(status=claimed) 认领，然后用 action=get 获取完整要求
4. 若被阻塞，专注于解除阻塞或通知 leader
