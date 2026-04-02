# workspace

工作区文件系统 Schema 定义，为 DeepAgent 提供结构化的文件操作目录。

---

## class WorkspaceNode

```python
class WorkspaceNode(Enum):
    AGENT_MD = "AGENT.md"
    SOUL_MD = "SOUL.md"
    HEARTBEAT_MD = "HEARTBEAT.md"
    IDENTITY_MD = "IDENTITY.md"
    USER_MD = "USER.md"
    MEMORY = "memory"
    TODO = "todo"
    MESSAGES = "messages"
    SKILLS = "skills"
    AGENTS = "agents"
    MEMORY_MD = "MEMORY.md"
    DAILY_MEMORY = "daily_memory"
```

常用工作区目录节点名称枚举，提供对标准工作区目录的类型安全访问。

| 值 | 说明 |
|---|---|
| `AGENT_MD` | 基础配置和能力文件 |
| `SOUL_MD` | 人格、性格和价值观文件 |
| `HEARTBEAT_MD` | 心跳日志和状态记录文件 |
| `IDENTITY_MD` | 身份凭证和权限文件 |
| `USER_MD` | 用户数据文件 |
| `MEMORY` | 记忆核心模块目录 |
| `TODO` | 待办事项目录 |
| `MESSAGES` | 消息历史目录 |
| `SKILLS` | 技能库目录 |
| `AGENTS` | 子智能体嵌套目录 |
| `MEMORY_MD` | 长期记忆索引文件 |
| `DAILY_MEMORY` | 每日结构化记忆目录 |

---

## class Workspace

```python
@dataclass
class Workspace:
    root_path: str | Path = "./"
    directories: List[DirectoryNode] = field(default_factory=list)
    language: str = "cn"
```

DeepAgent 的工作区 Schema 定义。

- `root_path`: 工作区根目录。
- `directories`: 工作区根目录下的目录节点定义列表。
- `language`: 工作区语言（`"cn"` 中文，`"en"` 英文）。

**目录行为**:
- 必需的顶层目录由 `DEFAULT_WORKSPACE_SCHEMA` 定义。
- 用户提供的 `directories` 缺少任何必需目录时，在 `__post_init__` 中自动补充。
- `get_directory(name)` 在显式目录中未找到时回退到 `DEFAULT_WORKSPACE_SCHEMA`。

**属性**:

| 属性 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `root_path` | `str \| Path` | `"./"` | 工作区根目录路径 |
| `directories` | `List[DirectoryNode]` | `[]`（自动填充默认 schema） | 目录节点定义列表 |
| `language` | `str` | `"cn"` | 工作区语言 |

---

### get_directory

```python
def get_directory(self, name: str | WorkspaceNode) -> str | None
```

返回指定名称的目录节点的 `path` 字段。先检查 `directories` 中的顶层条目，未找到时回退到当前语言的默认 schema。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `name` | `str \| WorkspaceNode` | 目录名称（字符串或枚举值） |

**返回值**: `str | None` — 目录路径，未找到时返回 None。

---

### get_node_path

```python
def get_node_path(self, node: str | WorkspaceNode) -> Path | None
```

返回顶层工作区节点的完整绝对文件系统路径。仅检查顶层节点（`directories` 的直接子节点），不支持嵌套节点。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `node` | `str \| WorkspaceNode` | 节点名称（如 `"memory"`、`"AGENT.md"`）或 WorkspaceNode 枚举 |

**返回值**: `Path | None` — 节点的完整绝对路径，未找到时返回 None。

---

### set_directory

```python
def set_directory(
    self,
    nodes: Union[DirectoryNode, List[DirectoryNode]],
) -> None
```

按名称添加或更新顶层目录节点。接受单个目录节点（字典）或节点列表。同名节点已存在时替换，否则追加。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `nodes` | `Union[DirectoryNode, List[DirectoryNode]]` | 要添加/更新的目录节点 |

---

### get_default_directory

```python
@classmethod
def get_default_directory(cls, language: str = "cn") -> List[DirectoryNode]
```

返回默认目录 schema 的深拷贝。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `language` | `str` | `"cn"` 中文或 `"en"` 英文 |

**返回值**: `List[DirectoryNode]` — 指定语言的工作区 schema 深拷贝。

---

## function get_workspace_schema

```python
def get_workspace_schema(language: str = "cn") -> List[DirectoryNode]
```

根据语言获取工作区 schema。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `language` | `str` | `"cn"` 中文或 `"en"` 英文，默认 `"cn"` |

**返回值**: `List[DirectoryNode]` — 指定语言的工作区 schema 深拷贝。

---

## DirectoryNode 格式

每个 `DirectoryNode` 是一个字典，包含以下字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `str` | 是 | 非空字符串，不含路径分隔符 |
| `path` | `str` | 否 | 相对路径 |
| `description` | `str` | 否 | 目录/文件描述 |
| `is_file` | `bool` | 否 | 是否为文件（而非目录） |
| `default_content` | `str` | 否 | 文件的默认内容 |
| `children` | `List[DirectoryNode]` | 否 | 子节点列表 |
