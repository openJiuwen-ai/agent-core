# autonomous 团队的 leader 照着共享文案向 create_task 传 `member`，字段被静默丢弃、任务无主落板

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-15 |
| 严重程度 | 中：autonomous 团队的 leader 以为建了成员，实际一个都没建；任务进公共看板后永远无人认领，leader 还向用户汇报"成员已创建" |
| 范围 | `agent_teams/tools/tool_task.py`（`TaskCreateTool.invoke` / 两个 schema 的参数 key）、`agent_teams/tools/tool_member.py`（`ShutdownMemberTool`）、`agent_teams/tools/tool_factory.py`、`agent_teams/tools/locales/{cn,en}.py`、`agent_teams/tools/locales/descs/{cn,en}/member/shutdown_member.md`、`agent_teams/tools/locales/descs/{cn,en}/fragments/shutdown_member_lifetime.md`（新增） |
| 测试基线 | `pytest tests/unit_tests/agent_teams/ --ignore=cli --ignore=observability` → 3592 passed / 36 skipped / 3 xfailed；另 1 failed（`agent/test_member_activity.py::test_observe_member_status_emits_after_the_debounce`）在未改动的 upstream/develop 基线上同样失败，与本修复无关 |
| 关联 spec | `S_08_team-tools-contract.md`（不变量 25：autonomous 形态对 `member` 的处置从"不改"改为"拒绝"；参数描述命名空间） |

## 现象

jiuwenswarm 的一个 web session（`dispatch_mode == "autonomous"`）里，用户要求"创建 5 个休眠成员、
建 5 个串行任务"。leader 的轨迹：

1. 读到工具文案里"调度模式下也可以是本次调用中其他任务用 `member` 内联声明的成员"与
   "`create_task` 内联且 `lifetime=dormant` 的成员落 DORMANT"，推断 `create_task` 有一个
   `member` 字段可以顺手建人。
2. 前两次调用同时带 `assignee` 与 `member`，被 `_validate_assignees` 以 "member not found"
   拒绝。
3. leader 用 `grep lifetime|dormant` 搜团队工作区，找到物化在 `team-workspace/prompts/tool/fragments/`
   里的 `create_task_inline_member.cn.md`，按其格式去掉 `assignee` 只留 `member` 重试。
4. 第三次调用**成功**：5 个任务落板、`assignee` 全为 `None`；`team_member` 表里只有 team-leader。
   leader 随即向用户汇报"5 名成员以 `create_task` 内联 `lifetime=dormant` 方式创建"。

## 根因

F_118 把任务内联成员限定在 scheduled 形态，并声明"autonomous 下工具、提示词、handler 零改动"。
但有三处文案 / 行为没有按形态分开，合起来构成一条让模型走进死路的引导链：

1. **参数描述表是两种形态共用的。** `STRINGS["create_task.task.assignee"]` 一条字符串同时服务
   `_autonomous_task_node_schema` 与 `_scheduled_task_node_schema`，正文里写着"调度模式下也可以是
   …用 member 内联声明的成员"。autonomous 的 leader 在自己的工具 schema 里读到了一个它没有的字段。
2. **`shutdown_member` 的描述不分形态**地讲"`create_task` 内联且 `lifetime=dormant` 的成员落
   DORMANT"。用户的"休眠成员"诉求正好撞上这句话，leader 把它当成官方路径。
3. **`TaskCreateTool.invoke` 对未知的 `member` 字段静默忽略。** 它只读
   title / content / task_id / depends_on / depended_by / assignee；`member` 既不在 schema 里，也不被
   拒绝。F_118 说"autonomous 下没人会写 `lifetime`，这些代码是死路"——没有考虑到文案会引导模型去写。
   同一类问题 F_76 已经在 `update_task` 上处理过：偷传的 `reviewer` **报错拒掉而非静默剥离**，
   理由完全相同（静默剥离让 leader 以为能力已生效）。

另有一个放大器：`team_workspace/assembler.py:_write_tool_md` 把 `descs/<lang>/**/*.md`（含
`fragments/create_task_inline_member.md`、`task/create_task_scheduled.md`、
`system/dispatch_scheduled_leader.md` 等整套 scheduled 文案）不分 dispatch_mode 全部物化到
`team-workspace/prompts/`，而 leader 有文件读取与 grep 工具——它能在自己的工作区里找到另一个形态的
使用说明。

## 决策

修复原则与 F_76 一致：**能力关掉时，讲这个能力的文案必须跟着消失；偷传的参数在写库前报错，不静默剥离。**

1. **参数描述按形态分命名空间**（`locales/{cn,en}.py`）。`create_task.task.assignee` 只讲 autonomous
   的语义（可选、须先 `spawn_teammate`、省略进公共池）；scheduled 的 `assignee` / `member` /
   `member.lifetime` 三条迁到 `create_task_scheduled.task.*`。这是 tools/AGENTS.md 既有规则
   （"语义变了的同名参数用新的 desc_key 命名空间"，先例 `send_message_scheduled.to`）的直接应用，
   resolver 零改动。`_scheduled_task_node_schema` / `_inline_member_schema` 改读新 key。
2. **`shutdown_member` 的 lifetime 说明变成 capability 槽** `{{shutdown_member_lifetime}}`
   （片段 `descs/<lang>/fragments/shutdown_member_lifetime.md`），`ShutdownMemberTool` 新增
   keyword-only `dispatch_mode`，autonomous 下 `omit` 该槽；工厂把 `dispatch_mode` 传进去。gate 与
   `update_task_verify_gate` / `build_team_verify_gate` 同源（`dispatch_mode == "scheduled"`）。
   槽放在描述末尾、独立成段，而不是留在"注意"列表里——节级槽两侧各留一个空行是现有间距归一化的
   约定，塞进列表项会破坏它。
3. **`TaskCreateTool.invoke` 显式拒绝 `member`**：批次纯校验之后、任何 DB 访问之前，任一任务带
   `member` 即返回 `success=False`，错误信息点明本团队是 autonomous 调度、没有内联建人，应先
   `spawn_teammate` 再以 `assignee` 指派（或不指派进公共看板）。不新增工具、不新增参数、不触碰
   scheduled 形态。

## 拒绝的方案

- **给 autonomous 的 `create_task` 也加内联成员。** F_118 v1 就是这么做的，评审时已因耦合过重
  拒绝（内联成员的退场扫描只存在于 `TeamScheduler`，autonomous 下 `scheduler is None`，成员建了
  也没人管退场）。本次只是让 autonomous 诚实地说"没有"，不是把能力补上。
- **在 `_validate_assignees` 里顺手拒 `member`。** 该函数两个形态共用，而 scheduled 在调用它之前
  已把 `member.member_name` 绑成 `assignee`、并把内联成员名加入 `batch_members`，在那里拒会误伤
  scheduled。拒绝必须落在 autonomous 自己的 `invoke` 里——形态差异由类吸收，不进共享纯函数。
- **把两份 `shutdown_member.md` 拆成 `shutdown_member` / `shutdown_member_scheduled` 两个 desc_key。**
  两份文案只差一句，拆成两个文件会让其余十几行从此双份维护；capability 槽正是为"同一描述、一句
  随能力出现 / 消失"准备的手段。
- **让 `team_workspace/assembler.py` 按 dispatch_mode 过滤 scheduled 专属文件。** 需要 assembler
  知道调度模式并维护一张"哪些 md 属于哪个形态"的清单，且物化出来的文件还服务于工作区演进
  （evolved prompt 的 baseline），过滤会把演进的输入也过滤掉。工具边界的硬拒绝已经堵死了
  "读到别的形态的说明然后照做"的后果，物化清单的收紧放到已知遗留。

## 验证

- `tests/unit_tests/agent_teams/tools/test_tool_variants.py`：
  - `test_autonomous_create_task_rejects_inline_member`：复现轨迹第 4 步的调用（只带 `member`、
    不带 `assignee`），修复前成功落 1 个无主任务，修复后报错且板上无任务、名册无该成员。
  - `test_create_task_assignee_prose_follows_dispatch_mode[cn|en]`：autonomous 的 `assignee` 描述不含
    指向内联对象的措辞，scheduled 的含。
  - `test_shutdown_member_lifetime_prose_follows_dispatch_mode[cn|en]`：autonomous 的描述不含
    `DORMANT` / `lifetime`，scheduled 的含，且无残留 `{{`。
- `tests/unit_tests/agent_teams/tools/test_locales_variants.py`：`shutdown_member` 加入 `_SLOTTED` 与
  `_CAPABILITY_SLOTS`（槽省略后不留空洞、不泄漏占位符）；`test_param_descriptions_are_shared_across_variants`
  改为断言两形态的 `assignee` 描述**不同**。
- 既有 `test_every_toolset_assembles`（lang × dispatch × role 笛卡尔冒烟）覆盖新片段的 cn / en 两份都存在。

## 已知遗留

- `team_workspace/assembler.py` 仍把全部 `descs/<lang>/**/*.md` 与全部 `prompts/*.md` 不分
  dispatch_mode 物化到团队工作区；有文件工具的 leader 依旧能 grep 到另一个形态的说明。本次靠工具
  边界拒绝兜底，物化清单按形态收紧另议。
- leader 汇报"成员已创建"前没有调 `list_members` 核实。这是模型行为，不是框架缺陷；`create_task`
  的成功结果里已回显 `assignee`（本例全为 `None`），本次不另加提示。
