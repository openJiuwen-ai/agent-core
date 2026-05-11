# Agent Teams CLI / TUI Spec

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | openjiuwen/agent_teams/cli/ |
| 最近一次修订 commit | 18823271 |
| 关联 feature | 待补 |

## 范围 / 边界

本规约管 `agent_teams/cli/` 子模块——基于 prompt_toolkit + rich 的交互式驾驶席。
它是 SDK 暴露给终端用户的一层"操作面板"，把 `Runner` 上的 team lifecycle facade 封装
成一组斜杠命令、把 streaming 输出投到 rich console、把 HumanAgent inbox 通知挂到同
一个 console 上。

**管：**

- 公共入口 `run_team_cli` 与可嵌入的 `TeamCli` 驱动类的契约
- 斜杠命令到 `Runner` facade 的映射、入参语义、错误翻译
- 普通文本 → `Runner.interact_agent_team` 的透传规则
- spec 注册（YAML + 内存两路）的合并语义
- CLI 本地状态（active 路由目标、stream handle、watch binding）的所有权与生命周期
- shell `! cmd` 透传与 tab 补全两级展开

**不管：**

- `Runner` facade 自身的语义（见 `S_06_runtime-pool-dispatch.md`）
- runtime mention 解析（`# / $ / @member`）的语法表（见 `S_07_interaction-views-and-hitt.md`）
- 流式渲染的 token 缓冲细节（沿用 `harness/cli/ui/renderer.render_stream`）
- 持久化状态、对象池条目（属 runtime 真理域）

## 不变量

CLI 是一层薄壳。任何破坏下列条目的实现都视为缺陷：

1. **CLI 不持有运行时真理。** `TeamCliState` 只镜像 UI 必需的字段——active routing
   target、`stream_handles`、`watch_bindings`、`history_session_ids`。team / session
   是否真正在跑，永远以 `TeamRuntimePool` 为准；CLI 通过 `Runner.list_active_teams`
   等查询拿真值，不缓存。
2. **CLI 不重复解析 mention 前缀。** 普通文本一律原样透传给 `Runner.interact_agent_team`，
   `# / $ / @member` 的分流由 runtime 的 `parse_interact_str` 一处完成。CLI 看到 `/`
   开头走斜杠分支、看到 `! ` 开头走 shell 分支，**剩下的什么都不动**。
3. **所有副作用走 `Runner` facade。** CLI 不直接持有 `TeamRuntimeManager` / `pool` /
   `messager` 实例，不绕过 facade 操作 session / agent / inbox。
4. **取消时序固定先 stop 后 cancel。** `/team stop` / `/team switch` / `/session switch`
   一律先 `Runner.stop_agent_team`（关 gate + teardown），再 `task.cancel()`；顺序反
   了会让 `agent.stream` 撞到半 teardown 状态、把 `gate_closed` race 出来。
5. **CLI 错误不让 Runner 状态损坏。** 命令处理函数对 `BaseError` 与未知 `Exception`
   分别捕获：前者翻译为中文提示打印；后者通过 `team_logger.exception` 留 trace 后印
   到 console；二者都不让异常传出 dispatch 边界。
6. **`stream_handles[team_name]` 单例。** 同一 team 同一时刻最多一个活跃 stream task；
   想重启必须先 stop。`/team start` 在已有未完成 task 时直接拒绝重入。
7. **`runtime_ready` 是 start/resume 的成功判据。** `_start_or_resume` 起 stream task
   后必须 `await handle.runtime_ready`（默认 30s 超时）；超时或异常都视为失败、清理
   handle、回滚 active/pending、不进 `history_session_ids`。
8. **打印只走 `state.console`。** prompt_toolkit 用 `patch_stdout(raw=True)` 接管标准
   输出，任何 `print()` 会撕裂输入区；后台 stream chunk、inbox 通知、命令反馈走同一
   个 rich `Console`。
9. **`run_team_cli` 的 lifecycle 全覆盖。** CLI 必须把 `Runner` 上的 team-scope 公共
   方法（见接口契约表）每一项映射到至少一条斜杠命令；新增 facade 时同步加命令，否
   则用户无法在 CLI 中触达。
10. **prompt_toolkit / rich 是软依赖。** 它们只能从 `cli/` 模块内部 import；不允许
    渗到 `runtime` / `agent` / `interaction` 等其它子模块——其它模块以 `cli` extras
    形式可选启用。
11. **shutdown 必须收尾。** `TeamCli.shutdown()` 退出前清掉所有 watch binding 与
    stream handle；不依赖 GC 或 `Runner.stop()` 的兜底。
12. **shell 透传不进 dispatch try/except 流。** `! cmd` 直接用 `asyncio.create_subprocess_shell`
    跑出去；不映射成任何 `Runner` facade，也不影响 active 路由。

## 接口契约

### 公共入口

```python
async def run_team_cli(
    *,
    specs: dict[str, TeamAgentSpec] | None = None,
    yaml_paths: Iterable[str | Path] | None = None,
    input_iter: AsyncIterator[str] | None = None,
    manage_runner: bool = True,
) -> None
```

- `specs` 与 `yaml_paths` **互补，不互斥**。两者同时给会都注册：YAML 走
  `SpecRegistry.bulk_load_yaml`、内存 spec 走 `bulk_register`；命名冲突时**内存
  优先**（log warning，不抛错）。dict key 仅作信息项，注册名永远取自 `spec.team_name`，
  key 不一致会 warning 但仍以 `spec.team_name` 入注册表。
- `input_iter` 给测试用：非 None 时跳过 prompt_toolkit、`patch_stdout` 也不挂；按行
  喂同一条 `route_text`。
- `manage_runner=True`（默认）由 `run_team_cli` 自己包 `Runner.start()` /
  `Runner.stop()`；嵌入到已托管 `Runner` 生命周期的宿主里时显式传 False。
- 退出路径：`/exit` / `/quit`（`_ExitCli`）/ EOF / `KeyboardInterrupt` / `input_iter`
  耗尽。任意一种都会先 `cli.shutdown()`、再视 `manage_runner` 决定是否 `Runner.stop()`。

### 嵌入式入口

```python
class TeamCli:
    def __init__(self, spec_registry: SpecRegistry, *, console: Console | None = None) -> None
    async def run(self, *, input_iter: AsyncIterator[str] | None = None) -> None
    async def shutdown(self) -> None
    @property
    def state(self) -> TeamCliState
    @property
    def inbox_callback(self) -> Callable[[HumanAgentInboundEvent], Awaitable[None]]
```

- `spec_registry` 由调用方提前装好；CLI 自身不替你 seed。
- `console` 可注入；不传则用默认绑定到当前 TTY 的 `Console`。
- `inbox_callback` 是同 console 绑定的 inbox sink，给 `/team watch` 用。

### 输入路由（`route_text`）

| 前缀 | 处理 |
|---|---|
| `/` | `dispatch_slash`（`SLASH_COMMANDS[head]`）|
| `! ` | `asyncio.create_subprocess_shell` 透传 |
| 其它 | `Runner.interact_agent_team(raw, team_name=active, session_id=active)`，结果包成 `DeliverResult` 用 `render_deliver_result` 翻译打印 |

字面 `/foo` 想发给 leader：用 `# /foo` 显式走 GodView 即可（runtime 一处解析），CLI
不做转义。

### 斜杠命令 → Runner facade 映射

`/spec` 子组（本地，不走 Runner）：

| 命令 | 入参 | 行为 |
|---|---|---|
| `/spec load <yaml>` | yaml 路径 | `SpecRegistry.add_yaml`，按 `spec.team_name` 入表 |
| `/spec list` | — | 渲染注册表（team_name / source / 成员数）|
| `/spec show <name>` | team_name | `model_dump(mode="json")` 打印 |

`/team` 子组（map 到 `Runner` 的 team-scope facade）：

| 命令 | 入参 | Runner facade |
|---|---|---|
| `/team list` | — | `list_active_teams()` + 注册表差集 |
| `/team status [name]` | team_name? | `list_active_teams()` 中找匹配 |
| `/team monitor [name [sid]]` | team_name?, session_id? | `get_agent_team_monitor(team_name, session_id)` → `get_team_info / get_members / get_tasks` |
| `/team start <name> <sid> [query...]` | team_name, session_id, query | `run_agent_team_streaming(spec, inputs={query}, session=sid)` + 等 `team.runtime_ready` |
| `/team switch <name> [sid] [query...]` | new_team, session_id?, query | 旧 team `stop_agent_team` + cancel stream → 新 team 走 `_start_or_resume`；失败回滚 active |
| `/team use <name>` | team_name | 仅切 active 路由目标，不调任何 facade |
| `/team pause [name]` | team_name?, session_id? | `pause_agent_team(team_name, session_id)` |
| `/team resume [name] [query...]` | team_name?, query | 仍走 `run_agent_team_streaming`（resume = 重新激活 stream）|
| `/team stop [name]` | team_name? | `stop_agent_team(team_name, session_id)` + cancel stream + 清 active |
| `/team delete <name> [--force]` | team_name, force | `delete_agent_team(team_name, session_ids=known∪active, force=force)`，捕获 `BaseError` 提示先 stop |
| `/team watch <member> [name]` | member_name, team_name? | `register_human_agent_inbound(team, sid, member, callback=cli.inbox_callback)` |
| `/team unwatch <member> [name]` | member_name, team_name? | `register_human_agent_inbound(..., callback=None)` |

`/session` 子组：

| 命令 | 入参 | Runner facade |
|---|---|---|
| `/session active` | — | 本地查 `state.active_*`，不走 Runner |
| `/session list` | — | 本地查 `state.history_session_ids` |
| `/session switch <sid> [query...]` | new_session_id, query | 当前 active team 走"先 stop 旧 session → `_start_or_resume` 新 session"；失败回滚 |
| `/session release [sid] [--force]` | session_id?, force | `Runner.release(session_id, force=force)`，捕获 `BaseError` 提示先 stop |

杂项：

| 命令 | 行为 |
|---|---|
| `/help` | 打印命令参考（plain text 不走 markup）|
| `/clear` | `console.clear()` |
| `/exit` / `/quit` | 抛 `_ExitCli` 退出主循环 |

### 错误语义

- 未知斜杠子命令：打印 subhelp，不抛错
- `BaseError`：直接 `print(f"[red]{head} failed: {exc}[/red]")`，不外泄
- 其它 `Exception`：`team_logger.exception` 留 trace + console 印 `crashed`；调度循环
  继续
- `interact` 失败的 `DeliverResult.reason`：经 `_translate_reason` 表查中文提示
  （`missing_target` / `not_active` / `gate_closed` / `human_agent_not_enabled` /
  `no_team_backend` / `unknown_human_agent:*` / `unknown_member:*` / `send_failed:*`）

### Tab 补全

`SlashCompleter` 两级：

- 第一级：`/` 后无空格，匹配 `SLASH_COMMANDS` 顶层键（隐藏 `/quit` 别名），display
  meta 取自 `_TOP_LEVEL_DESCRIPTIONS`
- 第二级：`/{group} ` 后第一个 token，匹配 `_SUB_ACTION_TABLES[group]`；超过两个
  token 不再补全（参数空间放给用户）

## 数据结构

### `SpecRegistry` / `SpecEntry`

`SpecEntry(spec, source, runtime_overrides)`，`source` 取值：

- 绝对 YAML 路径（`add_yaml` 注册时 `Path.expanduser().resolve()`）
- `"in-memory"`（`add_inmemory` / `bulk_register`）

合并优先级：

- YAML 注册时碰到既有 `in-memory` 条目：保留旧的，warning，返回旧 entry
- YAML 注册时碰到既有 YAML 条目：替换 + warning
- 内存注册时碰到既有 YAML 条目：替换 + warning
- 内存注册时碰到既有内存条目：替换（无 warning，是显式行为）

`load_spec_yaml` 复用 examples 模式：

- `${VAR}` 递归展开（缺变量保留原样）
- 顶层 `runtime` 块从 model 中剥离、单独返回（`session_id` / `initial_query` 等运行
  期 hint，不属于 Spec）
- 解析失败抛 `AGENT_TEAM_CONFIG_INVALID`

### `TeamCliState`

```text
spec_registry         : SpecRegistry              # 唯一注册表
console               : rich.Console              # 唯一打印通道
active_team_name      : str | None                # 当前路由目标 team
active_session_id     : str | None                # 当前路由目标 session
pending_team_name     : str | None                # /team switch 进行中标记，失败回滚用
pending_session_id    : str | None
stream_handles        : dict[team_name, StreamHandle]
watch_bindings        : dict[(team, sid, member), WatchBinding]
history_session_ids   : dict[team_name, set[session_id]]   # CLI 视野下的历史，给 /team delete / /session list
```

`set_active(team, sid)` 同时清 pending；`set_pending(team, sid)` 标记 in-progress。

### `StreamHandle`

```text
team_name      : str
session_id     : str
runtime_ready  : asyncio.Future[dict]   # 由 _wrap_stream 在首个 team.runtime_ready 事件 set
task           : asyncio.Task[None]     # 跑 Runner.run_agent_team_streaming 的 consume 协程
cancelled      : bool                   # CLI 主动 cancel 的标记，区分日志噪声
```

生命周期：`spawn_stream` 创建 → 写入 `state.stream_handles[team_name]` →
`await runtime_ready`（30s）→ 成功推进 active；stop 路径走 `stop_stream(handle)`
（设 `cancelled=True`、`task.cancel()`、`suppress(CancelledError)` 等待结束）。

### `WatchBinding`

`(team_name, session_id, member_name)` 三元组的 frozen dataclass，仅作存在标记 + 退出
时反注册的索引键。inbox callback 实例是 `TeamCli.inbox_callback`（每个 CLI 一个，绑
console），bind / unbind 都通过它 + `Runner.register_human_agent_inbound(callback=...)`。

### Stream 渲染特殊处理

`_wrap_stream` 在 chunk 流上做两件事：

1. 拦截 `payload["event_type"] == "team.runtime_ready"` 的 chunk：set
   `runtime_ready` future、不下发给渲染器（用户看不到内部 ack）
2. `llm_reasoning` chunk 自己渲染（带 `🤔 ` 前缀 + 离开 reasoning 时补 reset newline）；
   插入空 `_BoundaryChunk("message")` 让 harness `render_stream` flush `in_llm_output`
   状态。普通 `message` / 工具调用 chunk 直接交给 harness 渲染器（沿用单 agent CLI 的
   token 缓冲与样式）。

`show_reasoning=False` 透传给 harness 渲染器——reasoning 由本层负责，避免 harness 那
边把 reasoning 串到 `● ` 输出前面。

## 与其它 spec 的关系

- **`S_01_public-api-and-spec-flow.md`**：CLI 消费的 `TeamAgentSpec.build()` 单向流；
  CLI 永远不回写 Spec。
- **`S_06_runtime-pool-dispatch.md`**：CLI 命令的 facade 边界来源——`Runner.run_agent_team_streaming`
  / `interact_agent_team` / `pause_agent_team` / `stop_agent_team` /
  `delete_agent_team` / `release` / `list_active_teams` /
  `register_human_agent_inbound` / `get_agent_team_monitor`。CLI 必须覆盖这一组
  team-scope 公共方法的全部使用面；缺一即"facade 在 CLI 中无法触达"。
- **`S_07_interaction-views-and-hitt.md`**：普通文本透传后由 `parse_interact_str` 把
  `# / $ / @member` 分到 GodView / Operator / HumanAgent。CLI 不重复解析；`DeliverResult.reason`
  的稳定 token 表也归属那份规约，本规约只引用其翻译表。
- **`S_05_member-spawn-and-stream.md`**：stream chunk 的 `TeamOutputSchema` /
  `source_member` 字段语义在那里定义；本规约只描述 CLI 如何把它转成可视输出。
- **`harness/cli/ui/renderer.render_stream`**（不在本目录的 spec 范围）：team CLI 的
  渲染层复用 harness 的 token-buffered 渲染管线，team 这边只截 `team.runtime_ready`
  与 `llm_reasoning` 两类 chunk，其它一律下放。
