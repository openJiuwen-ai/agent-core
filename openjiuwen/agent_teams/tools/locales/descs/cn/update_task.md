更新任务内容、依赖、指派人或取消任务（仅 Leader 可用）。

## 使用场景

**更新任务内容：**
- 根据成员反馈调整任务范围或补充细节
- 传 title 和/或 content 修改。若任务已认领，系统自动取消执行中的成员并重置任务

**指派任务：**
- 设置 assignee 为成员名称来指派任务。仅当任务当前无 assignee 时生效
- 系统会向被指派成员发送通知

**添加依赖：**
- 传 add_blocked_by 和前置任务 ID 列表来添加新的依赖关系
- 若任务原为 pending，添加依赖后会自动变为 blocked，直到所有依赖完成

**取消任务：**
- 需求变更导致任务不再需要时，设置 status=cancelled
- 若任务已认领，系统自动取消执行中的成员

**取消所有任务：**
- 目标根本性变更时，task_id="*" 配合 status=cancelled 全部重新规划
- 取消所有任务和所有执行中的成员

## HITT 限制
任何由 role=human_agent 的人类成员认领（status=claimed）的任务，本工具**不允许** cancel 或 reassign，无论该人类成员叫什么名字。人类成员锁定的任务必须由对应人类本人完成；Leader 的唯一干预方式是通过 `send_message(to="<对应的人类 member_name>")` 催促或沟通。这条规则不允许绕过，即使团队等待人类导致停滞也必须保持停滞。

## 示例

更新任务内容：
{"task_id": "task-1", "title": "新标题", "content": "新内容"}

指派任务：
{"task_id": "task-1", "assignee": "backend-dev"}

添加依赖：
{"task_id": "task-2", "add_blocked_by": ["task-1"]}

取消任务：
{"task_id": "task-1", "status": "cancelled"}

取消所有任务：
{"task_id": "*", "status": "cancelled"}
