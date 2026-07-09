# 条件命名任务状态机 + 验证闸（设计稿，待评审）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-07-09 |
| 状态 | **分阶段交付**：Phase 1（条件命名状态机 + plan 闸，折叠 F_58）**已实现**；Phase 2（reviewer / verify 闸，使 `IN_REVIEW` 可达）**待建** |
| 范围（Phase 1） | `schema/status.py`（枚举 + 转移表）、`schema/events.py`、`schema/task.py`、`tools/database/graph.py`、`tools/database/task_dao.py`、`tools/task_manager.py`、`tools/tool_task.py`、`tools/team.py`、`agent/coordination/handlers/stale_task.py`、`observability/monitor_handler.py`、`prompts/`、`tools/locales/descs/`、`docs/specs/S_08`·`S_12` |
| 范围（Phase 2，待建） | `reviewer` 列（`models.py`/`schema/task.py`/DAO/迁移）、`verify_task` 工具、`member_complete` 分流 `IN_REVIEW`、`view_task(action=in_review)`、`TASK_SUBMITTED_FOR_REVIEW`/`TASK_VERIFIED`/`TASK_REVISION_REQUESTED` 事件、reviewer 权限集与 prompt |
| 测试基线（Phase 1） | `python -m pytest tests/unit_tests/agent_teams` → **1800 passed / 16 skipped**（改 9 个测试文件的旧状态断言，删 1 个 F_58 `STARTED` 镜像用例，重命名 1 个） |
| Refs | #751 |
| 关系 | **取代 F_58 的 `STARTED`**（折叠，`STARTED` 不进 git 历史），保留 F_58 的其余成果（见 §与 F_57/F_58 的关系）；延续 F_57 的工具形态框架 |

## 背景

两个驱动：

1. **命名不自洽**：当前 `TaskStatus` 里 `PENDING`/`BLOCKED` 是"条件"（任务此刻的状态），而
   `CLAIMED`/`STARTED` 是"事件"（认领了 / 开始了）——把转换动作误当成了状态。`STARTED`
   （F_58 新增）读作"它开始了"，不是一个条件。

2. **要加执行后的验证步骤，且执行↔验证可能循环**：任务做完要经验证，验证不过要返工，可能来回
   多轮。当前状态机没有这个阶段。

## 核心洞察

**plan 闸与 verify 闸是同构的镜像**——都是「成员提交产物 → 某角色裁决 → 过则前进 / 不过则
回退」：

- **plan 闸**在执行**前**（plan_mode）：准备计划 → leader 批 → 执行；驳回 → 回去改计划。
- **verify 闸**在执行**后**（有 reviewer）：提交结果 → reviewer 验 → 完成；不过 → 回去返工。

想清这一点，三个设计问题一起解：

- **状态是"条件"，转换是"事件"**。`claim`（自主）/ `start`（调度）是**进入同一个"执行中"条件
  的两条转换**——模式差异属于转换，不属于状态。所以 `CLAIMED` + `STARTED` 统一成一个
  `IN_PROGRESS`。
- **`PLAN_APPROVED` 不是状态，是转换**。"计划批准" = `PLANNING → IN_PROGRESS` 这条边。今天的
  `PLAN_APPROVED` 本质是"过了 plan 闸、正在执行" = `IN_PROGRESS`，是被误当成状态的执行态。
- **`PLANNING` 是 `IN_REVIEW` 的镜像**：一个执行前闸、一个执行后闸，都可选、都同构、都带回环。

## 目标状态机

### 枚举（条件命名）

```
PENDING       待办；可带 assignee（scheduled 已指派未开始）
BLOCKED       依赖未满足
PLANNING      plan 闸：成员准备计划 + 待 leader 审批（可选，plan_mode）
IN_PROGRESS   执行中（统一旧 CLAIMED + STARTED + PLAN_APPROVED 的执行态）
IN_REVIEW     verify 闸：成员提交结果 + 待 reviewer 验证（可选）
COMPLETED     终态（验证通过 / 已接受）
CANCELLED     终态
```

### 流程骨架

```
                 ┌─ reject 回环 ─┐        ┌─ fail 回环 → IN_PROGRESS ─┐
                 ↓               │        ↓                          │
PENDING ─(claim/start)→ PLANNING ─(approve)→ IN_PROGRESS ─(submit)→ IN_REVIEW ─(pass)→ COMPLETED
   │                (plan_mode)                    │                        (有 reviewer)
   └──────────(claim/start, build_mode)────────────┘
                                                   └──(complete, 无 reviewer)──────────→ COMPLETED
```

- build_mode 跳过 `PLANNING`；无验证跳过 `IN_REVIEW`。两个闸都是可选的、结构相同的。

### 完整转移表（含事件与触发者）

| From | To | 事件 | 触发者 | 说明 |
|---|---|---|---|---|
| PENDING | IN_PROGRESS | claim | teammate（autonomous·build） | 认领即执行，设 assignee |
| PENDING | IN_PROGRESS | start | 调度器（scheduled·build） | assignee 创建时已定 |
| PENDING | PLANNING | claim | teammate（autonomous·plan） | 进入 plan 闸，设 assignee |
| PENDING | PLANNING | start | 调度器（scheduled·plan） | 进入 plan 闸 |
| PENDING | BLOCKED | （自动） | 系统 | 依赖未满足 |
| BLOCKED | PENDING | （自动） | 系统 | 依赖全 completed |
| PLANNING | PLANNING | submit_plan / reject | teammate / leader | 提交计划、驳回回环（decision 侧信道记录） |
| PLANNING | IN_PROGRESS | approve_plan | leader | **"计划批准" = 这条边**，不是状态 |
| IN_PROGRESS | IN_REVIEW | member_complete_task（任务有 reviewer） | teammate | 成员声明做完 → 交验证 |
| IN_PROGRESS | COMPLETED | member_complete_task（无 reviewer） | teammate | 无验证时直通 |
| IN_REVIEW | COMPLETED | verify_task(pass) | reviewer | 验证通过 |
| IN_REVIEW | IN_PROGRESS | verify_task(fail) | reviewer | **返工回环**，feedback 投给 author（仍持有） |
| PENDING/PLANNING/IN_PROGRESS/IN_REVIEW/BLOCKED | CANCELLED | cancel | leader | 任意非终态可取消 |
| PLANNING/IN_PROGRESS/IN_REVIEW | PENDING | reset | leader | update_task 改内容时重置归属 |

`COMPLETED`/`CANCELLED` 为终态。

### 依赖 / 拒绝集

- `TASK_DEPENDENCY_REJECT_STATUSES`（不可再被加前置依赖）= `{PLANNING, IN_PROGRESS, IN_REVIEW,
  COMPLETED, CANCELLED}`——凡"已在推进"的（planning / 执行 / 验证）+ 终态都拒绝。
- `TASK_TERMINAL_STATUSES` 不变 = `{COMPLETED, CANCELLED}`；`PLANNING`/`IN_PROGRESS`/`IN_REVIEW`
  一律算"活跃/未结算"，团队完成判定自动把它们当未完成。
- 依赖解除 `_refresh_status_in_session` 仍只碰 `PENDING`/`BLOCKED`，不动其余——无需改。

## 两个对称闸

| | plan 闸（执行前） | verify 闸（执行后） |
|---|---|---|
| 状态 | `PLANNING` | `IN_REVIEW` |
| 可选性 | plan_mode 才有 | 有 reviewer 才有 |
| 提交动作（成员） | `submit_plan` | **复用** `member_complete_task`（成员"做完"；有 reviewer → IN_REVIEW） |
| 裁决者 | leader | 专职 reviewer 成员（任务 `reviewer` 列，leader 指派，可多个） |
| 裁决工具 | `approve_plan` | `verify_task`（新，工具内部判 pass/fail，见 §6） |
| 通过 → | `IN_PROGRESS` | `COMPLETED` |
| 不过 → | 留 `PLANNING`（改计划回环） | `IN_PROGRESS`（返工回环，author 一直持有） |
| 决策记录 | plan-index `decision` 侧信道 | 复用同款侧信道（verify verdict / feedback） |

"在等谁裁决"用**侧信道**（plan-index 的 `decision=pending` / event）表达，不为它再多立一个
"已提交待裁决"状态——一个闸一个状态。拆成 4 个闸态（成员在做 / 等裁决 各一个）是完美对称
但过度设计，等真出现第三个闸再抽象出通用 checkpoint。

## reviewer 验证设计

verify 闸的裁决者是**专职 reviewer 成员**（与 leader 审批计划同构，但角色是 reviewer）：

### 数据模型：任务新增 `reviewer` 列

- 任务表加 `reviewer` 列，存 **member 名列表**（nullable）——支持一个任务多个验证者，为未来
  **投票机制**（作为验证控制）预留。列由 **leader 指派**，reviewer 不自主选择。
- 指派入口：`create_task(reviewer=[...])` 与 `update_task(reviewer=[...])`；与 `assignee`（author）
  正交，两个 create_task 形态都可带。校验：reviewer 是真实成员、且 `reviewer ∉ {assignee}`
  （不自审）。

### 谁验证 / 怎么拿到验证任务

- **框架分发**（runtime，类比调度器 `start_task`）：任务进入 `IN_REVIEW` 时，框架把它派发/通知
  给该任务 `reviewer` 列里的成员——reviewer 不轮询、不自主认领验证任务。
- 成员侧看待验证：`view_task(action=in_review)`（返回 `reviewer` 含自己且 `status=IN_REVIEW`
  的任务）。

### 成员"做完" → IN_REVIEW（复用完成工具）

- **复用** `member_complete_task` / `claim_task(status=completed)`：成员声明做完时，若该任务
  **有 reviewer** → 落 `IN_REVIEW`；**无 reviewer** → 直接 `COMPLETED`。成员心智不变（"我做完了"），
  是否进验证由任务的 reviewer 列驱动（team 级能力，per-task 由是否配 reviewer 体现）。

### reviewer 裁决：`verify_task`（新工具）

- `verify_task(task_id, decision, feedback?)`，`decision ∈ {pass, fail}`——**一个工具，内部分支**：
  `pass` → `IN_REVIEW → COMPLETED`；`fail` → `IN_REVIEW → IN_PROGRESS`（打回重做），`feedback`
  定向投给 author。reviewer 不套 plan/verify 闸（它只裁决，不产出可交付物）。
- **多 reviewer（v1）**：`reviewer` 列支持多个，但**投票机制是后续**。v1 先采「首个裁决即生效」
  （任一 reviewer `pass` → COMPLETED，任一 `fail` → 打回）——数据模型已按列表落地，投票逻辑
  后续叠加，不改状态机。
- **author 不释放**：任务从进 `IN_REVIEW` 到 `COMPLETED`/`CANCELLED` 全程仍挂在 author 名下，
  author 的"一活跃"包含 `IN_REVIEW`（见 §一活跃）。故 `fail` 打回 `IN_PROGRESS` 时 author 一直
  持有，无重新获取问题——设计因此更简单。
- 权限：`verify_task` 进 reviewer 的工具集；`view_task(action=in_review)` 亦然。普通 teammate
  不得自验。
- 新事件：`TASK_SUBMITTED_FOR_REVIEW`（→ 框架通知 reviewer）、`TASK_VERIFIED`（pass）、
  `TASK_REVISION_REQUESTED`（fail，→ 通知 author，带 feedback）。

### 一活跃任务集（含 IN_REVIEW）

author 的"一成员至多一个活跃任务" = `{PLANNING, IN_PROGRESS, IN_REVIEW}`（按 `assignee` 查）。
IN_REVIEW 不释放 author，故 author 在等验证期间不领新活（v1 取简单、无并发返工复杂度）。
reviewer 的验证工作量按 `reviewer` 列查，**不受**一活跃约束（可同时有多个待验证任务排队）。

## 命名规约（写进 `tools/AGENTS.md` / `S_12`）

- **状态名描述"条件"，转换名描述"事件"**。
- **跟语义走，不跟后缀走**：主动做 → `-ing`（`PENDING`/`PLANNING`，成员主动在等/在规划）；
  被动被处理 → `in_xxx`（`IN_PROGRESS`/`IN_REVIEW`，任务被执行/被评审）。`REVIEWING`/`VERIFYING`
  会误读成"任务在验证别人"，故用 `IN_REVIEW`。
- **蹭行业词汇**：`To Do / In Progress / In Review / Done`（Jira / GitHub / Linear 标准），
  LLM 与人零歧义秒懂。不追求所有状态同一后缀——追求每个状态用最准、最通用的词。

## 拒绝的方案

- **保留 `CLAIMED`/`STARTED` 两个执行态**（F_58 现状）：拒绝。把"怎么进来的"（认领 / 调度启动）
  刻进了"待在哪"（执行中）。两模式差异属于转换，统一成 `IN_PROGRESS` 后 assignee 字段仍能区分
  "已指派未开始（PENDING+assignee）"与"执行中（IN_PROGRESS）"，语义无损且更通用。
- **保留 `PLAN_APPROVED` 状态**：拒绝。它是"过了 plan 闸正在执行" = `IN_PROGRESS`，多余的状态位。
  去掉后 plan 闸与 verify 闸对称。
- **`RUNNING`/`REVIEWING`（`-ing` 全局统一）**：拒绝。verify 是被动态，`REVIEWING` 误读；且偏离
  行业板卡词汇。
- **分层 / 复合状态机**（active 宏态 + 子状态）：拒绝。为 2 个闸 1 个环引入 HSM 框架是过度设计；
  扁平枚举 + 转移表原生支持回环（就是一条回边）。
- **把每个闸拆成"成员在做 / 等裁决"两个状态**（PLANNING+PLAN_REVIEW / IN_PROGRESS+IN_REVIEW）：
  暂拒。完美对称但状态翻倍；"等裁决"用侧信道足够。留作未来若出现第三闸再统一抽象。

## 与 F_57 / F_58 的关系 + 迁移

### 保留 F_58 的成果（只是把 STARTED 换成 IN_PROGRESS）

F_58 未提交，本设计**取代它的 `STARTED`**，但保留其余全部：
- scheduled `create_task(assignee)` 一律 seed `PENDING(assignee)`（不再 seed CLAIMED）——保留。
- `TeamTaskManager.start_task`（调度器调用）——保留，但目标从 `STARTED` 改为 `IN_PROGRESS`
  （`PENDING(assignee) → IN_PROGRESS`）。事件 `TaskStartedEvent` 保留语义（"开始执行"）。
- 一活跃任务 `get_other_active_task_id`——保留，活跃集扩为 `{PLANNING, IN_PROGRESS, IN_REVIEW}`
  （见 reviewer 设计 §一活跃）。
- plan 死锁的解法——保留（现在走 `PLANNING → IN_PROGRESS`，天然不死锁）。

### 持久化状态字符串迁移

旧值 → 新值：`claimed → in_progress`、`started → in_progress`、`plan_approved → in_progress`
（plan_mode 下 `claimed` 且 plan 未批的应 → `planning`，见下）。`planning` / `in_review` 是新增。

- 动态任务表按 session 分区、团队多为临时——提供一次性值迁移 helper（比照
  `engine._ensure_dynamic_table_indexes` 的迁移入口）。plan_mode 的 `claimed` 细分
  （decision=pending → `planning`，否则 → `in_progress`）尽力而为。
- **建议提交策略**：F_58 尚未提交，若单独提交 F_58 再提交 F_59 反转 `STARTED`，是把一个"引入即
  删除"的状态写进历史，纯 churn。**建议把 F_58 的代码折叠进本次**——从上一个已提交状态（F_57）
  直接走到 F_59 目标，`STARTED` 从不进入 git 历史。**已确认折叠**（见「评审确认的决策」E）。

## 评审确认的决策

| # | 决策 | 落地 |
|---|---|---|
| A | **author 不释放**；任务表加 `reviewer` 列（member 列表，多验证者，为未来投票预留） | 一活跃集 = `{PLANNING, IN_PROGRESS, IN_REVIEW}`（按 assignee）；`reviewer` 列独立 |
| B | 验证**先做 team 级**，per-task 设定后续 | v1：任务配了 reviewer 就走验证；per-task 精细策略留后 |
| C | 独立 reviewer 成员；任务 `reviewer` 字段 leader 指派、可多个；reviewer 非自主，**框架分发**验证任务 | `create_task`/`update_task` 带 `reviewer=[...]`；runtime 在 IN_REVIEW 时派发（类比 `start_task`） |
| D | **复用**成员完成工具 | `member_complete_task`：有 reviewer → IN_REVIEW，无 → COMPLETED |
| E | **折叠 F_58** | 从 F_57（已提交）直接到 F_59，`STARTED` 不进 git 历史 |
| F | reviewer **不套娃** | reviewer 只调 `verify_task`（内部判 完成 / 打回重做），不产出交付物、不进 plan/verify 闸 |

> 遗留到后续的：多 reviewer 的**投票机制**（v1 先"首个裁决即生效"）；per-task 验证策略；
> reviewer 侧是否需要一活跃约束（v1 不约束）。

### Phase 1 实现补充决策：`assign` 感知成员 mode

设计文档未明说 leader `assign` 一个 **plan_mode** 成员的任务应落到哪个状态。若 `assign`
一律落 `IN_PROGRESS`（执行态），会绕过 plan 闸——随后 `approve_plan`（要求源态 `PLANNING`）
无源可批，plan 流程死掉。故 `TeamTaskManager.assign` **感知成员 mode**：指派 plan_mode 成员落
`PLANNING`（进 plan 闸，须先 `approve_plan`），build_mode 成员落 `IN_PROGRESS`（直接执行）。
这与成员自服务侧的分化对称——build 成员 `claim` → `IN_PROGRESS`，plan 成员 `submit_plan` →
`PLANNING`。plan-mode 完成门控（旧"只能完成 PLAN_APPROVED"）由转移表 `PLANNING → COMPLETED`
非法天然兜底，故删除。DAO `claim_task(to_status=...)` 参数化承载 `IN_PROGRESS` / `PLANNING`
两个目标。

## 影响面（改动清单，实现时展开）

- **状态机核心**：`schema/status.py`（枚举重命名 + 转移表重构，加 `PLANNING`/`IN_PROGRESS`/
  `IN_REVIEW`，删 `CLAIMED`/`STARTED`/`PLAN_APPROVED`）、`graph.py`（reject 集）。
- **数据模型**：任务表加 `reviewer` 列（member 名列表 / JSON）——`models.py` + `schema/task.py`
  （`TaskGraphSpec.reviewer` / `NewTaskSpec.reviewer` / `TaskDetail.reviewer`）+ DAO 建行/读列。
- **DAO / manager**：CAS 谓词与状态判断从 `claimed`/`started`/`plan_approved` 改到新值；
  `start_task`/`claim`/`submit_plan`/`approve_plan`/`complete` 的目标态改写；`complete` 按有无
  reviewer 分流 IN_REVIEW / COMPLETED；新增 `verify_task` 后端（pass/fail 分支）；一活跃探针含
  IN_REVIEW；`get_review_tasks(reviewer)` 之类查询。
- **事件**：新增 `TASK_SUBMITTED_FOR_REVIEW`/`TASK_VERIFIED`/`TASK_REVISION_REQUESTED` +
  `_EVENT_TYPE_MAP` + `TaskBoardHandler` 路由。
- **工具 / 权限**：`verify_task` 工具 + reviewer 工具集（`tool_permissions`/`tool_factory`——
  reviewer 角色或按能力挂载）；`create_task`/`update_task` 加 `reviewer` 参数；`view_task` 加
  `in_review` action；描述文案（`locales/descs`）。
- **coordination / runtime**：IN_REVIEW 时框架把验证任务派发给 reviewer（类比调度器）；stale 扫描
  的活跃集、`_is_human_agent_locked`、`_nudge_idle_agent` 的 claimable 过滤同步。
- **prompts**：`leader_policy`/`teammate_policy` 状态流转段重写；reviewer 角色的 policy 段；
  `dispatch_*` 更新。
- **迁移**：动态表状态值一次性迁移 helper（`claimed`/`started`/`plan_approved → in_progress`，
  plan 未批的 `claimed → planning` 尽力而为）+ 新增 `reviewer` 列的 schema 迁移。
- **文档**：`S_12`（状态机主体重写）、`S_08`（不变量 16/19 + 依赖表 + 角色集 + `reviewer` 契约）、
  `tools/AGENTS.md`（命名规约 + verify 工具 + reviewer 列）、`coordination/AGENTS.md`（新事件路由）。
- **测试**：状态机全流程（build / plan / verify / 返工环）、reviewer 裁决（pass/fail）、多 reviewer
  首裁生效、迁移、一活跃含 IN_REVIEW、author 不释放。
