领取或完成任务（仅 Teammate 可用）。

## 使用场景

**领取任务开始工作：**
- 从 view_task 中找到 pending 且无人认领的任务后，设置 status=claimed 领取
- 应选择匹配自己领域专长的任务

**标记任务完成：**
- 完成任务描述的所有工作后，设置 status=completed 标记完成
- 重要：完成后应调用 view_task 寻找下一个可用任务

- 只有在完全完成任务后才能标记 completed
- 如果遇到错误、阻塞或无法完成，保持任务为 claimed 状态
- 被阻塞时，通过 send_message 通知 leader
- 认领任务后若长时间无法完成，应及时通过 send_message 与 leader 沟通，调整任务范围或拆分任务
- 以下情况不得标记 completed：
  - 测试未通过
  - 实现不完整
  - 遇到未解决的错误

## 状态流转

`pending` → `claimed` → `completed`

## 过期检查

更新前应通过 view_task(action=get) 获取任务最新状态。

## 示例

领取任务：
{"task_id": "task-1", "status": "claimed"}

完成任务：
{"task_id": "task-1", "status": "completed"}
