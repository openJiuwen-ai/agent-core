# 双工投递路由：Agent 干活时收到新消息，自动决定怎么投递

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-13 |
| 范围 | `agent_teams/duplex/`（新增）、`agent_teams/harness/{control,state,protocol,native_harness,team_harness}.py`、`agent_teams/agent/coordination/{dispatcher.py,handlers/message.py}`、`agent_teams/group_chat/message_handler.py`、`agent_teams/schema/blueprint.py` |
| 测试基线 | 新增 12 个用例全部通过；`tests/unit_tests/agent_teams`（除缺可选依赖的 cli/observability）3208 passed，140 failed 与基线 ce2aedda 完全相同（环境缺 `opentelemetry.exporter`） |
| Refs | #1897 |

## 背景

成员干活时会收到新消息：用户的新要求、队友的新发现、团队广播、群聊 @。原来邮箱里的消息一律
`deliver_input(use_steer=True)`，在下一次模型调用前插进去。这有两个问题：

- 推翻任务前提的纠正来得太晚。成员会继续做一段要返工的工作，直到下一次模型调用才看到纠正。
- 和自己无关的广播也会把空闲的成员叫醒，白白烧一轮。

本特性给每个成员配一个监工（小脑）。监工是一个只回答选择题的小模型。新消息先按场景分类，
需要时由监工判断，再决定怎么投递。执行成员（大脑）在监工判断期间继续工作。功能默认关闭。

例：成员正在设计 Kafka 接入方案。队友私信"环境不能部署 Kafka，改用 Redis Streams"。监工判断
必须打断。成员在安全位置停下，作废旧计划，带着这条消息重新规划。如果队友只说"Redis 连接信息在
配置文件里"，消息排到这一轮结束后处理，成员不被打断。

**监工只决定投递方式，不决定业务行为。** 它不改写、不删除、不回复消息。消息内容原样进成员的
上下文；监工唯一能做的是选择"什么时候、以什么方式"进去。"只记录"的消息仍留在团队消息记录里。

## 术语

| 中文 | 代码 | 效果 |
|---|---|---|
| 打断重规划 | `interrupt`（`DeliveryAction.INTERRUPT`） | 在安全位置暂停：正在生成就取消，正在执行工具就等它完成。保留上下文和工具结果，作废旧计划，带新消息重新规划 |
| 插入 | `steer`（`DeliveryAction.STEER`） | 不打断，下一次模型调用前插入（`send(immediate=True)`） |
| 排队 | `append`（`DeliveryAction.APPEND`） | 不打断，这一轮结束后再处理（`send(immediate=False)`，follow-up） |
| 直接处理 | `deliver now`（`DeliveryAction.DELIVER_NOW`） | 目标空闲，马上开始（`send` 在 IDLE 起新 round） |
| 只记录 | `visible only`（`DeliveryAction.VISIBLE_ONLY`） | 留在团队消息记录里并标为已读，不进入成员上下文，不唤醒 |
| 原样投递 | `passthrough`（`DeliveryAction.PASSTHROUGH`） | 不经路由，走 SDK 原有方式 |

## 一条消息怎么走

```
邮箱行（MessageHandler 排空邮箱）
  │
  ├─ 1. DuplexMessageHandler 按发件人和行类型分出场景（source）
  ├─ 2. policy.plan：要不要问监工，问什么
  │      不问 → 当场执行动作，调用方照常标已读
  │      要问 → 交给 DuplexScheduler，路由器接管这一行（暂不标已读）
  ├─ 3. scheduler：每个成员同一时间只做一次判断；判断期间新到的消息合成下一批
  ├─ 4. classifier 回答，附带概率（三种格式）
  ├─ 5. policy.resolve + final_action：执行前重新读目标状态
  └─ 6. router 执行动作，再标已读；投递或打断失败的行保持未读，等下一次定时轮询重投
```

两条原则：

1. "要不要打断"（urgency）只能让动作变轻，不能变重。判错的代价最多是消息晚一点被处理。
   "有没有关系"（relevance）只用于广播。判成无关时消息只记录，成员看不到，所以门槛定在 0.8。
2. 监工失败或超时，消息照常投递：目标空闲就直接处理，目标忙就排队。消息不会丢。

## 各场景怎么处理

| 消息 | 问监工什么 | 目标空闲 | 目标忙 |
|---|---|---|---|
| 用户的消息（`from == "user"`、`@member`、`@all`、HITT 控制者经 avatar 发出的、passive human 发出的） | 不问 | 直接处理 | 插入（原有行为） |
| 队友私信 | urgency | 直接处理 | 必须打断 → 打断重规划；否则排队 |
| 群聊里 @ 本成员 | urgency | 直接处理 | 同队友私信 |
| Leader 的广播 | 不问 | 直接处理 | 插入（原有行为），不限流 |
| 团队广播 | relevance（空闲时只问这一个）+ urgency | 无关 → 只记录；有关 → 直接处理 | 无关 → 只记录；必须打断 → 插入；否则排队 |
| 群聊没有 @ 本成员 | 不问 | 只记录 | 只记录 |
| 系统消息（框架模板、JSON 协议行、看板巡视、催办等） | 不问 | 原样投递 | 原样投递 |

- "必须打断"指监工回答 INTERRUPT，且概率 ≥ 0.9。"无关"指监工回答 SKIP，且概率 ≥ 0.8。阈值是
  `duplex/policy.py` 的常量，不进配置——阈值属于投递策略，不属于某个模型。
- 用户的消息优先级最高：不经过监工，也不会被降级。
- Leader 的广播不经过监工，也不限流。原因：Leader 的全员停工广播绝不能被过滤或延后。发件人等于
  `team_spec.leader_member_name` 的非群聊广播归入这一场景（`MessageSource.LEADER_BROADCAST`）。
- 广播不会触发打断重规划，最多插入。
- 成员的运行时不支持打断（如外部 CLI 成员）时，"打断重规划"退为插入。
- 每个发送者 10 秒内最多发 1 条广播，超出的只记录（只对队友广播限速，用户和 Leader 的广播不限）。
  同一条广播失败后重投，不算第二条，不会被限流。
- 同一个成员同一时间只做一次判断。判断期间到的新消息，合并成下一批再判断。一批里多条消息都判
  成打断时，只做一次打断，带上全部这些消息。
- 执行动作前重新看一次目标状态。目标已经空闲，就直接处理（只记录不变）。规划时目标空闲（直接
  处理）、执行时目标已经在忙，就排队，不插入。
- 群聊没有 @ 的行在 DAO 层就不进成员邮箱（`get_broadcast_messages` 按 mentions 过滤），本来就只记录；
  路由器对这一场景的规则是兜底。
- 系统提醒不经过邮箱路径（`TaskBoardHandler` / `StaleTaskHandler` 直接 `deliver_input`），路由器
  根本看不到它们，原样投递由结构保证。

## 监工：三种格式

每种格式只需要 api_base、模型名和 API Key。两道选择题：

| 问题 | 选项 | 说明 |
|---|---|---|
| urgency | INTERRUPT / APPEND | 证据不足时选 APPEND |
| relevance | DELIVER / SKIP | 证据不足时选 DELIVER |

给监工的输入是当前任务摘要（成员名、阶段、目标、当前计划步骤）加新消息。每条消息前面写明发件人
（`from <sender>: ...`），监工才分得清用户和队友。群聊消息只给监工原始正文，不给上下文摘录。

| 格式 | 适用 | 怎么拿到概率 | 复用的 core 客户端 |
|---|---|---|---|
| `openai` | DeepSeek、GLM、Kimi 等，OpenAI 格式的中转站 | Chat Completions，`max_tokens=1`、`logprobs=true`、`top_logprobs=20`。选项映射成字母 A、B…，从 `top_logprobs` 里取这些字母，归一化成概率。不返回 logprobs 直接报错，不改用文本猜测 | `OpenAIModelClient`。它在 Chat Completions 路径上把 `logprobs` / `top_logprobs` 原样透传给 SDK，并把响应的 `choice.logprobs` 放进 `AssistantMessage.logprobs` |
| `anthropic` | Anthropic 格式的中转站 | Messages API 没有 logprobs。用 `tool_choice` 强制模型调用一个工具，入参 `{choice: enum, probabilities: {label: number}}`；校验和约等于 1、choice 与最大概率一致。**这个概率是模型自报的，没有校准** | `AnthropicModelClient`（`tools` + `tool_choice={"type": "tool"}`） |
| `jev` | JEV 格式的服务：TypeSafe 直连，或 OpenRouter、MCL、Yale 等。只有 TypeSafe 用自己的 `/systemone`，其余都用 OpenRouter 通用的 `/decisions` | 服务端直接返回选项和概率 | `core/foundation/llm/system_one` 的 `JevSystemOneClient` / `ChoiceQuestion` / `ChoiceAnswer`；端点、模型、密钥变量的默认值由同目录的 `jev_defaults_for(api_base)` 按 api_base 推出 |

## 打断重规划（NativeHarness 原生命令）

`NativeHarness.interrupt(content)` 往 supervisor 推一条 `_CmdInterrupt`：

1. 不在 RUNNING、没有活动 round、或已有打断在等 → 返回 `UNAVAILABLE`。路由器改用普通插入。
2. 记下 round 的原始 query，置 `pause_requested`（与 pause 共用协作停止点）。
3. 正在生成（`model_call_in_flight`）→ 硬取消这次模型调用，**不回滚**，直接进 PAUSED 并提交；
   正在执行工具 → 进 PAUSING，等工具跑完，round 在下一个模型调用边界停下，`_on_round_done` 进
   PAUSED 后提交。
4. 提交：把 `DeepAgentState.task_plan` 置空（作废旧计划），用原始 query 起一个续跑 round，把
   "计划已作废，请重新规划"加新消息作为一条 steer 推进去，返回 `INTERRUPT`。
5. 显式 pause / abort / stop、round 崩溃或 supervisor 崩溃都会让等待中的打断以异常结束
   （superseded）。**打断失败不回退成插入**：回退会推翻一次显式暂停/停止，或者把已经推进去的
   重规划消息再投一遍。邮箱行保持未读，等下一次定时轮询重投，重投时重新判断。

不回滚的理由：pause 的 LLM 阶段回滚会退回上一个迭代边界，把这一迭代开头已经排进上下文的 steer
一起退掉。打断要求保留上下文和工具结果，所以只取消生成本身。ReAct 在取消时会清理上下文：
保留用户消息和完整的工具配对，丢掉不完整的工具块；若上下文停在用户消息上，会补一条取消标记。

PAUSING 期间到达的 `send`：原实现对 PAUSING 没有分支，消息被静默丢弃。打断把成员放进 PAUSING
的时间大大拉长（要等工具跑完），所以**打断等待期间**的 `send` 按 RUNNING 处理（插入进 steering
队列 / 排队进 follow-up），续跑 round 会取到。显式 pause 的 PAUSING 行为不变（见已知遗留）。

外部 CLI 成员的运行时没有 `interrupt`。`harness/protocol.py` 新增可选协议 `InterruptibleHarness`；
运行时不满足它 → 视为 `UNAVAILABLE` → 普通插入。`TeamHarness` 转发 `interrupt` 和
`work_summary` 给内部 NativeHarness。

## 怎么开启

`TeamAgentSpec.duplex: DuplexConfig | None`：

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | False | 总开关 |
| `mode` | `"off"` | `"active"` 才装配路由器；`enabled=True` + `"off"` 等于关闭 |
| `format` | `"openai"` | `openai` / `anthropic` / `jev` |
| `api_base` | `""` | openai：API base（含 `/v1`），必填；anthropic：API base，必填；jev：留空用 TypeSafe 的 `https://api.typesafe.ai/v1`；OpenRouter 填 `https://openrouter.ai/api/alpha`，其他服务填它自己的地址 |
| `endpoint` | `""` | 只对 jev 生效，端点路径。留空时按 api_base 推出：主机是 typesafe.ai 用 `/systemone`，其他主机一律用 `/decisions` |
| `model` | `""` | 模型名。openai / anthropic 必填；jev 按主机给默认值：typesafe.ai 主机取 `jev-1.13.0`，openrouter.ai 主机取 `typesafe/jev-1.13`，可留空；其他主机必填 |
| `api_key_env` | `""` | **只存环境变量名**。留空时 openai / anthropic 用 `DUPLEX_ROUTER_API_KEY`；jev 按主机选：typesafe.ai 用 `TYPESAFE_API_KEY`，openrouter.ai 用 `OPENROUTER_API_KEY`（这两个和浏览器决策是同一把密钥），其他主机用 `DUPLEX_ROUTER_API_KEY` |
| `timeout_seconds` | 2.0 | 一次判断的总时限 |

**为什么只存环境变量名**：spec 随 `model_dump` 进 checkpoint、跨进程拉起成员时序列化、也会落到
日志和团队快照里。存密钥原文就等于把密钥写进这些地方。平台沿用自己的密钥存储，把密钥注入成员
进程的环境变量；子进程继承环境，路由器在调用时读取。密钥缺失按监工失败处理，消息照常投递。

`spec.duplex.enabled and mode == "active"` 时，`EventDispatcher` 在构造期把邮箱 handler 换成
`DuplexMessageHandler` / `DuplexGroupMessageHandler`（定义在 `duplex/handlers.py`，开启时才延迟导入；与 `reliability` 的条件装配同一模式，见
handlers 维度 5：模式差异换类，方法体内不判模式）。只换 leader / teammate / external_cli 三种角色；
human agent 与 bridge avatar 不换。关闭时装配的就是原来的类，**行为与原来逐字一致**。

## 与 steer_batch_size 的关系

非 leader 成员的 `TeamPolicyRail` 每次模型调用最多从 steering 队列取 `steer_batch_size` 条
（默认 2）。打断提交的重规划消息是**一条** steer（一批里的多条打断消息合并成一条），所以重规划
提示和它带的消息在同一次模型调用里一起进上下文，不会被批大小拆开。插入的消息仍逐条受这个上限
约束，与原行为相同。

## 决策

- **路由器异步接管，不阻塞协调循环。** coordination 的 EventBus 串行处理事件。若在排空邮箱时
  同步等待监工（最多 2 秒一条），用户输入、审批结果都会排在后面。所以要判断的行交给每成员一个
  worker 任务，handler 立即返回；路由器执行完动作后自己标已读，并入队一次 `POLL_MAILBOX`
  把判断期间被挡住的行排出来。被接管的行在后续排空里被过滤，不会重复提交。这一批有投递或打断
  失败时不立即重新排空，失败的行留给定时轮询，避免热循环重试。
- **广播水位线：被扣住的广播挡住更新的广播。** 广播的已读状态是每个成员一条水位线，只前进不后退。
  标记一条广播已读，就等于标记所有更早的广播已读。所以一条广播在路由器手里（判断中）时，
  比它新的广播一律不投递、不标已读，留在邮箱里；被扣住的那条处理完（成功或失败）后再排空。为此
  双工模式下排空按时间从旧到新（原排空从新到旧），被扣住的广播总在更新的广播之前决定。失败的那条
  仍在水位线之上，下一次轮询重投。
- **投递钩子是 `MessageHandler._deliver_message`。** 基类实现就是原来的两行（`deliver_input`
  + 记为已投递）。双工类覆写它，不在基类方法体里判断开关。
- **策略是纯函数。** `duplex/policy.py` 无 I/O、无运行时依赖，场景表每一行都有单测。
- **一次判断 = 一批。** 同一批里需要的问题集合不同（私信只问 urgency，广播问两个）就分组，
  每组一次分类请求，并发发出。整批答案回来后，每行的最终动作只算一次：非打断动作按到达顺序先执行，
  执行完立即标已读并释放，不等打断；打断最后做。分类器每成员只建一个，首次判断时创建，路由器关闭时关闭。
- **路由前后两次读状态。** 规划时读一次（决定问什么），执行前再读一次（`final_action`）。
  执行时若成员有待处理的 HITL 中断或已在退场，行被释放、保持未读，交还给原有排空路径的闸门。

## 拒绝的方案

- **monkeypatch SDK（JiuwenSwarm 原型的做法）**：替换 `_format_message` / `deliver_input` /
  `NativeHarness` 工厂。依赖十几个私有名字，SDK 一改就静默失效；本次改为原生实现，原型的打补丁层
  全部删除。
- **打断失败回退成插入**：会推翻显式 pause/stop，或重复投递已推进去的重规划消息。
- **在 `deliver_input` 里同步判断**：阻塞串行事件循环，用户消息会被监工延迟。
- **把阈值放进配置**：阈值是两条原则的一部分（降级门槛与只记录门槛），不随模型变化。
- **OpenAI 格式拿不到 logprobs 时改用文本答案**：文本答案没有概率，0.9 门槛失去意义；直接报错，
  按监工失败处理。
- **让 `interrupt` 进 `HarnessProtocol`**：`HarnessProtocol` 是 `runtime_checkable`，加方法会让
  所有不支持打断的实现 `isinstance` 失败。改成独立的可选协议 `InterruptibleHarness`。

## 验证

- `tests/unit_tests/agent_teams/duplex/test_policy.py`：场景表逐行一张表（含 0.9 / 0.8 阈值边界、
  执行前重读状态、10 秒广播窗口）。
- `test_router.py`：同一时间只做一次判断、判断期间到达的消息合批、广播限速只记录；监工失败照常
  投递（忙 → 排队）；打断失败不回退插入且行保持未读；只记录不唤醒；用户消息不问监工；Leader 广播
  不问监工、不限流；被扣住的广播挡住更新的广播，投递失败后不立即重排、下次轮询重投成功；关闭时
  装配原类。
- `test_classifier.py`：三种格式各一例，mock transport、无网络：openai 请求带 `max_tokens=1` /
  `logprobs` / `top_logprobs=20`、字母概率归一化、无 logprobs 报错；anthropic 强制工具
  `tool_choice`、枚举入参；jev 两题一请求，TypeSafe 地址请求 `/v1/systemone`，OpenRouter 地址请求 `/api/alpha/decisions`，其他主机请求它自己路径下的 `/decisions` 并读 `DUPLEX_ROUTER_API_KEY`。openai 用例同时证明 core 的
  `OpenAIModelClient` 在 Chat Completions 路径上透传并返回 logprobs。
- `test_duplex_e2e.py`：真实 NativeHarness 上，队友私信"不能部署 Kafka，改用 Redis Streams"
  经路由器判为打断：生成被取消、计划作废、上下文未回滚、重规划消息进入下一次模型调用。
- `tests/unit_tests/agent_teams/harness/test_interrupt.py`：原生打断命令在工具阶段等工具跑完、
  作废计划、PAUSING 期间的 send 不丢；空闲时返回 UNAVAILABLE。

## 已知遗留

- 显式 pause 的 PAUSING 期间 `send` 仍被静默丢弃（原有行为，本次只修了打断等待期间这一段）。
- 硬取消生成时，若上下文停在用户消息上，ReAct 的取消清理会补一条 `[Request cancelled by user]`
  标记。紧随其后的重规划消息说明了原因，但标记文字本身不准确。改它要动 core 的 ReActAgent。
- 打断在工具阶段要等工具跑完，worker 在此期间不处理下一批（仍然只有一个判断在飞）。
- 广播被扣住期间，更新的广播最多晚一次判断的时间（默认 2 秒）才投递，不等打断（广播不会触发打断）。
- 设置页（JiuwenSwarm）负责把配置传给 `TeamAgentSpec.duplex`、把密钥注入环境变量，不在本仓库。
