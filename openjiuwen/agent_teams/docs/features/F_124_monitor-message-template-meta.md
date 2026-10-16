# Monitor 透出框架模板消息的 meta

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-16 |
| 范围 | `monitor/models.py`（`MessageInfo.meta`） |
| 测试基线 | `tests/unit_tests/agent_teams/monitor/` 全套通过（含新增 `MessageInfo` meta 用例 2 条） |
| 关联 | `S_14`（数据模型表）、`F_62`（调度器交接）、`F_63`（模板消息两阶段渲染） |
| Refs | #1435 |

## 背景

调度模式下，调度器以 leader 身份投递的交接消息（开工 / 送审 / 返工 / 催办）按 `F_63`
"发送存意图、投递才成文"：邮箱行 `content` 恒空，`meta` 带 `{template, refs, params}`，
正文由收件人 drain 邮箱时 `expand_message` 渲染。

`MessageInfo.from_internal` 只拷贝了 `content` 等列、丢了 `meta`。宿主（jiuwenswarm）的 monitor
handler 拿到的模板消息于是是一条空字符串——前端群聊（子话题）里只剩一串没有正文的
`@member`，看起来像 leader 反复 @ 人，与调度流程（开工 → 提交 → 送审 → 通过 / 返工）对不上。

## 决策

1. **`MessageInfo` 新增可选字段 `meta: dict | None`**，`from_internal` 用
   `message_template.parse_meta` 解析：只有带 `template` 键的合法 JSON 才透出，普通消息、
   畸形 meta、无 `template` 的 meta 一律 `None`。与投递侧判定"是否模板消息"共用同一函数，
   观察者与收件人对同一行的分类不会分叉。
2. **monitor 不渲染正文**。展示文案由观察者自己据 `meta` 组：完整模板是写给执行成员的任务
   简报（目标、上下游、交付方式），塞进群聊太长；而且渲染要按收件人语言、读当时的任务行，
   属于投递语义，不是只读观察视图该做的事（S_14 不变量 1）。
3. 字段是纯加法，旧消费方不受影响；`from_internal` 用 `getattr(msg, "meta", None)` 兼容无
   `meta` 列的行对象。

## 拒绝的方案

- **monitor 侧调 `expand_message` 直接给出渲染后的 `content`**：需要 task / member getter、
  语言与模板 loader，把投递期依赖拉进只读视图；渲染出的是整份任务简报，展示层还得再裁。
- **调度器发送时顺手写一份展示用短文案进 `content`**：破坏 `F_63` "content 恒空、表能答的
  不快照" 的契约，且收件人 drain 时会把它当普通正文投出去。

## 已知遗留

- 宿主侧的短文案映射（模板 key → "开工 / 请验收 / 返工 / 催办"）在 jiuwenswarm 维护；
  新增调度器模板时宿主需要同步，否则退化为通用的"调度消息"文案。
