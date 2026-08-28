# Swarmflow run_id 隔离、缓存重建与双层 Budget

让每次 swarmflow run 在 journal 缓存、token 账本两个维度上都按 `run_id` 隔离，并引入
session 级 / workflow 级两层 budget，使撞顶后能否重试的语义落在事件层可区分。

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-28 |
| 范围 | `workflow/engine/journal.py`（`get_cached`/`find_run_record`/`write_run_record`）、`workflow/engine/primitives.py`（缓存命中重建花费 + `current_agent`）、`workflow/engine/runtime.py`（`run_id`/`current_agent`/`workflow_budget` 字段）、`workflow/engine/runner.py`（`_write_seal_record`/`_write_pause_record`）、`workflow/backends/budget_rail.py`（双层计数）、`schema/events.py`（`budget_exhausted_scope`） |
| 测试基线 | `tests/unit_tests/agent_teams/workflow/test_pause_resume.py`、`test_swarmflow_invoke_control.py`、`test_budget.py` 全量通过 |
| Refs | #— |

## 背景

F_38 的 journal 是 content-addressed resume：`(call-path, sig)` 命中即短路。但同一
`(team, session, workflow_name)` 的多次 run 共享同一 journal 前缀，第二次 run 会**无条件
命中上一次 run 的缓存**——哪怕上一次已经正常完成或被 budget gate 停掉。这与本次运行的
budget 计算冲突：

- 上次 run 已耗尽 budget，本次 run 命中缓存直接返回旧结果，**花费不重建**，budget 不增长，
  撞顶检测失灵。
- 一个被 `WorkflowAborted` / `BudgetExhausted` 中断的 run，resume 时本应"从中断点续跑"，但
  若它已被 seal（终端），resume 会错误回放已 seal 的缓存，把终态结果当成在途结果。

budget 侧只有一个 leader 级账本（F_66），无法区分"本次 run 烧了多少"和"整个 session 烧了
多少"——撞顶后 leader 不知道是该改脚本重试（run 级），还是该加钱（session 级）。

## 数据结构 / 状态机

### journal run 级记录

journal 在 call-path 记录（`__call__:` 前缀）之外，新增 run 级记录，key 形如
`__run__:{type}:{run_id}`：

- `find_run_record(run_id, record_type)`：查某个 run 是否有某类记录（当前 `seal` / `pause`）。
- `write_run_record(run_id, record_type, payload)`：写一条 run 级记录，落 WAL + 刷进 `prior`
  使同 run 查询可见。

### `get_cached(ks, sig, run_id)`

命中条件从 `(ks, sig)` 升级为 `(ks, sig, run_id)` **双重检查**：

- 旧 run（无 run_id 字段的历史记录）自然失效——`run_id=None` 与任何新 run 的非空 run_id
  比对为不等，不命中。
- 新 run 只命中**自己**写下的记录；命中后通过记录里的 `tokens` 字段重建花费。

### Runtime 字段

`Runtime` 新增：

- `run_id: str | None`：当前 run 的身份，贯穿 journal 查询与进度事件。
- `current_agent: dict | None`：跟踪进行中的 agent（started 时填，completed/failed 时清），供
  pause 记录记录被中断的 agent。
- `workflow_budget: BudgetLedger | None`：per-run 账本，与 `budget`（leader/session 共享）
  分立。

### 双层 Budget

`SwarmflowBudgetRail` 在 `after_model_call` 记账时，当 `workflow_budget` 非 None，**同时**往
session 账本（`self._budget.add(tokens)`）与 workflow 账本（`self._wf_budget.add(tokens)`）
各记一笔——同一笔消耗两个池。`workflow_budget=None` 时只记 session 级（leader/worker 不区分
per-run 上限的场景）。

### `BudgetExhausted.scope`

`BudgetExhausted` 携带 `scope: "workflow" | "session"`：

- `scope="workflow"`：per-run 账本撞顶——可重试（改脚本或调高 `workflow_token_limit` 后同
  run_id relaunch）。
- `scope="session"`：leader/session 账本撞顶——终端（需新建 session 或调高
  `swarmflow_budget`）。

## 决策

1. **缓存命中必须重建花费**。命中缓存跳过 LLM 调用，但那次调用当初烧的 token 是真实的，
   必须加回本次 run 的 `workflow_budget`，否则撞顶检测会把命中缓存当成"免费"而失灵。重建靠
   journal 记录里的 `tokens` 字段（`_JournalRecordInput` 携带，`_make_record` 写入）。
2. **`run_id` 进 journal 查询，不进 journal 路径**。journal 落盘路径仍由
   `(team, session, workflow_name)` 决定（S_18 不变量），多 run 同名脚本共享同一 journal 文件；
   `run_id` 只参与**记录级**的命中判定（`get_cached` 的第三参数）。这样既隔离了 run，又不破坏
   resume 的路径稳定性。
3. **两层 budget 同源注入、独立计数**。`agent_configurator` 在 `enable_swarmflow` 时给 leader
   挂 `SwarmflowBudgetRail(swarmflow_budget, workflow_budget=None)`；run 启动时引擎按
   `META.workflow_token_limit` 建 per-run `BudgetLedger` 注入 rail 的 `workflow_budget`。
   `workflow_budget=None` 时 rail 退化为纯 session 级——向后兼容未配 `workflow_token_limit`
   的脚本。
4. **leader / TinyAgent 的 token 只算 session，不算 workflow**。leader 与判断意图的 TinyAgent
   不属于某次 run 的 worker 群，其消耗进 session 账本但不进 per-run 账本——避免一次 run 的
   撞顶被 leader 自身的开销触发。
5. **run 级记录走 WAL，与 call-path 记录同文件**。`write_run_record` 复用 journal 的 WAL 机制，
   不另开文件；`_program_order` 容错非 JSON 键（run 级 key 不参与 call-path 排序）。

## 拒绝的方案

- **journal 路径加 run_id 段**：拒绝。破坏 resume 的路径稳定性——同一名脚本每次 run 一个目录，
  resume 命中不了前缀。run_id 只进记录级查询，不进路径。
- **缓存命中不重建花费**：拒绝。撞顶检测会失灵，budget gate 形同虚设。
- **单层 budget + 标记撞顶来源**：拒绝。单账本无法区分"本次 run 烧了多少"与"session 烧了多少"，
  撞顶后无法判断可重试还是终端，必须两层。
- **leader/TinyAgent token 进 workflow 账本**：拒绝。leader 不属于某个 run，它的开销不该让
  某次 run 提前撞顶；session 级才是它的归属。

## 验证

- `test_pause_resume.py`：resume 命中前缀只重跑被打断的 agent，run_id 隔离下旧 run 记录不误命中。
- `test_swarmflow_invoke_control.py`：seal guard 命中后强制新 run_id，不回放 sealed 缓存。
- `test_budget.py`：双层计数下 workflow 撞顶（可重试）与 session 撞顶（终端）的事件 scope 区分。

## 已知遗留

- 真实 LLM e2e：双层 budget 的撞顶可重试 vs 终端语义需端到端验证（手动，不进 CI）。
- `workflow_token_limit` 配置入口在脚本 META，未提升到 `TeamAgentSpec` 层级。
