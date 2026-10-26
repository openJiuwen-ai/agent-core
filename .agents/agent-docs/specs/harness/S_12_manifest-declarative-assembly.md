# S_12 Manifest 声明式装配

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/manifest/`（9 文件） |
| 最近一次修订日期 | 2026-08-23 |
| 关联 feature | N/A |

## 范围 / 边界

本规约定义 harness 的 manifest 声明式装配子系统：描述符（descriptor）、目录（catalog）、
工厂引用（factory_ref）、输入模型、注册面。它把"配置一段 rails / 工具 / 子代理"从命令式
`create_deep_agent` 参数里剥离，变成可序列化、可发现的元素目录。

具体覆盖：

- `manifest/models.py`：`ElementKind`（TOOL / RAIL / SUBAGENT）、`InterfaceMethod`、
  `HarnessElementDescriptor`。
- `manifest/catalog.py`：`add_descriptor` / `get_catalog` / `list_elements` /
  `harness_element` 装饰器 / `record`。
- `manifest/introspect.py`：`factory_ref` / `resolve_factory`（`"module:qualname"` 编解码）。
- `manifest/inputs.py`：`InputSource`（PARAMS / CONTEXT）、`ConstructionInput` /
  `EmptyInput` / `param_field` / `context_field`。
- `manifest/harness_elements.py`：内置元素构建器（`_build_progressive_tool_rail` 等 +
  `build_*_subagent` 7 个预设）。
- `manifest/meta_elements.py`：元元素加载（dotted path / entry point / 临时 sys.path /
  模块遮蔽防护）。
- `manifest/builtin_elements.py`：内建元素（`_build_skill_use_rail` / worktree / lsp /
  web / vision / audio 组）。
- `manifest/registration.py`：`register_from_catalog` / `ensure_builtin_elements_registered`。
- `manifest/__init__.py` 导出面（`__all__` 16 符号）。

不在本规约范围内：
- 包加载（`resources/` 读 manifest 文件）—— `S_13`；`resources/`
  与 `extension_binder` 真正消费本 catalog。
- 各内置元素的实现 —— 各模块。
- DeepAgentSpec 装配流 —— `S_01` / `S_13`（Spec 模型）。

## 不变量

1. **描述符是 catalog 唯一单元**：`HarnessElementDescriptor` 承载 `kind` / `name` /
   `description` / `factory_ref` / `input_schema` / `input_model_ref` / `interface_methods`。
   `add_descriptor` 是注册唯一入口；`get_catalog()` 返回只读目录 dict；`list_elements()`
   返回可展示列表。
2. **元素三态**：`ElementKind.TOOL` / `RAIL` / `SUBAGENT`。**新增 kind = 改 models + 消费方 +
   本 spec**。
3. **工厂引用编解码唯一**：`factory_ref(target) -> "module:qualname"`；
   `resolve_factory(ref)` 反解为 live callable / class。目标必须真实存在于 `target.__module__`。
4. **输入模型两级**：`ConstructionInput`（pydantic）声明装配输入，`InputSource.PARAMS`
   （参数）与 `InputSource.CONTEXT`（构建上下文）区分来源；`param_field` / `context_field`
   是声明宏。`EmptyInput` = 无参元素。
5. **装饰器注册**：`@harness_element(...)` 把构建器（工厂函数 / 类）注册进 catalog；
   `record` 是其底层原语。构建器形态统一：`factory(params: dict, context) -> 元素`。
6. **内置元素可恢复**：`ensure_builtin_elements_registered()` 幂等注册全部内建
   （`harness_elements.py` + `builtin_elements.py`）；`register_from_catalog()` 批量注册
   catalog 外的扩展。`manifest/__init__` 的 `__all__` 是 public 面。
7. **元元素加载防遮蔽**：`meta_elements.py` 的临时 sys.path 加载带模块遮蔽防护
   （`_snapshot_shadowable_modules` / `_evict_conflicting_shadowed_modules` /
   `_restore_module_attrs`）——加载扩展包不得污染主进程模块查找。
8. **类 rail 统一**：`introspect.py` 的 `_RAIL_INTERNAL_METHODS`（`{"get_callbacks"}`）
   把无工厂类 rail 和有参工厂 rail 统一成同一 provider 契约；接口方法用
   `InterfaceMethod`（`name` / `description` / `is_async`）描述。

## 接口契约

```python
class ElementKind(str, Enum):
    TOOL = "tool"
    RAIL = "rail"
    SUBAGENT = "subagent"

class InterfaceMethod(BaseModel):
    name: str
    description: str = ""
    is_async: bool = False

class HarnessElementDescriptor(BaseModel):
    kind: ElementKind
    name: str
    description: str
    factory_ref: str
    input_schema: dict[str, Any] = {}
    input_model_ref: str | None = None
    interface_methods: list[InterfaceMethod] = []

class InputSource(str, Enum):
    PARAMS = "params"
    CONTEXT = "context"

def harness_element(...) -> Callable[[Callable[..., Any] | type], Callable[..., Any] | type]
def add_descriptor(descriptor: HarnessElementDescriptor) -> None
def get_catalog() -> dict[str, HarnessElementDescriptor]
def list_elements() -> list[dict[str, Any]]
def factory_ref(target: Callable[..., Any] | type) -> str
def resolve_factory(ref: str) -> Any
def register_from_catalog() -> None
def ensure_builtin_elements_registered() -> None
```

错误 / 返回语义：

- `resolve_factory` 引用不存在 → 抛（import 失败 / AttributeError 向上）。
- `add_descriptor` 同名重复 → 覆盖或抛（以 `catalog.py` 实现为准，默认后者）。
- `get_catalog()` 未注册任何元素 → 空 dict（不抛）。

## 数据结构

### 描述符注册流

| 阶段 | 组件 | 说明 |
|---|---|---|
| 声明 | `@harness_element` + `ConstructionInput` | 构建器 + 输入模型 |
| 注册 | `add_descriptor` / `ensure_builtin_elements_registered` | 进 catalog |
| 发现 | `get_catalog` / `list_elements` | 宿主 / 工具查询 |
| 解析 | `resolve_factory` + `ConstructionInput.resolve(params, context)` | 拿 live 构建器 |
| 装配 | factory 调用 → 元素实例 | 进 `DeepAgent`（rails/tools/subagents） |

### 内建元素族（节选）

| 族 | 元素 | 消费 |
|---|---|---|
| rail | `_build_progressive_tool_rail` / `_build_memory_rail` / `_build_verification_rail` / `_build_skill_create_rail` / worktree / lsp / skill_use | `S_04` |
| subagent | `build_explore/plan/browser/code/research/verification/general_purpose_subagent` | `S_18` |
| tool 组 | web（free_search/fetch/paid_search）/ vision / audio | `S_05` |



### BuildContext（`schema/build_context.py`）

| 字段 | 语义 |
|---|---|
| `language` / `member_name` / `role` | 装配参数 |
| `workspace` / `member_card_id` / `project_dir` | 环境绑定 |
| `extras` | 自由键 |
| `factory` | 重建 live context 的注册 factory（跨序列化重建，`S_13` 消费） |

## 与其它 spec 的关系

- `resources/` 加载 manifest 文件并用 `resolve_factory` 解析元素 —— `S_13`；
  `DeepAgentSpec.subagents` 里的 `factory_name` / `factory_kwargs` 即 `factory_ref` 载体 ——
  `S_13`。
- `extension_binder` 把解析出的 rails/tools/subagents 绑到 `DeepAgent` —— `S_02` / `S_04`。
- subagent 元素落到 `S_18` 的预设；rail 元素落到 `S_04`；tool 元素落到 `S_05`。
- 本 catalog 是 harness 侧对 `agent_teams` Spec 注册表思想的同构实现（`S_01` 不变量 9）。
