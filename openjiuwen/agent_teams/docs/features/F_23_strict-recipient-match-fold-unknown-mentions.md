# Strict ``@<member>`` Recipient Matching + Fold Unknown Mentions Back to Text

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-26 |
| 范围 | `openjiuwen/agent_teams/interaction/router.py`、`runtime/manager.py`、`tests/unit_tests/agent_teams/interaction/test_router.py`、`tests/unit_tests/agent_teams/runtime/test_dispatch_payload.py` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/`：1207 passed, 16 skipped |
| Refs | `#751` |

## 背景

`interact(str)` 的前缀语法由 `parse_interact_str` 解析，它是**纯语法**函数：不查 roster，把
每个 `@<member>` token 直接 fan-out 成带 `target` 的 payload。校验留给 dispatch。但两条 dispatch
路径行为不一致，且都不符合预期：

- **`#` 类(Operator)**：`UserInbox.direct` 完全不校验成员存在，直接 `send_message` 写消息总线
  —— 用户敲一个不存在的 `@ghost`，就会在 DB 里凭空生成一条发给 `ghost` 的消息。这是本次要修的 bug。
- **`$` 类(HumanAgent)**：`deliver_direct` 校验了成员存在，但不存在时返回
  `DeliverResult.failure("unknown_member:<target>")` —— 不写库，但也没有"降级处理"，整条输入被丢弃。

预期：**严格匹配 roster；匹配不到的 `@` 不是路由指令，按"无 @ 的一般输入"处理**——`#` 投给
leader 的 DeepAgent，`$` 投给该 avatar，`$`/`#` 两类一致。

## 数据结构 / 设计

核心模型(消除特殊情况)：**每个 `@mention` 要么是有效路由指令(消费成一条点对点消息)，要么无效
(它就是普通文本，折回到 leader/avatar 的一条"无 @ 消息"里)**。

- `router.py:resolve_targets(payloads, *, member_exists)`：解析后的 roster 校验步骤(async)。
  - 复用文件内既有的 `MemberExistsCheck` 谓词模式(与 `deliver_direct` 并列)，调用方注入
    `member_exists` 闭包，谓词归属调用方决定。
  - 已知 recipient 的点对点 payload 原样保留；未知 recipient 折回；god-view / avatar-drive /
    broadcast(`@all`/`@*`，无 named target)直接 passthrough。
  - 全部已知 → 原样返回；否则返回 `<已知点对点 payloads> + [<一条折回的无 @ payload>]`。
- `router.py:_named_target(payload)`：取出 payload 的点对点 recipient；god-view / avatar-drive /
  broadcast 返回 `None`(它们不查 roster)。
- `router.py:_fold_unknown_mentions(unknown)`：把未知 mention 重新拼回正文
  (`"@g1 @g2 <body>"`)，按 channel 折成 `GodViewMessage`(Operator 来源)或
  `HumanAgentMessage(sender=..., target=None)`(HumanAgent 来源)。
- `manager.py:TeamRuntimeManager._resolve_recipients(agent, payloads)`：把
  `backend.get_member(name) is not None` 适配成 `member_exists` 布尔谓词调 `resolve_targets`；
  backend 为 None(纯 god-view agent)时无 roster 可匹配，passthrough。
  在 `interact` 里**持有 gate ticket 之后**调用(与 dispatch 串行；gate 关闭时不触达 agent)。

## 决策

- **未知 `@` 折回为文本，而非丢弃 / 报错**。理由与既有语义一致：`parse_interact_str("@dev-1")`
  (无 body，不构成合法 mention)本就把 `@dev-1` 当纯文本保留。"`@` 指向不存在的成员"同理——它
  不是合法路由，就是文本。报错会破坏"用户随便敲"的体验；丢弃会无声吞掉用户内容。
- **降级正文保留原文(含失效 `@` 前缀)**。`# @ghost ship it` → leader 收到 `@ghost ship it`，
  而非 `ship it`。不丢用户内容；leader 能看到用户本想找谁(可能是 typo)。
- **部分匹配 = 有效照常点对点 + 无效折回一条**。`# @m1 @ghost on it`(m1 在、ghost 不在)→ m1 收到
  点对点 `on it`，**同时** leader 收到 `@ghost on it`。这是用户明确要求("无效的 @ 也同时投递给
  leader 或 $ 的成员，作为一般输入")。注意折回正文是"原文去掉已成功路由的 mention"——已路由的
  `@m1` 被消费，未路由的 `@ghost` 作为文本留下。
- **校验落在 `interact`(组级)，不落在 `_dispatch_payload`(逐条)**。"一个都没匹配上才整体降级 /
  部分匹配如何拆分"需要看整组 recipient；逐条 dispatch 看不到兄弟 recipient。`_dispatch_payload`
  保持"哑路由"，只按 payload 类型分发——直接调它的单测仍然有效。
- **str 与结构化 payload 统一走 `resolve_targets`**。结构化入参(SDK 直传
  `OperatorMessage(target=...)`)同样受严格匹配保护，不再有"绕过 str 解析就能给不存在成员写库"的洞。
- **逻辑归属 `router.py`**。这是"按 roster 解释 interact 语法"，属于 routing/grammar 的家，不埋进
  runtime manager；`manager._resolve_recipients` 只做 backend → 谓词的适配。

## 拒绝的方案

- **把校验塞进 `_dispatch_payload`**：逐条无法实现"整组判断"，多 recipient 部分匹配会错误地既
  发点对点又重复投 leader；且会让哑路由长出业务分支。
- **重构 `parse_interact_str` 让它直接产出已校验 payload**：parse 是 sync 纯函数、roster 查询是
  async；把 roster 揉进 parse 会污染纯语法层，并破坏其 ~30 条既有单测契约。改为"纯解析 + 独立 async
  解析(resolve)"两段式，parse 完全不动。

## 已知遗留

- `UserInbox.direct` 的 docstring 仍说"调用方负责校验 target 存在"——现在该校验由其调用方
  `interact`(经 `resolve_targets`)落实，inbox 本身保持哑写入，语义未变，未改文案。
- `TeamAgent.broadcast()` / `human_agent_say()` 等不经 `@` recipient 解析的入口不在本次范围。
