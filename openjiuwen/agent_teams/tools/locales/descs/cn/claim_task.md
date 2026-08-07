领取或完成任务（仅 Teammate 可用）。

## 使用场景

**领取任务开始工作：**
- 从 view_task 中找到 **pending 且 assignee 是你**（或尚无 assignee）的任务后，设置 status=claimed 领取
- Leader 创建时若已把任务指派给你：对 PENDING(assignee=你) 调用 claim_task 会直接开工，**不会**因为已有 assignee 而失败
- **不要**认领 assignee 指向其他成员的任务（会被拒绝）
- 应选择匹配自己领域专长的未指派任务；指派给你的任务应优先处理
- **同一时刻只能有一个进行中（in_progress）的任务**：若你已有认领在做的任务，需先完成它再领取新任务，否则领取会被拒绝

**标记任务完成：**
- 完成任务描述的所有工作后，设置 status=completed 标记完成
- 重要：完成后应调用 view_task 寻找下一个可用任务

- 只有在完全完成任务后才能标记 completed
- 如果遇到错误、阻塞或无法完成，保持任务为 in_progress 状态
- 被阻塞时，通过 send_message 通知 leader
- 认领任务后若长时间无法完成，应及时通过 send_message 与 leader 沟通，调整任务范围或拆分任务
- 以下情况不得标记 completed：
  - 测试未通过
  - 实现不完整
  - 遇到未解决的错误

## 状态流转

`pending` → `in_progress` → `completed`

## 过期检查

更新前应通过 view_task(action=get) 获取任务最新状态。

## 示例

领取任务：
{"task_id": "task-1", "status": "claimed"}

完成任务：
{"task_id": "task-1", "status": "completed"}
