# Agent Team Tools

## 模块地图

### 工具实现（按领域拆分）

| 文件 | 负责 |
|---|---|
| `tool_base.py` | `TeamTool` ABC、`MappedToolOutput` |
| `tool_permissions.py` | 权限集合（`LEADER_*`、`MEMBER_*`、`MEMBER_TOOLS_BY_DISPATCH`、`SHARED_TOOLS`、`HUMAN_AGENT_TOOLS`）、`_MEMBER_NAME_PATTERN` |
| `tool_team.py` | `BuildTeamTool`、`CleanTeamTool` |
| `tool_member.py` | `_SpawnToolBase`、`SpawnTeammateTool`、`SpawnHumanAgentTool`、`SpawnBridgeAgentTool`、`SpawnExternalCliTool`、`ShutdownMemberTool`、`ApprovePlanTool`、`ApproveToolCallTool`、`ListMembersTool` |
| `tool_task.py` | `TaskCreateTool` / `ScheduledTaskCreateTool`（各自独立，共享模块级纯函数 `_task_node_schema` / `_validate_task_batch`）、`ViewTaskToolV2`、`UpdateTaskTool`、`SubmitPlanTool`、`ClaimTaskTool`、`MemberCompleteTaskTool` |
| `tool_message.py` | `_SendMessageBase` → `SendMessageTool`（点对点、多播、广播）/ `ReportToLeaderTool`（scheduled 成员：仅 leader + user） |
| `tool_factory.py` | `create_team_tools` 工厂、`_wrap_invoke_with_logging` |
| `team_tools.py` | 向后兼容的 re-export shim —— 从上述领域文件重导出所有公共符号；现有 `from ... team_tools import ...` 调用点无需改动即可继续工作 |

### 基础设施

| 文件 | 负责 |
|---|---|
| `team.py` | `TeamBackend` —— 每个工具都对话的后端对象（spawn/shutdown/clean/approve、名册查询、cleanup-path registry）。`startup_member`（单次 UNSTARTED→STARTING CAS + spawn）、`startup`（经 `startup_member` 批量）、`_spawn_and_publish`（共享 helper）。`approve_tool` 写入 `protocol="json"` 的 DB 消息，作为解除中断的兜底投递 |
| `task_manager.py` | `TeamTaskManager` —— add_graph（原子批量图创建，`add` 是其单任务薄封装）/claim/complete/reset/cancel/approve_plan、事件发布、依赖刷新 |
| `message_manager.py` | `TeamMessageManager` —— 点对点 + 广播发送、已读状态查询 |
| `database/` | `TeamDatabase` + 建立在共享 `DbSessions` 上的按表 DAO（读写 session 分离 —— 见下文 *数据库并发*）。静态表 + 按 session 的动态表的 SQL 层。测试跑在 sqlite `:memory:` 的 `connection_string` 上（快、无文件） |
| `models.py` | `Team`、`TeamMember` 静态表 + 按 session 动态生成的 `TeamTask*` / `TeamMessage*` 工厂 |
| `member_options.py` | `TeamMemberOptions` / `MemberModelRef` / `MemberWorktreeOptions` 结构化 options 辅助（load/dump/build/merge/get_member_model_ref/get_member_permissions_override）。用统一的 `options` JSON 取代旧的 `model_ref_json` 列 |
| `structured_output_tool.py` | `StructuredOutputTool`（`input_params=schema_json`，捕获 `captured`）+ `StructuredOutputFinishRail`（一旦捕获就强制结束本轮）。给任何无原生 `response_format` 的 agent 用的通用结构化输出工具；被 swarmflow worker/session 与 tiny agent（`tiny_agent.py`）复用 |
| `locales/` | i18n 字符串（`cn.py`、`en.py`）与 Markdown 描述文件（`descs/<lang>/<tool>.md`） |

工具从不直接伸手进 `TeamDatabase` —— 一律经 `TeamBackend` 或某个 manager，使事件发布与状态流转保持集中。

### 数据库并发（SQLite 写串行化）

SQLite 同一时刻只允许一个写者（数据库级锁）。DB 层围绕 SQLite 的模型来构建，而不是用一堆假装能并行的写者去对抗它：

- **读写引擎分离（`initialize_engine` → `SqlEngines`）** —— 文件后端的
  SQLite 在同一文件上跑**两个**引擎：一个小的单写者池（`write_pool_size`，默认 2）
  和一个较大的读者池（`read_pool_size`，默认 8）。写在应用级锁上串行到写者池；
  读跑在读者池，因此慢写永远不会饿死读连接的可用性。`:memory:`（一条共享的
  StaticPool 连接）和 PostgreSQL / MySQL 两者共用单引擎 —— `SqlEngines` 的读字段
  别名到写字段。`TeamDatabase.engine` / `session_local` 是**写者**引擎 + 工厂
  （也被 DDL helper 用到）；`read_engine` / `read_session_local` 是读者池。
- **`DbSessions`（`database/engine.py`）** —— 四个 DAO 共享的一个 provider，
  暴露分别绑定到读者 / 写者工厂的 `read()` / `write()` async-context 访问器。
  `write()` 持有一把进程级 `asyncio.Lock`；`read()` 不持锁。这正是多成员负载下
  阻止 `QueuePool limit ... timed out` 耗尽的东西。写锁是**不可重入**的：当一个公开
  写委托给另一个（`verify_and_fix_task_consistency` →
  `_verify_and_fix_blocked_tasks`）时，只有最内层的 session opener 取锁。
- **PRAGMA（`engine.py` `_attach_sqlite_pragmas`）** —— 文件后端 SQLite 跑
  `journal_mode=WAL`（数据库级，首次连接时设一次）+ `synchronous=NORMAL` +
  `wal_autocheckpoint`（连接级，每次连接都设）。NORMAL 是安全、高吞吐的 WAL 搭配；
  FULL 的每次提交 fsync 是吞吐天花板。每连接的页缓存不对称：写者保留大
  `cache_size`（`write_cache_size_kb`，默认 64 MiB —— 有助 WAL checkpoint），而
  每个读者保留小的（`read_cache_size_kb`，默认 8 MiB），使得调大 `read_pool_size`
  不会成倍吃内存。所有旋钮都在 `DatabaseConfig` 上。
- **批量写** —— 每次 `COMMIT` 是一次 fsync，这是主导性的写成本。
  `MessageDao.mark_messages_read` 在一个事务里（一次 fsync）标记整个 mailbox 排空，
  而不是每条消息一次；coordination 的 `MessageHandler` 收集已投递的 id 后一次性 flush。
- **`retry_on_locked`（`engine.py`）** —— 对瞬时 `database is locked` 做有界退避；
  sleep 跑在 session 块之外，因此重试永远不会占住一条已 checkout 的连接。一旦写被串行化
  就很少见 —— 只在 WAL-checkpoint 边界或有外部进程碰同一文件时出现。
- **索引方案（按 session 的动态表）** —— 索引是查询驱动的，以压低 INSERT 写放大
  （每个二级索引对每行都是一次 B-tree 写）。任何动态表上都没有 `team_name` 索引：
  它们按 session 物理分区，因此 `team_name` 基数约为 1，在那建索引只会增加写成本。
  消息表带**两个复合索引**（`_inbox` = `(to_member_name, is_read, timestamp)` 服务
  direct-inbox 读 + 排序，`_bcast_ts` = `(broadcast, timestamp)` 服务广播读 /
  `has_unread`），取代五个弱的单列索引 —— 每条消息 INSERT 3 次 B-tree 写（PK + 2）
  而非 6 次。任务表保留独立的 `status` 索引，并把 `assignee` 折进复合索引
  `(assignee, status)`（服务 `get_tasks_by_assignee`），去掉没有任何查询读的死
  `updated_at` 索引。索引在 `models._get_message_model` / `_get_task_model` 里按表定义；
  `engine._ensure_dynamic_table_indexes` 在 session 表建立时把已存在的（持久团队）表
  迁移到此方案。

适用范围：这套串行化针对进程内（单 event loop）运行时。真正高并发写的部署应使用
PostgreSQL / MySQL 后端（`engine.py`），不要用 SQLite。

### 读取只取所需，校验落在边界

两条通用原则，写任何 DB 读取 / 校验前先过一遍：

1. **读取粒度对齐消费方**。加一个读取前先问"调用方到底用哪几列 / 只是判存在吗"，
   按需选最省的读法，不要为了一两个轻字段或一次存在性判断拉回整行重对象
   （序列化 card、私有 prompt、大 JSON 等）——这种浪费随数据量线性放大。存在性用
   `EXISTS`，只读子集就写窄投影方法返回轻量模型，只有真需要完整对象（重建运行时、
   渲染重字段）才读全行。宁可多一个投影方法，也不要把某个调用点扩到全行去喂给别人。

2. **不可信输入的校验放到边界层，别下推进共享热路径**。一个被多处复用的写操作
   （典型如 `message_manager.send_message`，十几个调用方大多发给已确定存在的对象）里塞
   存在性探测，会给每一次内部调用都加一次 DB 往返。校验（尤其是 LLM 传入的名字 / ID）
   属于**工具边界**那一层，在不可信数据进入处做一次即可。

具体落地示例：成员读取按 `member_exists`（判存在）/ `list_member_roster` 投影（名册视图）/
`get_member`·`list_members` 全行（需要 `desc` 等重字段）三档取用，`send_message` 的收件人
校验留在 `SendMessageTool`。细节见 `team.py` / `database/member_dao.py`。

## 工具目录与角色过滤

`create_team_tools(role=..., teammate_mode=..., lifecycle=..., exclude_tools=..., lang=...)` 是唯一入口。它把每个工具构造一次，再按角色过滤。

| 工具 | Leader | Teammate | 说明 |
|---|---|---|---|
| `build_team` | ✓ | | 入口工具 —— 描述里承载完整工作流。F_62：`enable_task_verification` 参数可覆盖 spec 默认（提示词驱动的"验证预期"开关）；dispatch_mode 是静态 spec 配置，**不在此选择**，spec 值随行记录进 `team_info` |
| `clean_team` | ✓（仅 temporary） | | 要求先关停每个 teammate；`lifecycle="persistent"` 时不接线（那类团队由 operator 经 SDK facade 拆除） |
| `spawn_teammate` | ✓ | | 拉起一个普通 LLM teammate；可选 `model_config_allocator` 回调；扁平 schema `member_name`/`display_name`/`desc`/`prompt?`/`model_name?`。始终接线 |
| `spawn_human_agent` | ✓ | | 拉起一个 HITT 人类成员；schema 仅 `member_name`/`display_name`/`desc`（无 `model_name`/`prompt`）；仅 `hitt_enabled()` 时接线 |
| `spawn_bridge_agent` | ✓ | | 拉起一个到远程 agent 的桥接；`desc` 兼作 connect briefing；可选 `mailbox_inject_mode`/`protocol`/`adapter_config`/`model_name`；仅 `bridge_enabled()` 时接线 |
| `spawn_external_cli` | ✓ | | 拉起一个第三方 CLI teammate；需要 `cli_agent`（在 `TeamAgentSpec.external_cli_agents` 声明的一个 kind）+ `desc`；仅 `external_cli_kinds()` 非空时接线 |
| `shutdown_member` | ✓ | | `force=True` 跳过正常关停序列 |
| `approve_plan` | ✓（仅 plan_mode） | | 仅 `teammate_mode == "plan_mode"` 时接线 |
| `approve_tool` | ✓（仅 plan_mode） | | 与 `approve_plan` 相同的门控 |
| `list_members` | ✓ | | 结果里排除调用者自身 |
| `create_task` | ✓ | | 整次调用经 `add_graph` 做**一次原子图变更**：批内依赖只用 `depends_on`（允许前向引用），`depended_by` 仅可指向已有任务（指向批内任务在工具边界拒绝）；全批成功或整体失败并返回真实 reason。单 spec 返回 `brief()`，批量返回 `tasks`+`count`。**两个形态**（见下文「工具形态」）：autonomous 无 `assignee`；scheduled 的 `assignee` 必填，随同一事务落库、任务停在 `PENDING(assignee)`（由调度器 `start_task` 开工），且额外带可选 `max_review_rounds`（≥1，须伴随 reviewer，验证返工轮数上限，F_62）。两形态都可带可选 `reviewer`（verify 闸验证者列表，须是真实成员且 ≠ assignee）。见 F_55 / F_57 / F_59 / F_62 |
| `update_task` | ✓ | | 一个工具处理标题/内容编辑、取消、指派、`reviewer`（设置/清除 verify 闸验证者，须真实成员且 ≠ assignee）、`max_review_rounds`（≥1，任务须已配或同时配 reviewer，F_62）以及 `add_blocked_by`。改派走 `TeamTaskManager.reassign`（DAO 原子 CAS 交换 assignee，发 `TASK_REVOKED`+`TASK_CLAIMED`，不发 `TASK_RELEASED`）；取消/编辑发 `TASK_CANCELLED`/`TASK_UPDATED`（带 `member_name`）——三者都**不再** `cancel_member`（编辑保持任务 `IN_PROGRESS`；仅 `IN_REVIEW` 锁）。强制「目标成员至多一个活跃任务」（活跃 = `{PLANNING, IN_PROGRESS, IN_REVIEW}`，见 F_59）；人类持有任务对 cancel / reassign / 改标题·内容均 leader-immutable。见 F_54 / F_56 |
| `view_task` | ✓ | ✓ | `action ∈ {list, get, claimable, in_review}`；默认 `list`。`in_review` 列出指派给本成员验证、当前 `IN_REVIEW` 的任务（verify 闸拉取入口，见 F_59） |
| `claim_task` | | ✓（仅 autonomous） | `status ∈ {claimed, completed}`（工具动词；claim 后任务落 `IN_PROGRESS`）；claim 前强制「本成员至多一个活跃任务」（活跃 = `{PLANNING, IN_PROGRESS, IN_REVIEW}`，见 F_54 / F_59）；完成路径追加一句下一步 nudge。scheduled 下**不注册**——没有自主认领 |
| `send_message` | ✓ | ✓ | **两个形态**：`SendMessageTool`（leader 全模式 + autonomous 成员）`to == "*"` → 广播，leader 调用会自动拉起 UNSTARTED 成员；`ReportToLeaderTool`（scheduled 成员）`to` 收成 `enum ["leader", "user"]`（角色词，投递时翻译成真实 leader），无多播/广播。也挂到 `human_agent` 作为一条用户驱动的转发通道 —— HITT prompt section 禁止自主使用；只有用户下达的"tell `<member>` …"指令才可触发。 |
| `member_complete_task` | | ✓（仅 scheduled） | `human_agent` 全模式可用；scheduled 下也进 teammate 工具集，替代 `claim_task`。只能完成自己的任务。行为与模式无关，仅描述分化（`desc_key`）。**verify 闸**：任务配了 `reviewer` 时，完成落 `IN_REVIEW` 交验证而非 `COMPLETED`；完成后 re-fetch 据实反馈（F_59） |
| `verify_task` | | ✓（成员，非 leader） | reviewer 对 `IN_REVIEW` 任务裁决，语义按 dispatch_mode（静态 spec 配置）二分、描述随语义分离（desc_key：`verify_task` / `verify_task_scheduled`，F_62）：**autonomous** 首裁即决——`decision=pass` → `COMPLETED`（解依赖，发 `TASK_VERIFIED`）/ `fail` → `IN_PROGRESS`（返工，`feedback` 定向 author，发 `TASK_REVISION_REQUESTED`）；**scheduled** 只记一票——追加 `team_review_vote_*` 行（重复调用=改票取最新）+ 发 `TASK_REVIEW_VOTE`，不翻转状态，由 leader 调度器按票数判定并经 `settle_review` 翻转。守卫两模式相同：任务须 `IN_REVIEW`、caller ∈ 任务 `reviewer` 列且 ≠ author。进 autonomous / scheduled / human_agent 成员集，不进 leader 集。见 F_59 / F_62 与 `S_22` |
| `workspace_meta` | ✓ | ✓ | workspace 锁 + 版本历史 |
| `async_tasks_list` | ✓ | | 列出后台 async 任务；仅 leader，始终接线 |
| `async_task_output` | ✓ | | 取某任务的完整输出（`block`/`timeout`；读磁盘 spill） |
| `async_task_cancel` | ✓ | | 取消一个仍在运行的后台 async 任务 |

Plan-mode 门控在工厂里强制：

```python
if role == "leader" and teammate_mode != "plan_mode":
    allowed = allowed - {"approve_plan", "approve_tool"}
```

持久团队门控在同一工厂里、紧接着强制：

```python
if lifecycle == "persistent":
    allowed = allowed - {"clean_team"}
```

理由：持久团队跨轮存活，由 operator 经 SDK facade（`delete_agent_team` 等）拆除。
在轮次中途暴露一个 leader 可调的 `clean_team` 会与 runtime pool 不变量竞态，并悄悄
de-register 一个 operator 仍认为存活的团队。临时团队保留该工具 —— 它们没有外部
operator；leader 是唯一能把它收尾的人。

`TeamBackend.__init__` 接一个 keyword-only 的 `on_team_cleaned` async 回调，**仅**在
`clean_team` 成功路径上触发（best-effort —— 回调抛错只记日志，不上抛）。宿主
`TeamAgent` 把它接到在 leader 本轮内同步置位 `TeamAgentState.team_cleaned`，这正是临时
团队 leader 在 `clean_team` 后结束自己 stream 的方式（leader 忽略自己的
`TeamCleanedEvent`）。见 `docs/specs/S_08` + `docs/features/F_10`。

`TeamBackend.__init__` 还接一个 keyword-only 的 `on_team_built`，在 `build_team` 创建
DB 行与初始成员之后触发。宿主 `TeamAgent` 用 `on_team_built` / `on_team_cleaned` 持久化
checkpoint 的 `db_state`（`created` / `cleaned`），同时把 checkpoint 写留在 agent-core
内部而非外层调用方。`on_team_cleaned` 在团队 DB 行删除后立即触发，早于 best-effort
的文件系统清理和事件发布。

Worktree 工具（`enter_worktree`、`exit_worktree`）对非团队调用方位于
`openjiuwen.harness.tools.worktree`。团队 teammate 的 worktree 隔离由 leader 侧的 spawn
宿主经 `isolation="worktree"` 创建，不作为手动 teammate 工具挂载。这两个工具在
`tools/locales/descs/` 下已无内容需要维护。

## 工具形态（tool variants，F_57）

一个**形态**保持 `ToolCard.id` / `name` 不变，替换 schema、描述与行为。三个正交维度各自
独立表达，互不嵌套：

| 维度 | 表达手段 | 例子 |
|---|---|---|
| 注册与否 | 权限集合减法 | `claim_task`（autonomous） vs `member_complete_task`（scheduled） |
| 同名不同形态 | `all_tools` **构造期**查表选类 | `create_task` / `send_message` |
| 同形态不同文案 | 不同 `desc_key` | `member_complete_task` |

**形态选择只发生在 `create_team_tools` 构造 `all_tools` 的那一刻，永远不在 `invoke` 里。**
每个工具实例仍满足「schema 扁平、`invoke` 直线、零 role/mode 分支」——分支被前移到装配器。
减法链一行不改，下游（权限集、`exclude_tools`、prompts、MCP）零感知。

形态表是模块级字面量（`tool_factory.py`），不是注册表 —— 形态是闭集（2 dispatch × 3 role），
缺失组合直接 `KeyError`，不静默回退：

```python
_CREATE_TASK_CLASS = {"autonomous": TaskCreateTool, "scheduled": ScheduledTaskCreateTool}
_SEND_MESSAGE_CLASS = {("scheduled", "member"): ReportToLeaderTool, ...}   # (dispatch, leader|member)
_MEMBER_COMPLETE_DESC_KEY = {"autonomous": "member_complete_task", "scheduled": "member_complete_task_scheduled"}
_VERIFY_TASK_DESC_KEY = {"autonomous": "verify_task", "scheduled": "verify_task_scheduled"}   # 裁决语义按模式分离（F_62）
```

`send_message` 的形态是 `(dispatch, role)` 二维：scheduled 下 leader 仍要广播 / 多播 /
`_auto_start_members`，只有成员侧收敛成 `ReportToLeaderTool`。**这个 role 维度必须由类的选择
吸收，不能塞进描述**——leader 和成员共享 `ToolCard.name`，若让 translator 按 dispatch 挑
描述片段，scheduled 的 leader 会拿到「只能发给 leader」的描述，与其行为相反。

**共享用最轻的手段，够用就好**——两个形态怎么共享代码，取决于共享的是数据还是行为：
- `create_task` 的两个形态（`TaskCreateTool` / `ScheduledTaskCreateTool`）**各自独立**，
  只共享两个模块级纯函数（`_task_node_schema` 造 schema、`_validate_task_batch` 校验批次）。
  `invoke` / `map_result` 各写一遍——它们是短小的线性流程，读一个类就看到全貌，好过为省几行
  去造一层带钩子方法的基类。
- `send_message` 的两个形态共享 `_SendMessageBase`，因为它们共享的是**真实行为**
  （`_send` / `_multicast` / `_broadcast` / `_auto_start_members` 是实打实的投递逻辑，不是钩子），
  子类只声明自己的 `to` schema 与一条直线 `_dispatch`。基类内**零形态分支**。

判据：共享的是纯数据 / 纯函数就用模块级函数；共享的是一坨有状态的行为才上基类。不要为了
「形态就该有基类」的对称感去造 `_XxxBase`。

**双层执法**：`ReportToLeaderTool` 的 `to` enum 是给宿主 LLM 的契约，`_dispatch` 里的白名单是
给 MCP 客户端的执法（`mcp/server.py` 直接 `await tool.invoke(arguments)`，不校验 schema）。
两层都必须有，不是重复。

### 参数描述：复用是免费的，扩展就是加 key

形态之间 `ToolCard.name` 相同，`t(tool, "param")` 的 key 是 `f"{tool}.{key}"` ——
`ScheduledTaskCreateTool` 写 `t("create_task", "task.title")` 拿到的就是同一条字符串，
**零机制**。扩展 = 新增 key（`create_task.task.assignee`）。语义变了的同名参数用**新的
desc_key 命名空间**（`send_message_scheduled.to`），其余（`content` / `summary`）继续复用
`send_message.*`。**绝不让 resolver 学会 variant**。

schema 复用走模块级纯函数，不是复制粘贴：
`_task_node_schema(t, *, extra_properties=None, extra_required=None)`。

## 描述模板化（`{{slot}}` + 共享片段，F_57）

`descs/<lang>/<desc_key>.md` 可以声明 `{{slot}}`，由 `descs/<lang>/fragments/<slot>.md`
填充。片段是**与形态无关**的公共散文（如 `artifact_handoff_policy`），可跨工具复用。

- **`desc_key` 不必等于工具 name**：一个工具的多个形态各有自己的 `.md`
  （`send_message.md` / `send_message_scheduled.md`），形态类自己选 key。
- **槽由 loader 从模板里枚举并强制填满**，缺片段 → 构造期 `FileNotFoundError`（带期望路径）；
  渲染后残留 `{{` → `ValueError`。这是为了堵住 `PromptAssembler.prompt_assemble` 的静默回填
  行为（缺 key 时把 `{{key}}` 原样写回，会直接喂给 LLM）。**不要**把不完整的 kwargs 交给
  `PromptTemplate.format`。
- **两条插值路径不要混**：md 的 `{{slot}}` 由本模块填；`STRINGS` 的 `{key}` 走
  `str.format_map`，只用于**运行时错误消息**（`update_task.error_human_agent_locked_*`）。
  参数描述一律是字面量。随场景变化的文案属于 md 槽或新 desc_key，不属于插值的 STRINGS 值。

`tests/unit_tests/agent_teams/tools/test_tool_variants.py::test_every_toolset_assembles` 是
笛卡尔冒烟（lang × dispatch × role）：漏写任何一份 en 片段 / STRINGS key / `_desc` 都在这里炸。

## 工具设计原则

参考：本文件旁的 `prompt_design.md` / `system_prompt_now.md` 有活的描述撰写示例。

### 描述 = 行为契约

工具描述不只是功能摘要 —— 它是给 LLM 的**行为契约**。每份描述都应包含：

1. **用例枚举** —— 何时调用此工具（如"用于任务通知、进度汇报、阻塞升级"）
2. **行为约束** —— 不该做什么（如"你的纯文本输出对其他 agent 不可见"、"用名字指代成员，永远不用内部 ID"）
3. **成本信号** —— 显式标出昂贵操作（如"广播 —— 昂贵，随团队规模线性"）

### 入口工具承载工作流

对一个子系统的"入口"工具（如团队协作的 `build_team`），描述应包含**完整工作流规范**
—— 调用顺序、设计原则、状态机、通知语义、空闲处理 —— 而非只是功能摘要。理由（由
`BuildTeamTool` 验证）：

- LLM 在即将调用工具时就地看到工作流，而不是埋在一份它可能已经忘掉的系统提示词里。
- 避免在系统提示词和工具描述里重复工作流步骤。
- 系统提示词（`leader_policy.md`）专注于**角色身份与决策原则**；工具描述拥有**操作流程**。

一个入口工具描述的具体小节：

| 小节 | 目的 |
|---|---|
| 调用顺序 | 防止错序（如"build_team 先于 create_task 先于 spawn_teammate"） |
| 设计原则 | 约束 LLM 如何设计任务（目标导向、单一负责人、粗粒度） |
| 工作流步骤 | 从启动到关停的完整编号流程 |
| 通知语义 | "消息自动投递，不要轮询" —— 消除 LLM 忙等的冲动 |
| 空闲状态 | "空闲是正常的，不是错误" —— 防止对预期内的沉默过度反应 |

### 描述里的反模式纠正

LLM 有可预测的失败模式。工具描述应主动应对它们：

| 模式 | 示例 | 为什么有效 |
|---|---|---|
| **鼓励行动** | "一有目标就调用 —— 别犹豫" | 降低决策瘫痪 |
| **消除忙等** | "别轮询回复，系统会通知你" | 防止耗 token 的循环 |
| **正常化沉默** | "空闲是正常的，不是错误" | 阻止过早关停/nudge |
| **强制通道** | "纯文本不可见 —— 你必须调此工具" | 确保正确的通信路径 |
| **标注成本** | "广播 —— 昂贵，随团队规模线性" | 抑制滥用 |

### 参数合并

如果两个参数在语义上难以让 LLM 区分，就合并它们。示例：`desc`（"团队做什么"）和
`prompt`（"给成员的指令"）被合并成单一 `desc` 字段 —— LLM 一贯混淆二者边界，而单一字段
表现同样好。被移除参数的 DB 列保留 nullable 以向后兼容。

### 一个工具一个关切 —— 但别把只差一个参数的东西拆开

如果两个工具共享同一 schema 结构、只在一个参数值上不同（如 `send_message` 与
`broadcast_message` 只差 `to: name` 与 `to: "*"`），把它们合成一个工具。用参数来分派，
而不是单独注册一个工具。`update_task` 是典范 —— 它把 cancel / edit / assign / add-dep
折进带可选字段的一次调用。

### 输入校验

在工具边界校验，带清晰错误地快速失败：

| 优先级 | 检查什么 |
|---|---|
| 1 | 必填字段非空 |
| 2 | 实体存在性（如目标成员存在） |
| 3 | 业务规则（如任务必须处于正确状态） |

错误消息约定：
- 校验错误：`"'field_name' is required"` —— 字段名加引号、小写
- 实体错误：`"Member 'dev-1' not found"` —— 实体类型首字母大写、值加引号
- 操作失败：`"Failed to send message to 'dev-1'"` —— `Failed to` 前缀
- 内部异常：`"Internal error: {e}"` —— 兜底，记录异常，不暴露堆栈

### 结构化的成功输出

成功时始终返回有意义的 `data`，而非裸 `ToolOutput(success=True)`。包含 `type` 以区分
分派路径（如 `"message"` 与 `"broadcast"`），并回显关键路由信息（`from`、`to`、`summary`）
供日志和下游处理。

### 异常处理

用 `try/except` 包住 `invoke` 主体，捕获后端服务的意外错误。经 `team_logger.error` 记录
异常，返回 `ToolOutput(success=False, error=...)`。永远不要让未处理的异常上抛 —— 工具调用
必须总是返回一个 `ToolOutput`。

## 结果映射：`TeamTool.map_result` + `_wrap_invoke_with_logging`

`TeamTool` 子类从 `invoke()` 返回一个原始 `ToolOutput`。工厂把每个工具都跑过
`_wrap_invoke_with_logging`（见 `team_tools.py:1037`），它会：

1. 在 debug 级记录 inputs/outputs。
2. 调用 `tool.map_result(output)` 生成面向模型的文本。
3. 把结果包进 `MappedToolOutput`，其 `__str__` 返回映射后的文本。

ability 层用 `str(result)` 渲染工具结果 —— `MappedToolOutput.__str__` 才是真正变成
LLM 可见的 `ToolMessage.content` 的东西。`ToolOutput.data` 仍然存在，供程序化消费者
（事件、日志）使用。

> **外部成员复用这套完全相同的工具（F_26）。** `mcp/` server 和 `skill/` CLI 在其
> `member` scope 下暴露真实的 `create_team_tools(role="teammate")` 实例
> （`view_task` / `claim_task` / `send_message`）并返回 `str(await tool.invoke(...))`
> —— 因此 `map_result()` 是进程内成员与外部 CLI 成员之间面向 LLM 文本的唯一来源。让工具
> 描述 / `map_result` 保持足够角色中立，对第三方 CLI 成员也读得通顺，而不只是进程内的
> DeepAgent。

本模块的 `map_result` 策略：

| 模式 | 工具 | 策略 |
|---|---|---|
| **纯文本** | `build_team`、`clean_team`、四个 `spawn_*` 工具、`shutdown_member`、`approve_*` | 一句确认 —— token 最少 |
| **结构化文本行** | `list_members`、`view_task`（list）、`create_task`（批量） | 一实体一行，密集格式 |
| **详情文本** | `view_task`（get） | 带标签行的完整字段 |
| **时间上下文** | `view_task`（list + get） | 两档都经 `timefmt.format_time_context` 把 `updated_at` 渲染成 `<绝对本地时间> (<相对差>)`。`map_result` 不能接参数，故内部调 `get_current_time()` 并在 `updated_at is not None` 上做保护 |
| **文本 + 行为引导** | `claim_task`（completed） | 任务完成后追加 `Call view_task now…` 以维持自主任务循环 |
| **默认 JSON** | `TeamTool` 基类 | 对任何忘了 override 的兜底 `json.dumps(output.data)` |

### Async 工具的两段式文本（`AsyncTool` 子类，如 `SwarmflowTool`）

对同步 `TeamTool`，`map_result` 是 LLM 可见工具文本的**唯一**来源。async 工具在**后续
一轮里**加了**第二个出口**：

| 阶段 | 出口 | 时机 | 文案 |
|---|---|---|---|
| **Launch** | `map_result` | `invoke` 后同步，经 `_wrap_invoke_with_logging` | 已启动回执（swarmflow → `swarmflow.launched`，含 `run_id` + `task_id`） |
| **Terminal** | `format_completed_injection` / `format_failed_injection` | 后台运行结束；`AsyncToolRuntime._run` 调 `AsyncToolRecord.format_*` | `swarmflow.completed` / `swarmflow.failed`（含 `run_id`） |

终态回调在 invoke 时经 `launch_async_tool(..., format_completed=, format_failed=)` 注册
（闭包捕获每次运行的 `run_id` + `completion_ctx`）。它们返回 `str | None`：非 `None` 覆盖
默认 `async_tool.*` 文案；`None` 或未设则回落默认（无 `run_id`，只有 `tool` +
`result`/`error`）。`run_background` 返回**原始脚本结果**（内部不做字符串拼装）；格式化是
终态回调的职责。见 `harness/AGENTS.md`（async 工具框架）、`workflow/AGENTS.md`
（SwarmflowTool）、`S_20` / `F_47`。

设计原则：
- **Token 效率**：只发模型下一步决策所需的东西。
- **行为引导注入**：把这些 nudge 留在 `map_result`，而非 descriptor —— 它们只在到达终态时触发。
- **错误语义**：错误结果返回纯错误文本，绝不用可能级联取消兄弟工具的 `is_error` 式标志。

## i18n

Locale 文件在 `locales/` —— 每种语言一个扁平 `STRINGS` dict（`cn.py`、`en.py`）。resolver 见 `locales/__init__.py`。

- Key 约定：ToolCard 描述用 `tool_name._desc`，参数用 `tool_name.param`，嵌套 schema 参数用 `tool_name.nested.param`。
- 多行描述用 `"""\`（三引号 + 反斜杠续行），而非括号内字符串拼接。
- `make_translator(lang)` 返回一个闭包 —— 无全局状态，在一个进程里并发多语言使用安全。
- 所有工具构造函数都接 `t: Translator` 参数。
- 缺 key 在构造时抛 `KeyError` —— 大声失败，不静默。
- 缺 `_desc`（既无 Markdown 文件也无 dict 条目）抛 `FileNotFoundError`，消息里带上两条查找路径 —— resolver 精确告诉你要创建什么。

### Markdown 描述文件

长 `_desc` 条目可以放在 `locales/descs/<lang>/<tool_name>.md` 的 Markdown 文件里，而非
`STRINGS` dict。两者都存在时 Markdown 文件优先。这是可选的 —— 短描述和参数字符串仍留在
`cn.py`/`en.py`。

- 文件命名：`descs/cn/build_team.md` → 对 `desc_key` `"build_team"`、lang `"cn"` 解析为其 `_desc`。
  **`desc_key` 通常等于工具 name，但一个工具的多个形态各有自己的 key**（`send_message_scheduled`）。
- 文件经 `PromptTemplate` 加载（与 `agent_teams/prompts/` 相同）并用 `@cache` 缓存（缓存的是**插值前**
  的对象，`format` 内部 deepcopy，填槽不污染缓存）。
- 支持 `{{slot}}` 占位符 —— 由 `descs/<lang>/fragments/<slot>.md` 填充，见上文「描述模板化」。
  `_desc` **不接受 `**kwargs`**；`t(tool, "param", **kwargs)` 的 `format_map` 只服务 `STRINGS`
  里的运行时错误消息。
- 把某个 `_desc` 从 `STRINGS` 迁到 `.md` 文件时，删掉 dict 条目并留一条注释。

当前 `descs/` 已覆盖：`approve_plan`、`approve_tool`、`build_team`、`claim_task`、`clean_team`、`create_task`、
`create_task_scheduled`、`list_members`、`member_complete_task`、`member_complete_task_scheduled`、`send_message`、
`send_message_scheduled`、`verify_task`、`verify_task_scheduled`、`shutdown_member`、`spawn_bridge_agent`、`spawn_external_cli`、`spawn_human_agent`、
`spawn_teammate`、`structured_output`、`swarmflow`、`update_task`、`view_task`、`workspace_meta`、`async_tasks_list`、
`async_task_output`、`async_task_cancel`。

`descs/<lang>/fragments/` 已覆盖：`artifact_handoff_policy`（两个 `send_message` 形态共用）、
`create_task_edge_semantics`、`create_task_granularity`（两个 `create_task` 形态共用）。

## Prompt 分层：工具描述 vs 系统提示词

| 层 | 拥有 | 示例文件 |
|---|---|---|
| 工具描述（`locales/descs/`） | 操作流程、调用顺序、工作流步骤、反模式、使用场景 | `build_team.md` |
| 系统提示词（`agent_teams/prompts/`） | 角色身份、决策原则、状态流转 | `leader_policy.md` |

规则：**不要跨层重复内容**。如果工作流住在工具描述里，系统提示词就不应重复它。

### 统一读工具：Action 分派 + 分档输出

把多个只读工具合并成一个时（如 `TaskList` + `TaskGet` → `view_task`），用 `action` 枚举
分派，并**按 action 分档输出**：

- **list action** —— 摘要视图：只返回路由/身份字段（id、title、status、assignee、`updated_at`）
  加依赖边（`blocked_by`）。`updated_at` 是个轻量路由字段（一个 int），list 视图把它渲染成
  相对时间，好让成员看出停滞的任务；省掉 `content` 这类重字段和 `team_id` 这类内部字段。
  这让常见的"扫全部任务"调用保持低 token 成本。
- **get action** —— 详情视图：返回完整 content 加双向依赖信息（`blocked_by` + `blocks`）。
  这是唯一返回 `content` 的路径。
- **默认 action** —— 选最常见的零参数用例做默认。`view_task` 默认 `list`。

参考实现：`ViewTaskToolV2`，`action ∈ {list, get, claimable}`。

### 读工具的描述结构

读工具描述遵循 4 段结构：

| 小节 | 内容 |
|---|---|
| **When to Use** | 按 action 的用例枚举（action=list："看进度、找瓶颈"；action=get："开工前拿需求"） |
| **Output** | 按 action 的字段列举 —— 显式说明包含与不包含什么（如"list 省掉 content"） |
| **Tips** | Token 成本指引、依赖语义、任务排序启发 |
| **Teammate Workflow** | 最常见 agent 工作流的编号步骤（list → find → claim → get → work） |

### 用 BaseModel 定义返回 schema

工具返回数据必须定义为 `schema/task.py`（或相关 `schema/` 模块）里的 Pydantic BaseModel 类，
而非裸 dict：

- **摘要模型**（`TaskSummary`）—— list 视图的轻量字段
- **详情模型**（`TaskDetail`）—— 单实体视图的完整字段
- **列表结果模型**（`TaskListResult`）—— 包住摘要列表 + count

后端方法（`task_manager.py`）返回类型化模型。工具 `invoke()` 在传给 `ToolOutput(data=...)`
前调 `model.model_dump()`（详情视图用 `exclude_none=True`）。这在边界上给了类型安全，又不把
`ToolOutput.data` 耦合到某个具体 schema。

### 后端方法的结果包装

`TeamBackend` / `TeamTaskManager` 方法不返回裸 `bool` —— 它们返回结果包装：

- `MemberOpResult` —— `ok`、`reason`；被四个 `spawn_*` 工具和 `shutdown_member` 用。
- `TaskCreateResult` —— `ok`、`reason`，外加 `task`，它经 `__getattr__` 代理属性访问，
  使旧的 `result.task_id` 调用点仍能工作。
- `TaskOpResult` —— `ok`、`reason`；被 `claim`、`complete`、`reset`、`assign`、`update_task`、
  `approve_plan`、`add_dependencies` 用。

工具 `invoke()` 必须在失败时把 `result.reason` 传进 `ToolOutput.error` —— 这是 LLM 用来
诊断哪里出错的通道。在这里返回泛泛的 "Operation failed" 会吞掉后端的诊断信息。

## ToolCard ID 约定

所有团队工具 ID 用 `team.{name}` 格式（如 `team.send_message`、`team.create_task`）。加新工具
时保持一致 —— 下游接线（rail、日志、UI 标签）会解析这个前缀。
