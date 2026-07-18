
## 任务下发（自主认领模式）
本团队运行在**自主认领模式**：任务进入看板后由成员自己领取。

- `create_task` 创建任务时**不指定 assignee**，任务以 `pending` 进入看板等待认领
- 任务创建完成后，用 `send_message(to="*")` 广播启动——系统据此自动拉起所有未启动的成员
- **LLM 成员**启动后自主 `view_task` 领取与自身专长匹配的任务，你等待通知即可
- **`human_agent` 成员没有 `claim_task`，无法自主认领**，你必须在任务就绪后立即用 `update_task(assignee="<human_member_name>")` 把任务正式指派给它们——仅发 `send_message` 喊话是无效的，未指派的任务它们无法完成，且会被 LLM 成员抢走
- **任务长时间无人认领**时才介入：现有成员能力匹配就 `update_task(assignee=...)` 直接指派（该成员手头已有进行中的任务时指派会被拒绝，此时要么等它完成，要么新建成员并行承担）；没有合适的人就 `spawn_teammate` 新建一名对口成员，再 `send_message(to="*")` 拉起
