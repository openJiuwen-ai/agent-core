## 任务内联成员

- 除了 `assignee`，任务的承担者也可以用 `member` 随任务**内联创建**（二选一）。常驻成员先 `spawn_teammate` 再 `create_task`；只为一个任务存在的专家用 `member` 内联，并指定它干完后的退场策略 `lifetime`（`shutdown` 自动关闭 / `dormant` 自动休眠）
- 执行中发现能力缺口时，也可以直接 `create_task(member=...)` 内联创建承担者

内联成员的退场由调度框架自动完成：名下任务全部终态、且没有未读消息时，按 `lifetime` 关闭或休眠。

- **休眠成员仍在名册上**（`list_members` 显示 `status=dormant`），不处理任何事件、不消耗资源
- **点名即醒**：`send_message(to=该成员)`、`targets` 含它、或给它派新任务（`create_task(assignee=...)` / `update_task(assignee=...)`），调度框架会在原有上下文上唤醒它；处理完它会再次休眠
- **广播不唤醒**：`send_message(to="*")` 只落进它的邮箱，等下次被点名时一并读到
- 彻底移除一个休眠成员：再对它调一次 `shutdown_member`；临时团队收尾时 `clean_team` 前仍需全员 SHUTDOWN
