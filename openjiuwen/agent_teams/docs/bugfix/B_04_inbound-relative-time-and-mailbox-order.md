# 被唤醒成员倒序读邮箱，入站消息的相对时间写死在历史里

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-15 |
| 严重程度 | 中：成员对消息先后与时效的判断出错；所有成员、所有接入路径都受影响 |
| 范围 | `agent_teams/agent/coordination/handlers/message.py`、`group_chat/message_handler.py`、`timefmt.py`、`inbound_render.py`、`rails/team_policy_rail.py`、`external/{format,client,runtime,member_runtime}.py`、`agent/bridge_inbound_compose.py`、`prompts/{cn,en}/inbound_tags.md` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/`：3695 passed, 36 skipped, 3 xfailed, 1 failed（`test_member_activity.py::test_observe_member_status_emits_after_the_debounce`，upstream/develop 上同样失败，与本修复无关） |
| 关联 spec | `S_09_prompts-and-rails.md`（新增不变量 28a）、`S_16_top-level-utilities.md`（`format_absolute_time` + 不变量 30） |

## 现象

一个休眠成员（`lifetime="dormant"`）先收到 leader 15:07:09 的广播，未读；15:07:35 用户 `@` 它
说「你好」把它唤醒。它的轨迹第 2 轮是：

```
USER  <team-inbound from="user" ... time="2026-10-15 15:07:35 +08:00 (刚刚)"> 你好 ...
USER  [STEERING] <team-inbound from="team-leader" ... type="broadcast" time="2026-10-15 15:07:09 +08:00 (26 秒前)"> ...
```

两个问题：

1. **顺序反了**：更早的广播排在更晚的私信后面，还以 `[STEERING]` 插进来。
2. **「刚刚」不可信**：标签在投递那一刻是对的，但它被写进对话历史后再也不会变。同一成员
   第 1 轮 13:59 收到的消息仍标着「(刚刚)」，第 2 轮 15:07 模型重读时那已是 68 分钟前；
   这条「你好 (刚刚)」此后每一轮也都会一直是「刚刚」。成员上下文里没有任何"现在几点"的
   参照，没法判断这些标签已经过时。

## 根因

**顺序**：`MessageHandler._read_all_unread` 把单播与广播合并后按 `timestamp` **降序**
（`reverse=True`，"newest first"）排列，`_process_unread_messages` 再按这个顺序逐条
`deliver_input`。成员空闲时第一条开启一轮，后面的进 steering 队列，于是最新的一条成了这一轮
的 query，更早的反而以 `[STEERING]` 跟在后面。git 历史里这个倒序是在
`refactor(agent_teams): split EventDispatcher ...` 中原样搬过来的，没有任何设计说明。
外部 `ExternalTeamClient.fetch_inbox` 有同类问题：`[*direct, *broadcast]` 直接拼接，两段各自
有序、合起来不是。

**相对时间**：F_24 让 `<team-inbound time>` 渲染成 `<绝对时间> (<相对差>)`，相对差按投递时刻
算。F_24 针对的是"LLM 看不到时间"，没有考虑入站块是**写进历史后被反复重读**的文本——工具结果
和事件快照下次会整份重新渲染，入站消息不会。

## 决策

- **未读按发送时间升序投递**：`_read_all_unread` 改为升序，最早的一条开启这一轮，其余按先后
  steer。`fetch_inbox` 合并后同样按 `timestamp` 排序。群聊的 `GroupMessageHandler` 本来就是
  升序取一条，不受影响。
- **入站文本只写绝对时间**：新增 `timefmt.format_absolute_time(timestamp_ms)`（`None` →
  `time.unknown`）。`_format_message`、`external/format.render_message`、bridge 入站头都改用它；
  `_format_message` / `render_message` / `render_messages` 去掉只为算相对差存在的 `now_ms` 参数。
- **每次输入附带当前时间锚点**：新增 `inbound_render.render_clock(now=...)`，渲染自闭合
  `<team-clock now="..."/>`。
  - 进程内成员：`TeamPolicyRail.on_user_message` 在剔除被覆盖快照之后、折入团队状态之前
    `insert(0, ...)`。`ON_USER_MESSAGE` 对新一轮 query、follow-up、steering、resume 都触发，
    所以每条被消费的输入都带一个锚点。
  - 外部 CLI 成员：`CliRuntimeBase.send` 把锚点放在正文前；三方 harness 成员：
    `ExternalHarnessMemberRuntime.send` 经 `_prepend_context` 前置，并作为单独一段
    `record_input`（与团队状态的记录方式一致）。
  - operator `read_inbox`：有消息时结果以锚点开头。
- **提示词同步**：`inbound_tags.md`（cn/en）说明 `time` 是绝对发送时间、列出 `<team-clock>`
  的含义与"旧锚点不刷新"，并把它列进平级顶层块。
- 任务看板、`view_task`、停滞提醒等**工具结果 / 事件正文**仍用 `format_time_context`：它们是
  "截至这次调用"的快照，下次会重新渲染。

## 拒绝的方案

- **投递时在每条消息上加 `delivered` 属性**：每条消息多一个时间，还是写死的；成员仍然不知道
  "现在"，问题只是换了个位置。
- **把相对时间改成在模型调用前动态刷新**：要改写已写进历史的消息，违反 S_09 不变量 13 的
  "只插入，不重写"，并让其后的 KV cache 全部作废。
- **只在消息批次里有 `<team-inbound>` 时才加锚点**：要解析正文判断，且 leader 的普通 query、
  事件输入同样需要时间参照；每条输入一个锚点更简单，成本是一行文本。
- **把锚点做成 prompt attachment（`<system-reminder>`）**：attachment 按内容变化投增量，
  时间每次都变，等于每轮多一条系统消息；且外部 CLI 成员没有 attachment 通道，三条路径无法对齐。
- **保留 `render_message(now_ms=...)` 参数但忽略它**：死参数只会误导调用方；本模块的这几个
  函数没有对外导出（`external/format.py` 仅由 `client.py` 使用），直接删除。

## 验证

- 新增：`test_message_handler_render.py::test_read_all_unread_is_oldest_first`（复现现象里的
  单播晚于广播场景）、`test_format_message_time_is_absolute_only`；
  `test_team_policy_rail.py::test_every_admitted_input_carries_one_clock_anchor`、
  `test_clock_anchor_follows_superseded_board_pruning`；
  `external/test_client.py::test_inbox_is_oldest_first_across_direct_and_broadcast`；
  `external/test_cli_runtime_team_context.py::test_send_stamps_every_message_with_a_clock_anchor`；
  `test_timefmt.py` 两例 `format_absolute_time`；`test_inbound_render.py` 两例 `render_clock`。
- 更新：断言整段消息正文的用例改为剥离锚点后比较（团队状态投递类用例）或固定时钟后把锚点
  写进期望（`external/test_member_runtime.py`）；`external/test_format.py` 改为断言只有绝对时间；
  `inbound_tags` 文档用例断言列出了 `<team-clock`。
- 全量基线见元信息。

## 已知遗留

- `<team-clock>` 不保证进入 leader 的系统提示词说明：leader 在 `build_team` 之前只拿到
  `team_bootstrap` + `team_extra`，`inbound_tags` 由 `build_team` 的披露给出（F_76）。建队前
  它会先看到锚点再看到说明；`now` 属性本身是自解释的，暂不为此改 leader 的 section 装配。
- 对话历史里已经写入的旧消息仍带相对差，本修复不回写历史。
