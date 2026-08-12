# Avatar 的控制者通道与团队 `user` 分离

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-08-05 |
| 范围 | `prompts/cn/human_agent_policy.md` · `prompts/en/human_agent_policy.md`（新增）；`prompts/sections.py`（`build_team_role_section` 按角色挑模板 + HUMAN_AGENT 不渲染执行模式行）；`prompts/cn/hitt_human_agent.md` · `en/hitt_human_agent.md`（收敛为静默契约）；`prompts/cn/inbound_tags.md` · `en/inbound_tags.md`（`from="controller"` 定义）；`inbound_render.py`（`CONTROLLER_SENDER` + `render_controller_input`）；`interaction/human_agent_inbox.py`（`_drive_agent` 打标记）；测试 `test_hitt.py` / `test_inbound_render.py` / `interaction/test_human_agent_inbox.py` / `runtime/test_dispatch_payload.py`；文档 `S_09` / `prompts/AGENTS.md` / `interaction/AGENTS.md` / `agent_teams/AGENTS.md` |
| 测试基线 | `tests/unit_tests/agent_teams/` 2327 passed, 19 skipped, 3 xfailed |
| Refs | #984 |

## 背景

现象：控制者通过 Inbox 给自己的 avatar 发一句话（`$alice 帮我看下 design.md`），avatar
回答时调用了 `send_message(to="user")`——把只该给控制者看的答复，投递给了 leader 那一侧的
**另一个真人**。

根因不是「prompt 少写一条禁令」，是**同一个词承载了两个实体**，而两条契约正面打架：

1. `build_team_role_section` 只分 LEADER / 其它，`HUMAN_AGENT` 落到 `teammate_policy.md`。
   那份契约里写着无条件义务：

   > 收到 `from="user"` 的消息……你都**必须**用 `send_message(to="user")` 作答

2. 控制者的输入是**裸文本**投进 avatar 的 harness 的（`HumanAgentInbox._drive_agent` →
   `interact` → `USER_INPUT` → `deliver_input`）。在 avatar 的视角里，这跟任何 harness 的
   普通 user turn 长得一模一样。

于是 avatar 把「控制者在跟我说话」读成「user 在跟我说话」，然后忠实执行了那条加粗的「必须」。
`hitt_human_agent.md` 的静默约束覆盖不到这里——它管的是带 `for="controller"` 的**团队通知**，
而这是控制者**直接对 avatar 说话**，本来就该响应，只是响应通道选错了。

对 avatar 来说，harness 里跟它说话的是**控制者**，团队名册外的 `user` 是委托整个团队的
**另一个人**。这两个实体从来没有在数据层面区分过。

## 决策

**1. HUMAN_AGENT 拿自己的 role policy，不再落回 teammate 版。**

新增 `prompts/<lang>/human_agent_policy.md`，`build_team_role_section` 加一档分支。契约核心
是一张三行表——avatar 面对的三种对象各走各的通道：

| 对象 | 通道 |
|---|---|
| 控制者 | **纯文本输出**（他直接读得到；他不在名册，`to="controller"` 这个收件人不存在） |
| 团队成员 | `send_message(to=<成员名>)`，仅当控制者明确让它转告 |
| `user` | `send_message(to="user")`，仅当控制者明确让它带话 |

外加一句点名的负向约束：**控制者不是 user，绝不要把给控制者的答复发成 `to="user"`**；
avatar 没有「收到消息必须回 user」的义务——那是 teammate 的契约。

同时该角色不渲染执行模式行（plan/build）：avatar 从不自主规划或认领，那行对它没有意义。

**2. 控制者输入带显式来源标记。**

`inbound_render.render_controller_input` 把 body 包成
`<team-inbound from="controller" type="direct">`，`_drive_agent` 投递前调它。来源从此是一个
属性，不是 LLM 要从语气里猜的东西。没有 `message_id` / `time`：控制者指令不是总线消息，
没有可引用、可标已读的身份，也是即时消费的。

`inbound_tags.md`（全角色共享）补一条 `from="controller"` 的定义，明确它与 `from="user"`
**不是同一个人**。

**3. `hitt_human_agent.md` 收敛成单一职责。**

它原本同时装着「静默约束」和「send_message 怎么用」，后者与新 role policy 重复。现在
HITT section 只管一件事：对 `for="controller"` 内容严格禁止任何自主行为；通道说明一律指向
role policy。同一件事两处各写一份，迟早漂移。

## 拒绝的方案

**在 `hitt_human_agent.md` 里加一条「禁止 `send_message(to="user")`」。** 最小 diff，但
teammate_policy 那条加粗的「必须」还留在 avatar 的 prompt 里。两条指令对撞，「必须」通常赢——
这正是 bug 现场的样子。加禁令是在治症状。

**工具层拒绝 human_agent 调 `to="user"`。** 一度考虑照 leader 的先例（`_send` 里
`is_leader` 分支）扩一档，或按形态表给 human_agent 装一个 `to` 不含 `"user"` 的
`send_message` 形态。**否决**：控制者让 avatar 给 user 带话（「跟 user 说这版数据我确认
过了」）是合法场景，封死它是把正确用法一起砍掉。错的是「avatar 自己决定要答复 user」，不是
「avatar 能发消息给 user」——约束属于契约层，不属于能力层。

**给 avatar 一个 `to="controller"` 的收件人。** 会凭空造出一个不存在的成员：控制者不在名册、
没有 agent 进程、没有邮箱。avatar 的文本输出本来就直达控制者，多一条通道只是多一处可选错的
岔路。

## 验证基线

- `tests/unit_tests/agent_teams/` 全量：2327 passed, 19 skipped, 3 xfailed。
- 新增回归：
  - `test_hitt.py::test_human_agent_role_section_replies_to_controller_in_plain_text_cn/_en`
    ——avatar 契约必须说「回控制者用纯文本」且点明控制者 ≠ user。
  - `test_hitt.py::test_human_agent_role_section_is_not_the_teammate_policy`——守住分层本身，
    顺带断言无执行模式行。
  - `test_inbound_render.py::test_render_controller_input_marks_the_sender`。
- 迁移的过时期望：原先断言 HITT section 里 `send_message` 措辞的两个用例改为断言 role
  section；三处 `interact.assert_awaited_once_with(<裸 body>)` 改为断言标记 + body 包含。

## 已知遗留

- 控制者输入的标记只在**进程内 avatar**路径上（`HumanAgentInbox._drive_agent`）。外部 CLI
  成员当前不承载 HUMAN_AGENT 角色，如果将来支持，`external/runtime.py` 的投递路径需要同样
  打标记。
- `send_message` 的工具描述（`descs/<lang>/send_message.md`）里 `"user"` 一行仍写着「仅
  teammate 使用……必须把答复发回 user」。对 avatar 而言这句的前半句不准确，但工具描述是
  role 无关的共享文本，改它会把 teammate 的强制通道说软。当前由 role policy 覆盖此差异；
  若后续要精确化，走 desc_key 分化（形态表机制）而不是在描述里写角色分支。
