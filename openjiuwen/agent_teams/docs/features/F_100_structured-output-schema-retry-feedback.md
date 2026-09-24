# structured_output 校验重试回灌与工具内 schema 校验

- 日期：2026-09-22
- Refs: #751
- 范围：`workflow/engine/primitives.py`、`tools/structured_output_tool.py`、`tools/locales/{cn,en}.py`、`tools/locales/descs/{cn,en}/common/structured_output.md`、对应 UT
- 测试基线：`tests/unit_tests/agent_teams/workflow/` + `tools/test_structured_output_tool.py` + locale 校验

## 背景（真实故障）

fork-multi-model 工作流中 GLM-5.3-Flash 答题 worker 连续 3 次 attempt 全部
`'q_id' is a required property` 失败（schema 要求 `answers` 为对象数组、每项必含
`q_id`+`answer`，模型提交的结构缺 `q_id`），最终 `failed after 3 attempts`。
两个独立缺陷：

1. **盲重试**：`_attempt_calls` 对 schema 校验失败的原样重跑同一 prompt，
   模型没有任何纠错信息，三次重复同一错误。
2. **schema 语义错误只能跨 turn 修正**：args 格式错误（非法 JSON）在工具层
   抛异常、错误 tool_message 同 turn 回流模型自我修正；但 schema 语义错误
   （缺必填键）要等 turn 结束后引擎校验才发现，重试 = 重跑整个 turn，
   模型把几千 token 的答案全部重新生成只为补一个字段——两条错误路径不对称。

当时还观察到"每轮并行挂 2 个相同 `structured_output` 调用（前端 6 条 = 3×2）"，
据此做过 duplicate 提交拒绝；后续 claim-denial-crosscheck 全量日志复核
（26 次调用、三模型）中每次调用都来自单 tool_call 响应、整个 run 唯一次
`tool_call_count=2` 是两个不同工具的正常并行——同名双发未复现，该防御
随之 drop（见"拒绝的方案"）。

## 决策

1. **校验失败重试带错误回灌**（`primitives.py`）：`make_call()` →
   `make_call(feedback)`；`coerce` 抛出的 jsonschema/pydantic 错误经
   `_validation_retry_feedback()` 拼进下一次 attempt 的 prompt（`agent()`
   的 `backend.run` 与会话的 `backend.send_turn` 两条路径同构接入，
   `_with_retry_feedback()` 统一拼接）。backend 异常路径不回灌（模型没
   产出，无错可纠）。
2. **工具内 schema 校验**（`structured_output_tool.py`）：`invoke` 先
   `jsonschema.validate(inputs, self.card.input_params)` 再捕获——schema
   违规直接抛 `ValidationError`（不置 `captured`/`called`），错误
   tool_message 经 ability_manager **同 turn** 回流模型当场修正，与格式
   错误的异常路径完全对称。`StructuredOutputFinishRail` 只在调用无异常时
   force-finish（`ctx.exception is None` 判定），失败调用的错误天然有时间
   被模型消化，rail 零改动。校验与描述渲染用的是同一份逐节点动态传入的
   schema，永不脱节。
3. **提示词/描述强化**：工具描述（descs cn/en）与 `swarmflow_worker.schema`
   / `structured_output.reminder`（locales cn/en）统一补「每层必填属性逐个
   给全（含数组元素）、属性名与嵌套结构完全按 schema、不得改形状」。

## 拒绝的方案

- **duplicate 提交拒绝**（曾落地后 drop）：`called` 已置位时二次 invoke 返回
  `success=False`。动机（同名双发覆盖 `captured`）未被复现，防的是不存在的
  场景；且返回 `success=False` 不走异常路径，`FinishRail`（只认
  `ctx.exception`）会照常 force-finish——若用于"首次提交校验失败"会把错误
  回流路径掐死。需要 turn 内报错时，抛异常（决策 2）才是与格式错误一致的
  正确姿势。
- **前端按参数去重 tool_call 事件**：事件 id 各不相同是真实调用，隐藏
  观测数据等于掩盖后端行为。
- **API 层 `parallel_tool_calls=false`**：provider 支持参差（GLM/DeepSeek
  对该请求参数行为不一），且需动 core 模型客户端链路，风险大于收益。
- **attempt 间在 session 内累积失败轮次上下文**：session 路径重试是新
  turn，引擎不感知 avatar 内部消息；在 prompt 追加 feedback 等价且对
  单发/会话两路径统一。

## 验证

- 新增 `test_attempt_calls_feeds_validation_error_to_next_attempt`：
  第一次提交缺 `q_id` 校验失败 → 第二次 attempt 的 prompt 携带含
  `q_id` 的 feedback → 修正后成功。
- 新增 `test_invoke_raises_on_schema_violation`：缺 `q_id` 的 invoke 抛
  `jsonschema.ValidationError`、`captured`/`called` 保持未置位；补全后
  重发正常捕获。
- 既有 `_attempt_calls` 用例的 `make_call` 闭包统一改签名
  `make_call(feedback=None)`。

## 遗留

- 回灌文案为英文模块常量（jsonschema 错误本身为英文）；如需本地化
  走 `agent_teams/i18n.py` 再议。
- 工具内校验与引擎层回灌是两层叠加：turn 内修正（便宜）优先，turn 级
  重试（带 feedback）兜底；两层各有上限（max_iterations / retries+1），
  不会无限循环。


## 追加（2026-09-21 二轮）：schema 派生必填结构显式化

通用话术（"每层必填属性给全"）仍依赖模型自己回读 `parameters`；弱模型恰恰读不出
`items.required`。新增 `describe_schema_requirements(schema)`（`structured_output_tool.py`）：
遍历 object/array 嵌套，逐层渲染必填键清单（如 `- top level: required keys: answers` /
`- answers[]: required keys: q_id, answer`），schema 无任何 required 时返回空串。

- **工具描述**：i18n 基础描述尾部拼接该摘要，并暴露为 `required_structure` 属性；
  所有消费方（worker / avatar session / tiny agent）自动受益。
- **turn prompt 复述**：avatar `_agent_turn` 的 nudge、worker `run()` 的任务文本、
  tiny_agent run / chat turn 的 reminder 之后各追加一份——模型在任务文本、工具
  描述、提示词三处都能看到具体到字段名的必填结构。
- 摘要引导语为英文（与 jsonschema 错误一致）；键名来自脚本作者的 schema 原文。
- `parameters` 本身保持作者 schema 原样透传不改写——校验契约不因提示优化而变。
- schema 是逐节点动态传入的（同一脚本内答题 / 综合 / 裁决节点各持不同 schema），
  渲染在每实例构造时对传入 schema 现算，与 `invoke` 校验用的是同一份。

验证：`test_description_carries_required_structure`（数组项 + 嵌套 object 两分支）、
`test_required_structure_empty_without_required_keys`。
