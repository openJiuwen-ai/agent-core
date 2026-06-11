# Agent Teams CLI

`agent_teams` 子系统的交互式驾驶席（prompt_toolkit + rich）。把 `Runner` 暴露的 team 生命周期 facade（`run_agent_team_streaming` / `interact_agent_team` / `pause_agent_team` / `stop_agent_team` / `delete_agent_team` / `release` / `list_active_teams` / `register_human_agent_inbound` / `get_agent_team_monitor`）映射成一组 `/team` `/session` `/spec` 子命令，并接管输入区路由 + 后台 stream 渲染 + HumanAgent inbox 实时通知。

## 模块构成

| 文件 | 职责 |
|---|---|
| `app.py` | `run_team_cli(*, specs=None, yaml_paths=None, input_iter=None, manage_runner=True)` 公共入口；包 `Runner.start/stop` |
| `tui.py` | `TeamCli`：prompt_toolkit `PromptSession` + rich `Console` + `patch_stdout` 主循环；持 `TeamCliState` 与 inbox callback；`shutdown()` 清理活跃 stream + watch binding |
| `commands.py` | `SLASH_COMMANDS` 顶层字典 + `_TEAM_ACTIONS / _SESSION_ACTIONS / _SPEC_ACTIONS` 子命令派发；每个 `_cmd_*` 直接调 `Runner` facade；`SlashCompleter` 二级 tab 补全 |
| `routing.py` | `route_text(cli, raw)` 三分支：`/cmd` 走 `dispatch_slash`，`! cmd` 走 shell，普通文本透传 `Runner.interact_agent_team`；`render_deliver_result` 把 `DeliverResult.reason` 翻译成中文 |
| `stream_renderer.py` | `spawn_stream(spec, session_id, ...) → StreamHandle`：起后台 task 跑 `Runner.run_agent_team_streaming`，识别 `team.runtime_ready` 事件 set future，每个 chunk 用 rich console 染色打印；`stop_stream(handle)` 取消任务 |
| `inbox_sink.py` | `make_inbox_callback(console)` 返回 `HumanAgentInboundEvent → console.print` 的 async callback，绑定到 `Runner.register_human_agent_inbound` |
| `spec_loader.py` | `SpecRegistry`（`add_yaml / add_inmemory / get / names / entries`）+ `load_spec_yaml(path)`：复用 examples 模式做 `${ENV}` 展开、剥 `runtime` block、`TeamAgentSpec.model_validate` |
| `state.py` | `TeamCliState` / `StreamHandle` / `WatchBinding` dataclass。CLI 仅镜像它需要的 UI 状态（active routing target、stream task、inbox 订阅），运行时真理仍归 `TeamRuntimePool` |

## 行为铁律

- **不在 CLI 重新 `parse_interact_str`**：runtime `manager.interact` 对 `str` 入参已经调过一次。CLI 透传 raw 文本，让 `# / $ / @member` 三种前缀只在一处解析；否则 `# # body` 会被双重 strip。
- **取消时序固定**：`/team stop` / `/team switch` / `/session switch` 触发时**先** `Runner.stop_agent_team`（关 gate + teardown agent），**再** `task.cancel() + suppress(CancelledError)`。顺序反了让 `agent.stream` 撞到半 teardown 状态，gate close race 出现。
- **`gate_closed` ≠ team 死了**：可能正赶上 stream 退出 finally。CLI 把这个 reason 翻译成"轮次已结束，等待 wakeup 或先 `/team resume`"，避免误导用户去 `/team start`。
- **静止前置**：`/session release` / `/team delete` 在仍有活跃 entry 时 manager 报 `AGENT_TEAM_BUSY_INVALID`。CLI 捕获 `BaseError` 提示用户先 `/team stop` 或加 `--force`，不静默吞错。
- **HumanAgent 不写专门命令**：人类成员的回复跟 GodView/Operator 共用同一个文本输入框，靠 `parse_interact_str` 的 `# / $ / @` 前缀分流到三种 `InteractPayload`。inbox 通知是**输出向**的（`/team watch` 注册回调），不是输入向的对称命令。
- **打印只走 `cli.state.console`**：CLI 在 `patch_stdout` 上下文里运行，`print()` 会撕裂输入区。后台 chunk 渲染 + inbox 渲染都走同一个 `Console` 实例，prompt_toolkit 负责让它们互不干扰。

## 输入路由

```text
input := "/cmd args..." | "! shell-cmd" | <plain text>

/cmd  → dispatch_slash → SLASH_COMMANDS[head] → sub-action handler
! ... → asyncio.subprocess shell pass-through
plain → Runner.interact_agent_team(text, team_name=active, session_id=active)
        ↳ runtime.parse_interact_str 决定 GodView / Operator / HumanAgent
```

字面 `/foo` 给 leader：用 `# /foo` 显式走 GodView 即可，不需要 escape。

## 测试

- 路径镜像：`tests/unit_tests/agent_teams/cli/`
- 单测 mock `Runner.*` 用 `AsyncMock`，断言每个 `_cmd_*` 调用参数（`team_name` / `session_id` / `force`）正确
- 集成测走 `MemoryDatabaseConfig + InProcessMessager + spawn_mode="inprocess"`，`TeamCli.run(input_iter=...)` 注入预置命令序列；不真启 prompt_toolkit
- prompt_toolkit / rich 是软依赖（`pyproject.toml` 的 `cli` extras），不要让它们在非 CLI 模块里成为硬 import

## 提交约定

本子模块改动同 `agent_teams` 母模块：commit footer 固定 `Refs: #751`。
