# 异步工具框架路线一：统一 ID + 管控工具 + 磁盘溢写（DAB）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-22 |
| 范围 | `id_generator.py`（新增）、`harness/async_tools.py`、`tools/tool_async.py`（新增）、`tools/tool_factory.py`、`tools/tool_permissions.py`、`tools/locales/{cn,en}.py` + `descs/{cn,en}/async_task*.md`（新增）、`paths.py`、`rails/team_tool_rail.py`、`i18n.py` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/{test_id_generator.py,harness/test_async_tools.py,tools/test_tool_async.py,test_team_tools.py,workflow/}` → 207 passed, 14 skipped |
| Refs | #751 |

## 背景

起步版异步工具框架（`F_35` / `S_20`）落地了两段式 `AsyncTool`（invoke 立即返回
`launched(task_id)`，完成结果经 `send(immediate=False)` 注入下一轮）+ `AsyncToolRuntime`
（registry + tasks）+ swarmflow 作为第一个实现。`docs/designs/async_tool_framework_roadmap.md`
在其上规划了五个阶段；本次实现**路线一 = D（统一 ID）→ A（管控工具）→ B（磁盘溢写）**，
都在 team 层 NativeHarness 上验证（E 下沉、C 通知队列不在范围）。

三个痛点：

- **D**：task_id 各处用 `uuid.uuid4().hex` 散落生成，无前缀——日志 / 取回工具里无法从 id
  一眼看出任务类型（swarmflow？session_spawn？）。
- **A**：`registry` 已持每任务 status / result / error，但 LLM 无工具访问——无法主动列清单、
  按需取回某任务完整输出、主动取消跑飞的任务。
- **B**：`result` 全量进内存 + 全量注入 LLM——大输出（长报告、大 JSON）撑爆上下文 token。

三条两段式不变量贯穿全程未破坏：① invoke 立即返回 launched 闭合 tool_use；② 完成结果绕过
工具协议经 `send(immediate=False)` 注入，绝不回原 tool_use_id；③ 框架零 `TeamAgent` 依赖。

## 数据结构 / 状态机

`AsyncToolRuntime` 起步版 → 本次：

```
inject                                          （不变）
registry: dict[task_id, AsyncToolRecord]        （不变）
tasks: set[asyncio.Task]            →  _tasks: dict[task_id, asyncio.Task]   # id 映射，支持按 id 取消
                                       _events: dict[task_id, asyncio.Event] # per-task 完成信号
                                       output_dir_resolver / spill_threshold # 溢写（B）
```

`AsyncToolRecord` 加 `output_file: str | None`（溢写时为磁盘路径）。

任务终态（completed / error / cancelled）统一调 `_signal(task_id)` set per-task event，
`wait` 据此阻塞唤醒。

## 决策

1. **`id_generator` 放 agent_teams 层（非 core/common）**：`generate_id(kind, *, length=8)`
   返回 `{prefix}{base36*8}`，前缀表 `{async_tool:x, swarmflow:w, session_spawn:s}` + 默认 `t`。
   实现初稿误放 `core/common`，按 review 反馈移回 `agent_teams/id_generator.py`（与 `paths.py` /
   `i18n.py` 同级）——它索引的 kind 都是 team-scoped，core 不该为此引入工具。
2. **`AsyncTool.invoke` 用 `generate_id(self.card.name)`**：swarmflow（card.name="swarmflow"）
   自然拿 `w` 前缀；未注册工具名 → `t`。比 `generate_id("async_tool")` 写死更自适应。
3. **`tasks` set → `_tasks` dict[task_id, Task] + `_events`**：起步版 `set` 无标识，无法按
   task_id 取消 / 等待。改 id 映射后 `cancel(task_id)` / `wait(task_id)` 直接寻址；per-task
   `asyncio.Event` 比轮询 status 更省更准（完成即唤醒）。`cancel_all` / done-callback 一并改。
4. **管控工具是普通 `TeamTool`（非 `AsyncTool`）**：list / output / cancel 是同步查询 / 操作、
   立即返回，没有两段式 launch。它们持 `parent_agent`、经 `parent_agent.async_tool_runtime`
   操作，与启动工具共享同一 runtime 实例。
5. **管控工具 leader-only 且始终注入（不 gate）**：放 `LEADER_ONLY_TOOLS`。当前唯一 async 工具
   swarmflow 就是 leader-only，teammate 无任何 async 工具；管控工具本身无害（registry 空时 list
   返回 "No async tasks."），故不像 swarmflow 那样按 `swarmflow_model_resolver` gate。
6. **`async_task_output` 的 block/timeout 自己实现**：仓库**无** `TaskOutputTool`（roadmap 的
   "仿 600s"引用失准）。`timeout` 单位 ms（默认 30000），`min(timeout, 600000)` 封顶防死等，
   `runtime.wait` 内部转秒喂 `asyncio.wait_for`；超时返回 running record 而非 raise。
7. **B 溢写默认开启，阈值 32KB**：起步版决策是"完整回灌不截断"（用户明确要求）。32KB 保守
   倾向内联——常规结果（含 swarmflow 中等报告）仍完整内联，仅真正超大才溢写 + 注入"摘要 +
   路径 + `async_task_output` 取回提示"。平衡"完整回灌"初衷与"防撑爆上下文"。
8. **溢写目录经 resolver 惰性闭包注入，不让 runtime 知道 team_name/session_id**：`NativeHarness`
   有 `session_id` 但**无** `team_name`，team_name 只在装配链可见。`TeamToolRail._wire_async_spill`
   设 `runtime.output_dir_resolver` 为闭包——运行时取 `harness.session_id` 拼
   `paths.async_tool_output_dir(team_name, session_id)`，并在首次解析成功时
   `register_cleanup_path`（幂等，`clean_team` rmtree）。runtime 只认 `Callable[[], Path|None]`，
   保持通用、不耦合 team 概念（为 E 阶段下沉铺路）。

## 拒绝的方案

- **`id_generator` 放 `core/common`**（roadmap 原计划）：拒绝。前缀表全是 team-scoped kind，
  core 公共层不该为此扩面；用户明确要求不扩散到 common。
- **`tasks` 保留 `set`**：拒绝。无法按 task_id 定位 cancel / wait 的目标 task。
- **`wait` 轮询 `status`（100ms）**：拒绝。per-task `asyncio.Event` 完成即唤醒，无轮询延迟 /
  CPU 浪费，也更易测（gate 控制完成时机）。
- **管控工具继承 `AsyncTool`**：拒绝。它们不是后台任务，无 `run_background`，强套两段式只会
  让 list / cancel 变成"启动一个查询任务"的荒诞结构。
- **溢写默认关闭（opt-in）/ 阈值 8KB**：均拒绝。关闭则 B 的价值不立即兑现；8KB 太激进，会把
  大量中等结果变成"路径 + 摘要"，偏离"完整回灌"过多。选 32KB 默认开，最大程度保留完整内联。
- **runtime 持 `team_name` / `session_id` 字段**：拒绝。破坏 runtime 的通用性（它本应只认注入
  入口），且 E 阶段下沉到通用 harness 层时要返工。resolver 闭包把 team 上下文隔离在 rail 侧。
- **`paths.async_tool_output_path(team_name, session_id, task_id)` 便利函数**：不加。无调用方
  （runtime 用 resolver 给的 dir 自己拼 `{task_id}.output`，读盘方用 `record.output_file`
  字符串）——加了就是死代码。只保留被 resolver + 清理用的 `async_tool_output_dir`。
- **DAB 拆成三个独立特性 / 三份 F 文档**：拒绝。路线一是一次连贯落地，合并为一份特性，按
  feat / test / docs 三个连续提交（agent_teams 规约）。

## 验证

- `test_id_generator.py`（5）：前缀映射（swarmflow→w / async_tool→x / session_spawn→s /
  未知→t）、长度、base36 字符集、批量唯一性。
- `test_async_tools.py`（12，含起步版 4 + 新增 8）：get / list_all / cancel（取消后
  status=error + "cancelled"）/ wait（终态立即返回、超时返回 running 不 raise、完成 event 唤醒）；
  溢写（≤32KB 内联无 output_file、>32KB 写盘 + output_file + 注入含路径且 <全量、resolver=None
  不溢写）。
- `test_tool_async.py`（9）：list 渲染 / 空、output 缺 task_id / 未知 / 完成取回 / block 等待 /
  读盘分支、cancel / 未知。
- 回归：`test_team_tools.py`（装配未因 +3 leader 工具破坏）、`workflow/`（swarmflow 全套不受
  id 换前缀 / 溢写影响）。
- 合计 207 passed, 14 skipped。未跑 ruff / mypy（项目约定）。

## 已知遗留

- **C 阶段（作用域通知队列）**：多任务同时完成无合并、subagent 嵌套无作用域隔离——本次未做。
- **E 阶段（下沉通用 harness 层）**：`async_tools.py` / `tool_async.py` 仍在 agent_teams 子树、
  依赖 `TeamTool` / `paths.py` / `TeamBackend`；下沉到 `openjiuwen/harness/` 让普通 DeepAgent
  复用需抽象 `AsyncToolHost` Protocol。resolver 设计已为此预留（runtime 不耦合 team）。
- **流式溢写**：当前溢写是任务完成后一次性写盘；swarmflow 多 phase 大量中间产物的流式追加
  （`launch` 传 writer 句柄）未做。
- **sessions_spawn / worker backend 的 id 未迁移**：本次 D 只替换 `AsyncTool.invoke`，未统一
  其它模块的 id 生成（避免触碰未授权范围）。
