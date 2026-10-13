# 事件映射规则：厂商记录 → 协议事件 → TurnResult

映射是纯函数加一个按 Turn 存活的累积器（`<Class>TurnAccumulator`）：`consume(record)` 返回
`MappedEvent(payload, item_id)` 列表，harness 依次 `_emit`；记录流结束后 `finish()` 收尾、
`build_terminal_result(...)` 产出唯一终态。STARTED 与终态 `TurnLifecycleEvent` 由基类发，
**provider 永远不发 `TurnLifecycleEvent`**。

## 输出文本 `OutputEvent`

| 规则 | 说明 |
|---|---|
| 一个输出块一个 `output_id` | 同一条消息的增量共享 id；不同消息、不同通道用不同 id。模板用 `f"{turn_id}:{channel}:{seq}"`，厂商有 `messageId` 时按它切块。 |
| 增量用 `DELTA`，收尾用 `FINAL` | `FINAL` 的 `content` 是**完整文本**。IO adapter 对 FINAL 只补发前缀之外的部分，所以 DELTA+FINAL 不会重复。 |
| 没有增量的厂商只发 `FINAL` | opencode 一次性 JSON 的 `text` 记录就是这样。 |
| 通道 | 回答 `ANSWER`，推理 `REASONING`，厂商系统提示 `SYSTEM`。 |
| 工具调用前收尾 | 工具调用之前的文本属于发起工具调用的那次回复：先 `finish()` 再发工具 STARTED。 |
| 被厂商隐去的推理 | 有 token 数没正文时报告"被隐去"，不要丢弃（不变量 5）。 |

`TurnResult.final_output` 取最后一个回答块的全文；`messages` 放每个收尾块的 `TurnMessage`。

## 工具 `ItemLifecycleEvent(item_type="tool")`

IO adapter 读这些键（`io_adapter.py` 的 `_project_item`）：

| 阶段 | `data` 键 | 必需 |
|---|---|---|
| STARTED | `name`（模型调用的名字）、`arguments`（JSON 对象） | 是 |
| COMPLETED | `tool_name`、`result`（结构化结果） | 是 |
| COMPLETED | `rendered_result`（模型实际读到的文本，字符串） | 有就给 |
| COMPLETED | `is_error` | 建议 |

- STARTED 与 COMPLETED 用**同一个** `item_id`（厂商调用 id）。
- 参数晚到（ACP `pending` 时 `rawInput` 为空）：等第一条带参数或已结束的更新再发 STARTED。
- 只在完成时出现一次的工具记录（opencode `tool_use`）：同时发 STARTED + COMPLETED。
- 工具名取第一次见到的名字；显示标题可能变化（opencode 完成时 `title` 变成路径）。
- 子 agent / step 等非工具条目用别的 `item_type`（DSH 用 `step`），IO adapter 只投影 `tool`。

## 用量 `UsageUpdatedEvent`

- `mode=CUMULATIVE` 表示"本 Turn 到目前为止的总量"；`DELTA` 表示增量。选与厂商语义一致的那个，
  不要把累计量当增量发。
- `TurnUsage` 字段：`input_tokens / output_tokens / cached_input_tokens / reasoning_output_tokens /
  total_tokens`，厂商特有项放 `provider_data`（如 `cached_write_tokens`）。
- 厂商不报费用就不报 `cost`，不要自己估算（Codex 先例）。

## 模型请求 `ModelRequestEvent`

宿主声明 `MODEL_REQUEST_OBSERVATION` 时每次模型请求发一个，早于它引起的工具条目，工具在 `causation_ids`
里引用它；轨迹的 Model / Input 泳道只来自它。推断、对齐、补全规则见 [observation.md](observation.md)。

## 诊断与未知记录

- 厂商仍在自动重试的错误：`DiagnosticEvent(WARNING, data={"kind": "retrying", "category": ...})`，
  Turn 继续；团队可靠性层把它当进度事件。
- 不认识的记录：`ProviderEvent(provider=<card>, event_type=<厂商类型>, schema_version="1",
  payload=to_json_object(record))`。**永远不要静默丢弃**，也不要把厂商原始对象塞进 payload
  （先 `to_json_safe`）。
- Turn 之外到达的通知（ACP 会话加载回放、命令列表更新）不是 Turn 事件，直接丢弃。

## 终态

| 情况 | 事件 | `TurnResult` |
|---|---|---|
| 正常完成 | `FINISHED` | `status=COMPLETED`，`stop_reason` 写厂商原值 |
| 宿主中止（`turn.abort_requested`） | `ABORTED` | 用 `build_interrupted_result` / 基类 `interrupted_result`，自动区分 `USER_ABORT` 与 `HARNESS_STOP` |
| 厂商自己中断 / 拒答 | `ABORTED` | `termination.kind=PROVIDER` / `POLICY` |
| 失败 | `FAILED` | `status=FAILED` + `TurnError`（共享类别） |
| 记录流断了却没有结束信号 | `FAILED` | `code=<UPPER>_MISSING_TURN_END`，除非厂商就是以 EOF 结束（`STREAM_END_IS_TURN_END=True`） |

`TurnResult` 校验：FAILED 必有 `error`、只有 FAILED 能带 `error`；INTERRUPTED 必有 `termination`。

## 失败分类

共享类别（openjiuwen 团队可靠性层按它一一映射，`openjiuwen.agent_teams.schema.external_runtime_reliability`）：

| 类别 | 典型来源 | 需要用户处理 |
|---|---|---|
| `auth_required` | 401/403、ACP `-32000`、未登录 | 是 |
| `quota_exceeded` | 额度/预算用尽 | 是 |
| `rate_limited` | 429 | 否（可重试） |
| `request_rejected` | 400、JSON-RPC 参数被拒（未知模型） | 是 |
| `server_unavailable` | 5xx、529 | 否（可重试） |
| `network_timeout` | 超时、连接断开、空闲超时 | 否（可重试） |
| `process_start_failed` | CLI 不存在 / 无权限 / 启动即崩 | 是 |
| `sdk_error` | 协议错误、进程中途退出、未分类 SDK 异常 | 否 |
| `unknown` | 兜底 | 否 |

- 只按结构化字段分类（错误码、HTTP 状态、异常类型），不解析消息文本。
- 异常只取类型名加首行（≤200 字符）；stderr、请求体、凭据绝不进入 `TurnError`。
- DSH 的 `protocol` / `provider` / `provider_sdk` 类别是历史偏差，新 provider 不要照抄。
- 启动阶段失败一律包成 `ProviderStartupError(message, error=TurnError)`，宿主据此分类，不读异常文本。
