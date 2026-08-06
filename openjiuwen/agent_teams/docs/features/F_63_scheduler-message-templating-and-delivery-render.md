# 调度器消息模板 + 投递时渲染

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-07-14 |
| 状态 | **已实现**（v2：评审否决「i18n 保留降级短句」——成员侧文案唯一来源收敛为模板，i18n 成员侧键删除，见 R7；v3：评审再否决「content 预存机器引用行」——模板消息 content 恒空，meta 是单一事实，降级文本由投递点从 meta 现场合成，见 R8；实现期修正见 §实现补充） |
| 测试基线 | `python -m pytest tests/unit_tests/agent_teams` → **1905 passed / 16 skipped**（F_62 基线 1878，净新增 27 例：`test_message_template.py` 11、`test_database.py` 2、`test_message_handler_render.py` 1、`test_team_scheduler.py` 1，其余为既有用例改断言） |
| 范围 | `tools/models.py`（消息表 `meta` 列）、`tools/database/`（engine 迁移 + message_dao 透传）、`tools/message_manager.py`、`agent/scheduling/`（render 瘦身 + 发送带 meta）、顶层 `message_template.py`（新模块：占位符标准 + resolver）、`agent/coordination/handlers/message.py` 与 `external/format.py`（两个投递渲染点）、`prompts/{cn,en}/scheduler_*.md`（新模板，成员侧文案唯一来源）、`i18n.py`（成员侧键删除）、`docs/specs/S_12·S_22` 同步 |
| Refs | #751 |
| 关系 | F_62 交接消息面的直接演进：把「发送时刻定格全文快照」升级为「两阶段模板渲染」。不碰 F_62 的状态机、投票、扫描与激活语义 |

## 背景：F_62 消息面的三个问题

F_62 的调度器交接消息（`agent/scheduling/render.py`）在发送时刻把文案完全定格，两种做法各错一半：

1. **`render_task_start` 嵌入 `task.content` 全文快照**——任务全文在 task 表已有一份、
   消息表再存一份（数据重复）；快照定格在发送时刻，之后任何 `update_task` 修订对已落库
   消息不可见（陈旧）；消息历史（`view_message`、external `read_inbox`）被大段任务全文
   反复污染（上下文噪音）。
2. **`render_review_request` / `render_review_renudge` 只带 title**——reviewer 收到指令后
   被迫先调 `view_task` 才能知道验收什么，多一轮工具往返，违背「调度器消息即完整指令」
   的模式体验。
3. **文案无编排能力**——任务字段无法内联进定制化文案的任意位置；要么全文快照、要么
   光秃 title，没有中间态。

## 核心洞察

1. **消息行是持久事实，投给 LLM 的是视图。** 消息表承担离线补投与历史可见（F_62 不变量
   「交接 = 持久邮箱消息」），它该存**意图 + 引用**；成员收到的 user message 是视图，视图
   在投递时刻物化。存储归一化、视图反归一化——即**两阶段渲染**：发送时刻定「哪类消息、
   绑定哪个任务」，投递时刻才定「最终文本」。
2. **模板是代码资产，不是数据。** 模板文本住 `prompts/`（随代码版本走），消息行只落
   模板 key。模板改版立即对所有未投递消息生效；带 `{{...}}` 裸占位符的半成品永不落库
   （落库即降级路径变砖）。
3. **投递时刻真相在手边。** hydrate 点按引用现查 task/member 行，拿到的永远是投递时刻
   的最新状态。推论：大部分「复杂控制逻辑」不需要往消息里塞数据——例如补投时任务已
   settle 的催办可按现查状态作废——能表查的一律现查，消息里只放引用。
4. **meta 是单一事实，降级文本失败时现场合成。** 模板消息的 content 恒为空——「以防
   万一」的预存引用行与 meta 信息冗余（kind 即 template key、task_id 即 refs.task），
   防的唯一故障（meta 列本身损坏）是 DB 损坏级事件，预存副本同样救不了。展开失败时
   投递点**从 meta 现场合成**一行 fallback（template key + task_id，成员 `view_task`
   兜底）：eager 冗余变 lazy 合成，正常路径零副本。附带收益：消息表形状清晰二分——
   人发的消息 content 有话、meta 空；框架模板消息 content 空、meta 有配方。成员侧
   文案唯一来源是模板（不走 i18n），杜绝双源漂移。

## 设计

### 1. 存储：消息表加一列通用 `meta`（一次迁移，永久扩展）

`TeamMessageBase` 增加 `meta: str | None`（TEXT，nullable，JSON 序列化）。旧库迁移走
`engine.py` 既有的动态表 ALTER 机制（`_ensure_dynamic_table_indexes` 同族，与
`review_round` / `max_review_rounds` 同一条路）。

```
content: ""                                            ← 恒空：意图完全由 meta 表达
meta:    {"template": "scheduler_review_request",
          "refs": {"task": "T1"},
          "params": {"feedback": "..."}}               ← 单一事实，唯一文案来源
```

（`content` 列 `nullable=False` 不动，空串即可。）

**meta 三铁律**（防垃圾抽屉，落 S_12）：

- **meta 单一事实**：模板消息 content 恒为空串，不存在需要与 meta 保持一致的第二份
  表述。降级文本不预存——展开失败时投递点从 meta 现场合成（template key + task_id
  一行，成员 `view_task` 兜底）。推论：**所有消息消费路径必须能处理「content 空 +
  meta 在」的行**，不得有 `if not content: skip` 式防御误伤（实现时 grep 确认）。
- **framework-only 投递载荷**：meta 只影响「这条消息如何被投递 / 渲染」，永不承载业务
  事实——任务真相在 task 表、票在 vote 表，meta 里只放引用与渲染参数。LLM 工具面
  （send_message 工具）不暴露 meta。
- **`protocol` 字段不动**：`protocol="json"` 维持「机器旁路控制报文」语义（tool approval
  fallback，绕过 LLM 渲染直接喂中断恢复）。调度器消息是「需投递展开的 LLM 正文」，
  `protocol` 恒为 `"plain"`。

### 2. 占位符标准：`{{namespace.field}}`

- **唯一语法** `{{ns.field}}`，正则单遍扫描替换；**非递归**——填充值不再扫描占位符
  （task.content 是 LLM 写的文本，必须防二次展开注入）。
- 缺字段 / 未知命名空间渲染为 `<missing:ns.field>` 而非抛异常——与错误系统 lazy-safe
  渲染哲学一致；整条模板展开抛异常时由投递点从 meta 合成 fallback 行（见 §6 降级链）。
- **命名空间注册表 + 字段白名单**（不做 getattr 直通，防内部列泄进提示词）：

| 命名空间 | 数据源 | 解析时机 | v1 白名单 |
|---|---|---|---|
| `task.*` | task 行，经 `meta.refs.task` 查 | 投递时刻现查 | `task_id` `title` `content` `status` `assignee` `reviewer` `review_round` `max_review_rounds` |
| `member.*` | member 行，经 `meta.refs.member` 查 | 投递时刻现查 | `member_name` `display_name` `desc` |
| `param.*` | `meta.params` 内嵌值 | 发送时刻定格 | 键即字段，值须为标量字符串 |

规约：**能表查的一律走 `refs` 现查；`param.*` 只收无法表查的瞬时渲染参数**（v1 仅
rework 的 fail-feedback 聚合文本——票表聚合含轮次语义，发送时刻定格最不易错）。

### 3. 模板落位：`prompts/{cn,en}/scheduler_*.md`——成员侧文案的唯一来源

markdown 模板正文按语言放 `prompts/`（`load_template` 机制现成，扁平命名与
`dispatch_scheduled_leader.md` 风格一致），符合分层约定「运行时短串进 `i18n.py`，
模板正文进 `prompts/`」。v1 五份（只覆盖走邮箱的成员交接；leader 直投的升级 / 摘要
不走消息表、没有 meta 通道，其一行式文案维持 i18n 现状）：

```
scheduler_task_start.md        # 开工（含 plan 闸变体段或独立文件，实现时定）
scheduler_review_request.md    # 送审派发
scheduler_review_renudge.md    # 催办
scheduler_rework.md            # 打回返工（用 {{param.feedback}}）
scheduler_verified_report.md   # 通过后要求汇报
```

**成员侧不再走 i18n**（评审修正）：`i18n.py` 的 `scheduler.task_start` /
`task_start_plan` / `review_request` / `review_renudge` / `rework` /
`verified_report` 键（cn/en）**直接删除**，不保留短句化变体——否则同一条消息的文案
分裂在 i18n（短句）与模板（全文）两处、双语共四处维护，口径必然漂移。文案只有模板
一份。leader 侧键（`leader_task_done*` / `leader_escalation_*` / `leader_all_done`）
不动。

### 4. 发送侧：`agent/scheduling/render.py` 变薄

各 `render_*` 函数从「经 i18n 拼全文」变为「只组装 meta」（template key + refs +
params），content 恒传空串；`_send_as_leader(member_name, content, *, meta=None)`
透传；`message_manager.send_message` 与 `message_dao.create_message` 增加
`meta: dict | None = None` 关键字参数（JSON 序列化落列）。

### 5. 投递侧：顶层新模块 `message_template.py`（两个消费点共用）

与 `inbound_render.py` 平级（`handlers/message.py` 与 `external/format.py` 都要用，
不能陷进 coordination 子包）：

```python
async def expand_message(msg, *, task_dao, member_dao, language) -> str:
    """Return the delivery-time text for one message row.

    meta.template present -> load template (recipient language) -> resolve
    {{ns.field}} via the namespace registry -> full markdown. Any failure
    (missing template / task row gone / JSON error) synthesizes a one-line
    fallback from meta itself (template key + task_id). Messages without
    meta return msg.content unchanged.
    """
```

两个投递点改造：

- `handlers/message.py` `_format_message`：先 `expand_message` 得正文，再进
  `render_inbound`。带 template 的框架指令消息**不再附加 `reply-hint` note**（响应动作是
  调工具，不是回信）；hitt-silence 语义不变。
- `external/format.py`：同样先展开再渲染（external client 直连共享 DB，有 DAO）。

**信封语义澄清**：展开后的完整 markdown 作为 `<team-inbound from="team_leader" ...>` 的
body 投递。信封是全团队消息协议的统一外壳（sender / message_id / 时间戳，成员系统提示词
按它教学），调度器消息不例外；废弃的是「短句 + 固定格式详情附件块」的内容形态，不是信封。
XML 转义走既有 `_esc_text`，无新增面。

### 6. 降级链

```
meta.template 展开成功            → 完整 markdown 文案（字段内联，一体成型，收件人语言）
模板丢失 / 任务行不在 / parse 错  → 投递点从 meta 现场合成一行 fallback
                                     （template key + task_id，成员 view_task 兜底），
                                     log warning
meta 列本身损坏                   → DB 损坏级事件，预存副本同样救不了，不设防
```

降级文本是失败路径的现场合成（lazy），不是每条消息预付的副本（eager）——正常路径
零冗余。消息消费路径核查：LLM 工具面**没有**消息历史查询工具（成员收消息只有 mailbox
注入一条路），真实消费点共三处且全在改造名单内——`handlers/message.py`
`_format_message`（进程内投递，含 HITT）、`external/format.py`（`read_inbox`）、
bridge relay `_bridge_deliverable_for`（见遗留）。实现时 grep 确认无
`if not msg.content: skip` 式防御误伤空 content 行。

## 被拒绝的方案

- **R1 维持全文快照嵌入**：数据重复 + 快照陈旧（正确性问题，不只是审美）+ 历史噪音。
- **R2 复用 `protocol="json"`、content 存结构化 payload**：`protocol="json"` 的既有语义是
  「机器旁路报文，绕过 LLM」（唯一生产者 tool-approval fallback、唯一消费者中断恢复拦截，
  `_format_message` 不看 protocol）——与「需投递展开的 LLM 正文」语义相反；MessageHandler
  被迫变成按 type 分流的 tag 分发器（特殊分支繁殖）；content 失去自描述，所有展示路径
  不改就显示裸 JSON（降级变砖 vs 变短句）。
- **R3 `task_ref` 专列**：中间方案。「按任务查消息」是假想查询需求（唯一消费者就是投递
  点）；每种新的结构化需求都要再 ALTER 一次表——通用 `meta` 列一次迁移覆盖后续演进。
- **R4 不落消息表、唤醒时直接合成注入**：破坏 F_62「交接 = 持久邮箱消息，离线成员 sweep
  补投」不变量——内存记账崩溃即漏投。
- **R5 hydrate 为固定格式 `<team-note kind="task-detail">` 附件块**：中间方案。所有消息
  共享同一详情附件格式，任务字段无法内联进文案，生硬（评审定稿意见）。
- **R6 模板文本落库**：模板改版对已落库消息永不生效；`{{...}}` 半成品进历史 / 外部
  inbox 即变砖。模板是代码资产。
- **R7 i18n 保留成员侧「降级短句」键**（本文初稿方案，评审否决）：同一条消息的文案
  分裂在 i18n（短句）与 prompts 模板（全文）两处、双语共四处维护，口径必然漂移；
  调度器成员侧文案的唯一来源应是模板。
- **R8 content 预存非本地化机器引用行**（v2 方案，评审否决）：引用行与 meta 信息完全
  冗余（kind 即 template key、task_id 即 refs.task），防的唯一故障「meta 列损坏」是
  DB 损坏级事件、预存副本同样救不了；且 LLM 工具面不存在消息历史查询路径，「不认识
  meta 的读者」是臆想读者。降级文本改由投递点失败时从 meta 现场合成——eager 冗余变
  lazy 合成，模板消息 content 恒空。

## 改动清单

| 文件 | 改动 |
|---|---|
| `tools/models.py` | `TeamMessageBase.meta`（TEXT nullable）+ docstring 三铁律；`content` 列不动（模板消息存空串） |
| `tools/database/engine.py` | 动态消息表 `meta` 列 ALTER 迁移 |
| `tools/database/message_dao.py` | `create_message(..., meta=None)` 序列化落列 |
| `tools/message_manager.py` | `send_message(..., meta=None)` 透传 |
| `message_template.py`（新） | 占位符解析 + 命名空间注册表 + 白名单 + `expand_message`（含失败时从 meta 合成 fallback） |
| `prompts/{cn,en}/scheduler_*.md`（新 ×5×2） | 成员交接 markdown 模板（成员侧文案唯一来源） |
| `agent/scheduling/render.py` | 各 render 只组 meta，content 恒空串；scheduler 发送带 meta |
| `agent/coordination/handlers/message.py` | `_format_message` 先 expand；template 消息去 reply-hint；确认空 content 行不被防御分支跳过 |
| `external/format.py` | read_inbox 路径先 expand |
| `i18n.py` | **删除** scheduler 成员侧六键（task_start / task_start_plan / review_request / review_renudge / rework / verified_report，cn/en）；leader 侧键与 `scheduler.none` 去留实现时定 |
| `docs/specs/S_12` | 消息表新列 + meta 三铁律 |
| `docs/specs/S_22` | 消息面段改为两阶段渲染契约 |

## 测试计划

- `message_template` 纯函数：占位符替换 / 白名单拒绝 / 非递归（值含 `{{...}}` 不二次展开）/
  `<missing:...>` / param 标量校验 / 失败时从 meta 合成 fallback 行。
- 存储：meta 落列与读回、旧库迁移加列、无 meta 消息行为不变、空 content + meta 行可
  正常投递（不被未读扫描 / 渲染防御跳过）。
- 投递：`_format_message` 展开成功注入全文、模板丢失合成 fallback、任务行删除合成
  fallback、external `read_inbox` 同语义、reply-hint 抑制仅作用于 template 消息。
- 调度器端到端：task_start / review_request 消息行 content 为空串 + meta 正确组装；
  i18n 成员侧键删除后无残留引用。

## 实现补充（设计→落地的两处修正）

1. **消费点是四处不是三处——HITT 回调是设计遗漏。** 设计阶段清点的消费路径漏了
   `handlers/message.py::_notify_human_agent_inbound`：它直接读 `row.content` 推给人类成员的
   SDK 回调，而**人类成员完全可以是 scheduled 团队的 assignee / reviewer**——不展开就会给
   controller 推一条空消息。实现时 grep 出来一并修掉（该路径同样走 `_expand`）。
   教训：「消费点清单」必须靠 grep 得出，不能靠记忆。同一趟 grep 也确认了全树唯一的
   `not msg.content` 防御在 `_try_parse_approval_payload` 里、由 `protocol != "json"` 先短路，
   不误伤空 content 的模板行。
2. **「行取不到」与「字段答不上」是两种失败，语义必须分开**（`RefUnresolved`）。
   `<missing:ns.field>` 只适用于**模板 bug**（模板写了 `{{param.x}}` 但没传）——渲染成内联标记、
   照常投递。而 `refs` 指向的**任务行已删**是另一回事：文档失去了主语，此时渲染出一篇满是
   `<missing:task.title>` 的简报是垃圾，必须走 fallback 行。实现上 `_resolve_namespaces` 在
   行取不到时 raise `RefUnresolved`，由 `expand_message` 的兜底转成 fallback。

## 已知遗留

- approval `protocol="json"` 报文未来可统一到「content 空 + meta 载荷」模型——独立重构，
  不混本次。
- broadcast 消息暂不支持 meta 展开（v1 调度器只发定向消息，`render_messages` 的 `bodies`
  映射已按 message_id 键预留）。
- `member.*` 命名空间已实现但当前无模板使用（调度器 v1 交接全部只 ref task）。留着是因为
  "谁是这个任务的验证者/承担者"的展开迟早要用——但如果一直没用上，就该删掉，不要养着。
