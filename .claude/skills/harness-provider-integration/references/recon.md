# 调研与取证：先拿到厂商的真实记录

写任何映射之前，先回答下面的问题，并且每个答案都要能指回一条**真实运行记录**或厂商**源码**。
铁律：映射之后的东西只能证明映射本身；字段丢没丢，只有厂商原始记录说了算。

## 1. 必答问题

| # | 问题 | 决定什么 |
|---|---|---|
| 1 | 厂商提供哪些驱动入口？Python SDK（是否覆盖中断、会话恢复、审批 / 提问回调）/ 原生常驻协议（`codex app-server` 这类，看有没有 `generate-json-schema` / `generate-ts` 之类的协议导出）/ 结构化 stdout 的一次性模式（`-p ... --output-format stream-json`、`run --format json`、`exec --json`）/ ACP（`<cli> acp`、`--acp`）/ HTTP server？ | 接入模式（见 SKILL.md 优先级表） |
| 2 | 一个外部 Turn 对应厂商的什么？（一次 `session/prompt`、一次进程调用、一次 `thread.turn()`） | `_execute_turn` 的边界 |
| 3 | Turn 结束的信号是什么？（RPC 响应的 `stopReason`、`turn_end` 记录、EOF+退出码） | 终态判定；`STREAM_END_IS_TURN_END` |
| 4 | 会话怎么延续？进程内常驻 / 会话 id + resume 参数 / `session/load` | PERSISTENT_SESSION、CHECKPOINT |
| 5 | 中途能否中止？（`session/cancel`、SDK `interrupt()`、只能杀进程） | GRACEFUL_ABORT / FORCE_ABORT |
| 6 | 运行中能否追加输入（steer）？ | STEER（多数 CLI 不能） |
| 7 | 系统提示词怎么注入？（SDK 参数、CLI flag、配置文件、只能拼进首条输入） | `system_prompt_mode` / prelude |
| 8 | MCP 服务器怎么挂？（会话参数、CLI flag、配置文件、`<cli> mcp add`）支持 stdio / http？ | MCP_TOOLS、`_acp_mcp_servers` 等 |
| 9 | 模型侧看到的 MCP 工具名长什么样？ | `MCP_TOOL_NAME_PATTERN`（不变量 12） |
| 10 | 工具审批 / 向用户提问以什么形态出现？ | TOOL_APPROVAL / USER_INPUT |
| 11 | 文本是否有增量？推理是否可见？用量在哪里报？ | OutputEvent DELTA/FINAL、UsageUpdatedEvent |
| 12 | 错误以什么结构出现？有无稳定错误码 / HTTP 状态？ | failure_classifier |
| 13 | 模型怎么选？（启动 flag、会话配置项、配置文件）两种都实测，厂商之间相反 | `MODEL_ARGS` / `MODEL_CONFIG_ID` |
| 14 | 技能（skills）从哪个项目目录发现？ | `SKILLS_SCAN_DIR` |
| 15 | 需要登录吗？未登录时报什么？ | `auth_required` 映射；e2e 前置条件 |
| 16 | CLI 有没有在本地记下每次模型请求？（会话日志、请求日志、本地数据库、OTLP 遥测）记了哪些：发出时间、模型、发出的消息、系统提示词、工具清单、逐请求用量？主 agent 与子 agent 怎么区分？ | 轨迹 Input / Model 泳道、`_observe_request`（[observation.md](observation.md)） |

## 2. 取证方法

### CLI

```bash
<cli> --version
<cli> --help
<cli> <subcommand> --help          # acp / run / exec / mcp
```

把一次性模式的输出整段落盘（**不要只看终端**，终端会吞掉 stderr 与结构）：

```bash
cd <scratch>; echo "TOKEN-ABC123" > secret.txt
<cli> run --format json "Read secret.txt with your file tool and reply with exactly the token." > out.jsonl 2> err.txt
```

ACP：用一个最小 JSON-RPC 客户端录下全部往返（initialize → session/new → 带工具的 prompt →
被 `session/cancel` 的 prompt → 再一次 prompt 验证会话延续 → session/load）。录制脚本要点：

- 单 reader 读 stdout，每行 `json.loads`；把 `(方向, 消息)` 逐条写 jsonl；
- 收到 `session/request_permission` 时选 `allow_once` 回包，否则 agent 会一直等；
- `initialize` 时 `clientCapabilities` 的 `fs` / `terminal` 设 `false`，让 agent 用自己的工具；
- 统计 `session/update` 里 `sessionUpdate` 的种类分布，每种取一条样本。

### 原生常驻协议

- 先找协议的机器可读定义：`<cli> app-server generate-json-schema --out <dir>` 之类的导出，列出全部请求、
  通知、服务端请求及参数，比读 SDK 源码可靠。
- 再用最小客户端录真实往返：握手 → 开会话 → 文本 Turn → 工具 Turn → 运行中追加输入 → 中断 → 换进程续会话
  → 审批（把策略调到会问）→ 挂 MCP → 向用户提问（常要打开特性开关）。服务端请求要回包，否则 Turn 一直等。
- 厂商自己落盘的记录（Codex 的 `~/.codex/sessions/**/rollout-*.jsonl`）是判定"模型侧看到什么"的依据：
  MCP 工具名、实际使用的模型都从这里读，不从协议通知推断。

### SDK

- 读安装目录里的 SDK 源码（`python -c "import x, inspect; print(inspect.getsourcefile(x))"`），
  找：客户端构造参数、会话 API、流式迭代器的元素类型、中断 / steer 方法、异常类型、服务端请求回调。
- SDK 往往再拉起一个 CLI 子进程：确认它用的是自带二进制还是 PATH 上的（Claude / Codex 的 e2e 都必须
  显式指向本机 CLI，因为自带版本可能过旧）。
- 生成类型（pydantic）是便利而不是边界：Codex 的 `item/tool/requestUserInput` 没有生成类型，但底层
  回调照样把原始 method + params 交上来。

## 3. 证据规则

1. 每个映射分支、每条能力声明，都能指回一条落盘的原始记录或一段 SDK 源码。
2. 单测夹具从真实记录裁剪而来；凭想象写的夹具只会把错误映射钉死。
3. 记录里出现、映射用不上的字段，保留在 `ProviderEvent` / `provider_data` 里，不丢。
4. 记录中可能有凭据、请求体、绝对路径：录制文件放 scratchpad，不进仓库；夹具只留结构必需字段。

## 4. 已验证的实例（2026-10，用于校准判断，不代替你自己的取证）

### opencode 1.17.18

- 入口：`opencode acp`（ACP v1，stdio）；`opencode run --format json`（一次性 JSONL）。
- ACP `initialize` 回 `agentCapabilities.loadSession=true`、`mcpCapabilities.http=true`，
  `sessionCapabilities` 还有 `resume/fork/list/close`。
- `session/new` 回 `configOptions`（含 `category: model` 的选择项）；但 `session/set_config_option`
  对列表里的合法值也回 `-32602 model not found`——在 opencode 1.17 上会话内切模型不可用（2.0 起可用，
  见下）。
- 工具调用：先 `tool_call`（`status: pending`、`rawInput: {}`），再 `tool_call_update`
  （`in_progress`，此时才带 `rawInput`），最后 `completed`（`content` 为渲染文本，`rawOutput`
  为结构化结果）。`title` 第一次是工具名，完成时变成文件路径。
- 文本：`agent_message_chunk` / `agent_thought_chunk` 带 `messageId`，是真正的增量。
- `session/prompt` 响应带非标准 `usage`（`inputTokens/outputTokens/totalTokens/cachedReadTokens`）。
- `session/cancel` 后 prompt 响应 `stopReason: "cancelled"`，会话可继续使用。
- MCP 工具在模型侧命名为 `{server}_{tool}`（如 `fixture_lookup_marker`）。
- 一次性 JSON：记录类型 `step_start` / `tool_use`（只在完成时出现一次，带完整 input/output）/
  `text`（整段，无增量）/ `step_finish`（`reason: tool-calls | stop`，带 tokens / cost）；每条都带
  `sessionID`；没有显式 turn 结束记录，**EOF + 退出码 0 即结束**（`STREAM_END_IS_TURN_END = True`）。
- 本 skill 的 ACP 模板在 opencode 上零厂商代码通过 text / tool / follow-up / abort / resume 五项 e2e，
  MCP stdio 挂载与调用也通过。

### opencode 2.0.27（与 1.17 的差异）

- `session/set_config_option`（`configId: "model"`）生效，可选列表含 `opencode/*-free` 免费模型；`acp`
  仍无模型参数。→ `MODEL_CONFIG_ID = "model"`。
- **MCP 改为代码模式**：模型调用 `execute` 工具，在 JavaScript 里写 `tools.<server>.<tool>`（也会写成
  `tools.<server>["<tool>"]`）；工具 item 名是 `execute`，内部调用记在 `completed` 的
  `rawOutput.metadata.toolCalls`。→ `MCP_TOOL_NAME_PATTERN = "tools.{server}.{tool}"`，e2e 按
  `toolCalls` 断言而不是按代码字符串。
- 读文件参数字段由 `filePath` 改为 `path`；`completed` 的 `rawOutput` 只剩 `metadata`，渲染文本在
  `content`；`tool_call` 多了非标准的 `name` 字段。
- 用量多了 `thoughtTokens`；`usage_update` 带 `cost`。
- 项目技能目录 `.opencode/skills`、`.opencode/skill`、`.agents/skills`、`.claude/skills` 都会被发现。
- 不存在的模型 / 会话都回 `-32602`，`data` 带对应 id。
- ACP 模板只填常量、按真实形状改夹具，即通过 6 项契约 e2e 与工具参数、MCP、技能、模型四项厂商 e2e。

### codex-cli 0.162.1（`codex app-server`，原生协议）

- JSON-RPC 消息不带 `jsonrpc` 成员，但接受带它的；握手 `initialize`（`capabilities.experimentalApi`）+
  `initialized` 通知；`generate-json-schema` 导出完整协议（105 个客户端请求、84 种通知、10 种服务端请求）。
- Turn：`turn/start` → 同 `turn.id` 的 `turn/completed`（`completed` / `interrupted` / `failed`）；
  `turn/steer` 要带 `expectedTurnId`，追加内容并入同一 Turn；`turn/interrupt` 后 thread 可继续。
- `thread/start` 的 `developerInstructions` 是原生系统提示词；`thread/resume` 跨进程续会话。
- `turn/start` 的 `model` / `effort` 对后续 Turn 一直生效（只发一次）；rollout 的 `turn_context.model`
  证明切换生效。
- 审批：`approvalPolicy: never` 下未审批的 MCP 调用直接失败，需 `mcp_servers.<key>.default_tools_approval_mode="approve"`；
  宿主接管时 MCP 审批以 `mcpServer/elicitation/request`（`_meta.codex_approval_kind: mcp_tool_call`）到达，
  只给服务名，工具名要和最近的 `mcpToolCall` item 对上。
- `request_user_input` 默认关闭，需 `-c features.default_mode_request_user_input=true`。
- **MCP 是代码模式**：模型调用 `exec`，写 `tools.mcp__<key>__<tool>(...)`，rollout 里的工具名是
  `mcp__<key>__<tool>`（openjiuwen 内置 codex provider 声明的 `mcp__<key>.<tool>` 与此版本不符）。
- 项目技能目录 `.agents/skills`；错误按 `codexErrorInfo`（字符串或带 `httpStatusCode` 的对象变体）分类。

### kimi-code 2.1.1

- 入口：`kimi acp`（ACP v1，stdio）；`initialize` 回 `loadSession: true`、`mcpCapabilities.http/sse`。
  ACP 模板只填常量即通过 text / tool / follow-up / abort / resume / entry point 六项契约 e2e，
  外加工具参数、MCP、技能、模型四项厂商 e2e。
- **模型只能会话内切**：`kimi acp -m` 报 unknown option，`kimi -m <model> acp` 被静默忽略；
  `session/set_config_option`（`configId: "model"`）生效并回报 `currentValue`，`session/load` 保持切换后的
  模型。→ `MODEL_CONFIG_ID = "model"`。
- 工具调用：`tool_call` 以 `pending`、`rawInput: null`、标题 `Read` 到达，随后**数条不带 `rawInput` 的
  `in_progress`**，参数在之后一条 `in_progress` 里；`rawOutput` 是字符串。
- 文本块不带 `messageId`；`session/prompt` 应答不带 token 用量，`usage_update` 是上下文窗口占用。
- MCP 工具名 `mcp__{server}__{tool}`；项目技能目录 `.kimi-code/skills`（`.agents/skills` 也会被发现）。
- 错误只有 ACP 标准码：订阅不含所选模型与 5 小时额度耗尽**都是** `-32000`（"Authentication required:
  401/403 …"），无结构化字段可区分，只能归 `auth_required`；`config.toml` 里不存在的模型是 `-32603`。
  反复跑真实 e2e 会耗尽额度，之后所有 Turn 都失败——e2e 大面积失败先看错误正文是不是额度。
- 凭据在 `~/.kimi-code`：测试里改写 `HOME` 时要把真实 `HOME` 传给子进程，否则 `session/new` 回 `-32000`。
- 一次性模式：`kimi -p <prompt> --output-format stream-json`，会话 `-S <id>` 恢复、`-c` 继续上次；
  `-p` 不能与 `--yolo` 同用；未登录时报 `auth.login_required`（stderr）并以退出码 1 结束。
