# common/exception — Agent Notes

框架统一异常体系。**StatusCode 是语义主键，异常类表达控制语义**——两者解耦，通过映射表绑定。

## 模块地图

```
exception/
├── __init__.py          # 空文件；本模块不从包顶层导出，显式 import 子模块
├── codes.py             # StatusCode 枚举（~930 条），全局错误码的单一真相源
├── errors.py            # BaseError 体系 + build_error / raise_error 工厂
├── status_mapping.py    # StatusCode → 异常类的解析规则（override / keyword / range）
└── code_template.py     # 代码生成辅助：统一生成 StatusCode name / 模板消息
```

## 核心设计点

### StatusCode 是主键，不是附属信息

`StatusCode` 是带 `(code: int, msg: str)` 值的 `Enum`。`msg` 是带占位符的模板（`{workflow}`, `{reason}` 等），实例化时才渲染。

**不要**绕开 `StatusCode` 直接 `raise BaseError("some message")`——所有异常都必须挂一个 StatusCode，否则下游 API/RPC/日志拿不到结构化 `code`。

### 语义分层：异常类 = 控制流

```
BaseError
├── FrameworkError      # fatal=True,  recoverable=False  —— 基础设施/依赖坏了，终止
│   └── ConfigurationError
├── ValidationError     # fatal=False, recoverable=False  —— 输入/约束不对，重试无意义
│   └── GuardrailError
├── ExecutionError      # fatal=False, recoverable=True   —— 执行期错误，可重试/重规划
│   ├── ApplicationError
│   ├── ExternalServiceError / ExternalDataError
│   ├── WorkflowError / ComponentError / AgentError / RunnerError
│   ├── GraphError / ModelError / ToolError / ContextError
│   ├── ToolchainError / SessionError / SysOperationError
└── Termination         # fatal=False, recoverable=False  —— 正常控制流终止（非错误）
    └── RunnerTermination
```

**选异常类 = 选控制语义**（retry? abort? terminate gracefully?）。选 StatusCode = 选错误身份（哪个模块、哪种失败）。两者正交。

### 消息模板 lazy-safe

`BaseError._render_message()` 用 `_SafeDict` + `str.format_map` 渲染——**缺 key 不抛异常**，占位符原样输出为 `<missing:key>`。这是刻意设计：错误路径上不能再产生新的异常。

渲染失败的兜底也只返回原模板字符串，最坏情况下用户看到未填充的占位符，但调用栈不会被二次污染。

## 使用入口

构造异常三种写法，**优先用前两种**：

```python
# 1. 需要抛出 —— 通常场景
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error

raise_error(StatusCode.WORKFLOW_EXECUTION_ERROR, reason=str(e), workflow=wf_id)

# 2. 需要传递对象（延迟抛、包进 Result）
from openjiuwen.core.common.exception.errors import build_error

err = build_error(StatusCode.TOOL_EXECUTION_ERROR, cause=e, reason=str(e))
return Result.fail(err)

# 3. 直接 raise 具体异常类 —— 需要额外字段时才这么做
from openjiuwen.core.common.exception.errors import ToolError
raise ToolError(StatusCode.TOOL_EXECUTION_ERROR, card=tool_card, reason=str(e))
```

`build_error` / `raise_error` 通过 `STATUS_TO_EXCEPTION` 表自动选类。**不要自己拼 `FooError(status, ...)` 除非这个类有特殊构造参数**（目前只有 `ToolError` 带 `card`、`RunnerTermination` 带 `reason`）。

模板占位符通过 `**kwargs` 传入，多余的 key 会保存在 `self.params` 里用于结构化日志。

## StatusCode → Exception 解析规则（status_mapping.py）

三级优先级：

1. **MANUAL_OVERRIDES** — `_MANUAL_OVERRIDES_RAW` 里写死的特例（如 `TOOL_EXECUTION_ERROR → ToolError`）
2. **KEYWORD_RULES** — 按 StatusCode name 关键字匹配（`INVALID/VALIDATE` → ValidationError，`INIT/CONNECT` → FrameworkError 等）
3. **RANGE_RULES** — 按 code 数值区间兜底（100000-119999 → WorkflowError，190000-198999 → SessionError 等）
4. 兜底 `ExecutionError`

**新增 StatusCode 时的行为**：映射在 `build_status_exception_map()` 被调用时构建（`errors.py` 模块级 `STATUS_TO_EXCEPTION = build_status_exception_map()`），所以新增 StatusCode 会自动并入。如果默认解析不对，优先调整命名让它命中关键字规则；实在不行才加 `_MANUAL_OVERRIDES_RAW`。

### 循环依赖规避

`status_mapping.py` 依赖 `errors.py` 的异常类，但 `errors.py` 又要用 `build_status_exception_map()`。解法：`_get_exception_class_registry()` 是**函数内 lazy import**，不在模块顶层 import `errors`。**不要把这个 lazy import 改成模块级**，会立刻破坏 import 顺序。

## StatusCode 枚举规范（codes.py）

`codes.py` 近千行，按数字区间分段，每段前有注释块标识 scope 和 failure 类别。**保持这个分段结构**——它是人肉索引，是这个文件可维护的唯一理由。

### 区间分配（见 `code_template._code_range_by_scope`）

| Scope | Range |
|---|---|
| WORKFLOW | 100000–100999 |
| COMPONENT | 101000–119999 |
| AGENT | 120000–129999 |
| RUNNER | 130000–139999 |
| GRAPH | 140000–149999 |
| CONTEXT | 150000–154999 |
| RETRIEVAL | 155000–157999 |
| MEMORY | 158000–159999 |
| TOOLCHAIN | 160000–179999 |
| PROMPT | 180000–180999 |
| MODEL | 181000–181999 |
| TOOL | 182000–182999 |
| COMMON | 188000–188999 |
| SESSION | 190000–198999 |
| SYS_OPERATION | 199000–199999 |

**新增 code 前先确认区间**——不要跨段塞。区间和 `status_mapping.RANGE_RULES` 必须保持一致，改一处就得同步另一处。

### 命名规范（code_template.py）

`{SCOPE}_{SUBJECT}_{FAILURE_TYPE}`，例如 `WORKFLOW_COMPONENT_ID_INVALID`。

- `SCOPE` ∈ `ALLOWED_SCOPES`
- `FAILURE_TYPE` ∈ `ALLOWED_FAILURE_TYPES`：
  - Validation 语义：`INVALID / NOT_FOUND / NOT_SUPPORTED / CONFIG_ERROR / PARAM_ERROR / TYPE_ERROR`
  - Framework 语义：`INIT_FAILED / CALL_FAILED`
  - Execution 语义：`EXECUTION_ERROR / RUNTIME_ERROR / PROCESS_ERROR / TIMEOUT / INTERRUPTED`

failure_type 直接决定 `status_mapping` 的关键字匹配结果——**命名对了，分类就对了**。

### 消息模板规范

- 统一英文、小写 scope/subject：`"workflow execution has error, error='{reason}', workflow='{workflow}'"`
- 可参数化：占位符用 `{name}`，不要用 `{0}` 或 `%s`
- 超时类模板需要 `{timeout}`，通用错误类模板需要 `{error_msg}` 或 `{reason}`
- 占位符缺失会渲染成 `<missing:key>`——调用方漏传参数不会炸，但会留下可搜的痕迹

## 测试关注点

- 新增 StatusCode 后跑一次 `build_status_exception_map()`，确认分类命中预期（没有意外落到 `ExecutionError` 兜底）
- 模板带占位符时，验证 `BaseError(status, ...).message` 和 `to_dict()["message"]` 渲染正确
- 序列化走 `__reduce__`——测试跨进程传递（`multiprocessing` / pickle round-trip）不丢 `status / details / cause / params`
- 单测路径：`tests/unit_tests/core/common/exception/`

## 禁忌

1. **不要 `raise Exception(...)` / `raise RuntimeError(...)`** — 项目内部抛异常必走 StatusCode 体系，外部库抛的异常在边界处转成 BaseError
2. **不要在业务代码里 `except BaseError as e: return e.status.code`** — 要判断控制语义请用 `isinstance(e, ExecutionError)` / `e.recoverable` / `e.fatal`
3. **不要给 StatusCode 塞运行时数据** — 它是枚举常量，线程安全靠的就是不可变。动态字段走 `details=` / `**kwargs → params`
4. **不要重复定义 code 数值** — 区间是硬分段，但 Python Enum 本身不检测重复值（会 alias）。新增前 grep 一下
5. **不要在 `_render_message` / `_format_template` 里抛异常** — 错误路径不能产生错误
