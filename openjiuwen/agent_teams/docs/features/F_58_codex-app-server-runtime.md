# Codex app-server 常驻成员运行时

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-07-21 |
| 范围 | `openjiuwen/agent_teams/external/cli_agent/codex/`、backend registry、spawn |
| 测试基线 | external CLI 全量单测 110 passed |
| Refs | S_18，F_22，F_25 |

## 背景

原先 `cli_agent="codex"` 使用 `codex exec --json`：每个 turn 都启动一个新进程，
并且 Jiuwen 没有稳定地把返回的 Codex thread 与团队成员生命周期绑定。这会造成
同一成员接到 Leader 的后续消息时上下文断裂，也无法把即时消息明确映射到
正在运行的 turn。

Claude 在新架构中已经使用独立 SDK backend，证明“backend 自己管理原生会话，
对上只实现 `CliRuntimeBase`”是合适的分层。Codex 因此也应该使用独立后端，
而不是继续把 app-server JSONL 协议塞进通用 adapter。

## 决策

1. **`codex` 是独立 app-server backend**
   - 默认命令为 `codex app-server --listen stdio://`。
   - `backends.py` 将其标记为 `kind="app_server"`，spawn 在通用 adapter 之前分流。
   - 旧的一次性实现改名为 `codex-exec`，仅作显式兼容选项。

2. **每个 Jiuwen 成员一个进程、一个 thread**
   - `CodexAppServerRuntime` 在首次 turn 时执行 `initialize` 和 `thread/start`。
   - `thread_id` 保存在 runtime 实例中，后续任务都向它发送 `turn/start`。
   - 每次 `spawn_external_cli` 生成独立 runtime，所以 `codex-1` 与 `codex-2`
     不可能共享 thread 或模型历史。

3. **协议读写单点化**
   - stdout 只有一个 reader task，负责拆分 response、server request 和 notification。
   - request 使用递增 id + Future 匹配，默认 30 秒超时；写入经 lock 串行化。
   - `item/agentMessage/delta` 映射为 `OutputSchema`，`turn/completed` 收口一轮。

4. **团队消息与 Codex turn 对齐**
   - runtime IDLE 时的消息开新 turn。
   - 正常在途消息排到下一 turn；`immediate=True` 调 `turn/steer`。
   - abort / Leader shutdown 调 `turn/interrupt`。若 app-server 不确认，或 runtime 已进入
     TERMINATED，立即终止进程，保证 shutdown 不因等待 Codex 而卡死。

5. **Prompt 和 MCP 保持独立边界**
   - 成员角色提示词经 `thread/start.developerInstructions` 下发，属于 thread。
   - 团队 MCP 通过 app-server 进程参数的 `-c mcp_servers.*` 注册，子进程继承
     `OPENJIUWEN_TEAM_JOIN`，属于成员身份。

## 拒绝的方案

- **继续每轮 `codex exec`**：进程频繁重启，steer / interrupt 无法与活跃 turn
  精确对齐，且会话续接依赖解析 CLI 事件。仅保留为 `codex-exec` 兜底。
- **把所有 Codex 成员接到同一 thread**：会泄露角色私有 prompt 和任务上下文，
  违反成员隔离不变量。
- **把 app-server 协议做成通用 `CliAgentAdapter` 配置**：JSON-RPC request/response
  关联、server request 回应和 turn 状态是有状态行为，不是几个字段可以正确表达的
  静态启动知识。
- **默认 SSH 运行**：当前 Codex backend 只对本地 stdio 协议做了完整生命周期
  验证，在传输层和冷恢复契约完善前显式拒绝 SSH。

## 验证

- backend registry 区分 Claude SDK、Codex app-server 与通用 adapter。
- MCP `-c` 参数包含 command / args / join env / 120s startup timeout / required。
- 伪 app-server 端到端测试确认：一个 process、一次 initialize、一次
  thread/start、三次 turn/start，且 shutdown 发送一次 turn/interrupt 后进程退出。
- external CLI 全量单测：110 passed。

## 已知遗留

- 当前只保证同一 runtime 实例内的多轮连续性。Codex 会在自身数据目录保留
  thread，但 Jiuwen 还没有把 thread id 回写 team checkpoint，所以成员冷重建后
  会创建新 thread。
- 尚未实现 Codex thread 的团队删除清理。当前 shutdown 负责结束 app-server
  进程，不删除 Codex 自身的历史记录。

## 后续迁移注记（2026-07-21）

- backend registry 已把 `codex` 从独立 `app_server` kind 收敛到 `sdk` kind；
  `app_server` 不再是公共 backend 分类。
- `openai-codex>=0.144.4` 已作为可选依赖声明，`load_codex_sdk()` 只在构建 Codex runtime
  时导入，因此未安装 Codex extra 不影响其它 backend 的模块导入；选择 Codex backend
  时则立即返回清晰的配置错误。
- 上述过渡已由 [[F_66_codex-python-sdk-runtime]] 完成：当前 runtime 直接使用
  `AsyncCodex`，本文保留为早期 raw app-server 实现的历史决策记录。
