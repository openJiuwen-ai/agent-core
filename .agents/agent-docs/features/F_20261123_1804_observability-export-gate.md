---
id: F_20261123_1804_observability-export-gate
title: 采集需求与导出需求拆分：observability 导出闸门
type: F
author: hh0205
author_email: huanghao206@huawei.com
date: 2026-11-23
designs: []
design_note: 落实 GitCode jiuwenswarm issue 4327 第 3 条（trajectory_ui 不能影响 observability 功能）的 Core 侧能力，无独立 D 文档。
modules:
  - openjiuwen/extensions/observability
  - openjiuwen/harness/observability
  - openjiuwen/agent_teams/observability
files:
  - openjiuwen/extensions/observability/demand.py
  - openjiuwen/extensions/observability/runtime.py
  - openjiuwen/extensions/observability/AGENTS.md
  - openjiuwen/harness/observability/setup.py
  - openjiuwen/agent_teams/observability/setup.py
  - tests/unit_tests/extensions/observability/test_demand.py
  - tests/unit_tests/extensions/observability/test_runtime.py
  - docs/zh/2.开发指南/API文档/openjiuwen.harness/rails/evolution/trajectory_rail.md
  - docs/en/2.Development Guide/API Docs/openjiuwen.harness/rails/evolution/trajectory_rail.md
---

# 采集需求与导出需求拆分：observability 导出闸门

## 关联依据

- 需求：GitCode jiuwenswarm issue 4327 第 3 条——「trajectory_ui 只控制前端轨迹文件生成，
  不能影响 observability 的功能」。平台侧的调用改动记录在 Swarm 仓
  `F_20261123_1804_trajectory-ui-capture-only-demand`。
- spec：observability 模块没有独立 spec；本次形成的长期约束写入
  `openjiuwen/extensions/observability/AGENTS.md`（`demand.py` 职责与不变量 6）。
- 日期：2026-11-23 创建，2026-11-24 定稿。
- 实际实施范围：`demand.py` 的导出记账与判定、`runtime.py` 的导出闸门、两层
  `acquire_observability` 透传 `export`，以及对应单测、模块规则和 `trajectory_rail` 中英文 API 文档。
  按 trace / scope 区分导出、采样与脱敏后移未实施，见「已知遗留」。

## 背景

进程内只能有一个全局 `TracerProvider`，由第一个初始化者决定配置；provider 创建时会按
`ObservabilityConfig` 挂上 exporter，OTel 不支持事后移除 processor。原有 demand 协调只有一种
需求——「需要 provider」，于是任何只想采集 span 的一方（Web 轨迹库、演进的
`TrajectorySpanProcessor`）一拉起 provider，就会连带写 trace 文件或向 OTLP / Langfuse 上报。
平台侧打开「轨迹诊断」即触发导出，正是这个耦合的表现。

## 实际实现与开发决策

### 1. 两份记账：需要 provider 与需要导出

- `demand.py` 在原有的 active runtime 集合之外新增 `_EXPORT_RUNTIMES`，记录哪些 runtime
  希望导出；对外提供 `set_export_demand()` 与 `is_export_enabled()`。
- `acquire_observability_demand(..., export: bool = True)` 以及
  `harness` / `agent_teams` 的 `acquire_observability(config, *, export=True)` 新增仅关键字参数。
  默认 `True`，既有调用方行为不变。
- 导出记账在「runtime 已 active 直接返回」之前刷新，所以每次 sync 都能热切换导出，
  不需要重建 provider。原因：processor 无法移除，重建 provider 又会打断其他 runtime。
- `release` / `reset` 同步清理导出记账。

### 2. 闸门只包按配置构建的 exporter

- `runtime.py` 新增 `_ExportGate(SpanProcessor)`，包住按配置构建的 Batch / Simple exporter
  processor：`on_end` 先查 `is_export_enabled()`，关闭时丢弃；`force_flush` / `shutdown` 恒直通，
  闸门关闭时 provider 仍能正常收尾。
- 采集侧 processor（`TrajectorySpanProcessor`、`SpanRecordProcessor`）与 exporter 平级挂载，
  不经过闸门，所以关闭导出不影响轨迹和演进数据。
- `span_exporter_override` 不经过闸门：调用方显式传入 exporter 就是明确的导出意图
  （在线 RL 的无参插件路径即如此）。
- 闸门出现异常时 fail-open：放行并告警，转发本身也受保护。原因：显式开启 observability
  的用户不能因闸门缺陷丢数据，而 `on_end` 同步运行在 `span.end` 的调用线程上，不能向业务抛错。

### 3. 判定规则

| 条件 | 判定 | 理由 |
| --- | --- | --- |
| 协调器正在销毁自己创建的 provider（`_TEARDOWN`） | 跟随释放方的导出需求 | 销毁时强制结束的残留 span 不应因 active 集合已空而被默认放行 |
| 没有任何 active runtime | 导出 | 保留直接调用 `init_observability` 的旧行为（RL 采集器、评估器） |
| provider 由外部直接初始化（`_EXTERNAL_EXPORT_OWNER`） | 导出 | 生命周期归外部所有，其导出意图先于任何协调需求，仅采集的需求不能关掉它 |
| 其他 | active runtime 中至少一个要求导出 | 只看真正 active 的 runtime，失败或已释放的记账不能打开闸门 |

`_EXTERNAL_EXPORT_OWNER` 在首次协调 acquire 发现「provider 已存在且不是协调器创建」时置位，
在协调器自己创建 provider 时清除，使导出意图的保护范围与 `_PROVIDER_OWNED` 的生命周期所有权一致。

### 4. acquire 失败不改变共享状态

initializer 抛错，或返回后没有 provider，都按同一条回滚路径处理：

- 撤销本次写入的导出需求；
- 仅当调用前没有 provider、调用后有时，在 `_TEARDOWN` 下用共享 shutdown 释放本次创建的
  provider；调用前已存在的 provider 属于外部或其他 runtime，原样保留；
- 恢复调用前的导出需求，原异常继续抛给调用方；清理异常只记录告警，不覆盖原故障。

原因：首次 acquire 可能已经创建 provider，之后才在 runtime 装配（如 root span fallback）阶段失败。
如果残留 provider 不回收，下一个仅采集的 acquire 会把它误判为外部 owner 并打开导出。

### 5. 判定结果在锁内计算、无锁读取

记账每到达一个一致状态，就在 `_DEMAND_LOCK` 内计算判定结果并发布到 `_EXPORT_GATE_OPEN`；
`is_export_enabled()` 只读这个值，不取锁。只在一致的位置发布——例如释放最后一个需求时先置
`_TEARDOWN` 再发布——无锁读者不会看到「active 已空、默认放行」的中间态。

原因：`is_export_enabled()` 在每次 `span.end` 内同步执行。若在这里取锁：

- 最后一次 release 在整个 finalizer（含 exporter flush / shutdown）期间持锁，业务线程的
  `span.end` 会被阻塞，违反模块「导出不阻塞业务线程」的不变量；
- `ObservabilityRuntime.shutdown` 持有 runtime 锁时结束残留 span 会再取 demand 锁，而
  acquire / release 是先取 demand 锁、再在 `is_initialized()` 里取 runtime 锁，锁顺序相反，
  直接 shutdown 与协调 acquire 并发时会死锁。

`publish_span_snapshot` / `publish_stream_frame` 同样在业务线程上执行，它们读取的共享 processor
引用只写一次，也改为无锁读取。这条约束作为不变量 6 写入模块 `AGENTS.md`。

## 拒绝的方案

- **只在需要导出时才挂 exporter**：provider 由第一个初始化者定配置，processor 又不能移除；
  同一进程里仅采集与需要导出的需求会交替出现，无法事后补挂或摘除。
- **给轨迹、演进各建独立 provider**：进程只允许一个全局 `TracerProvider`，不可行。
- **闸门判断时取锁实时计算**：会出现上面第 5 节的阻塞和死锁。
- **采样移到导出闸门（provider 恒全量采集）**：能顺带解决 `sample_rate < 1` 时轨迹库也被抽样，
  但要把 `ParentBased` 改为 `ALWAYS_ON`，改变跨组件采样语义并带来全量建 span 的开销；
  本次保持最小变更，另立需求。
- **仅采集时把「无 active runtime」的默认值改为不导出**：会破坏所有直接初始化 provider 的调用方。

## 行为

| 场景 | provider | 按配置构建的 exporter |
| --- | --- | --- |
| 协调 runtime `acquire(export=True)` | 创建或复用 | 导出 |
| 协调 runtime 仅 `acquire(export=False)`（轨迹库、演进） | 创建或复用 | 不导出 |
| 一个 runtime 要求导出、另一个仅采集 | 共用 | 导出（进程级，见遗留） |
| 外部先直接初始化 provider，再有仅采集的协调需求 | 已存在 | 导出 |
| 直接 `init_observability`，没有协调 runtime | 创建 | 导出（旧行为） |
| `span_exporter_override` | 创建或复用 | 导出（不受闸门约束） |
| 仅采集的 provider 销毁时强制结束的残留 span | 销毁中 | 不导出 |
| 要求导出的 provider 销毁时的残留 span | 销毁中 | 照常导出 |
| initializer 失败 | 本次创建的被回收 | 导出需求恢复到调用前 |

## 实际执行与证据

| 执行日期 / 验证范围 | 实现版本与环境 | 命令 / 复现步骤 | 实际结果 | 未覆盖项 / 失败原因 |
| --- | --- | --- | --- | --- |
| 2026-11-24 demand / runtime 定向回归 | 本分支工作区；Windows 11，Python 3.11 仓库 `.venv` | `python -m pytest tests/unit_tests/extensions/observability/test_demand.py tests/unit_tests/extensions/observability/test_runtime.py -o addopts= -q` | 37 passed | 无 |
| 2026-11-24 三目录 observability 回归 | 同上 | `python -m pytest tests/unit_tests/extensions/observability tests/unit_tests/harness/observability tests/unit_tests/agent_teams/observability -o addopts= --asyncio-mode=auto -q` | 346 passed，1 failed | 见下方说明，两次失败均与本改动无关 |
| 2026-11-24 Swarm 侧联跑 | Swarm 工作区，`PYTHONPATH` 指向本 Core 源码 | `test_trajectory_runtime_wiring.py`、`test_debug_trace.py`、`test_ttse_plugin_runtime.py` | 116 passed | 依赖锁定版本的干净安装未验证 |
| 2026-11-23～24 真实副作用复验 | 真实 Swarm sync + 真实 demand / 闸门 + 真实 file exporter 与 SQLite，span 由代码生成 | 双仓工作区外的实验脚本 | 全部符合预期，见下方说明 | 未接真实 OTLP / Langfuse 服务、真实模型和 UI |

回归用例要点：

- 真实链路用例只把导出传输端换成内存 exporter，provider、demand 协调器和闸门都用真实实现，
  断言的是最终导出的 span 集合，而不是闸门的布尔值。覆盖外部 owner 不被仅采集需求关闭、
  仅采集销毁时残留 span 不导出、要求导出时照常 drain、失败 acquire 回滚、首次创建失败回收
  provider 并允许重试（Agent / Team 两个方向）、外部 provider 上装配失败后继续导出。
- 失败路径新增的 6 个用例在回收逻辑加入前为 4 failed / 2 passed：4 个首次创建失败用例都停在
  provider 残留断言，2 个外部 provider 用例本就通过。
- 无锁读取的两个用例（`test_span_end_is_not_blocked_by_a_teardown_holding_the_demand_lock`、
  `test_direct_shutdown_does_not_deadlock_against_a_demand_lock_holder`）在加锁版本上均失败：
  业务 `span.end` 等待销毁过程；acquire 取 runtime 锁超时。用例自带超时，回归时报失败而不会挂住进程。

三目录回归的失败说明：`test_processor_delivers_exact_file_exporter_bytes_and_hints` 在 Windows 上
偶发起止时间戳同纳秒；`test_streaming_llm_call_records_ttft_and_reasoning` 在整套并跑时偶发失败，
单独运行时改动前 3/3、改动后 5/5 通过。两者都不经过本次改动的代码路径。

真实副作用复验结论：

- 仅开轨迹诊断：SQLite 有数据，trace 目录中没有 jsonl；有残留 span 时释放后也没有。
- 首次 Agent acquire 装配失败后 provider 被回收；随后 Team 仅采集时闸门保持关闭，SQLite 记录该
  span，jsonl 中没有。
- 仅开演进：演进订阅照常收到轨迹，jsonl 为 0；热切换打开 `observability.agent.enabled` 后
  jsonl 写出，演进不受影响。

### 覆盖与资料同步

- 已验证：demand 记账与判定、闸门、失败回滚、销毁时序、并发锁行为，以及 Swarm 两条 sync
  链路的导出参数和落盘副作用。
- 未验证：真实网络导出端、真实模型调用、Linux / macOS。
- 已同步：模块 `AGENTS.md`（`demand.py` 职责、不变量 6）；`trajectory_rail` 中英文 API 文档说明
  轨迹只依赖 processor，仅需轨迹时可传 `export=False`，开关按进程生效，override 不受影响。

## 已知遗留

| 遗留项 | 性质 | 影响与现有处置 | 后续入口 |
| --- | --- | --- | --- |
| 导出判定是进程级的 | 范围外（需求残留） | Team 要求导出、Agent 仅采集时，同进程独立单 Agent 的 span 也会被导出；Swarm 配置注释已写明 | 沿 trace / root 传播来源和导出策略，或由产品确认「任一开启即整进程导出」的契约；平台侧拟将两个开关合并后统一设计 |
| 在 `on_end` 时判定，不在 span 开始时判定 | 有意取舍 | 热切换导出开关时，进行中的 trace 可能只导出一部分（缺根 span 或缺子 span） | 与上一项一并在 `on_start` 记录判定 |
| `sample_rate` 与脱敏在采集阶段生效 | 范围外 | 轨迹库和演进数据同样被抽样、脱敏 | 另立需求（采样迁移与脱敏下移） |
| 仅采集时 file exporter 仍会创建 trace 目录 | 已知限制 | 只产生空目录，不写文件 | 如需消除，延迟到首次写入时再建目录 |
| 平台侧依赖本接口 | 交付顺序 | Swarm 传 `export=` 需要含本变更的 Core；旧 Core 会抛 TypeError | 本 PR 合入后，Swarm 将 pyproject 与 uv.lock 中的 Core 固定 commit 推进到该提交，并在干净环境验证 |
