解散团队并删除所有资源（团队记录、成员、任务 — 级联删除）。**仅 Leader 可用**。

**重要**：如果任何成员未处于 SHUTDOWN 状态，clean_team 将失败。请先用 shutdown_member 关闭所有成员，再调用 clean_team。

在所有任务完成、结果汇总后调用。
返回：成功时 {success, data: {team_name}}。