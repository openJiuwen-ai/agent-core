# 一次性 CLI 模式：每个 Turn 启动一次进程

适用：CLI 没有可驱动的常驻协议，只有"给一个 prompt、打印结构化输出、退出"的非交互模式
（`-p ... --output-format stream-json`、`run --format json`、`exec --json`），并能用会话 id
在下一次调用里继续对话。能用 ACP 就优先 ACP（中止更温和、能审批、能挂 MCP）。

## 模板做了什么

| 协议动作 | 实现 |
|---|---|
| `start` | 按 `cli_path` → `command[0]` 查 PATH → 包内自带 CLI 兜底解析可执行文件（都没有或兜底 bundle 损坏 → `ProviderStartupError(process_start_failed)`），读 checkpoint 里的会话 id；**不启动进程** |
| 一个 Turn | 启动一次 CLI：`command + MODEL_ARGS + SESSION_ARGS + extra_args + PROMPT_FLAG + prompt`；逐行读 stdout 映射，直到 EOF 或结束记录；回收退出码 |
| 会话延续 | 映射层从记录里取 `session_id`，`_adopt_session_id` 记住并发 checkpoint；下一轮带 `SESSION_ARGS` |
| `abort` | 终止该次进程（`FORCE_ABORT`） |
| 系统提示词 | 首轮 prompt 前置 `<system-instructions>` 围栏；CLI 有自己的 system prompt flag 时改用 flag |
| 非 JSON 行 | 作为 `__non_json_line__` 记录交给映射，落为 `ProviderEvent` |
| 退出码非 0 且无错误记录 | `FAILED`，`code=<UPPER>_EXIT`，`provider_data.exit_code` |

## 需要厂商化的点（SCAFFOLD 标记）

| 标记 | 怎么关 |
|---|---|
| `argv` | 按 `<cli> --help` 填 `SESSION_ARGS`、`MODEL_ARGS`、`PROMPT_FLAG`（prompt 是某个选项的值时，如 kimi 的 `-p`）；默认 `command` 要包含选择结构化输出的 flag，但不要把 `PROMPT_FLAG` 写进 `command`——会话参数会插在它和 prompt 之间 |
| `prompt-delivery` | CLI 能从 stdin 读 prompt 就设 `PROMPT_VIA_STDIN = True`（Windows 命令行总长上限 32767 字符） |
| `mapping` | 用真实记录重写 `mapping.py` 的 `consume` 分支 |
| `turn-end` | 厂商没有显式结束记录（opencode）就设 `STREAM_END_IS_TURN_END = True` |
| `system-prompt` | 有 flag 就改用 flag |
| `mcp-config` | 通过 CLI 的配置文件 / flag 挂 MCP 后，删掉 `_validate_context` 里的拒绝并声明 `MCP_TOOLS` |
| `failure-codes` / `failure-exceptions` | 用真实错误记录填错误码表 |
| `capabilities` | 会话真的能续上再加 `PERSISTENT_SESSION` / `CHECKPOINT` |
| `fixtures` | 用真实记录改写 `fake_cli.py` |

## 实例：opencode `run --format json` 的映射（2026-10 实测）

```jsonc
{"type":"step_start","sessionID":"ses_…","part":{"type":"step-start","messageID":"msg_…"}}
{"type":"tool_use","sessionID":"ses_…","part":{"type":"tool","tool":"read","callID":"call_…",
  "state":{"status":"completed","input":{"filePath":"…"},"output":"<path>…</content>",
           "metadata":{"preview":"TOKEN-ABC123"},"time":{"start":…,"end":…}}}}
{"type":"step_finish","sessionID":"ses_…","part":{"reason":"tool-calls",
  "tokens":{"total":12463,"input":12367,"output":66,"reasoning":30,"cache":{"write":0,"read":0}},"cost":0}}
{"type":"text","sessionID":"ses_…","part":{"type":"text","text":"TOKEN-ABC123","messageID":"msg_…"}}
{"type":"step_finish","sessionID":"ses_…","part":{"reason":"stop", …}}
```

对应映射：

| 记录 | 映射 |
|---|---|
| 任一记录的 `sessionID` | `accumulator.session_id` |
| `tool_use`（`state.status` 为 `completed` / `error`） | 同时发 STARTED（`name=part.tool`、`arguments=state.input`）与 COMPLETED（`result=state.output`，`rendered_result` 同值字符串，`is_error`） |
| `text` | 回答通道 `FINAL`（无增量），按 `messageID` 成块 |
| `step_finish` | 累加 `tokens` 为本 Turn 的 CUMULATIVE 用量；`reason` 记入 `stop_reason` |
| `step_start` | 丢弃或 `ProviderEvent` |
| EOF + 退出码 0 | 结束（`STREAM_END_IS_TURN_END = True`） |

argv：`command=("opencode", "run", "--format", "json")`，`SESSION_ARGS=("--session", "{session_id}")`，
`MODEL_ARGS=("--model", "{model}")`，`PROMPT_FLAG=()`（prompt 作为末尾位置参数）。

对照 kimi-code 0.28.1：`command=("kimi", "--output-format", "stream-json")`，`SESSION_ARGS=("-S", "{session_id}")`，
`MODEL_ARGS=("-m", "{model}")`，`PROMPT_FLAG=("-p",)`；`-p` 与 `--yolo` 互斥。

## 已知坑

- 每轮冷启动 CLI，延迟高；工具审批、steer、ask-user 在这个模式下都做不了。
- 中止靠杀进程：厂商可能没来得及落盘最后一条消息，下一轮续会话时它不在历史里。
- CLI 可能把会话 id 只打印在第一条记录里，也可能每条都有：映射里任何一条带 id 都要接住。
- 子进程的子进程（MCP server、node worker）随父进程退出通常会自行结束；如发现残留，再按平台
  做进程组终止（POSIX `start_new_session` + `killpg`，Windows `CREATE_NEW_PROCESS_GROUP` /
  `taskkill /T`），两个平台都要覆盖。
