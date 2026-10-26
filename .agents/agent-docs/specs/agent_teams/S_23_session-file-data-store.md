# Session 文件数据存储（message/task 正文外置）

`openjiuwen.agent_teams.team_workspace.session_file_store` 子系统的设计规约：message/task 长正文从 SQLite 移入 session 目录文件，DB `content` 列只存占位符。本文描述"系统当前是什么样"。

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/team_workspace/session_file_store.py`、`openjiuwen/agent_teams/paths.py`（`team_session_dir`）、`openjiuwen/agent_teams/tools/database/message_dao.py`、`openjiuwen/agent_teams/tools/database/task_dao.py`、`openjiuwen/agent_teams/runtime/manager.py` |
| 最近一次修订日期 | 2026-08-19 |
| 关联 feature | `features/F_81_session-file-data-store.md` |

## 范围 / 边界

**这个规约管：**

- message/task `content` 的文件外置机制：`SessionFileStore.put/get/remove_session`、`FileAddress`、`CONTENT_IN_FILE` 占位符。
- 路径推导规则（direct/broadcast/task 三种 kind → 逻辑路径），根 = `paths.team_session_dir`（`openjiuwen.agent_teams.paths`）。
- DAO 的占位符读判（`_is_placeholder` / `_deref_row`）与降级路径（IO 失败存回原文）。
- session 释放时的文件回收（`remove_session` 接线到 `delete_team` / `release_session`）。

**这个规约不管：**

- A/B/C 三类可演进文本（prompt / tool desc / card）的写盘与读侧覆盖——归 `S_23`（evolvable-team-workspace，A/B/C 演进机制）的对应章节；本块与演进缓存 `WorkspaceCache` 隔离（D3）。
- message/task 文件的 frontmatter：content 是运行时动态数据，纯文本，无演进概念、无 frontmatter。
- task 的 `type`/`required`/`enum` 等 schema 结构——归 `S_12_schema-data-models`。
- 历史 DB 行的迁移：旧行 content 不是占位符，原样返回，不迁移。

## 不变量

1. **DB content 列只存两种值**：`#file#`（正文在 session 文件）或原文（未启用/降级/空 content）。不存任何路径信息。
2. **路径完全由行内字段推导**：`kind` + `object_id`（+ `to_member`）→ 逻辑路径；根 = `paths.team_session_dir(team_name, session_id)`，`session_id` 来自 ContextVar。DB 行自身足以推导，无需指针。
3. **无阈值**：所有 message/task 正文都写文件，不做长度分级。
4. **实时解引用，不走缓存**：`update_task` 覆盖同名文件，每次 `get()` 读文件返回最新值；不经过 A/B/C 演进缓存。
5. **占位符判定是唯一读分支**：DAO 仅当 `content == "#file#"` 时解引用；历史内联行、降级行、空 content（模板消息）天然排除。
6. **降级对称**：`put()` 抛 `OSError` 时返回原文（非占位符），DB 存原文、读回原文；`get()` 抛 `ValueError`（越界）/`FileNotFoundError`（文件丢失）时 DAO 捕获后返回占位符本身（不静默展示、不崩溃）。
7. **文件绑定 session，session 结束整体回收**：`remove_session()` 一次删 `messages/` + `tasks/` 目录；孤儿文件（写成功 + SQL 失败）随 session 回收，不主动扫描。
8. **multicast 每行一文件**：N 条 DB 行各写各的文件，各自可从行内字段独立推导路径。
9. **空 content 不 spill**：`content == ""` 时不写文件、不产生占位符，DB 存空串。
10. **文件 IO 不进 SQLite 写事务**：spill 在事务外完成，写事务只插行/更新行。

## 接口契约

### `SessionFileStore`

```python
CONTENT_IN_FILE = "#file#"   # DB content 列的占位符常量，不带任何路径信息

class SessionFileStore:
    # 无构造参数：根解析直接经 agent_teams.paths.team_session_dir，不自行拼接根路径。

    def put(self, text: str, address: FileAddress) -> str:
        """原子落盘（tmp + replace），返回 ``CONTENT_IN_FILE``。

        IO 失败抛 ``OSError``——由 DAO 层捕获并决定降级策略（存回原文）。
        """

    def get(self, address: FileAddress) -> str:
        """按 ``FileAddress`` 推导逻辑路径并读文件返回正文（不接收 DB 值）。

        路径校验：解析后的绝对路径必须在 team session 根内，越界抛
        ``ValueError``；文件丢失抛 ``FileNotFoundError``（含 team/session/
        address 诊断）。
        """

    def remove_session(self, *, team_name: str, session_id: str) -> None:
        """删除 session 下的 ``messages/`` 和 ``tasks/`` 目录。"""
```

### `FileAddress`

```python
@dataclass(frozen=True)
class FileAddress:
    team_name: str
    session_id: str
    kind: str            # "direct" | "broadcast" | "task"
    object_id: str       # message_id 或 task_id
    to_member: str | None = None  # direct 消息的收件人
```

### DAO 集成（message_dao / task_dao）

- 构造：`MessageDao(sessions, file_store=...)` / `TaskDao(sessions, file_store=...)`，`TeamDatabase.initialize()` 注入 `SessionFileStore()`。
- 写路径 `_to_stored`：file_store 存在 + session_id 存在 + content 非空 → `put()` 返回 `#file#`；否则返回原值。
- 读路径 `_is_placeholder(content)` + `_deref_row(row)`：仅占位符行按行字段构造 `FileAddress` 调 `get()`；单条 `get_*` 与列表 `get_*s` 都经 hydration。
- `update_task`：content 路径总是 spill（覆盖同名文件），DB 行成功保持 `#file#`、IO 失败持久化原文。

### `paths.team_session_dir`（根解析函数）

```python
def team_session_dir(team_name: str, session_id: str) -> Path:
    """per-session 目录：{team_home}/sessions/{session_id}/"""
```

## 数据结构

### 路径推导

| kind | 行内字段 | 逻辑路径 |
|---|---|---|
| direct | `to_member_name`、`message_id` | `messages/to_<to_member_name>/<message_id>.md` |
| broadcast | `message_id` | `messages/broadcast/<message_id>.md` |
| task | `task_id` | `tasks/<task_id>.md` |

### 生命周期

| 场景 | 行为 |
|---|---|
| 新建消息/task | `put()` 写文件，DB 存 `#file#` |
| 更新 task content | `put()` 覆盖同名文件（task_id 不变），DB 占位符恒定 |
| session 结束 | `remove_session()` 整体回收 `messages/` + `tasks/` |
| multicast | N 条 DB 行各写各的文件，session 结束后随目录回收 |
| 文件写入成功 + SQL 失败 | 文件成为孤儿（无 DB 引用），随 session 回收 |
| `put()` IO 失败 | DAO 降级：content 列存原文（非占位符），读原样返回 |

## 与其它 spec 的关系

- **S_23（evolvable-team-workspace，A/B/C 演进机制）**：本块与其机制同构（文件外置）但**根解析与缓存语义隔离**——本块根 = `paths.team_session_dir`、实时解引用不走缓存；A/B/C 走 `WorkspaceCache`（装配期定稿、build 一次常驻）。
- **S_12（schema-data-models）**：task 状态机与票表字段布局归 S_12；本块只引用 content 列。
- **S_13（team-workspace）**：session 目录布局与 team 根路径的既有定义来自 S_13 拓扑。
