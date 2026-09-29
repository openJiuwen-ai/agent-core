# Swarmflow Model Hint Fail-Fast（options-bag 值治理）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-09-29 |
| 范围 | `workflow/engine/primitives.py`（`_validate_model_hint` + `agent()` / `AgentSession.send()` 接线 + `_opened_model` 首轮锁定 + `_attempt_calls` TimeoutError 消息注入）、`workflow/engine/backends/base.py`（`AgentBackend.model_pool_names()`）、`workflow/backends/team_worker_backend.py`（`model_pool_names()` 透传）、`agent/agent_configurator.py`（`_SwarmflowModelResolver` 类）、`tools/locales/descs/{cn,en}/workflow/swarmflow.md`、`tests/unit_tests/agent_teams/workflow/test_operator_options.py` |
| 测试基线 | `tests/unit_tests/agent_teams/workflow/test_operator_options.py` → 10 passed；变更区域针对性测试 292 passed |
| Refs | #1795 |

## 背景

SDD-0020 v4（options-bag value governance）：引擎此前对 options 只做**键**治理（`_build_opts`
校验未知键 fail-fast），**值**不治理。`agent(model=...)` 是第一个有真实值的 hint，其行为是
F_31 决策 4 定下的"未命中静默回退 base spec model"。两个缺口随使用暴露：

1. **UI 说谎**：脚本写 `agent(prompt, model="deepseek-v4")`，名字拼错（或 pool 未配该名）时
   AGENT_STARTED 事件照常带这个名字发出、journal 照常落盘，worker 却跑在 base spec 的模型上。
   事后从事件流完全无法发现——签名里的名字与实际执行的模型不一致。
2. **会话中途换模型是付费陷阱**：`AgentSession` 的 avatar 在首个 cache-miss 轮把模型固化进
   harness，后续轮传不同 hint 被静默忽略；但 hint 仍折入 `call_signature`——结果是**缓存
   MISS + 在旧模型上付费重跑**，作者以为换了模型，实际两样都没发生。

治理原则与既有键治理对称：不可信脚本的 option **值**拿到与键同等的 fail-fast 待遇。

## 决策

### D1 引擎层 fail-fast（`_validate_model_hint`）

`agent()` 与 `AgentSession.send()` 在 `_build_opts` 之后、`call_signature` 计算之前调用
`_validate_model_hint(rt, opts)`：backend 的 `model_pool_names()` 返回非 None 且 hint 不在池内
→ 抛 `EngineError`（消息列出可用模型，空池时注明 "no model pool configured"）。时序保证：
**签名不计算、AGENT_STARTED 不发、journal 不写**——错误调用在产生任何可观测副作用之前被拦截，
resume 重放同脚本得到同样的确定性报错。

### D2 会话模型首轮锁定

`AgentSession` 新增 `_opened_model`（`__slots__` 成员），在 `_ensure_open`（avatar 开启）时
记录当轮 hint。后续轮：hint 与锁定值不同 → 抛 `EngineError` 并指引 `fork()`；相同 hint 重复
传是 no-op。`fork()` 派生的子会话独立走自己的首轮锁定（fork 时传新 hint 合法）。锁定时机是
**首个 cache-miss 轮**——纯 cache-hit 的会话从未开 avatar，无锁定可言。

### D3 backend 协议：`model_pool_names() -> list[str] | None`

`AgentBackend.model_pool_names()` 默认返回 `None` = "无池概念，不校验"——`MockBackend`、
测试 stub、旧式 backend 行为逐字节不变（与 `KNOWN_OPTIONS` 的白名单扩展模式对称：能力靠
backend 自声明，引擎只消费）。`TeamWorkerBackend.model_pool_names()` 用 `getattr` 鸭子探测
注入 resolver 的 `pool_names` 属性：有则活读返回（pool 刷新可见），旧式闭包 resolver 无此
属性 → `None` → 退回不校验。

### D4 resolver 升级为 callable 类（`_SwarmflowModelResolver`）

`agent_configurator` 的 `swarmflow_model_resolver` 从闭包升级为可调用类，调用契约逐字不变
（`inject_team_handles → rails → tool_factory → runner → backend` 五站注入链零改动）。
新增 `pool_names()`：活读 `spec.model_pool` 的 `model_name` 列表。纵深防御：validation 与
resolution 之间 pool 收缩（hint 过了校验但 resolve 落空）时经 `team_logger.warning` 告警，
`_warned` 集合按名去重——每个失效名只告警一次。

### D5 文档同步

cn/en swarmflow 工具描述：`model` 条目补"名字必须在团队模型池内，否则脚本立即报错并列出
可用模型"；`timeout` 条目补 agent 路径的重试语义与"失败消息自带秒数"；删除从未实现的
`phases[].model` 声明（META 的 phases 项没有 per-phase model 覆盖，文档一直在描述不存在的
能力）。

### D6 捆绑交付的独立修复：TimeoutError 消息注入

`_attempt_calls` 里 py3.11 裸 `TimeoutError` 的 `str()` 为空，`AGENT_FAILED` 的 message 因此
不带预算数字。修复：捕获时重建 `TimeoutError(f"timed out after {timeout}s")`，走既有
error_detail 管线。**该修复与 model fail-fast 无关**（有自己的文档条目 D5 与测试用例），
本应独立成 `fix(swarm)` 提交——实际与特性捆在同一提交交付，在此如实归档。

## 拒绝的方案

- **保留静默回退（F_31 决策 4 的原语义）。** 当年"回退让写错名字不至于挂掉整个工作流"的
  理由，在会话签名陷阱与 UI 失真暴露后不再成立：错误的代价从"跑错模型"升级为"烧缓存 +
  假事件流"。fail-fast 的报错消息带可用模型清单，修复成本低于事后排查。
- **只在 resolution 层（backend）校验。** 太晚——`AGENT_STARTED` 已发、journal 已写，报错
  出现在事件流之后，UI 先显示一个随后失败的名字，与治理目标矛盾。
- **`model_pool_names()` 设为抽象必选方法。** 破坏 `MockBackend` 与所有既有 stub 的字节级
  兼容；引擎离线单测（铁律 1）依赖 MockBackend 零业务装配。默认 `None` 让能力自声明。
- **允许会话中途换模型（重建 avatar 或热切换）。** 换 model 必然改 `call_signature` →
  cache MISS + 旧模型付费重跑，这正是要消灭的行为。`fork()` 是既有 sanctioned 路径
  （F_81），报错消息直接指引它。
- **resolver 注入面用 `typing.Protocol` 结构化子类型。** `pool_names` 是**可选**能力（旧式
  闭包没有），Protocol 成员无法可选；鸭子探测 + `None` 降级让旧注入零改动存活。若未来
  resolver 注入面正式化，可再收窄为 Protocol。
- **`phases[].model`（META 阶段级模型覆盖）顺手实现。** 文档声明过但从未实现；本期删除
  声明而非补实现——per-call hint 已覆盖该场景，阶段级覆盖没有真实需求支撑。

## 验证

`tests/unit_tests/agent_teams/workflow/test_operator_options.py`（10 passed，新文件）：

- 池外 hint 抛 `EngineError`（backend 零调用）且早于签名/事件/journal；消息列出可用模型
- 池内 hint 正常透传；`pool=None`（MockBackend / 旧式 backend）跳过校验、行为逐字回退
- 会话第二轮不同 hint 报错并含 fork() 指引；同 hint 重复传正常；`fork()` 子会话可换模型
- resolver 纵深告警按名去重（`resolve_member_model` monkeypatch 为恒 None）
- 超时失败消息携带预算秒数、重试控制流不变（3 次尝试后 agent 级失败、脚本继续）

变更区域针对性测试 292 passed（含 `agent_teams/workflow/` 邻近既有用例）。

## 已知遗留

- **提交纪律偏差（流程注记）**：本特性实际以 `feat(swarmflow)` + `test(swarmflow)` 两个
  提交落地，scope 应为 `swarm`，工具描述文档捆进了 feat 提交，且未按「三连提交」约定拆出
  独立 `docs(swarm)` 提交——本 F 文档与 S_18 / F_31 修订为事后补救。
- **首轮无 hint、次轮带 hint** 的会话：报错消息显示锁定值为 `None`，行为正确（锁定的是
  base spec 模型）但措辞可再优化。
- **`getattr` 鸭子探测**（D3）待 resolver 注入面正式化后收窄为 Protocol。
- D6 的 TimeoutError 修复未独立提交，git 历史上无法单独 revert。
