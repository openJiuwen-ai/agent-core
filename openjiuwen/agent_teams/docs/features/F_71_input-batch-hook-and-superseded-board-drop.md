# F_71 输入批次钩子与被覆盖看板的剔除

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-07-31 |
| 范围 | **core**：`single_agent/rail/base.py`（`UserMessageInputs.parts`）、`single_agent/agents/react_agent.py`（`_admit_user_message` 收列表 + `_extract_user_parts`）、`controller/schema/event.py`（`from_user_input` 收 list）；**harness**：`task_loop/task_loop_event_handler.py`（`_extract_query` 合并全部 text frame）、`task_loop/task_loop_event_executor.py`（`_extract_input_parts` + `_input_parts`）；**agent_teams**：`harness/native_harness.py`（follow-up 整批 drain）、`harness/state.py`（`original_query` 可为 list）、`inbound_render.py`（`SNAPSHOT_EVENT_KINDS` / `snapshot_kind_of` / `drop_superseded_snapshots`）、`rails/team_policy_rail.py`（`_drop_superseded`）、`agent/coordination/handlers/task_board.py`（板子改 `use_steer=False`） |
| 测试基线 | `tests/unit_tests/` 13260 passed / 286 skipped / 4 xfailed（唯一失败 `test_execute_javascript_code_success` 在干净树上同样失败，node 输出带 ANSI 颜色码，与本次无关） |
| Refs | #751 |

## 背景

一个 teammate 在忙的时候，框架照样在往它那儿投任务看板。实测上下文里出现了这样一条
steering 消息：

```
[STEERING] <team-event kind="task-board"> ... 1 个任务 ...
           <team-event kind="task-board"> ... 2 个任务 ...
           <team-event kind="task-board"> ... 3 个任务 ...
           <team-event kind="task-board"> ... 4 个任务 ...
```

四条里三条是纯噪声。看板是**全量快照**——第 N 条完整包含前 N-1 条说过的一切，模型只关心
最新那份。而这坨东西不是每轮重算的 attachment，是**写进对话历史永久留存**的（[[F_70]]），
且随任务数增长每条都变长，累计是 O(n²)。

问题还不止于此。板子当时走的是 `deliver_input` 的默认 `use_steer=True`，**打断成员正在
进行的推理**。而本目录 `handlers/AGENTS.md` 的决策维度 3 早就写着「看板巡视——只是提醒，
不该打断正在干的活 → `use_steer=False`」——文档和代码在这一点上不一致，代码是错的。

## 决策

### D1 板子改走 follow-up（`use_steer=False`）

`TaskBoardHandler._nudge_idle_agent` 的投递加 `use_steer=False`。判据就是维度 3：巡视从不
说「你手上的活作废了」，继续干不会白干，没有理由打断。

### D2 follow-up 队列整批消费，不再一条一轮

D1 单独做会**更贵**。follow-up 队列原本是 `pending_follow_ups.pop(0)`——一条一个 round。
5 条排队的板子从「1 轮里 3 条噪声」变成「5 个完整 LLM round」。

根因是两个输入队列语义不对称：steering 队列 drain 出来 `"\n".join` 合并成一次模型调用，
follow-up 队列却一条一轮。这个差异没有任何理由支撑它。所以 `_drain_pending_follow_ups`
改成整批 drain，一起驱动下一轮。顺带修掉一个一直存在的浪费：**任何**攒下来的 follow-up
（异步工具完成回灌、spawn 摘要、用户追问）此前都各烧一轮，而且成员对最旧那条动手时看不见
后面还排着什么。

### D3 钩子拿到的是**输入列表**，不是拼好的字符串

第一版把剔除做成了对拼接后正文的正则抠取（`collapse_snapshot_events(text)`）。**这版被
否掉了**：批次里每条 entry 本来就是一条完整独立的输入，把它们拼成一坨再用正则往回抠，是
先毁掉结构再重建结构。

`UserMessageInputs` 因此由 `message: UserMessage` 改成 `parts: list[str]`，
`AgentCallbackEvent.ON_USER_MESSAGE` 在**拼接之前**触发，rail 直接对列表增删：

```python
parts[:] = drop_superseded_snapshots(parts)   # 整条剔除
parts.insert(0, team_context_text)            # 前插，取代 prepend_to_content
```

`_admit_user_message(ctx, context, parts, *, source, prefix="")` 在钩子返回后才
`"\n".join(parts)` 建 `UserMessage`。`prefix`（`[STEERING] `）不是 part，所以 rail 前插
落在它后面而不是顶掉它。

**`prepend_to_content` 随之删除**——列表模型下前插就是 `insert(0, ...)`，那个处理
`str | list[block]` 两种 content 形态的函数不再有调用者。

### D4 批次以「多个 text frame」的形态穿过 task-loop

难点在 D3 的列表要怎么从 `NativeHarness` 到达 inner `ReActAgent`。中间隔着 task-loop：
`submit_round(query)` → `InputEvent` → `CoreTask.description`（**str**）→ `react_agent.invoke`。
这条链是字符串管道，把 list 硬塞进 `query` 会在 `InputEvent.from_user_input` 抛
`TypeError`，而把整条管道改成收列表要动 description、日志、rail 改写 query 等一大片。

不需要新通道：**`InputEvent.input_data` 本来就是 `List[DataFrame]`，而 `InputEvent` 本身
已经挂在 `CoreTask.inputs` 上随任务走到 executor**。一批排队输入天然就是 N 个 `TextDataFrame`。
于是：

- `from_user_input` 增加 list 分支 → 每条一个 text frame；
- `_extract_query`（handler）把**全部** text frame 合并成 description。原实现 `return` 第一条
  就走人——不改的话这次会静默丢掉除第一条外的所有输入；
- `_extract_input_parts`（executor）从同一个 event 取回未拼接的列表，超过一条时放进
  `effective["_input_parts"]`，与既有的 `_steering_queue` / `_resume_continuation` 同一条 lane；
- `react_agent` 把它收进 `ctx.extra["_input_parts"]`，`_extract_user_parts` 优先用它。

description 仍是拼好的字符串，所以既有的日志、失败重试、pause 缓存全部逐字不变。

### D5 只有「全量幂等快照」可以被剔除

`SNAPSHOT_EVENT_KINDS = frozenset({"task-board"})`。判据是**这一条是否完整覆盖前一条**：

| kind | 可剔除？ | 理由 |
|---|---|---|
| `task-board` | ✅ | 全量巡视，最新一条含全部信息 |
| `roster-change` | ❌ | 增量，丢一条名册就断了 |
| `stale-claim` | ❌ | 带 `task_id`，一条一个任务 |
| `all-done` / `workflow` / `stale-pending` | ❌ | 是事件不是状态 |

集合只有一个元素也要显式存在：它声明的是**语义属性**，让后来想加 kind 的人先回答
「它是不是幂等全量快照」。

`snapshot_kind_of` 用「`strip()` 后以 `<team-event kind="X"` 开头**且**以 `</team-event>`
结尾」判定，即**这条 entry 除了快照什么都没有**。和别的内容拼在一条里的一律不动——剔除的
前提是丢掉它不会连带丢别的东西。（本次归档时带 note 的块以 `</team-note>` 结尾、因而也被这条
判定挡在外面；[[F_72_nested-team-note-inside-annotated-block]] 把 note 改为嵌进块内部后，带
note 的板子重新算「纯快照」并参与剔除——note 只修饰它所在的那块板，板过期它也过期，一起丢
才是对的。）

### D6 只剔 teammate 的板子，leader 保持现状

`TeamPolicyRail._drop_superseded` 在 `role == LEADER` 时直接返回。

两种板子是**不同的东西**：teammate 的板子是「现在能认领什么」的工作队列，只有当前那份
可执行；leader 的板子是全团队未完成工作，而 leader 读的正是**相邻两份之间的差异**——哪个
任务出现了、哪个动了——来决定重规划还是收尾。把中间几份压掉，等于删掉它要看的信号。

## 数据流

```
TaskBoardHandler._nudge_idle_agent
  └─ deliver_input(<team-event kind="task-board">…, use_steer=False)
       └─ harness.send(content, immediate=False)
            └─ RUNNING → loop_controller.enqueue_follow_up(content)

round 结束
  └─ _drain_pending_follow_ups(session) -> ["板1", "板2", "板3"]      # D2 整批
       └─ _start_round(["板1","板2","板3"], is_follow_up=True)
            └─ submit_round(query=list)
                 └─ InputEvent(input_data=[frame1, frame2, frame3])   # D4
                      ├─ CoreTask.description = "板1\n板2\n板3"
                      └─ CoreTask.inputs = [event]
                           └─ executor: effective["_input_parts"] = [...]
                                └─ react_agent: ctx.extra["_input_parts"]
                                     └─ _admit_user_message(parts=[...])
                                          ├─ fire ON_USER_MESSAGE      # D3
                                          │    └─ TeamPolicyRail:
                                          │         parts[:] = ["板3"] # D5/D6
                                          │         parts.insert(0, team_ctx)
                                          └─ UserMessage("\n".join(parts))
```

## 拒绝的方案

- **正则抠取拼接后的正文**（第一版实现）。批次本来就是结构化的 N 条输入，拼完再往回抠是
  先毁结构再重建。钩子改成看列表之后，`_EVENT_BLOCK_RE` 整块删掉。
- **只改 `use_steer=False`、压缩策略照旧**。压缩会变成死代码（每条 follow-up 各成一条
  user message，一批里根本不会出现两条板子），同时 N 条板子变 N 个 round，比改之前更贵。
  D2 是 D1 的必要条件，不是顺手优化。
- **让 `CoreTask.description` / task-loop 全链路收 `list[str]`**。要动 description、日志、
  `TaskIterationInputs.query`、rail 改写 query 等一大片通用管道，且 description 本就该是
  一段可读文本。D4 用已有的 `input_data: List[DataFrame]` 达到同样效果，零新概念。
- **给 round 另开一条 `_input_parts` metadata 通道**（`submit_round(..., input_parts=...)` →
  `event.metadata` → task metadata → executor）。可行，但 `InputEvent` 本来就随 task 走到
  executor、`input_data` 本来就是列表，再造一条平行通道是重复表示。
- **让 `NativeHarness` 自己剔除**。它在 `agent_teams/` 下，物理上够得着
  `inbound_render`，但那会把「哪些 kind 可剔、leader 例外」这类团队策略钉进 harness，而
  策略属于 rail；harness 的铁律 1 也要求它不牵扯 team 语义。
- **在 `TaskBoardHandler` 投递前把队列里已有的板子替换掉**。要 handler 反向伸进
  `harness.loop_controller` 的队列，破坏分层，而且是「替换 pending」这种特例逻辑。
- **把 follow-up[1:] 推进新 round 的 steering 队列**。仍然是两条 user message（query 一条、
  steering 一条），跨消息的剔除做不了。
- **保留 `UserMessageInputs.message` 与 `parts` 并存**。同一份数据两个可变视图，rail 改哪个
  都对、合起来就错；`message` 直接去掉。

## 验证

```bash
source .venv/bin/activate && export PYTHONPATH=.:$PYTHONPATH
```

- `tests/unit_tests/agent_teams/test_inbound_render.py`（15 passed）：`snapshot_kind_of` 认
  纯板子 / 拒非快照 kind、拒纯文本、拒 `<team-inbound>` 正文里被转义的伪标签（带 note 的板子
  当时也被拒，[[F_72]] 之后改为接受，见上）；`drop_superseded_snapshots` 只留最新、不改入参、无覆盖时原样返回、保序保留非快照
  entry；`SNAPSHOT_EVENT_KINDS` 集合本身被钉住（扩集合是正确性决策）。
- `tests/unit_tests/agent_teams/test_team_policy_rail.py`（48 passed）：teammate 三条板只剩
  最新一条；**leader 三条一条不少**；单条板永不丢；非快照 entry 全留且 `[STEERING] ` 前缀
  仍在最前；剔除发生在团队状态前插**之前**（`<team-context>` 在幸存板之前）。
- `tests/unit_tests/core/single_agent/rail/test_on_user_message.py`（7 passed）：rail 就地改
  `parts`（丢空白 entry + 前插）后 `_admit` 拼出的正文；三种 source；默认空批次；
  **每个 `UserMessageInputs` 拿到独立列表**（共享可变默认值会让上一批的编辑漏进下一批）。
- `tests/unit_tests/agent_teams/harness/test_state_transitions.py`（7 passed）：新增
  `test_follow_ups_queued_together_drive_one_round` —— q2/q3 同批排队 → **一个** round、
  query 为 `"q2\nq3"`、`_input_parts == ["q2", "q3"]`，且单条输入的那一轮**不带**
  `_input_parts`。
- `tests/unit_tests/harness/test_deep_agent_rail_event_routing.py`（3 passed）：[[F_70]] 留下的
  路由完整性断言仍绿（`ON_USER_MESSAGE` 必须在 `_BRIDGE_EVENTS` 里，否则回调挂到外层 agent
  永不触发）。
- 全量 `tests/unit_tests/`：13260 passed / 286 skipped / 4 xfailed。唯一失败
  `test_execute_javascript_code_success` 经 `git stash` 复验在干净树上同样失败（node 输出带
  ANSI 颜色码），与本次无关。

**端到端尚未复验**：需要真跑一次团队，看忙碌 teammate 被唤醒时的上下文里只剩一条
`<team-event kind="task-board">`，且它落在 follow-up 轮而不是打断当前轮。

## 已知遗留

- **跨消息不剔除**。只在「同一批还没写进历史的输入」里做减法。上一轮已经写进历史的板子
  留在原地——改写历史会让它之后的 KV cache 全部作废，且与 [[F_70]] 的「只插入，不删除、
  不重写」直接冲突。代价是长会话里仍会沉淀多份历史板子，真要收就该由上下文压缩路径去做。
- **leader 的板子仍会攒**。D6 是有意的，但 leader 一轮里读五份全量板子确实贵。真正的解法是
  让 leader 侧的板子事件在**渲染前**先合流（一个投递窗口只渲染一次），而不是渲染完再剔除。
- **`InputEvent` 的 list 分支只有 team harness 在用**。`Runner` 的单 agent 入口仍只传单条
  query，`base.py` 两个 `from_user_input` 调用点未接列表。不是缺陷，只是这条能力目前单点消费。
- **`_extract_input_parts` 在 rail 改写过 query 时主动放弃**（`iter_inputs.query == query` 才
  透传）。改写过 query 说明这一轮跑的已经不是原批次，parts 与 query 不再是同一份内容；宁可
  退回单条，不做不一致的猜测。
