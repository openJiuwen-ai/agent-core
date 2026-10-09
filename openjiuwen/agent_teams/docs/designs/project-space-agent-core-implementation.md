# 项目空间 · 块 A 实现记录

块 A 已落到 `feat/project-space-agent-core`。规格仍以
`project-space-agent-core-requirements.md` 为准。这里只记接线方式和有意没搬的部分。

## 底账

公开消息是数据库里的一条广播。`meta.type` 为 `group_chat`，正文在 `content`。
`history.jsonl` 只按当前 `meta.session_id` 从这些行重写。水位继续用
`MessageReadStatus.read_at`，没有新表，也没有 `.notified.json`。

没有团队行时 `post_message` 直接失败，原因 `invalid_group_chat`。这条路径不调用
`build_team`。

## 接线

| 点 | 做法 |
|---|---|
| 投影 | `group_chat/conversation.py`。锁用已有的 `filelock.FileLock`，替换用同目录临时文件加 `os.replace`。符号链接用 `is_symlink()` 拒绝 |
| 归档 | `group_chat/handler.py` 的 `post_message`。`message_id` 是 `uuid5(team, session, client_message_id)`。相同 ID 且内容一致则不插第二行，并再次 `publish_broadcast` |
| 发布 | `TeamMessageManager.publish_broadcast` 从原来的 `broadcast_message` 抽出。普通广播的写入和返回值不变 |
| 会话 | `SessionManager.bind_session` 调用 `TeamBackend.bind_group_session`。投影目录跟着这次绑定的会话 |
| 过滤 | 群广播只在「不是 `passive_human`、不是发送者、名字在 `mentions`、水位未盖住、会话匹配」时可见。普通广播仍是非发送者且水位未盖住 |
| 完成判定 | 普通广播仍走原来的一条 SQL。群广播另扫 `mentions`。无 mention 的群消息、以及点名被动真人，不挡住 `has_unread_messages` |
| 叫醒 | `EventDispatcher` 对消息类事件只注册一个路由。未读里有群行时走 `GroupMessageHandler`，从旧到新一次一条；否则仍走原来的 `MessageHandler`。`activate_and_flush` 没动 |
| 宿主 | `inputs["query"]` 和 `interact_agent_team` 接受 `type=group_chat`。作者固定 `user`。非法字典在进 leader 模型前失败。leader 不在 `notified_members` 时，确认包之后关闭流 |
| 启动 | leader 扫描未读点名。`UNSTARTED` 走 `startup_member`。`ERROR` 先 CAS 到 `RESTARTING`，再 `restart_teammate`。被动真人不进这个名单，`spawn_teammate` 也不给它起进程 |
| 工具 | `group_send_message` 在共享工具集，必填 `content` 和 `client_message_id`。`human_agent` 与 `passive_human` 没有这个工具 |
| 被动真人 | 新角色 `passive_human`，注册即 `READY`。不并进 `human_agent_names()`。工具透传先认被动真人，再拒绝 avatar。执行器按发送者单独建 task / message manager，缓存在 leader 的 `TeamBackend` 上 |

公开群广播在入站回调处直接返回。定向消息和原来的内部广播仍通知可达真人，被动真人加在这两类收件人里。

## 没搬的东西

caozhenhua 工作区的 `history.json`、`.notified.json`、定向 `send_message` 叫醒、
`enable_group_chat`、`group_context_tail`、`post_group_message`、可指定的 `sender`，
都没有进入这条分支。

develop 上这两处也没有照搬：

- `deliver_group_message` 在缺团队行时自己 `build_team`。这里改为失败。
- `sync_history` 把该团队全部群广播都写进当前会话文件。这里只投影 `meta.session_id`
  与当前会话相同的行。

块 B 的 `ensure_team_on_start`、`prompt_overrides`、`Runner.spawn_team_member` /
`remove_team_member`、`get_progress_report` 没有实现。

## 现有行为

普通 `send_message`、任务板、avatar 名单和「最新优先、一批标已读」的普通邮箱保持原样。
只有未读集合里出现群聊行时，这一次排空才改成从旧到新、一次一条。HITT 关闭时，
`spawn_human_agent` 和 `spawn_passive_human` 都不会出现在 leader 工具里。
