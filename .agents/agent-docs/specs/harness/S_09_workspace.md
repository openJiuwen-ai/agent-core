# S_09 Workspace

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/workspace/`（3 文件） |
| 最近一次修订日期 | 2026-08-23 |
| 关联 feature | N/A |

## 范围 / 边界

本规约定义 harness 的 workspace 子系统：目录语义、链接管理、目录构建。
`workspace/` 只有 3 个文件，是 DeepAgent 的"文件系统地基"。

具体覆盖：

- `workspace/workspace.py`：`Workspace` 类（目录查询 / 链接管理 / 默认 schema）、
  `WorkspaceNode` 枚举、`get_workspace_schema`。
- `workspace/directory_builder.py`：`DirectoryBuilder`（用 `SysOperation` 递归建目录）。
- `workspace/__init__.py`（空导出面）。

不在本规约范围内：
- 工作目录与项目根分离的语义（cwd / project_root）—— `S_02`（`_apply_inherited_artifact_cwd`）
  与 `tools/worktree/`（`S_05`）。
- worktree 自身（git worktree 管理）—— `S_05` / `S_12`。

## 不变量

1. **`Workspace` 是工作区访问的唯一入口**：`get_directory(name)` / `get_node_path(node)`
   查询目录；`set_directory(...)` 设置。`WorkspaceNode` 枚举是内置目录的稳定标识。
2. **目录 schema 唯一来源**：`get_workspace_schema(language="cn")` /
   `Workspace.get_default_directory(language)` 返回 `DirectoryNode` 列表；
   `__post_init__` 校验节点合法性（`_validate_directory_node`）。语言决定默认结构与默认内容
   文件（`_load_default_content(language, file_path)`）。
3. **链接管理有两条命名通道**：`link_team(team_id, target_path)` / `unlink_team` /
   `list_team_links` 与 `link_worktree(slug, target_path)` / `unlink_worktree` /
   `list_worktree_links`；底层统一走 `_list_links(subdir)`。链接用**目录链接**（Windows 上
   `_create_windows_junction`，非 Windows `_create_directory_link`）；`_is_directory_link`
   是链接身份判定。
4. **目录构建走 `DirectoryBuilder`**：`build(directories)` 用 `SysOperation` 递归创建；
   `_is_safe_path` 是路径安全闸（拒绝越界路径）。`SysOperation` 注入来自 `DeepAgent` 的
   `sys_operation`（`S_01` / `S_04` 的 `SysOperationRail`）。
5. **内容基目录**：`_get_content_base_dir()` 定位默认内容文件（语言化默认 README 等）；
   解析失败不炸（降级用默认文本）。
6. **`Workspace` 是公开 API 一员**：`openjiuwen/harness/__init__.py` 导出 `Workspace`
   （`S_01` 不变量 1）；`WorkspaceSpec`（`schema/deep_agent_spec.py`）承载装配侧 workspace
   描述：`root_path` / `language` / `stable_base`。

## 接口契约

```python
class WorkspaceNode(Enum): ...

class Workspace:
    def get_directory(self, name: str | WorkspaceNode) -> str | None
    def get_node_path(self, node: str | WorkspaceNode) -> Path | None
    def set_directory(self, ...) -> None
    def link_team(self, team_id: str, target_path: str) -> Path
    def unlink_team(self, team_id: str) -> bool
    def link_worktree(self, slug: str, target_path: str) -> Path
    def unlink_worktree(self, slug: str) -> bool
    def list_team_links(self) -> list[tuple[str, str]]
    def list_worktree_links(self) -> list[tuple[str, str]]
    @classmethod
    def get_default_directory(cls, language: str = "cn") -> List[DirectoryNode]

def get_workspace_schema(language: str = "cn") -> List[DirectoryNode]

class DirectoryBuilder:
    def __init__(self, sys_operation: SysOperation, root_path: str = "")
    async def build(self, directories: List[Dict]) -> None
```

错误 / 返回语义：

- `get_directory(name)` 未注册 → `None`（不抛）。
- `link_*` 目标不存在 / 链接冲突 → 抛（`_create_directory_link` 内部）。
- `unlink_*` 无此链接 → `False`。
- `DirectoryBuilder.build` 不安全路径 → `_is_safe_path` 拦截（跳过 / 抛，视实现）。

## 数据结构

### DirectoryNode

| 字段 | 语义 |
|---|---|
| `name` | 节点名（`WorkspaceNode` 值） |
| `path` | 相对根路径 |
| `content_file` | 默认内容文件（语言化） |

### workspace 目录族

| 组 | 语义 |
|---|---|
| `get_default_directory` / `get_workspace_schema` | 内置目录 schema（语言化） |
| team / worktree 链接 | 外部编排注入的工作区链接 |

## 与其它 spec 的关系

- workspace 注入 `DeepAgent`（`WorkspaceSpec` → `BuildContext.workspace`）—— `S_01` / `S_12`。
- worktree 目录链接与 `tools/worktree/` 的 `WorktreeManager` 配套 —— `S_05`。
- 目录构建用 `SysOperation`（`SysOperationRail` 100 先铺）—— `S_04`。
- `init_workspace`（`DeepAgent.ensure_initialized` 阶段）消费 `Workspace` —— `S_02`。
- team 侧 workspace 是独立实现（`agent_teams/team_workspace/`），不共享本模块。
