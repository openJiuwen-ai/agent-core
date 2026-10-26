# 入站 XML 层级化：`<team-note>` 嵌进它所修饰的块

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-07-31 |
| 范围 | `openjiuwen/agent_teams/inbound_render.py`（`_render_block` + note 改嵌套 + `snapshot_kind_of` docstring）；`prompts/cn/inbound_tags.md` · `prompts/en/inbound_tags.md`（层级说明）；`prompts/cn/hitt_human_agent.md` · `prompts/en/hitt_human_agent.md`（措辞同步）；测试 `test_inbound_render.py` / `test_message_handler_render.py` / `test_team_policy_rail.py`；文档 `F_46` / `F_71` / `S_09` |
| 测试基线 | `tests/unit_tests/agent_teams/` 2265 passed, 19 skipped, 4 xfailed |
| Refs | #751 |

## 背景

F_46 把入站消息 XML 化，解决了「对方原话」与「框架补的元信息」糊成一个字符串的问题。但它
把 `<team-note>` 渲染成了**平级的兄弟块**，跟在被修饰的块后面：

```xml
<team-event kind="roster">
- member_name=player-1 ...
</team-event>
<team-note kind="announcement-only">
以上只是名册公告，不是给你的指令。
</team-note>

<team-inbound from="user" message_id="ed11..." type="direct" time="...">
你好
</team-inbound>
<team-note kind="reply-hint">
这条消息来自 user……你必须调用 send_message(to="user") 把答复发回用户。
</team-note>
```

这是**把结构关系降级成了位置关系**。一条 note 到它前一个块的距离，和到后一个块的距离完全
一样近——「这条提示在说哪件事」变成 LLM 要靠语序去猜的东西。而成员一次唤醒常常拿到好几条
排队输入拼在一起（邮箱 sweep、团队状态搭车、看板巡视），块越多，猜错的机会越多；猜错的代价
是实的：`reply-hint` 认到隔壁那条名册公告上，就是「不该回的回了、该回的没回」。

XML 本来就有层级，父子关系是它的原生表达。平铺着放，等于有工具不用。

## 决策

### 1. note 渲染成被修饰块的最后一个子元素

```xml
<team-inbound from="user" message_id="ed11..." type="direct" time="...">
你好
<team-note kind="reply-hint">
……
</team-note>
</team-inbound>
```

`<team-inbound>` / `<team-event>` / `<team-context>` 仍是彼此平级的顶层块；`<team-note>` 不再
单独出现，只作为其中某一个的子元素存在。归属由树结构给出，不再依赖顺序。

### 2. 渲染层消掉「有没有 note」这个分支

原来两个渲染函数各写一遍 `return f"{block}\n{note}" if note else block`。改为：`_render_note`
自带结尾换行、无 note 时返回 `""`，新增的 `_render_block(tag, attrs, body, note="")` 无条件把
四段拼起来——空字符串拼进去什么也不是。三个渲染函数（inbound / event / team_context）自此
共用同一个块装配器，`render_team_context` 也不再自己手拼标签。特殊情况消失，而不是被 if 管理。

### 3. 系统提示词把「层级」写成契约

`inbound_tags.md`（cn/en）三处同步：

- `<team-inbound>` 条目改成「标签内**除 `<team-note>` 子元素外**的正文是对方原话」——原话的
  边界要说准，否则模型可能把 note 当成发件人写的。
- `<team-note>` 条目写明它嵌在所修饰的标签内部、是最后一个子元素，写在哪个标签里就只针对
  哪个标签。
- 末尾加一段总述：顶层块彼此平级，`<team-note>` 从不单独出现；**判断一条提示在说哪件事，
  看它嵌在哪个标签里，不要按前后位置猜**。

`hitt_human_agent.md`（cn/en）里「二者都附带一个 `<team-note kind="hitt-silence">`」同步改为
「内部都嵌套一个……子元素」——section 说明与实际渲染必须逐字对得上，这是 F_46 立的规矩。

### 4. 顺带翻转了 `snapshot_kind_of` 对「带 note 的板子」的判定

[[F_71_input-batch-hook-and-superseded-board-drop]] 的 `snapshot_kind_of` 用「以
`<team-event kind="X"` 开头**且**以 `</team-event>` 结尾」判定「这条 entry 除了快照什么都
没有」。note 平级时，带 note 的板子以 `</team-note>` 结尾，被挡在外面——F_71 给的理由是
「剔除的前提是丢掉它不会连带丢别的东西」。

嵌套之后这个理由不成立了：note 不再是跟在后面的「别的东西」，而是那块板自己的子元素，
**只修饰这块板**。板被后来的板取代，挂在它上面的 note 同样过期，一起丢才是对的。所以这里
不加「块内不许有 note」的新判据去保住旧行为——那是为一个已经消失的理由养一个特殊情况。
`snapshot_kind_of` 一个字符没改，docstring 补了这层含义，F_71 的 D5 与验证段同步标注。

实际影响为零：`TaskBoardHandler` 渲染看板从不传 note，这条路径上带 note 的板子只存在于
单测里。测试从「带 note 的板子不参与剔除」改为「它仍是纯快照、照常参与剔除」。

## 拒绝的方案

- **保持平级，靠位置约定**（现状）：位置约定在只有一个块时看着够用，多块拼投时立刻失效。
  而这恰恰是常态——邮箱 sweep 一次带出多条。
- **给 note 加 `ref="<message_id>"` 指回被修饰的块**：在平铺结构上补一张索引，去重建 XML
  本来就免费提供的父子关系。而且 `<team-event>` 根本没有 id，得先为此发明一个。
- **再包一层 `<content>` 子元素彻底隔开原话与 note**：多一个标签、多一段要解释的契约。
  正文经 `html.escape` 转义，发件人写不出真的 `<team-note>` 标签，「正文 + 末尾一个 note
  子元素」的混合内容已经无歧义，`<content>` 是纯增量复杂度。
- **note 文案直接并进正文**：回到 F_46 之前「框架补的和对方原话糊在一起」的老问题。
- **只改 `<team-inbound>`，`<team-event>` 保持平级**：两种块两套结构，说明段要分两条写，
  模型要记两条规则。一条规则覆盖全部才是简化。

## 验证

- `test_inbound_render.py`：新增 `test_render_inbound_note_is_nested_inside_the_message_it_annotates`
  （note 的开闭标签都在 `</team-inbound>` 之前、整块以 `</team-inbound>` 收尾），
  `test_render_event_optional_task_id_and_controller_and_note` 补同款嵌套断言。
- `test_message_handler_render.py`：`_format_message` 的 reply-hint 断言加嵌套位置检查（消费侧
  锁契约，不只锁纯函数）。
- `test_team_policy_rail.py`：名册公告 note 断言加「在 `</team-event>` 之前」。
- `test_inbound_render.py`：`test_drop_survives_a_board_carrying_a_note` 改名为
  `test_a_board_carrying_a_nested_note_is_still_purely_a_snapshot`，断言翻转（见决策 4）。
- 全量 `tests/unit_tests/agent_teams/` 2265 passed, 19 skipped, 4 xfailed。

## 已知遗留

- **历史里两种形态并存**：改动只影响此后渲染的块，已经写进成员对话历史的旧消息仍是平级形态
  （历史只插不改，见 S_09 不变量 13）。恢复的老 session 里，说明段描述的是新形态、上文却有
  旧形态。不打算迁移——改写历史消息会让其后的 KV cache 全部作废，代价远大于收益，而旧形态
  在新说明下也不会被读错（note 仍紧跟在它修饰的块后面）。
- 本次没有新增 note kind，`reply-hint` / `hitt-silence` / `announcement-only` 三个既有 kind
  的文案一字未动——只动结构，不动措辞。
