# S_08 安全引擎

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/security/`（14 文件）、`openjiuwen/harness/rails/security/` |
| 最近一次修订日期 | 2026-08-23 |
| 关联 feature | N/A |

## 范围 / 边界

本规约定义 harness 的安全引擎：权限模型、文件守卫、层级策略、宿主接口。
`security/` 是工具执行的安全面，`rails/security/` 是把它挂接到 `S_04` rail 体系的桥。

具体覆盖：

- `security/models.py`：`PermissionLevel`（ALLOW / ASK / DENY）、`PermissionResult`、
  `PermissionsSection` / `FileGuardSection` / `FileGuardPathEntry` / `FileGuardDefaults` /
  `ApprovalOverrideEntry`。
- `security/core.py`：`PermissionEngine`（`check_permission` / 全局策略求值 / trusted dirs /
  config 更新）。
- `security/file_guard.py`：`FileGuardChecker` + 路径规则编译 + action 提取。
- `security/tiered_policy.py`：内置规则 YAML + `severity_to_decision` / `strictest` /
  `_tool_category`。
- `security/checker.py`：`ExternalDirectoryChecker`（外部路径校验）。
- `security/host.py`：`ToolPermissionHost` / `PermissionConfirmationRequest` / `PermissionSceneHookInput`
  / `RequestPermissionConfirmationHook`。
- `security/factory.py`：`build_permission_interrupt_rail` 等装配。
- `rails/security/`：`BaseSecurityRail` / `PermissionInterruptRail` / `SafetyPromptRail` /
  `SecurityRail`（决策类型见 `S_04`）。

不在本规约范围内：
- rail 基类与事件路由 —— `S_04`。
- 工具契约本身 —— `S_05`。
- 权限链路在 `deep_agent.py` 的具体挂点 —— `S_02` / `S_04`。

## 不变量

1. **权限三态唯一**：`PermissionLevel.ALLOW`（直接执行，无需确认）/ `ASK`（弹确认框，用户决定）/
   `DENY`（拒绝执行，返回错误）。任何权限决策最终落到这三态；`severity_to_decision` /
   `strictest` 是把规则 / 多轴折成单态的唯一工具。
2. **`PermissionEngine` 是权限求值唯一入口**：`async check_permission(...)` 返回
   `PermissionResult`；`check_tool_permission_directly` / `evaluate_global_policy_directly`
   是同步直查；`enabled()` 门控；`update_config` / `update_trusted_dirs` / `update_llm`
   运行时可改。
3. **`PermissionsSection` 是权限配置的唯一载入形态**；`update_config` 接受
   `PermissionsSection | dict`；`write_permissions_section_to_agent_config_yaml` 落盘；
   `merge_*_allow_into_permissions` 系列是外部 allow 合并的唯一路径。
4. **文件守卫是独立的第二决策轴**：`FileGuardChecker`（`security/file_guard.py`）基于
   `EffectiveFileGuardConfig` 求值工具访问路径；`normalize_path_guard_config` 是配置归一唯一
   入口（legacy = `_normalize_legacy`，native = `_normalize_native`）；`FileGuardAction` /
   `FileGuardMode` 定义 in 本模块。
5. **外部目录校验**：`ExternalDirectoryChecker.check_external_paths` 校验 shell 命令引用的
   外部路径；`merge_external_directory_allow_into_permissions` 把放行合并回权限。shell AST
   解析（`security/shell_ast.py`）是**唯一**从命令文本抽路径的途径。
6. **层级策略**：`tiered_policy.get_builtin_security_rules()` 从内置 YAML（`resources/
   builtin_rules.yaml` 同类机制）读取规则；`severity_to_decision(severity, permission_mode)`
   把严重度折成 `PermissionLevel`；`_tool_category` 给工具分类。内置规则路径经
   `get_package_builtin_rules_path()` 解析。
7. **宿主接口**：`ToolPermissionHost` 是工具的权限宿主协议；`RequestPermissionConfirmationHook`
   （`PermissionSceneHookInput` → `PermissionConfirmationResult`）是确认回调契约；
   `PermissionConfirmationRequest` 携带确认请求。
8. **到 rail 的桥唯一**：`security/factory.py:build_permission_interrupt_rail(...)` 构造
   `PermissionInterruptRail`；`deep_agent.py` 的 `build_permission_interrupt_rail` 导入路径
   一致。安全 rail 特性（`supported_events` / 静默降级）见 `S_04` 不变量 8。

## 接口契约

```python
class PermissionLevel(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"

class PermissionEngine:
    def enabled(self) -> bool
    def update_config(self, config: PermissionsSection | dict[str, Any]) -> None
    def update_trusted_dirs(self, trusted_dirs: list[Path]) -> None
    def trusted_dirs(self) -> list[Path]
    def update_llm(self, llm: Any, model_name: str | None) -> None
    def set_permission_checks_active(self, fn: Callable[[], bool] | None) -> None
    def check_tool_permission_directly(self, ...) -> PermissionResult
    def evaluate_global_policy_directly(self, ...) -> PermissionResult
    async def check_permission(self, ...) -> PermissionResult

class FileGuardChecker:
    def enabled(self) -> bool
    def mode(self) -> FileGuardMode
    def evaluate(self, ...) -> PermissionLevel
    def collect_ask_accesses(self, ...) -> list[Any]

class FileGuardPathRule: ...
class FileGuardAxisDefaults: ...
class EffectiveFileGuardConfig: ...
def normalize_path_guard_config(...) -> EffectiveFileGuardConfig

class ExternalDirectoryChecker:
    def check_external_paths(self, ...) -> Any

def build_permission_interrupt_rail(...) -> PermissionInterruptRail
def merge_permission_allow_rule_into_permissions(...) -> Any
def merge_external_directory_allow_into_permissions(...) -> Any
def merge_file_guard_access_allows(...) -> Any
def persist_cli_trusted_directory(...) -> None
def write_permissions_section_to_agent_config_yaml(...) -> None
```

错误 / 返回语义：

- `check_permission` 决出 `PermissionResult`（级别 + 原因），不抛业务异常。
- 外部目录校验失败 → 拒绝执行（`DENY` 路径），`check_external_paths` 返回可注入权限的
  放行集。
- `persist_cli_trusted_directory` 无写权限 → 抛（CLI 侧处理）。

## 数据结构

### PermissionsSection（关键字段族）

| 字段 | 语义 |
|---|---|
| `approval_overrides` | `ApprovalOverrideEntry` 列表（工具名 → 级别） |
| `file_guard` | `FileGuardSection` / `FileGuardDefaults` / `FileGuardPathEntry` |
| 外部 allow | `merge_*` 系列合并进来的放行规则 |
| 全局策略 | `evaluate_global_policy_directly` 消费的全局级规则 |

### 决策轴

| 轴 | 组件 | 输出 |
|---|---|---|
| 工具权限 | `PermissionEngine.check_permission` | `PermissionResult` |
| 文件路径 | `FileGuardChecker.evaluate` | `PermissionLevel` |
| 外部路径 | `ExternalDirectoryChecker.check_external_paths` | 放行集 / 拒绝 |
| 内置规则 | `tiered_policy` YAML | `severity → PermissionLevel` |
| 宿主确认 | `RequestPermissionConfirmationHook` | `PermissionConfirmationResult` |

## 与其它 spec 的关系

- 安全 rail（`BaseSecurityRail` / `PermissionInterruptRail`）挂接 rail 体系 —— `S_04`
  （含 `S_04` 不变量 8 的静默降级坑）。
- 工具执行走权限链 —— `S_05`；权限挂在 `DeepAgent` 的 `permissions` / `permission_host`
  配置字段 —— `S_01`。
- 内置规则 YAML 与 `resources/builtin_rules.yaml` 同型 —— `S_13` 的资源加载。
- shell AST 抽取路径供 `ExternalDirectoryChecker` 使用，与 `BashTool` / `PowerShellTool`
  的 shell 工具定义配套 —— `S_05`。
- 与 `agent_teams` 的 `tools/AGENTS.md` 权限体系是同一 `core` 权限基座的不同宿主面，
  各自独立维护。
