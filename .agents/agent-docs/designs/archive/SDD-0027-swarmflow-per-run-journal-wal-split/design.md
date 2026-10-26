---
id: SDD-0027
title: Swarmflow Journal/WAL 按 run 拆分与 WAL 日志化治理
slug: swarmflow-per-run-journal-wal-split
status: shipped
stage: archive
owner: "@xiajing"
reviewers: ["@xiajing"]
created: 2026-09-16
updated: 2026-09-16
version: 1
domain: core
modules: [agent_teams/workflow]
tags: [swarmflow, journal, wal, concurrency, resume, durability, back-compat]
related: [SDD-0003, SDD-0008, SDD-0012]
approved-date: 2026-09-16
dev-start-date: 2026-09-16
archived-date: 2026-09-16
implementation:
  status: shipped
  repos: [agent-core]
  prs: []
  commits:
    - agent-core@f73959555
    - agent-core@ed4d5538d
    - agent-core@0365481f4
    - agent-core@d00a64a49
    - agent-core@1b4f88eac
    - agent-core@ac71451ca
    - agent-core@045eea481
    - agent-core@11ba3d0ba
  tracking: "openjiuwen/agent_teams/workflow/（branch bugfix/swarmflow-wal-per-runid，基于 origin/develop f1d7fd692）"
---

# Swarmflow Journal/WAL 按 run 拆分与 WAL 日志化治理

## 1. 背景与问题 (Why)

SwarmFlow 的 `agent()` 调用是昂贵的 LLM 调用。SDD-0003 为引擎建立了内容寻址缓存：每次调用按
"结构位置 + 内容指纹"（key + sig）记账，结果落 journal 快照；崩溃恢复靠 append-only 的 WAL
sidecar；同 run_id resume 时指纹没变的调用直接复用缓存、跳过 LLM。

这套基座最初的隐含假设是**一个 workflow 同时只有一个 run 在跑**——journal 与 WAL 都是
per-workflow 的**共享文件**：

```
{workflow_dir}/journal.jsonl        # 共享快照（finalize 原子重写）
{workflow_dir}/journal.jsonl.wal    # 共享 append 日志（所有 run 混写）
```

SDD-0008（并发治理）放开了多 run 并行（L1 max_workflows 默认 16）后，这个假设失效。此后
f1d7fd692 引入 WAL compaction（load 时收缩已 seal run 的死记录）试图治理共享 WAL 的无限增长，
并发 ST 实证它暴露了**三个数据丢失竞争**：

| 竞争 | 机制 | 后果 |
|---|---|---|
| A | compaction 的 tmp + `os.replace` 重写 WAL，与并发 run 的 append 交错 | 已 flush 返回（durable 承诺已成立）的记录被重写吞掉 |
| B | finalize 的 `_discard_wal_if_durable` unlink 共享 WAL | 并发兄弟 run 落在 WAL 里的记录被删，其崩溃恢复断裂 |
| C | 两个 run 的 `save` 先后 `os.replace` 同一 journal.jsonl | 后 finalize 的 run 覆盖前者的快照 |

关键定性：竞争 A 丢的不是"torn line"级别的未完成写（那只是该调用重算），而是**durability
承诺本身**——一个量级的问题。共享文件 + 实例级 `_wal_lock`（只护同 run 内 parallel/pipeline
分支，不跨 run）注定无解，方向性修正不可避免。

拆分本身又引出第四个问题：**升级断裂**。老 session 的全部记录只存在于共享文件里，per-run
路径下一个 pause 中的老 run resume 时找不到任何文件，全部 miss 重跑——既慢又重复烧钱。

本特性把四个问题一次解决：journal 与 WAL 按 run_id 拆分、撤销 compaction、WAL 永不主动删除、
共享文件降级为只读兼容种子。

## 2. 目标与非目标

**目标**

- 根治三个共享文件并发竞争：任意两个并发 run 的 journal/WAL 文件互不相交
- 保持 WAL 的崩溃恢复语义（append 即 durable、torn line 容错）与 resume 命中语义不变
- 升级前 pause 中的老 session 在新代码下 resume 时，已完成的调用前缀照常命中缓存
- 老布局共享文件升级后**字节冻结**（永不写），新记录只进 per-run 文件
- engine 保持业务无关（不 import agent_teams 的 paths 模块）

**非目标**

- 滚动老化 / 磁盘配额治理（L1 journal 覆盖收缩、L2 配额淘汰）——方案已设计（见 §10），
  按 owner 决策留待下一阶段实施
- 把老共享文件按 run_id 拆开迁移——读侧种子已覆盖需求，写侧迁移只会重新引入共享文件写竞争
- 跨 session / 跨进程的文件锁（fcntl/msvcrt）——拆分后不存在共享文件，锁失去必要性

## 3. 术语与上下文

- **journal 快照**（`journal-{run_id}.jsonl`）：一个 run 的正式快照，`save` 以 tmp +
  `os.replace` 原子写出，内容是本 run 实际消费的记录（`used`），按程序序排列、字节稳定
- **WAL**（`wal/{run_id}.wal`）：append-only 崩溃恢复日志；miss 的调用完成即 append，
  HIT 不重写；容忍尾部 torn 行；**永不主动删除**
- **run_id**（`wf_{12hex}`）：SDD-0012 引入的多 run 身份；记录级 `get_cached(ks, sig, run_id)`
  三重检查使其同时是缓存隔离键
- **seal / pause**：run 级终端 / 可恢复记录（`__run__:seal|pause:{run_id}`），落 WAL；
  `_seal_guard` 在 relaunch 前读 seal，sealed run 强制新 run_id
- **legacy 共享文件**：拆分前的 `journal.jsonl` + `journal.jsonl.wal`；升级后只读

## 4. 方案设计 (What / How)

### 4.1 总体架构

```
{team_home}/sessions/{session_id}/workflows/{workflow_name}/
├── script.py                       # 内联脚本落盘（不变）
├── journal-{run_id}.jsonl          # 每 run 自己的快照（竞争 C 消失）
├── journal.jsonl                   # legacy 共享快照（冻结，只读种子）
├── journal.jsonl.wal               # legacy 共享 WAL（冻结，只读种子）
└── wal/
    └── {run_id}.wal                # 每 run 自己的 WAL（竞争 A/B 消失）
```

路径单一真相源在 `agent_teams/paths.py`：`workflow_run_journal_path(team, session, name,
run_id)` / `workflow_run_wal_path(...)`——run_id 非 `None` 拼 per-run 文件名，`None` 回退
legacy 共享路径（离线 / 预演调用方零影响）。run_id 经 `_safe_segment` sanitize，防目录穿越。

分层：**engine（`workflow/engine/`）业务无关**，只接收 `wal_path` / `legacy_resume` 字符串
参数；**路径编排（`workflow/runner.py`）** 负责 run_id → 路径的映射与目录创建；**工具层
（`tool_swarmflow.py`）** 的 seal guard 用同一套解析读 seal 记录。

### 4.2 数据流与时序

resume 一个升级前 pause 的老 run（读侧兼容的核心时序）：

```
relaunch(resume_id=R)
  │
  ├─ _seal_guard: Journal.load(per-run journal, per-run wal, legacy=shared)
  │    读序: shared journal → shared wal → per-run journal → per-run wal
  │    （last-wins：per-run 较新，同 key 覆盖 legacy；pause 记录不触发拦截）
  │
  └─ run_swarmflow(R)
       ├─ resume/journal_path = per-run journal   （写目标）
       ├─ wal_path              = per-run wal     （写目标）
       ├─ legacy_resume         = shared journal  （只读种子）
       └─ run_workflow
            └─ Journal.load(...) → prior（含 R 的老记录 + 混居的异 run 记录）
                 ├─ node 0..k-1: get_cached(key, sig, R) HIT   ← 从 legacy 种子复用
                 ├─ node k (foreign record, 同 key 同 sig 异 run_id): MISS ← 三重检查拒掉
                 └─ node k..N-1: MISS → 真跑 → append 到 per-run wal
       finalize → journal-{R}.jsonl（used 全集：HIT 复用 + 新鲜）
```

HIT 不写 WAL（`use()` 对 prior 同对象跳过 append），所以 relaunch 后 per-run WAL 只含新鲜
记录——这本身就是 HIT 的取证锚点。

### 4.3 接口与数据模型

> **Requirement:** 并发 run 的持久化文件 SHALL 互不相交——run R 的 journal/WAL 路径仅由
> `(team, session, workflow_name, R)` 决定。
> **Scenario:** WHEN run A 与 run B 并发执行同一 workflow THEN 两者 append / replace /
> unlink 的文件集合无交集。

> **Requirement:** engine SHALL 保持业务无关——不 import `openjiuwen.agent_teams` 的路径
> 或团队模块。
> **Scenario:** WHEN `run_workflow` 需要持久化 THEN 它只消费调用方传入的
> `journal_path` / `wal_path` / `legacy_resume` 字符串参数。

> **Requirement:** legacy 共享文件 SHALL 只读。
> **Scenario:** WHEN 带 run_id 的 run 加载/落盘 THEN 共享 `journal.jsonl(.wal)` 的字节
> 保持不变，新记录全部落在 per-run 文件。

> **Requirement:** 记录级隔离 SHALL 独立于文件布局。
> **Scenario:** WHEN prior 中存在与查询同 key 同 sig 但 run_id 不同的记录 THEN
> `get_cached` 返回 None（MISS），异 run 数据不被服务。

关键签名（代码事实）：

```python
# engine/journal.py
@classmethod
async def load(cls, path, wal_path=None, legacy_path=None) -> Journal
# 读序: (legacy_path, f"{legacy_path}.wal", path, wal_path)，last-wins

# engine/runner.py
async def run_workflow(..., journal_path=None, wal_path=None, legacy_resume=None, ...)
# legacy_resume 透传给 Journal.load(legacy_path=...)

# workflow/runner.py
def _resolve_journal_path(script, team, session, run_id=None) -> str
def _resolve_wal_path(script, team, session, run_id=None) -> str | None
def _resolve_legacy_resume(script, team, session, run_id) -> str | None
# run_id 空 / META 不可读时返回 None（种子永不破坏启动）

# tool_swarmflow.py::_seal_guard
journal = await Journal.load(per_run_journal, wal_path=per_run_wal, legacy_path=legacy)
```

### 4.4 关键流程

**正常 pause → relaunch（同 run_id）**：pause 落 pause 记录到 per-run WAL（不写 journal）；
relaunch 读 per-run journal（缺失）+ per-run WAL（老前缀）→ HIT 前缀、续跑尾部 → finalize
写 per-run journal。legacy 种子对纯新代码 run 是 no-op（文件不存在即跳过）。

**seal 后 relaunch**：seal 记录落 per-run WAL + finalize 快照；relaunch 时 seal guard 读到
seal → 强制新 run_id → 新文件全 miss（SDD-0012 隔离语义原样）。升级前 sealed 的 run：seal
在共享 WAL 里，seal guard 经 legacy 种子读到 → 同样拦截。

**升级窗口（老 run resume）**：见 §4.2 时序。老 run 完成一次 finalize 后，其记录固化进
per-run journal，legacy 依赖自然终止（同 run_id 的 seal 挡住再 resume）。

### 4.5 配置与开关

无新增配置。滚动老化（L2 配额 `swarmflow_wal_max_runs`）是下一阶段的配置面，本特性不含。

## 5. 关键决策与权衡 (ADR)

### 5.1 per-run 文件拆分，而非共享文件加锁

- **决策：** journal 与 WAL 的文件名拼 run_id（目录布局不变），彻底消除共享写路径。
- **背景：** 三个竞争全部源于"多个 run 写同一文件"；`_wal_lock` 是实例级 asyncio.Lock，
  跨 run 无效；进程级共享需要 fcntl/msvcrt 文件锁——Windows/POSIX 语义分裂、事件循环内
  阻塞、异常路径解锁可靠性都是新负担。
- **备选与理由：** ①全局文件锁——复杂度换来的仍是一个"大家排队写"的热点；②记录级锁 +
  共享文件——竞争 C（save 的 os.replace）没有记录级答案。拆分让每个 run 的写路径物理独占，
  是"用布局换锁"的一次性决策。
- **影响：** F_87 决策 2 原本"run_id 进查询不进路径"——其担忧（路径变则 resume 命中不了
  前缀）被证伪：resume 本就携带 run_id（resume_id 即 run_id），per-run 路径由 run_id 直接
  定位，命中更确定。F_87 已补修订注记。

### 5.2 移除 compaction；WAL 永不主动删除

- **决策：** 删除 `_compact_wal` / `_discard_wal_if_durable` 整个机制；finalize 只做
  `save()`；任何代码路径不 unlink WAL。
- **背景：** compaction 的定性错误在于把"共享文件重写与并发 append 交错"当作"已容忍的
  torn line"——后者丢的是未完成写（该调用重算即可），前者丢的是已 flush 返回的记录
  （durability 承诺已成立）。
- **备选与理由：** ①compaction + 跨 run 锁——锁的复杂度 + compaction 的崩溃窗口双输；
  ②按 run 终态删（sealed 就删）——正是竞争 B 的形态。WAL 是日志，应像日志一样治理：
  要么整树清理（`delete_team` 兜底，已验证），要么未来按时间/大小 rolling，绝不按 run
  终态删。
- **影响：** 文件对按 run 数线性累积是新的增长面（诚实清单见 §12 遗留）；膨胀从"单文件
  内死记录堆积"转移为"文件数增长"，后者可被未来的 rolling 策略干净治理。

### 5.3 legacy 共享文件降级为只读种子，不迁移

- **决策：** 共享 `journal.jsonl(.wal)` 永不再写；带 run_id 的 load 把它们读进 prior
  （顺序在 per-run 源之前）。
- **背景：** 拆分瞬间制造了升级断裂——老 session 的记录只在共享文件里。修复要么读侧
  叠加、要么写侧迁移（把老文件按 run_id 拆成 per-run 文件）。
- **备选与理由：** 写侧迁移被否决——拆老文件就是一次共享文件的读-改-写，恰好重新引入
  本特性刚消灭的共享文件写竞争；且老文件里混居多 run_id 的记录，迁移的正确性依赖每条
  记录的 run_id 归属判定（F_87 之前的记录甚至没有 run_id 字段），复杂度无收益。读侧
  种子零写风险：异 run_id 记录读进 prior 也被 `get_cached` 三重检查自然拒掉——ST 用
  "同 key 同 sig 仅 run_id 不同"的 foreign 记录实证。
- **影响：** 老文件成为长寿命只读依赖（该 run 完成一次 finalize 后自然终止依赖）；
  `delete_team` 整树清理时一并消失。

### 5.4 engine 业务无关：路径编排留在 workflow 顶层

- **决策：** engine 只接收 `wal_path` / `legacy_resume` 字符串参数；run_id → 路径的映射
  （`_resolve_*` 家族）全部在 `workflow/runner.py`。
- **背景：** engine 铁律（不 import agent_teams 业务模块）是它能独立单测、与上游 dw 引擎
  同步的前提。per-run 拼接本质是团队目录布局知识，不属于引擎。
- **备选与理由：** 在 engine 内拼 run_id——省一个参数但把 `_safe_segment` / team_home
  布局渗进 engine，破坏分层。`wal_path` 缺省时 engine 派生 `f"{journal_path}.wal"` 的
  legacy 约定保留（对引擎直连调用方向后兼容；swarmflow 路径下该分支不可达）。

### 5.5 被否决的备选与取舍理由

- **WAL checkpoint 特殊事件**（finalize 后向 WAL append `{"type": "checkpoint", ...}`
  高水位标记，收缩按此截断）：journal 快照本身就是更完整的 checkpoint（完整记录集而非
  水位），覆盖判定已隐含此信息。若未来 journal 与 WAL 合并成单文件，此格式是基础。
- **gzip 归档旧 WAL**：磁盘便宜，复杂度不值；真到那一步先做 L2 配额淘汰。

## 6. 影响面与兼容性

- **升级兼容**：老 session pause 中的 run resume 后照常 HIT（ST 实证）；老 sealed run 照样
  被 seal guard 拦截；`args` 冷恢复记录（`find_run_record(run_id, "args")`）经 legacy 种子
  可见。F_87 之前的记录（无 run_id 字段）天然 MISS——与"跨 run 本就 miss"同量级，接受。
- **离线 / 预演路径**：run_id=None 的调用方（`preprocess_swarmflow`、引擎单测）读写共享
  路径的行为逐字节不变。
- **引擎直连调用方**：`run_workflow` 签名仅追加 keyword-only 可选参数（`wal_path` /
  `legacy_resume`），全调用面零改动。
- **清理语义**：`delete_team` 整树删除 team_home（含 wal/ 子树与 legacy 文件）——已验证
  为唯一清理兜底。
- **BREAKING**：无。老布局共享文件保留在原位继续可读。

## 7. 测试与验证

| 层 | 覆盖 | 结果 |
|---|---|---|
| UT（engine journal） | legacy 种子读入 / per-run 覆盖优先 / 异 run 记录三重检查拒掉 / HIT 不写 per-run WAL / legacy 字节冻结 / 下次 load 双源齐备 | 全过 |
| UT（workflow runner） | `_resolve_legacy_resume` 路径映射（run_id 有/无）/ 无 META 返回 None / per-run journal·wal 路径 / 双 journal 并发隔离 | 全过 |
| UT 回归 | workflow 目录全量（journal/runner/paths/pause_resume/budget/engine/…） | 296 passed 零回归 |
| ST：wal_concurrency | 真 LLM 双 run 并发，快 run finalize 后慢 run WAL 完好 | 竞争 A/B/C 全部 NOT-REPRODUCED |
| ST：journal_residue | 10 节点反复 pause/relaunch + 改脚本 | 快照每 key 一条、run_id 唯一 |
| ST：cache_opt A–D | 三 run 文件互不影响 / pause-resume 同 run_id / isolation 变更 / [MISS,HIT,MISS] 隔离序列 | 全过 |
| ST：legacy_compat | 真 LLM 5 节点 pause → 伪造老布局（共享 WAL 混 foreign 记录 + pause 记录，删 per-run 文件）→ 同 run_id relaunch | 10/10 PASS；引擎日志 `prior_records=0→4`、`[HIT,HIT,MISS,MISS,MISS]`、legacy md5 冻结、seal guard 不被 pause 误触发 |

ST 文件按仓库纪律本地 untracked（真实凭据经 .env），运行方式记录在
`doc/analysis/2026-09/2026-09-15-per-runid-split-pr-review.md`。

## 8. 可观测性

- 引擎日志 `[wf] journal loaded: prior_records=N path=… wal=…`（`run_workflow` 每次加载
  打一行）——N 的跳变即 legacy 种子生效的直接证据
- 每个 agent 的 `CACHE_HIT` / `CACHE_MISS` verdict 行（`rt.log_sink` → team 日志）
- seal guard 拦截时 `team_logger.warning` 一行
- 无新增指标 / 告警面

## 9. 风险与回滚

- **主要风险**：文件对按 run 数线性累积（§12 遗留）——长寿命 session 磁盘占用 =
  O(runs × 平均 run 体积)，治理方案已设计未实施
- **回滚**：还原为共享路径即回滚（`workflow_run_*_path(None)` 形状就是 legacy 布局）；
  per-run 文件留在盘上不阻碍共享路径读写；legacy 只读种子在回滚方向同样成立（共享文件
  自升级起未被写过，内容仍是拆分前状态）

## 10. 开放问题 (Open Questions)

- [ ] 滚动老化 L1（journal 覆盖收缩：per-run WAL 的纯冗余段在自身 journal 存在后可惰性
      收缩，无跨 run 竞争）与 L2（`swarmflow_wal_max_runs` 配额淘汰，sealed 优先、活跃
      保护）的实施时机——设计已定稿于
      `doc/plan/2026-09/2026-09-15-wal-rolling-aging-design.md`（L1 journal 即 checkpoint
      的洞察、WAL 特殊事件方案的否决记录均在其内）

## 11. 评审记录 (Iteration Log)

| 轮次 | 日期 | 版本 | 评审人 | 结论 | 主要修改/讨论 |
|---|---|---|---|---|---|
| 1 | 2026-09-16 | 1 | @xiajing | approved | owner 自审；code-review（high）复核已提交部分无高危 findings，唯一 Medium（文件累积增长面）已回写 F_40 已知遗留 |

## 12. 实现记录与偏差

### 实际实现

分支 `bugfix/swarmflow-wal-per-runid`（基于 origin/develop f1d7fd692），8 个提交：
per-run 拆分（f73959555 / ed4d5538d / 0365481f4）+ legacy 只读种子（d00a64a49 / 1b4f88eac /
ac71451ca）+ F_40 增长面遗留回写（045eea481）+ F_38/F_87 文档同步（11ba3d0ba）。撤销
compaction 约 -120 行，新增路径层 + 读侧种子约 +200 行——净复杂度下降（删整个机制，加薄层）。

### 与设计的偏差

无。engine 参数命名与设计一致（`wal_path` / `legacy_resume` / `legacy_path`）。

### 链接

见 frontmatter `implementation.commits`。同步文档：`F_40`（修订 3/4 + 已知遗留）、
`F_38`（修订 2026-09-16）、`F_87`（决策 2 修订 + 补证）、`S_18`（2026-09-16 修订）、
`workflow/AGENTS.md`；检视报告 `doc/analysis/2026-09/2026-09-15-per-runid-split-pr-review.md`。

### 遗留与后续

- **滚动老化未实施**（§10）——文件对按 run 数线性累积，F_40 已知遗留已量化（O(runs ×
  平均 run 体积）），治理设计定稿待实施
- **本地 untracked 的 `test_parallel_cancel_race.py` 挂起**——前会话遗留文件，不在本分支
  提交中，与本特性无关，留给文件所有者
- **code-review 的两条 Low**（avatar_session_backend `abort_all` 的 CancelledError 收集
  行为 / `tool/__init__.py` 惰性导入掩盖 ImportError）——均在前会话遗留的**工作区未提交
  改动**里，非本分支内容，已记录留待所有者处置

## 13. 参考与关联设计

- SDD-0003：中断恢复与崩溃可恢复状态持久化（journal/WAL 基座，本特性的直接前驱）
- SDD-0008：并发治理（放开多 run 并行——共享文件竞争的引入点）
- SDD-0012：双层 budget 与 run_id 隔离（run_id 语义与三重检查的定义处）
- 源仓文档：`F_38` / `F_40`（修订 3/4）/ `F_87` / `S_18`、
  `doc/plan/2026-09/2026-09-15-wal-rolling-aging-design.md`（滚动老化设计）、
  `doc/analysis/2026-09/2026-09-15-swarmflow-wal-journal-per-runid-handoff.md`（交接）、
  `doc/analysis/2026-09/2026-09-15-per-runid-split-pr-review.md`（检视）
