# Session 文件数据存储（message/task 正文外置）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-13 |
| 范围 | `openjiuwen/agent_teams/team_workspace/session_file_store.py`（新增）、`openjiuwen/agent_teams/paths.py`（复用 `team_session_dir`）、`openjiuwen/agent_teams/tools/database/{__init__,message_dao,task_dao}.py`（`SessionFileStore` 注入 + 占位符读判）、`openjiuwen/agent_teams/runtime/manager.py`（`remove_session` 接线）；测试 `tests/unit_tests/agent_teams/team_workspace/test_session_file_store.py` |
| 测试基线 | `test_session_file_store.py` 13/13 PASS（store 层 7 + DAO 集成 6）；ruff/isort 全过 |
| Refs | `specs/S_23_session-file-data-store.md` |

## 背景

Team 模式下 message/task 的 `content` 长正文存 SQLite `content` 列。两个问题：

- **DB 膨胀**：长文本（任务描述、验收标准、消息正文）全部内联进 SQLite，session 结束虽然 DROP 动态表，但历史体积长期占用。
- **演进方不可见**：正文散在 DB 行里，演进方（人或自演进程序）无法用统一方式（改文件 + 重启）调整——与 A/B/C 三类可演进文本的"文件是唯一编辑面"哲学不一致。

本特性把 message/task 正文从 SQLite 移到 session 目录文件：DB `content` 列只存占位符 `#file#`，文件路径由行内字段 + ContextVar `session_id` 推导，DAO 透明解引用——调用方只看到正文，感知不到占位符。

## 数据结构 / 状态机

### 占位符与地址

```python
# DB content 列的值：表示"正文在 session 文件里"，不携带任何路径
CONTENT_IN_FILE = "#file#"

@dataclass(frozen=True)
class FileAddress:
    team_name: str
    session_id: str
    kind: str            # "direct" | "broadcast" | "task"
    object_id: str       # message_id 或 task_id
    to_member: str | None = None  # direct 消息的收件人
```

### 路径推导规则（DB 不存指针，路径读时现算）

| kind | 行内字段 | 逻辑路径 |
|---|---|---|
| direct | `to_member_name`、`message_id` | `messages/to_<to_member_name>/<message_id>.md` |
| broadcast | `message_id` | `messages/broadcast/<message_id>.md` |
| task | `task_id` | `tasks/<task_id>.md` |

根 = `paths.team_session_dir(team_name, session_id)`（`openjiuwen.agent_teams.paths`）——`team_name` 在行内，`session_id` 来自既有 ContextVar `get_session_id()`。

### 目录形态

```text
<TeamName>/sessions/<session_id>/
├── messages/
│   ├── to_<member>/<msg_id>.md      # direct
│   └── broadcast/<msg_id>.md
└── tasks/<task_id>.md
```

### 生命周期

- **写**：DAO 写路径 `_to_stored` 调 `put()` 原子落盘（tmp + replace），返回 `#file#`；IO 失败降级存回原文。
- **读**：DAO 读路径仅当 `content == "#file#"` 时按行内字段构造 `FileAddress` 调 `get()` 解引用；历史内联行、降级行、模板消息（空 content）天然被排除。
- **清理**：session 释放时 `runtime/manager.py` 在 DROP 动态表后调 `SessionFileStore.remove_session()` 整体回收 `messages/` + `tasks/` 目录。

## 决策

### D1：DB 不存指针，路径由行内字段推导

占位符只是"内容在文件里"的标记，路径信息完全由行内字段（`to_member_name`/`message_id`/`task_id`）+ ContextVar `session_id` 推导。DB 存指针纯属冗余——`#file#` 恒定，无版本、无路径、无 session 依赖。

### D2：无阈值，短文本也写文件

不做"超过 2000 才溢出"的分级判断。规则简单统一：所有 message/task 正文都进文件。代价是短文本多一次文件 IO，换来的是读侧逻辑单一（只有 `#file#` 与原文两种形态）。

### D3：不走演进缓存（与 A/B/C 三类隔离）

message/task `content` 是**运行时动态数据**——task 可被 `update_task` 覆盖同名文件，每次 `get()` 都实时解引用文件。**不使用** A/B/C 的 `WorkspaceCache` 演进文本缓存（该缓存只服务装配期定稿的演进文本，build 一次常驻、运行期零 IO）。两者机制同构（文件外置）但根解析不同：本块根 = `paths.team_session_dir`，B 类静态字段根 = `agent_teams.paths` 的 `team_member_workspace_dir`/`team_home`。

### D4：multicast 每行一个文件

同一条内容发给 N 个收件人时，DB 本就按收件人各插一行（独立 `message_id` 主键）；文件侧同样**每行各写一个文件**——任何一行"共享"别人的文件都意味着要存指针，违背 D1。代价：同一条内容在磁盘上存在 N 份小文本，可忽略；session 结束时随目录整体回收。

### D5：`put()` 失败降级存回原文

IO 失败时 `_to_stored` 捕获 `OSError`，返回原文而非占位符——DAO 把原文存进 content 列，读路径原样返回。**占位符方案的正确性依赖这个降级兜底**：历史行/降级行与文件行靠"是否等于 `#file#`"区分。

### D6：空 content 不 spill（模板消息）

模板消息（`content=""` + `meta.template`）不写文件：`_to_stored` 对空字符串直接返回原值，DB 存 `""`（非占位符），投递时由 `expand_message` 展开模板。空串与 `#file#` 不同，天然不会被误判为占位符。

### D7：session 结束整体回收（孤儿可接受）

消息/task 的 DB 表是 per-session 动态表，session 结束时直接 DROP，没有逐条删除。文件同理——`remove_session()` 一次删整个 `messages/` + `tasks/` 目录。文件写入成功但 SQL 失败产生的孤儿文件在 session 内不可见（无 DB 行指向），随 `remove_session` 回收；后台自动扫描本期不实现。

### D8：文件 IO 不在 SQLite 写事务内

单条 `create_message` 先 spill 再进事务；`create_direct_messages`（multicast）同样先把 N 个文件全部 spill 完成，再开单事务插 N 行。同步文件 IO 不持有进程级 SQLite 写锁，避免阻塞事件循环。

### D9：类名 `SessionFileStore`（非 `FileDataStore`）

存储严格绑定 session 域：所有文件都解析到 `paths.team_session_dir(team_name, session_id)` 下，随 session 创建/回收，**没有第二个根**。`FileDataStore` 听上去像通用文件后端，实际只服务 session 下的 message/task——命名收敛到它真正服务的资产类别。

## 拒绝的方案

### R1：`file:` 指针（初版曾落地后推翻）

首版实现里 DB content 列存 `file:<logical_path>` 指针，`get()` 解引用指针。问题：**路径信息进 DB**——multicast 共享一个指针时无法按行推导；指针与 session_id 分离（DB 行不带 session，指针自身也不带），读侧要额外传 session。推翻为 `#file#` 占位符 + 行内字段推导（D1）。

### R2：`SessionRootProvider` 抽象接口

设计阶段考虑过把"根解析"抽象成接口。否决理由：`SessionFileStore` 只服务 message/task（根 = session-root），一个根解析函数、无第二实现，抽象成接口是伪抽象——直接依赖 `agent_teams.paths` 的 `team_session_dir` 即可（2026-08-19 重构：`TeamWorkspacePaths` 类删除，全部改用 `agent_teams.paths` 函数）。未来若文件覆盖 DB 扩展到新根，在 `agent_teams.paths` 加函数即可，不新增接口层。

### R3：multicast 共享一个文件 + 成员指针

广播/群发场景让 N 行共享一个文件，DB 行存"文件 + 收件人"指针。否决理由：违背 D1（DB 必须能独立推导路径），且引入"谁是指针所有者、文件何时删"的额外状态。

### R4：正文进 `WorkspaceCache` 演进缓存

把 message/task content 当作 A/B/C 类演进文本处理（装配期读文件进缓存）。否决理由：content 是运行时动态数据，`update_task` 会覆盖文件，每次 `get()` 必须读到最新值——缓存与"实时解引用"语义冲突（见 D3）。

### R5：空 content 也 spill

模板消息的空正文同样写文件。否决理由：产生无意义空文件，且读路径要额外区分"占位符但文件为空"与"内联空串"两种形态——不如空 content 直接不 spill（D6）。

## 验证

- `tests/unit_tests/agent_teams/team_workspace/test_session_file_store.py` **13/13 PASS**：
  - store 层（7）：put 返回占位符并写入推导路径；direct/broadcast/task 三种 kind 路径推导；同名覆盖；缺失文件抛 `FileNotFoundError`；路径越界抛 `ValueError`；direct 缺 `to_member` 抛错；`remove_session` 删除 messages/tasks 目录。
  - DAO 集成（6）：message 写读 round-trip（DB 存 `#file#`、读回正文）；broadcast round-trip；multicast 3 行 3 文件各自推导命中；模板消息空 content 不 spill（DB 存 `""`、无文件）；task 写读 + `update_task` 覆盖文件；插入失败产生的孤儿文件被 `remove_session` 回收。
- ruff / isort 全过。
- 关联 ST：`agent_team_evolvable_st.py`（主 ST，块 B 覆盖为信息性打印）。

## 已知遗留

- **后台孤儿扫描器未实现**：文件写入成功 + SQL 失败产生的孤儿文件依赖 `remove_session` 回收，不主动扫描。
- **session 回收的完整链路未在 ST 覆盖**：`remove_session` 已接 `delete_team`/`release_session`，但动态成员目录删除调用链的端到端验证留待后续。
