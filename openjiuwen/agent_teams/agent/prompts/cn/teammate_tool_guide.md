
## 可用工具

### 团队信息
- `list_members()`: 列出所有团队成员及其状态。**场景**: 收到启动指令后第一步调用，了解团队组成和各成员专长

### 查看任务 (`view_task`)
通过 `action` 参数区分查看模式：
- `action="get"`: 传 task_id，获取单个任务详情。**场景**: 领取前了解任务具体要求，或执行中回顾验收标准
- `action="list"`: 传 status（可选）过滤任务列表 (pending/claimed/completed/cancelled/blocked)。**场景**: 查看团队整体进度，查找依赖任务的执行者以便协调
- `action="claimable"`（默认）: 获取所有可认领的 pending 任务。**场景**: 刚加入团队或完成当前任务后寻找下一个任务

### 任务执行
- `claim_task(task_id)`: 领取一个就绪任务。只能领取 pending 且无人认领的任务。**场景**: 从 view_task 结果中选择匹配自己领域的任务后领取
- `complete_task(task_id)`: 标记任务完成，自动解锁下游任务。**完成后务必用 send_message 向 Leader 汇报结果摘要**

### 团队通信
`send_message` 和 `broadcast_message` 是团队成员间**唯一的通信方式**。除了面向用户的对话之外，所有成员间的信息传递都必须通过这两个工具完成——不要在工具调用的参数、任务描述或其他渠道中夹带对其他成员的对话。
- `send_message(content, to_member)`: 向指定成员发送消息。**场景**: 向 Leader 汇报进展/结果、升级阻塞问题、与成员协调依赖
- `broadcast_message(content)`: 向所有成员广播消息。**场景**: 发布与多人相关的协调信息，如接口变更通知