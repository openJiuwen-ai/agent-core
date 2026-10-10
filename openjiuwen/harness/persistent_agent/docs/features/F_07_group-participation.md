# F_07 群消息参与判断

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | feature |
| 日期 | 2026-10-09 |
| 范围 | `persistent_agent/execution/participation.py`、`persistent_agent/execution/entries.py`；测试 `tests/unit_tests/harness/persistent_agent/test_entries.py` |
| 测试基线 | `tests/unit_tests/harness/persistent_agent` 289 passed |
| Refs | 开发计划 PA-07；架构设计 §6 |

## 背景

执行入口原先在校验 `RequestInput` 时拒绝全部群消息。架构设计要求：只有群消息需要判断；判断是一次独立的轻量模型调用，发生在加载窗口和构造 DeepAgent 之前；忽略只记账，不产生公开回复，也不把该消息写入窗口。

`seams.group_message_reader` 仍固定拒绝，归属 PA-23。本项若等待该接入点，就无法与窗口组装并行落地。判断因此直接调用已经注入的 `GroupChatPort.recent_messages`。

## 决策

1. 仅 `RequestInput.kind == group_message` 进入判断。私聊和定时任务不读取群端口，直接进入既有执行入口。
2. 未注入 `GroupChatPort` 时，群消息在任何事件和构造之前拒绝，错误为 `group chat port is required for group participation`。
3. 判断输入只有职责（`system_prompt`，缺省时用 AgentCard 名称）、当前消息和最多 8 条最近群消息。当前消息保留发送者、是否 @ 本同事、回复关系和附件引用。与触发消息 `message_id` 相同的记录从最近消息中剔除。不挂工具，不调用 `session_state_store` 或 `window_assembler`。
4. 模型只输出 JSON：`decision` 为 `ignore` / `respond` / `intervene`，并附非空 `reason`。被 @ 只写入提示，不强制回应。
5. 每次尝试先 `BudgetPort.admit`。拒绝时不调用模型，记 `execution.paused(budget_denied)`，不记 `participation.decided`，也不记 `ignored`。准入异常记 `execution.paused(error)`。
6. 调用成功后 `report` 并记 `model.completed`，再记 `participation.decided`。`ignore` 接着记 `execution.final(ignored)` 并结束，不构造 DeepAgent。`respond` 与 `intervene` 把该事件的 `event_id` 写入随后的 `execution.started.decision_event_id`，再进入既有执行体。
7. 模型异常、输出无法解析，或群记录为 `incomplete`，最多尝试 3 次。仍失败则记 `execution.final(failed)`，不记 `participation.decided`，不记 `ignored`。

## 拒绝的方案及原因

| 方案 | 不采用的原因 |
| --- | --- |
| 在完整 DeepAgent 回合结束后用静默标记跳过回复 | 判断发生在窗口加载之前；忽略不构造实例，也不占用窗口 |
| 经 `group_message_reader` 读取最近消息 | 该接入点尚未实现，等待它会使 PA-07 依赖 PA-23 |
| 判断失败时记 `ignored` | 忽略表示同事决定不参与；失败必须与忽略分开，供服务端重试或报错 |
| 为判断增加不占 Session 队列的路径 | 本期每条群消息仍是 Session 队列中的一个 Request；该路径属于后续扩展 |

## 对契约与状态的影响

- 群消息不再在 `_require_request` 中无条件拒绝。缺少群端口时仍拒绝。
- 忽略和判断失败不写会话状态记录，不组装窗口。回应和插话之后仍使用 PA-06、PA-10 的接入点；二者未接入时，正式入口继续拒绝。
- `participation.decided` 由本项记账。`S_02` 中该事件不再标为待接入。

## 验证

2026-10-09：`tests/unit_tests/harness/persistent_agent` 289 passed。`test_entries.py` 覆盖：缺少群端口时在事件之前拒绝；被 @ 仍可忽略且不构造实例、不写窗口、不产生 `output.proposed`；未被 @ 可以回应或插话，并把判断事件写入 `execution.started`；格式错误和模型异常各重试 3 次后记 `failed`；预算拒绝不调用模型；群记录 `incomplete` 不记为忽略；私聊和定时任务不读取群端口。改动文件 Ruff check / format 通过。

## 已知遗留

- 最近群消息的持久化和游标增量读取仍由 PA-23 实现。本项只消费调用方注入的 `GroupChatPort`。
- 回应和插话之后的窗口写入、候选发送仍分别属于 PA-06、PA-10、PA-08。
- AgentServer 收到 `ignored` 后不向群里发送，该行为不在本模块。
