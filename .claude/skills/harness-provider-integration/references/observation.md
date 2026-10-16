# 轨迹适配：模型请求观测 `ModelRequestEvent`

团队成员的轨迹页按三条泳道展示一个 agent 做了什么，数据全部来自 provider 的事件流，宿主不读厂商数据：

| 泳道 | 来源 | 缺了会怎样 |
|---|---|---|
| Model | 每个 `ModelRequestEvent` 记一个 inference span | 只看得到工具，看不到模型何时思考、说了什么 |
| Input | `input_observed=True` 的请求附带上下文窗口提交（`context.window.commit`） | 看不到模型每次收到了什么 |
| Tools | `ItemLifecycleEvent(item_type="tool")` | 看不到工具调用 |

只映射了工具的 provider 在轨迹上只有 Tools 一条线。宿主声明了 `HostCapability.MODEL_REQUEST_OBSERVATION`
（团队成员运行时总会声明）时，provider 就该上报请求；卡片在 `optional_host_capabilities` 里列出它。

## 事件契约

- 一个物理模型请求一个事件，在请求结束后发出一次，早于它引起的工具条目、早于 Turn 终态。
- 它引起的工具条目在信封 `causation_ids` 里写它的 `request_id`（`_emit(..., causation_ids=...)`），
  轨迹据此把工具挂到对应请求下面；不写就是一个无主工具。
- `request_id` 在会话内稳定唯一；`output_message` 是这次请求产出的助手消息：
  `reasoning` / `text` 块，加上每个工具调用一个 `tool_call` 块
  （`content={"name", "arguments"}`，`data={"call_id": <与工具条目 item_id 相同>}`）。
- `input_observed=True` 只在**从请求本身**拿到了输入时才标：`system_instructions`、`input_messages`
  （每条消息的 `message_id` 在它留在会话里期间不变，宿主据此把上下文窗口记成增量）、`tool_definitions`。
  拿不到就标 `False`、输入留空，只有输出侧有意义，Input 泳道为空——不要拿自己拼的对话冒充请求内容。
- `usage` 只记这一次请求的用量（`input_tokens` 是整个 prompt，缓存命中是它的细分）；只有回合级用量时不要
  摊到请求上，那是 `UsageUpdatedEvent` 的事。
- 时间：`started_at` 是请求发出的时刻（回合开始，或上一个工具结果回来），`ended_at` 是响应结束的时刻；
  `time_to_first_chunk` 有就填。

## 两级来源

厂商事实优先于从流里推出来的东西，和 Claude Code / Codex 的做法一致（openjiuwen
`harness_providers/claudecode/observation.py`、`codex/observation.py`）：

1. **厂商自己的请求记录**：CLI 写在本地的会话 / 请求日志、本地数据库、OTLP 遥测。取证时找它（见
   [recon.md](recon.md)），能还原每次请求的输入就能补全 Input 泳道和逐请求用量。
2. **从输出流推断**：找不到记录，或记录还没写出来时，按流切分请求，只填输出侧。

两级不是二选一：推断给出请求边界、时间与工具归属（流是实时的），厂商记录补上内容。补全时**保留推断的
`request_id`**——工具条目已经引用了它。

## ACP 模板已做的部分

`mapping.py` 的 accumulator 在宿主观测请求时按流推断：

- 一段 thought / message 输出连同其后的工具调用是一次请求；工具调用开始时请求结束、先发请求事件再发
  工具 STARTED（带 `causation_ids`）。
- 紧跟着到达的并行工具调用归同一请求；工具结果回来后再出现输出，或在两次之间没有输出的工具调用，开启
  下一次请求。
- 回合结束时把仍在产出的请求按回合结局收尾（完成 / 取消 / 失败）。
- 事件带 `data={<provider>: {"observation": "acp_stream"}}`、`input_observed=False`、会话当前模型。

harness 在发出每个请求事件前调用 `_observe_request(request)`，这是 `SCAFFOLD(observation)`：

```python
async def _observe_request(self, request: ModelRequestEvent) -> ModelRequestEvent:
    record = await self._records.wait_for(request, timeout_s=...)   # 找到这次请求的厂商记录
    if record is None:
        return request                                             # 没有就保持推断结果
    return dataclasses.replace(
        request,
        input_observed=True,
        system_instructions=record.system,
        input_messages=record.messages,     # 稳定的 message_id
        tool_definitions=record.tools,
        usage=record.usage,
        model=record.model or request.model,
    )
```

要点：

- **对齐**：优先用工具调用 id 把推断的请求对到记录（记录里的工具调用 id 与 ACP `toolCallId` 相同时）；
  纯文本请求按顺序取下一条未用的记录。只认主 agent 的记录，子 agent 的请求不进本成员的流。
- **等待有上限**：记录与流各走各的通道，会有先后。等几百毫秒足够覆盖刷盘延迟；等不到就原样返回，
  永远不要卡住通知路径。
- **会话恢复**：`session/load` 后从头解析记录重建会话状态（系统提示词、已有消息），但只把恢复之后的
  请求拿来对齐。
- 读到的厂商内容同样过 `to_json_safe`，不把凭据、环境变量写进事件。

## 其它模式

SDK / 原生协议 / 一次性 CLI 模式没有模板实现，照同样的契约做：厂商流或 SDK 能给出逐请求的响应
（Codex `rawResponse/completed`）就从它推断，再用厂商记录补全；模型请求事件放在 mapping 里与工具条目
同序发出。

## 测试

- 单测：宿主声明 `MODEL_REQUEST_OBSERVATION` 时，请求事件先于它引起的工具 STARTED，工具的
  `causation_ids` 含该请求；不声明时不发请求事件。用厂商记录补全的 provider 再加：记录存在时
  `input_observed=True` 且 `input_messages` 与记录一致；记录缺失时退回推断结果。假运行时的记录要照真实
  记录的形状写。
- e2e：在真实 CLI 上跑一个调用工具的回合，用 `HarnessTrajectoryRecorder` 或直接检查事件，确认 Model /
  Tools 都有、工具都挂在请求下；有厂商记录的再确认 Input 有内容、逐请求用量与 CLI 自己的统计一致。
