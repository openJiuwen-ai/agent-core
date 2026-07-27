# 调度模式 runtime + 评审投票

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-07-10 |
| 状态 | **已实现**（v2 改为"调度器以 leader 身份消息驱动交接" + 轮数上限升级；v3 增加无票停摆超时升级；v4 终稿：dispatch_mode 回归静态 spec 配置、build_team 不选协调模式、提示词/工具描述按模式清晰分离；演进见 §实现补充） |
| 测试基线 | `python -m pytest tests/unit_tests/agent_teams` → **1878 passed / 16 skipped**（净新增 36 例：`test_review_voting.py` 11、`agent/test_team_scheduler.py` 13、`agent/test_mode_handlers.py` 5、`test_dispatch_choice.py` 5、`test_database.py` 迁移 2） |
| 范围 | `agent/scheduling/`（新包）、`agent/coordination/`（kernel 接线 + 模式 handler 类）、`tools/`（verify_task 投票分流与 desc_key 形态、scheduled create_task 轮数参数、build_team 验证开关、task_manager/DAO/models 投票与轮数存储）、`schema/`（配置字段 + 事件）、`i18n.py`、`prompts/`、`docs/specs/S_03·S_08·S_12·S_22` |
| Refs | #751 |
| 关系 | 落地 F_59 的两个「遗留到后续」：**runtime 调度器**（S_12 明言 "`start_task` 何时发生属 runtime 调度器职责"）与**多 reviewer 投票机制**（S_08 不变量 20 "v1 首个裁决即生效，投票机制留后"）；复用 F_57 工具形态框架 |

## 背景

F_59 交付了条件命名状态机 + 两个对称闸（plan / verify），并为调度模式铺好了全部静态管线：

- `TeamAgentSpec.dispatch_mode: Literal["autonomous", "scheduled"]`（`schema/blueprint.py:282`）
  已贯穿 rails → 工具变体（`tool_factory.py` 三张形态表）→ 双端提示词
  （`dispatch_scheduled_{leader,teammate}.md`，已向 LLM 承诺"调度框架会自动通知并拉起对应成员"）。
- `TeamTaskManager.start_task`（`task_manager.py:1320`，`PENDING(assignee) → IN_PROGRESS`，
  CAS + 一活跃探针 + 幂等重入）与 `TASK_STARTED` 事件（`schema/events.py:255`）齐备。

但**全树没有任何代码调用 `start_task`**——调度器是"文档里已命名、代码里未出生"的缝。同时
verify 闸的"首裁即决"是结构性的：首个裁决把任务带离 `IN_REVIEW`，第二个 reviewer 的
状态守卫直接失败（`task_manager.py:683`），全树无票据存储。

## 核心洞察

四条，决定了整个设计的形状：

1. **调度器不需要理解事件，只需要理解看板。** 状态机已把"解锁"编码成 `BLOCKED → PENDING`
   自动翻转、把"占用"编码成一活跃探针。所以调度器的核心是一对**幂等的扫描函数**（开工 /
   验票）：任何事件都只是"看板可能变了"的唤醒提示，`POLL_TASK` 和激活扫描是同一对函数的
   兜底触发。不存在 per-event 分支，崩溃恢复免费。
2. **交接轮转 = leader 身份的定向消息，投递即启动。** 任务状态交接的每个节点（开工、送审、
   打回、通过），由调度器渲染专用提示词、**以 leader 身份**经邮箱发给目标成员。这条通道
   免费获得三样东西：消息持久化（成员离线不丢，重启后邮箱 sweep 补投）、**幂等懒启动**
   （复用 `send_message` 既有的 `_auto_start_members` → `startup_member` 的
   `UNSTARTED→STARTING` CAS，已启动成员自然跳过）、与人发消息完全一致的成员侧接收路径
   （`MessageHandler` 零改动）。成员之间**不**直接 `send_message` 交接；F_59 Phase 2 的
   verify 闸事件自反应（`task_board.py:279/314/346`）收窄为自主模式专用。
3. **票是事实，判定是策略。** reviewer 的票（谁、哪个任务、哪一轮、pass/fail、feedback）
   落 DB 追加写，永不丢失；"够不够票"是策略（v1 阈值制，未来可换加权/一票否决/LLM 仲裁），
   活在 leader 调度器里可替换。工具只记事实，调度器只做判定。
4. **leader 是升级点，不是转发器。** 常规轮转不打扰 leader（"不过度让 leader 参与"）；
   只有两类信息注入 leader 的输入流：**必须它决策的**（评审轮数耗尽的升级）与**它需要感知
   的结果**（任务终态一行摘要、全部完成的收尾总结）。自主模式那种"每个 board 事件都唤醒
   leader 巡视全板"在调度模式下关闭。

## 设计

### 1. 新包 `agent/scheduling/`（与 coordination 平齐）

```
agent/scheduling/
├── __init__.py      # 导出 TeamScheduler
├── scheduler.py     # TeamScheduler：事件粗筛 + 开工扫描 + 验票扫描（judge/settle/升级）
├── verdict.py       # 纯函数投票判定：tally + threshold 数学，无 IO
├── render.py        # 交接消息渲染（开工/送审/打回/通过/升级/摘要），文案走 i18n
├── AGENTS.md        # 模块契约
└── CLAUDE.md        # @AGENTS.md 壳
```

coordination 的铁律"不做决策"保持不破——**决策全部在新包**。两者关系：

```
messager ─→ EventBus ─→ wake_callback
                          ├─ 1. EventDispatcher.dispatch   （coordination：唤醒/渲染，不决策）
                          └─ 2. TeamScheduler.on_event     （scheduling：决策 + leader 身份消息编排）
```

- `CoordinationKernel.setup(role=LEADER)` 时构造 `TeamScheduler`（休眠态）；`start()` 把
  wake_callback 组合为"先 dispatch 后 schedule"。teammate 进程不构造调度器——**调度器只有
  leader 有**。
- 激活条件 = `role == LEADER` 且 team 已建成且生效 dispatch_mode == "scheduled"。两个激活点：
  `build_team` 成功回调 `on_team_built`（`tools/team.py:213`，已有挂点）与 `kernel.start`
  恢复路径（team 行已存在——warm resume / 冷恢复）——调度器随 kernel 自动复位，激活先于任何
  teammate spawn。调度器仅在 `spec.dispatch_mode == "scheduled"` 的 leader kernel 上构造。
- 依赖面：`infra.task_manager`（状态翻转）、"leader 身份发消息 + 懒启动"原语（自
  `tool_message` 下沉到 `TeamBackend`/`message_manager`，与 `send_message` 工具共用同一实现）、
  `AgentRoundController.deliver_input`（**仅限注入 leader 自身**：升级与摘要；对 teammate
  只走邮箱，永不直投）。
- 生命周期随 kernel：无后台任务（搭 EventBus 循环便车），pause/stop 即失活。

#### TeamScheduler.on_event —— 两个幂等扫描

任何任务/成员 transport 事件、`POLL_TASK` tick、激活时刻，触发同一对扫描：

**开工扫描（reconcile）**：对每个成员——
- 无活跃任务（`{PLANNING, IN_PROGRESS, IN_REVIEW}`）且名下有 `PENDING(assignee=member)` →
  取最早创建的一个 → `task_manager.start_task(task_id)`（CAS，并发/重复触发无害）→
  成功后渲染**开工消息**（任务要点 + 验收标准 + "view_task 看全貌"；plan_mode 成员则为
  "先 submit_plan"），以 leader 身份 DM 给 assignee——投递路径自动把未启动成员拉起
  （`UNSTARTED→STARTING` CAS 幂等，已启动则只是一封邮件）。**成员是否已启动不构成开工
  前置条件**，调度器不做在线过滤。
- plan_mode 成员：`start_task` 感知成员 mode（镜像 F_59 Phase 1 `assign` 的补充决策），
  落 `PLANNING`；DAO `start_task` 加 `to_status` 参数（同 `claim_task` 参数化先例）。
  `TASK_STARTED` 事件保留（board 观测真相），不再承担成员唤醒职责。

**验票扫描（judge/settle/escalate）**：对每个 `IN_REVIEW` 任务——
- **送审派发**：当前轮（`review_round`）尚未派发过 → 渲染**评审请求**（任务要点 + author
  完成说明 + "调 verify_task 投票"），以 leader 身份逐个 DM 给 `reviewer` 列成员（同样
  自动拉起未启动 reviewer）。派发去重按 `(task_id, round)` 内存记账；崩溃后重发一次无害。
  本轮开启超过节流窗口（10min）仍未集齐票 → 对未投票 reviewer 重发提醒。
- **判定**：读当前轮的票（每 reviewer 取最新一票），`verdict.judge(votes, reviewers,
  threshold)` 三值：
  - `pass_count ≥ ceil(threshold × n)` → **settle pass**：`task_manager.settle_review(pass)`
    （内部复用 `complete_task`：`IN_REVIEW → COMPLETED` + 解依赖 + 发 `TASK_VERIFIED`）；
    随后 DM author"验证通过，请 `send_message` 向 leader 汇报结果与产物"，并注入 leader
    一行终态摘要。
  - pass 不可达（`fail_count > n − ceil(threshold × n)`）且 `review_round <
    max_review_rounds` → **settle fail**：`settle_review(fail, feedback=聚合 fail 票)`
    （内部 `revise_task`：`IN_REVIEW → IN_PROGRESS` + 发 `TASK_REVISION_REQUESTED`）；
    随后 DM author 返工消息（任务信息 + 聚合 feedback + 轮次 r/N）。
  - pass 不可达且 `review_round ≥ max_review_rounds` → **升级**：任务**留在
    `IN_REVIEW`**（不再自动打回），渲染升级消息（任务、N 轮未过、各轮票与 feedback 摘要、
    可选处置：`update_task` 改派/改 reviewer、reset、cancel、增删成员）**直接注入 leader
    作为 user message 输入**，由 leader 调整任务规划与成员配置。升级按 `(task_id, round)`
    去重；leader 处置（reset/reassign/cancel 均覆盖 `IN_REVIEW` 源态）后看板事件自然触发
    下一轮扫描。
  - 未定且本轮开启已超 `review_stall_timeout`（评审确认新增）→ **停摆升级**：与轮数升级
    同一注入路径与去重（reason 标"评审停摆"，附当前票况与未投票 reviewer 名单），任务留
    `IN_REVIEW` 由 leader 处置——覆盖"reviewer 整轮无票，轮数机制永不触发"的死角。
    轮开启时刻即任务行 `updated_at`（status 生命周期语义，进 `IN_REVIEW` 时已 bump，
    不加新列）。
  - 其余 → 未定，等下一票。
- settle 的状态 CAS（源态必须 `IN_REVIEW`）+ 单判定者（leader 单事件循环）保证不双结算；
  投票落库与判定之间崩溃由激活/`POLL_TASK` 扫描兜底补判。
- 单 reviewer 在 2/3 阈值下数学退化：一票 pass 即完成、一票 fail 即打回——与自主模式现
  行为一致。

**leader 感知**（洞察 4）：调度器注入 leader 的输入仅三类——任务终态一行摘要
（COMPLETED/CANCELLED）、升级消息、全部任务终结后的收尾总结（挂 `TASK_LIST_DRAINED`，
接续既有 all-done/`TeamCompletionHandler` 收尾语义）。调度模式下 `TaskBoardHandler` 的
leader 全板巡视 nudge 关闭（见 §4）。

### 2. 模式配置：dispatch_mode 是静态 spec 配置（评审终稿）

**`build_team` 不选择协调模式**。`TeamAgentSpec.dispatch_mode` 在构建期决定一切，系统提示词
与工具描述**按模式各一套、互不混写**：

- **提示词**：`dispatch_autonomous_*` / `dispatch_scheduled_*` 模板按 spec 模式装配
  （既有 F_57 管线），调度版 leader 模板只讲调度契约（assignee 必填、框架代办交接、
  reviewer/轮数指引、升级处置），不含任何"另一种模式"的说明。
- **工具**：`create_task` 两个形态照旧构建期查表（autonomous 无 `assignee`；scheduled
  `assignee` 必填 + 可选 `max_review_rounds`）；`verify_task` 语义按模式二分，**描述随语义
  分离**（desc_key 形态：`verify_task` 首裁即决 / `verify_task_scheduled` 投票记录，照
  `member_complete_task` 的 `_MEMBER_COMPLETE_DESC_KEY` 先例）。
- **运行时消费**：`TeamBackend.dispatch_mode` / `TeamTaskManager._dispatch_mode` 直接持
  spec 值（每个进程相同，来自 spec 或其 `TeamSpec` 镜像）；调度器仅在
  `spec.dispatch_mode == "scheduled"` 的 leader kernel 上构造；coordination 的模式 handler
  类按 spec 值在 dispatcher 构造期装配。`team_info.dispatch_mode` 列保留为**记录**（建团队
  时写 spec 值，供观测/外部工具读），运行时真相始终是 spec。
- **build_team 的运行时开关**只剩 `enable_hitt` 与 `enable_task_verification`（提示词驱动
  的"验证预期"开关，沿 `enable_hitt` 的 ceiling/override 范式，效果值随行记录）。

### 3. 评审投票：票据落 DB（追加写），判定在调度器

#### 数据结构

- **新动态表 `team_task_review_vote_{session}`**（与任务表同款 per-session 动态建表 +
  `engine._ensure_dynamic_table_indexes` 迁移入口）：

  ```
  id          INTEGER PK AUTOINCREMENT   # 追加写，天然无并发冲突
  team_name   FK
  task_id     TEXT                       # 复合索引 (task_id, round)
  round       INT                        # 对应任务行 review_round
  reviewer    TEXT
  decision    TEXT                       # pass | fail
  feedback    TEXT NULL
  created_at  BIGINT
  ```

  纯 INSERT（"高效插入"）：改票 = 再插一行，tally 取每 reviewer 最新一票；全量留痕可审计。
- **任务行加两列**：`review_round INT DEFAULT 0`——`submit_for_review` 的 DAO CAS 在同一条
  UPDATE 里 `status → in_review, review_round += 1`，开新轮与状态翻转原子，旧轮票自然作废；
  `max_review_rounds INT NULL`——per-task 评审轮数上限，`create_task`/`update_task` 可设，
  NULL → 用 spec 默认。

#### verify_task 分流（同一工具，按 spec 模式选策略，描述随语义分离）

守卫不变（任务 `IN_REVIEW`、caller ∈ reviewer 列、caller ≠ author）：

- **生效 scheduled**：只记事实——INSERT 票 + 发新事件 `TASK_REVIEW_VOTE`（payload:
  task_id、reviewer、decision、票数摘要），**不翻转状态**；工具返回"票已记录（pass 2/3，
  等待判定）"。判定/翻转/后续轮转由 leader 调度器完成（§1）。同一 reviewer 重复调用 =
  改票，幂等无错误路径。
- **生效 autonomous**：维持现状首裁即决（`_verify_pass`/`_verify_fail` 直接翻转）——
  自主模式没有判定者组件，S_08 不变量 20 改写为按模式二分。

`task_manager` 新增 leader 侧 `settle_review(task_id, decision, feedback="")`：无 reviewer
资格守卫（调用方是框架不是成员），状态 CAS `IN_REVIEW` 起跳，内部完全复用
`_verify_pass`/`_verify_fail` 的事件与解依赖路径。

#### 配置

- **`TeamAgentSpec.verify_vote_threshold: float = 2/3`**（校验 `0 < t ≤ 1`）。判定只在
  leader 调度器，**不需要**镜像 `TeamSpec` 跨进程。
- **`TeamAgentSpec.default_max_review_rounds: int = 3`**：per-task 未设时的轮数上限默认值。
  同样只在 leader 侧消费。
- **`TeamAgentSpec.review_stall_timeout: int = 1800`**（秒）：单轮评审停摆升级阈值
  （§1 停摆升级）。10min 的未投票 reviewer 重发提醒沿用 stale 常量风格做包内常量，不进 spec。
- **`TeamAgentSpec.enable_task_verification: bool = False`**：团队级"强制任务校验"开关
  （类比 plan 闸的 team 级形态）。纯提示词驱动——开启时 leader 的任务创建指引要求按判断
  为每个任务指派 0~N 个 reviewer（不做硬校验）；进 `CapabilityOverrides` 允许 build_team
  时显式覆盖。reviewer 指派机制本身（列、校验、不自审）沿用 F_59 Phase 2，零改动。

### 4. coordination 成员侧：各模式拥有自己的 handler 类（评审二轮修正）

调度模式下成员的任务流转唤醒**一律来自 leader 身份的调度器消息**（走既有
`MessageHandler`，零改动）。模式差异**不写进共享 handler 的 if 分支**，而是各模式一对
handler 类，选择只发生在装配点：

- `TaskBoardHandler` / `StaleTaskHandler` 保持自主模式原实现（零模式分支）。
- `ScheduledTaskBoardHandler(TaskBoardHandler)`：`EVENT_METHOD_MAP` 把 verify 闸三事件
  （submitted_for_review / verified / revision_requested）与 `TASK_REVIEW_VOTE` 全部路由到
  重写后的 `on_task_board_event`（resume_polls-only——自反应会与调度器 DM 双投递；leader
  感知走调度器摘要/升级/收尾，claimable 池不存在）。TASK_CLAIMED/REVOKED/CANCELLED/UPDATED
  的定向 steer 继承（`update_task` 两模式同义）。`TeamCompletionHandler` 模式无关，照旧。
- `ScheduledStaleTaskHandler(StaleTaskHandler)`：重写 `on_poll_task` 的**组合点**——只扫
  自己的 stale 活跃任务（漏投递兜底），不做 leader stale-pending 自催（排队中的
  `PENDING(assignee)` 是常态）。
- 选择机制：`EventDispatcher(dispatch_mode=...)` 构造期查 `_TASK_BOARD_CLASS` /
  `_STALE_TASK_CLASS` 字面量表（F_57 同款，未知值 KeyError）——dispatch_mode 是静态 spec
  配置，每个角色相同、运行期不变，全部 handler 一次装配注册。

## 拒绝的方案

- **事件广播 + 成员侧自渲染开工/送审/打回**（v1 设计稿方案）：拒绝（评审意见 1/2/3）。
  它把成员唤醒寄托在"事件恰好被在线进程收到"上——未启动成员收不到 `TASK_STARTED`，还得
  另做在线过滤 + `MEMBER_SPAWNED` 补偿；而 leader 身份邮箱消息天然持久化 + 幂等懒启动
  （`_auto_start_members` 现成），成员侧零新代码。事件降级为 board 观测真相。
- **调度器内存聚票**：拒绝。leader 重启丢票、并发到达二次消歧。票落 DB 追加写 + 调度器
  只做判定——评审确认（决策 A）。
- **投票判定放进 verify_task 工具事务**：拒绝。判定策略要可替换（阈值 → 加权/否决/仲裁），
  烧进工具事务就焊死了；且 leader 侧判定天然单点串行。代价是"最后一票 → 完成"多一跳
  事件延迟，可接受（决策 A）。
- **超限自动打回继续磨**：拒绝。轮数耗尽说明任务规划或人员配置有问题，机械返工只是烧 token；
  留 `IN_REVIEW` 升级给 leader 调整（决策 E）。
- **build_team 动态选择协调模式（spec 作 ceiling）**：中期方案，评审终稿拒绝。为了让 leader
  在构建期之后还能二选一，工具与提示词必须同时兼容两套契约（统一形态 create_task 超集
  schema + invoke 期按生效模式校验、双契约提示词、生效值持久化/恢复/载荷覆盖一整条管线），
  系统提示词和工具描述都变得含混——描述是行为契约，一份契约里写两种行为就是没有契约。
  模式回归 spec 静态后这条管线整体删除，每套模式的提示词/描述干净自洽。
- **调度器做成 coordination 第 8 个 handler**（`ReliabilityHandler` 先例）：拒绝。调度器是
  决策引擎，塞进 coordination 违反其四铁律第 1 条。平齐新包 + kernel 组合回调，边界干净。
- **运行时热切换 dispatch_mode（build_team 之后再改）**：本次不做。生命周期中途换模式的
  看板语义（存量 `PENDING(assignee)` 与 claimable 池互转）没想清楚，等真实需求。
- **每事件精确增量调度**：拒绝。看板是会话级小表，全量扫描一次两个索引查询的量级；增量
  逻辑引入 per-event 分支与漏派风险，违背洞察 1。
- **票表 (task, reviewer) 唯一约束 + UPSERT**：拒绝。追加写 + 取最新一票已覆盖改票语义，
  还白得审计留痕。

## 评审确认的决策

| # | 决策 | 落地 |
|---|---|---|
| A | 票据**落 DB**（高效追加写），**判定在调度器**（策略可替换，不焊死阈值制） | 票表 + `review_round`；`verify_task`（scheduled）只 INSERT + 发事件；`TeamScheduler.judge/settle` 持阈值策略 |
| B | **（终稿推翻中期方案）dispatch_mode 是静态 spec 配置，build_team 不选择协调模式**；系统提示词与工具描述按模式各一套、清晰分离，不做双模式兼容混写 | spec → rails/工具/提示词构建期装配；`verify_task` desc_key 形态分离；调度器仅在 scheduled spec 的 leader 上构造；`team_info.dispatch_mode` 列仅为记录 |
| C | **交接轮转由调度器以 leader 身份的邮箱消息驱动**：开工/送审/打回/通过四类节点都是"渲染专用提示词 + leader 身份 DM"；成员间不直接 send_message 交接；成员侧 verify 闸事件自反应收窄为自主模式 | §1 扫描内嵌 DM 编排；§4 门控；send+autostart 原语自 `tool_message` 下沉共用 |
| D | **投递即启动，启动幂等**：调度器不做在线过滤，DM 路径复用 `_auto_start_members` 的 `UNSTARTED→STARTING` CAS，已启动成员跳过 | §1 开工/送审派发 |
| E | **评审轮数上限**：per-task `max_review_rounds`（create_task 设，默认 `spec.default_max_review_rounds = 3`）；超限不再自动打回，任务留 `IN_REVIEW`，升级消息**直接注入 leader** 作 user message，由 leader 调整任务规划与成员配置 | §1 验票扫描 escalate 分支 + §3 两列 |
| F | **评审停摆超时也升级 leader**：单轮开启超 `review_stall_timeout`（默认 1800s）仍未产生判定（含整轮无票），与轮数升级同路径注入 leader，附票况与未投票名单 | §1 验票扫描停摆分支；轮开启时刻复用任务行 `updated_at` |
| G | 配置命名/默认值确认：`enable_task_verification = False`、`verify_vote_threshold = 2/3`、`default_max_review_rounds = 3`、`review_stall_timeout = 1800` | §3 配置 |

## 实现补充（与设计稿的三处偏差）

1. **消息原语不需要下沉**：设计稿计划把 "leader 身份 DM + 懒启动" 从 `tool_message` 下沉到
   backend；实现发现两个既有原语组合即可——`infra.message_manager.send_message`（leader 进程
   的 manager 天然以 leader 身份发）+ `TeamAgent.auto_start_member`（interact dispatch 层已有
   的 `UNSTARTED→STARTING` CAS 懒启动）。`tool_message._auto_start_members` 保持原样，零重构。
2. **leader 自发事件的可见性**：coordination 的 `_filter_self` 会丢弃 leader 自己发布的事件
   （create_task / settle 产生的 `TASK_CREATED` / `TASK_VERIFIED` / `TASK_UNBLOCKED` /
   `TASK_LIST_DRAINED`），调度器天然看不到。解法：`_filter_self` 丢弃 self `task_*` 事件时改投
   `InnerEventType.SCHEDULER_SCAN`（新 inner 事件，coordination 无 handler 监听），调度器把它
   当纯扫描提示；配合扫描的有界不动点循环（`_MAX_SCAN_PASSES`），settle 解锁的后继任务在同一
   次扫描内开工。副作用：leader 本地 settle 导致的看板排空不会经 `TASK_LIST_DRAINED` 事件到达，
   收尾摘要由 `_digest_task_done` 的 remaining==0 分支直接兜底。
3. **编号**：本特性设计期编号 F_60，与并行落地的 `F_60_native-harness-pause-abort-resume` /
   `F_61_cold-resume-across-stop-start` 冲突，改号 F_62。
4. **handler 模式差异从 if 门控改为模式子类，且看板翼与团队同生**（评审二、三轮意见）：
   首版实现把调度模式差异做成共享 handler 内的 `if _scheduled_dispatch()` 分支——把"怎么
   进来的"刻进了共享代码路径；二版改为模式子类但 leader 构造期先装 autonomous 默认、
   确认后再 set——把谎言写进装配序。终版为 §4 的结构：自主类原样、调度类重写组合点、
   看板翼**没有占位默认**——成员构造期按生效模式装配，leader 构造期 `task_board` /
   `stale_task` 为 None，团队物化点挂载（后续经评审四轮改为 framework 增量注册）。
   kernel / EventBus 本体不能推迟到 build_team 之后——leader 的首条用户输入经
   `enqueue_user_input → USER_INPUT` 走同一循环投递，先有循环才有调用 build_team 的那轮。
   物化挂载与增量注册机制随第五轮的静态化一并删除（模式构造期已知，无需延迟装配）。
5. **模式回归 spec 静态配置**（评审五轮意见，终稿）：中期的"build_team 动态选择 + 生效模式
   管线"（ceiling/生效值分裂、`CapabilityOverrides.dispatch_mode`、载荷覆写、恢复回填、
   物化挂载/增量注册、统一形态 create_task、双契约提示词）整体删除，回归"spec 决定一切、
   构建期装配、每套模式一份提示词/描述"。留下的增量：`verify_task_scheduled` desc_key
   形态、调度器仅在 scheduled spec 的 leader 上构造、`team_info` 列降级为记录。
   测试基线更新为 **1878 passed / 16 skipped**（净新增 36 例）。

## 影响面（改动清单，实现时展开）

- **新包**：`agent/scheduling/`（`scheduler.py` + `verdict.py` + `render.py` + AGENTS/CLAUDE）。
- **kernel / agent**：`setup` 构造调度器（leader）、`start` 组合 wake_callback + 恢复激活、
  `on_team_built` 激活挂线（`team_agent.py` 回调注册处）。
- **消息原语**："leader 身份 DM + 懒启动"从 `tool_message` 下沉到 `TeamBackend` /
  `message_manager`，`send_message` 工具与调度器共用同一实现。
- **数据**：`models.py` 票表基类 + 任务行 `review_round`/`max_review_rounds`；`engine.py`
  动态表迁移（建票表 + ALTER 加列）；`task_dao.py` `insert_review_vote` / `get_review_votes` /
  `submit_for_review` 带 round 自增 / `start_task(to_status=...)`。
- **manager**：`verify_task` 按生效模式分流；新增 `settle_review`；`start_task` 感知成员
  mode 落 `PLANNING`/`IN_PROGRESS`。
- **事件**：`TASK_REVIEW_VOTE` + payload + `_EVENT_TYPE_MAP` + board 观测路由。
- **工具/权限**：`CapabilityOverrides.dispatch_mode` + `enable_task_verification`；
  `BuildTeamTool` schema 加参；统一形态 create_task（上限 scheduled 的 leader，含
  `max_review_rounds`）；`update_task` 加 `max_review_rounds`；`verify_task` 描述更新
  （两种模式语义）；locales 文案。
- **backend**：`TeamBackend` 生效 dispatch_mode 字段 + 持久化/恢复（照 `_enable_hitt`）；
  `SpawnPayloadBuilder` 读生效值。
- **schema**：`TeamAgentSpec.verify_vote_threshold` / `default_max_review_rounds` /
  `review_stall_timeout` / `enable_task_verification`；`dispatch_mode` docstring 语义升级。
- **coordination**：`task_board.py` 三组门控（§4）；`i18n.py` 调度器消息文案。
- **prompts**：`dispatch_scheduled_leader.md` 重写（模式判据 + 双契约 + 升级处置指引）；
  `dispatch_scheduled_teammate.md` 微调（"开工/返工/通过通知以 leader 消息形式到达"）。
- **docs**：新 `S_22_scheduling-runtime.md`（调度器契约：触发集、双扫描不变量、DM 编排、
  与 coordination 的边界）；`S_03`（kernel wake_callback 组合 + 生命周期挂点 + 成员侧
  门控）；`S_08`（不变量 18/20 改写：create_task 统一形态、verify 按模式二分、build_team
  参数）；`S_12`（票表 + 任务行两列 + `TASK_REVIEW_VOTE`）；`tools/AGENTS.md`、
  `agent/AGENTS.md`、新包 AGENTS。
- **测试**：`verdict.judge` 纯函数全分支；调度器开工扫描（一活跃/最早优先/幂等/plan 闸
  落点/DM 编排与懒启动）与验票扫描（阈值边界、不可达早判、轮数上限升级、派发去重、崩溃
  补判、停摆重发）；投票 DAO（追加写、改票取最新、round 隔离）；`verify_task` 双模式分流；
  build_team 参数校验 + 生效值持久化恢复 + spawn 载荷覆盖；task_board 门控；迁移用例。

## 测试基线

实现后跑 `python -m pytest tests/unit_tests/agent_teams`（F_59 后基线 1800 passed / 16
skipped），新增用例在其上只增不减。
