# 消息通道纪律：按内容形态分流 + `content` 硬上限

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-07-16 |
| 范围 | `openjiuwen/agent_teams/tools/tool_message.py`、`tools/locales/{cn,en}.py`、`tools/locales/descs/{cn,en}/fragments/artifact_handoff_policy.md`、`prompts/{cn,en}/{leader,teammate}_policy.md` |
| 测试基线 | `tests/unit_tests/agent_teams/tools/` + `test_predefined_team.py` → 129 passed（新增 `test_tool_message.py` 8 例） |
| Refs | #751 |

## 背景

成员在简单信息传递上也先 `write_file` 再 `send_message` 发路径——"收到，已完成"
这种一句话的回复也要落一次盘。收件人为读一句话要多读一次文件，而这句话本来就该
直接躺在消息里。

根因在文案：`artifact_handoff_policy` 片段与两份 policy 都把"文件优先"写成了
**无条件铁律**（"成果交接一律走文件"、"Result Handoff: Files First"），只在从句里
提了一句"复杂或大量的内容"。LLM 读到的是标题和祈使句，不是从句里的限定词——于是
把它当成"所有出站信息都先落盘"执行。这不是模型犯蠢，是契约写歪了：**规则的触发
条件必须和规则本身一样显眼**。

反过来也不能把闸门全撤了：真正的产物（调研报告、方案全文、数据表）塞进消息正文会
污染每个收件人的上下文窗口，这正是当初写"文件优先"的原因。所以问题不是"要不要
落盘"，而是**谁来决定**——需要一条判据，以及一道在判据被忽略时兜底的执法。

## 决策

### 1. 判据是内容形态，不是收件人

三处文案（片段 + leader_policy + teammate_policy，cn/en 共 6 份）统一改成同一口径，
且把条件放到与规则同等的显眼位置：

- **短内容直接发**——指令、请求、确认、简短回复、进度同步、结论、决策、问答；并写明
  为这类内容建文件"只是白白多一次写盘加一次对方读盘"（给出代价，不只是给许可）。
- **成型产物走文件**——复杂、大量、需被反复查阅的内容落 `.team/`，消息只带路径 + 摘要。
- **拿不准看长度**——一屏内直接发；要滚屏读、或对方之后可能回头再查，就落盘发路径。

原文案里"消息只传指令、路径、结论和决策，不传数据"这句其实已经蕴含了正确规则，但它
和"必须先落盘"挨着写，后者的祈使语气盖过了前者。现在把两条拆成并列的两段，各自带
自己的触发条件。

顺带修了两处隐含"总有产物文件"的表述（teammate 完成汇报第 5 步、leader 向用户汇报），
改为"有产物文件时附路径"——否则文案自身就在暗示每个任务都该产出文件。

### 2. 执法：`content` 超 `MAX_CONTENT_CHARS` 直接拒

提示词是劝导，劝导会被忽略。`_SendMessageBase._reject_oversize_content` 在超过 2000
字符时返回失败，消息不落库。

- **落在 `invoke` 里、`_dispatch` 之前**：一处覆盖两个形态（`SendMessageTool` /
  `ReportToLeaderTool`）与三条路径（unicast / multicast / broadcast），也覆盖 MCP
  客户端——`mcp/server.py` 直接 `await tool.invoke(...)`，从不校验 schema。放在
  `_send` 里就要在三个投递原语上各写一遍，还漏掉 multicast 的批量写路径。
- **按字符计，不按 token**：无 tokenizer 依赖、无按语言分支，这个界只需量级正确。
- **2000 的来由**：约一屏中文；中文 2000 字已是一篇短报告，英文 2000 字符≈300 词也
  远超"几句话"。正常的指令 / 回复 / 摘要基本落在 500 以下，误伤面很窄。
- **错误文案必须给出下一步**：`write_file` → `.team/` → 重发只带路径 + 摘要，并显式
  禁止"拆成多条消息"绕过。只说"太长"会让 LLM 原地重试或把正文切片——后者比超长消息
  更糟，收件人要自己拼回去。文案进 `STRINGS`（`send_message.error_content_too_long`，
  带 `{actual}` / `{limit}`），符合"运行时错误消息走 STRINGS 插值、描述走 md"的分工。

### 3. 没有收件人豁免

规则约束的是**内容形态**，收件人不改变一份产物是不是产物，所以谁都不豁免——`user` 也
一样。用户经自己的助手 agent 读交接文件，路径对他同样可用。

初版曾给 `to="user"` 开了豁免（理由是"用户在团队外读不到 `.team/`"），前提是错的，已
删除。删掉后 `_reject_oversize_content` 不再需要 `to_raw` 参数，退化成一条纯长度判断：
特殊情况消失了，而不是被 `if` 管理起来。

### 4. 上限值写进工具描述

只靠错误消息，LLM 要撞一次墙才知道界在哪，白费一次往返。片段里写明 2000 与豁免规则。
数字因此出现在两处（常量 + md），`test_tool_message.py` 断言
`str(MAX_CONTENT_CHARS) in card.description`（cn/en 各一次）——改常量忘改文档就炸。

## 拒绝的方案

- **把上限做成 `TeamAgentSpec` 配置项**：没人要求按团队调这个值，加一个字段就要贯穿
  spec → context → 工厂 → 工具四层，还得回答"两个成员配了不同上限怎么办"。一个模块级
  常量改起来是一行。等真出现了需要不同上限的部署再说。
- **只改提示词、不加硬闸**：本次的起因恰恰是"提示词写着复杂内容才落盘，LLM 照样全落盘"
  ——反过来同样会发生。契约要靠执法兜底。
- **超长时工具自动落盘、把路径替回消息**：省事但错。工具不知道该写哪个路径、什么文件名、
  正文该怎么切分带摘要；替 LLM 做了这个决定，产物文件会变成一堆 `msg_1.md` 垃圾。让它
  自己写文件，它才会顺手起个像样的名字和摘要。
- **按 token 而不是字符**：要引入 tokenizer 依赖 + 按模型分支，换来的精度对一个"量级
  正确即可"的界毫无价值。
- **对 `summary` 也设限**：`summary` 不进消息正文，只用于预览和日志；没有观察到问题，
  不预防性设计。

## 验证

- 新增 `tests/unit_tests/agent_teams/tools/test_tool_message.py`（8 例）：三条路径全拒、
  错误文案含 `write_file` / `.team/` / 实际长度 / 上限且经 `map_result` 到达模型、拒绝后
  全队零消息行、边界 2000 通过 / 2001 拒、`to="user"` 同样受限、scheduled 形态同样受约束、
  cn/en 描述都载明上限。
- `tests/unit_tests/agent_teams/tools/` + `test_predefined_team.py`：129 passed。
  其中 `test_tool_variants.py::test_every_toolset_assembles`（lang × dispatch × role
  笛卡尔）覆盖改动后的片段渲染。

## 已知遗留

- 上限只管 `send_message`。任务 `content`（`create_task` / `update_task`）没有对应约束——
  leader 把整份需求塞进任务正文是同一类问题的另一个面，但尚未观察到，不预先设计。
- 2000 是个拍出来的量级值，没有线上分布数据支撑。若日后发现某类合法消息被反复误伤
  （例如 leader 转发用户的长需求），应先看分布再调，而不是顺手加豁免。
