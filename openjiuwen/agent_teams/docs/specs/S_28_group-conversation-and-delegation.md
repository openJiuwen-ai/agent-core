# S_28 群聊文件与成员输入

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 最近一次修订日期 | 2026-09-17 |
| 关联模块 | `agent_teams/team_workspace`、`agent_teams/tools`、`agent_teams/agent/coordination`、`core/runner` |
| 关联 feature | [F_112](../features/F_112_group-conversation-and-delegation.md) |

## 1. 范围与边界

core 提供两个宿主输入入口：

- **公开群消息**：完整聊天保存在 team workspace 的会话 `history.json` 中。没有 Agent mentions 时只归档；点名时向指定成员发送最新增量摘录、本次触发消息和历史文件路径。
- **定向成员输入**：向指定 team/session/member 的现有邮箱写入消息，不进入公开聊天历史，也不推进群通知时间。

两者的通知都使用 `TeamMessageManager`、`MessageDao`、`MESSAGE` 事件和现有邮箱处理。普通广播保持原有广播语义；公开群消息不会自动广播给全部成员。

专家团代理的绑定、任务归属、独立执行会话、执行 ID 和结果关联属于宿主。core 不解释父子团队关系或业务完成状态。

## 2. 配置与作用域

| `TeamAgentSpec` 字段 | 默认值 | 行为 |
|---|---|---|
| `enable_group_chat` | `False` | 开启公开群消息能力和 `group_send_message` 工具 |
| `group_context_tail` | `5` | 单次通知最多包含的摘录条数，范围 `1..20` |

群工具使用 backend 持有的 `group_chat_spec` 和实际会话绑定的 `group_session_id`。作者、team 和 session 由宿主运行上下文确定，模型不指定任意目标群。团队切换会话沿用 Runner 的现有生命周期。

公开历史按 team/session 隔离；成员通知时间按该目录中的 member 分开保存。通知继续使用原有按 session 隔离的消息表及 team/recipient 查询。

群聊不要求额外的持久 checkpoint 或专用 Harness。Agent 必须能读取通知中给出的 `history.json` 文件，消息投递与恢复能力以原有邮箱和运行器为准。

## 3. 接口契约

### 3.1 Runner SDK

```python
async def post_group_message(
    content: str, *, team_name: str, session_id: str,
    client_message_id: str, mentions: list[str] | None = None,
    sender: str = "user", attachments: list[dict] | None = None,
    db_config=None, workspace_path=None,
): ...

async def post_member_input(
    content: str, *, team_name: str, session_id: str,
    member_name: str, db_config=None,
) -> dict: ...
```

`post_group_message` 返回 `ConversationAppendResult`：

| 字段 | 含义 |
|---|---|
| `message` | 已归档的公开消息，重复请求返回原记录及时间 |
| `notified_members` | 本次已写入定向通知的成员名列表 |
| `duplicate` | 是否命中已有公开消息；为 `True` 时不再次通知 |
| `context_path` | 当前会话 `history.json` 的绝对文件路径 |

`post_member_input` 返回 `{"message_id": "...", "status": "queued"}`。`queued` 表示写入了现有邮箱，不表示成员已接收、读完或执行完成。接口没有投递 ID 参数，每次成功调用都是一条普通邮箱消息；宿主重试可能产生重复输入。

存在同 team/session 的活跃 runtime 时，SDK 使用 runtime 自身的 backend 和数据库；公开历史使用有效 workspace。传入离线参数不会覆盖运行实例配置。

没有匹配 runtime 时，调用方需要提供已有数据库的 `db_config`。公开群消息可指定 `workspace_path`，未指定则使用已登记位置或默认位置。已有历史的会话不能换到另一 workspace。离线调用不创建团队、登记成员或启动模型，也不通知另一 session 的 runtime。

团队和目标成员必须已经登记，已离团成员不能接收新输入。宿主负责调用者身份、访问授权，以及使用 Runner 启动或恢复目标团队。

### 3.2 群聊 Agent 工具

```python
group_send_message(content, client_message_id, mentions=[])
```

该工具仅操作当前群；不暴露任意路由和附件参数。历史读取直接使用已有 `read_file` 工具，路径来自通知或 `context_path`。

公开消息需要正文或附件；`mentions` 最多 100 个成员名并去重，未知和已离团目标被拒绝。`user` 与 `passive_human` 可以出现在公开 mentions 中，但不产生模型通知。SDK 不解析正文的 `@name`，宿主显式传入 `mentions`。

附件只保存 JSON 引用，core 不上传、复制或读取二进制内容。

## 4. 文件与时间进度

```text
<有效 team workspace>/
  conversations/
    <团队隔离目录>/
      <会话隔离目录>/
        history.json
        .notified.json
```

每个 team/session 的全部公开聊天保存在一个 `history.json` 中，顶层为 JSON 数组。每个数组元素保存 `message_id`、team/session、`client_message_id`、发送者、正文、mentions、附件引用和毫秒时间戳。

同一群的并发写入者使用同一个 runtime home 下的注册目录锁。每次追加在文件锁内读取数组、检查重复 ID、追加新元素，再通过现有 `atomic_write` 原子替换文件。文件使用两空格缩进的多行 JSON，便于 `read_file` 按行分段读取。追加需要读取和重写当前会话的完整数组；读者不会看到写到一半的 JSON。目录标识经过安全处理，避免路径穿越和标识清洗碰撞。

若 `history.json` 不存在，但目录中存在按消息分文件的旧记录，读取或追加会明确报错，要求先转换为 `history.json`；运行时不忽略、删除或自动迁移这些记录。

同 team/session/client ID 对应稳定消息 ID。相同内容返回原归档；改变发送者、正文、mentions 或附件时报冲突，不覆盖原文件。归档重复请求不重发通知。

`.notified.json` 保存各成员最近成功写入群通知的时间，只有通知进入现有邮箱后才推进。首次通知的起点为 0，后续读取区间为：

```text
上次通知时间 < timestamp <= 本次触发消息时间
```

从区间中取最新 N 条，且总数不超过 N。本次触发消息始终保留，即使时钟回退使其落在普通区间之外。每条摘录正文最多 2,000 字符，并标记截断与附件数量；历史文件仍保留完整内容。

这个时间表示已通知到哪里，不是阅读回执。它独立于内部广播的 `read_at`，也不会被普通成员输入推进。时间使用现有 epoch 毫秒时钟，不增加序列号；并发请求可能产生重叠摘录，同毫秒消息没有额外的严格顺序。

通知提供当前会话 `history.json` 的绝对文件路径，不生成按成员或按次划分的完整历史副本。Agent 使用 `read_file` 直接读取文件，按时间范围和触发消息查阅相关记录。自定义 workspace 有轻量位置登记，供离线访问和显式清理使用；生产者与 Agent 必须能访问同一个历史文件。

## 5. 通知与故障边界

群消息流程：

1. 校验发送者、mentions 和消息内容。
2. 将完整公开消息写入历史文件；重复归档直接返回，不再次通知。
3. 为每个 Agent mention 读取其时间进度并生成摘录。
4. 调用现有 `TeamMessageManager.send_message`，先写消息表，再发布 `MESSAGE` 事件。
5. 成功写入邮箱后保存该成员的通知时间。

文件归档、邮箱写入和通知时间更新不是一个事务。中断时可能只完成其中一部分；重复 client ID 只复用历史，不补发通知。需要重新通知时由宿主明确发起，接口不提供原子归档与投递保证。

消息总线只是唤醒入口。事件发布失败后，已保存的消息仍可由未读扫描消费。leader 在 `MESSAGE` 事件和 `POLL_MAILBOX` 时查找有未读定向消息的 `UNSTARTED` / `ERROR` 成员，调用现有 `auto_start_member` 启动或恢复；失败时消息仍留在邮箱。

成员消费沿用 `MessageHandler → deliver_input → Harness.send`，交给 Harness 后标记邮箱已读。这里的已读不证明输入已经进入持久模型上下文。进程崩溃和调用重试仍可能造成输入丢失或重复；业务去重和执行结果确认由宿主处理。

SDK 和 Agent 工具都不等待任务完成。平台可以先保存执行结果，再通过定向输入通知大群代理人；`queued` 与业务完成必须分别表达。

## 6. 生命周期与清理

暂停和停止团队不删除聊天历史和成员通知时间，已有邮箱状态按现有生命周期保留。显式删除团队或会话时删除相应归档及位置登记；旧消息表由原有数据库删除流程处理。

历史没有自动过期策略。文件权限、数据保留、附件可访问性和跨宿主共享目录由宿主负责。离线归档不等于自动唤醒，团队运行依旧由 Runner 生命周期驱动。

## 7. 与其它规约的关系

- [S_03 协调协议](S_03_coordination-protocol.md)：定向消息事件、邮箱轮询和成员投递。
- [S_04 会话与恢复](S_04_session-and-recovery.md)：会话绑定和恢复。
- [S_06 运行时对象池与派发](S_06_runtime-pool-dispatch.md)：活跃 runtime 匹配及生命周期。
- [F_112 群消息文件归档与成员通知](../features/F_112_group-conversation-and-delegation.md)：整体结构和 jiuwenswarm 专家团代理分工。
