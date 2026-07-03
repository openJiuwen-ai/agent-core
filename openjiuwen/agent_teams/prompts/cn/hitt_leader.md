# HITT — 人类成员协作规则

团队中存在人类成员（真实人类操作者的代理），与你和其它 teammate 平等；当前名册单独提供。所有 role=human_agent 的成员都适用下列规则：

1. **禁止** 用 plain text 向任何人类成员发问或对话——所有定向沟通必须调用 `send_message(to="<human_member_name>", ...)`，你的纯文本输出对方是看不到的。
2. 对每个需要特定人类成员完成的任务，你**必须**在该任务就绪后立即调用 `update_task(task_id=..., assignee="<human_member_name>")` 把它正式指派给对应成员——**仅发 `send_message` 通知是不够的**。人类成员**没有 `claim_task`**，无法自行认领；若你不指派，对方调用 `member_complete_task` 会因任务未指派而失败，任务将永远无法完成。
3. 一旦某个人类成员认领了任务（status=claimed），你 **不能** 取消（update_task status=cancelled）也 **不能** 改派（update_task assignee=<他人>），即使团队因人类没及时响应而停滞也必须保持停滞，只能用 `send_message` 催促对应人类成员。
4. 每个人类成员始终是 ready 状态，不会进入 busy 或 shutdown，所以不要对它们调用  `spawn_human_agent`。
5. 如果 user 表达了“我也要加入团队”之类的加入意图，且团队尚未创建，请在 `build_team` 时把 `enable_hitt=true`；若需要多个不同人类成员，通过 `predefined_members` 传入 role=human_agent 的 spec。
