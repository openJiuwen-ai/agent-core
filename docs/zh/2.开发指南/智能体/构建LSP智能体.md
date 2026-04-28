# 构建 LSP 智能体

本章节介绍如何使用 `LspRail` 和语言服务器协议（LSP）为 `DeepAgent` 赋予代码导航与诊断能力。挂载该 Rail 后，智能体将获得内置的 `lsp` 工具，可用于浏览源码并获取实时诊断信息——跳转到定义、查找符号引用、列出文件中的所有符号、修改文件内容触发重新分析，以及读取错误或警告信息。

通过本章节，你将了解：

- `LspRail` 的作用及其在智能体生命周期中的位置。
- 如何用一行代码将其挂载到 `DeepAgent`。
- 如何通过 `InitializeOptions` 和 `CustomServerConfig` 调整 LSP 子系统配置。
- `lsp` 工具支持的所有操作及其返回结果。
- 自动诊断注入管道（`after_tool_call` → `before_model_call`）的工作原理。
- 如何在不使用智能体的情况下，直接调用底层的 `initialize_lsp` / `call_lsp_tool` API。

---

## 前提条件

为需要导航的语言安装对应的语言服务器。以 Python 为例，安装 [pyright](https://github.com/microsoft/pyright)，可选以下任意方式：

```bash
# npm（推荐）
npm install -g pyright

# pip
pip install pyright

# pipx（隔离安装）
pipx install pyright
```

设置 LLM 所需的环境变量：

```bash
export API_KEY=...
export API_BASE=https://api.openai.com/v1
export MODEL_NAME=gpt-5.2
export MODEL_PROVIDER=OpenAI
```

---

## 完整示例

可运行的端到端示例请参见 [`examples/lsp/deep_agent_lsp_demo.py`](../../../../examples/lsp/deep_agent_lsp_demo.py)，涵盖全部 9 个演示场景：

| 演示 | 操作 | 说明 |
|---|---|---|
| 1 | `goToDefinition` | 跳转到函数定义处 |
| 2 | `findReferences` | 查找符号的所有引用位置 |
| 3 | `documentSymbol` | 列出文件中的所有符号 |
| 4 | `workspaceSymbol` | 在整个工作区中搜索符号 |
| 5 | `goToImplementation` | 查找抽象方法的具体实现 |
| 6 | `prepareCallHierarchy` | 准备调用层级项 |
| 7 | `incomingCalls` | 查找调用某函数的所有调用方 |
| 8 | `outgoingCalls` | 查找某函数调用的所有函数 |
| 9 | `before_model_call` 注入 | `edit_file` → 自动触发 LSP → 自动注入诊断 → 智能体修复所有错误 |

```bash
# 需要 API_KEY、API_BASE、MODEL_NAME 以及已安装的 pyright
uv run python examples/lsp/deep_agent_lsp_demo.py
```

---

## 工作原理

`LspRail` 是一个 `DeepAgentRail`，负责将 LSP 子系统接入智能体的生命周期：

| 生命周期事件 | `LspRail` 的行为 |
|---|---|
| `init()` — 智能体启动 | 初始化 `LSPServerManager`；向智能体的 `ability_manager` 注册 `LspTool` |
| 智能体运行中 | LLM 可自由调用 `lsp` 工具；语言服务器在首次请求时按需启动；`publishDiagnostics` 通知被缓冲到 `LspDiagnosticRegistry` |
| `after_tool_call` — `edit_file` / `write_file` 执行后 | 向语言服务器发送 `textDocument/didChange`，触发对修改文件的重新分析；新诊断以异步方式（fire-and-forget）缓冲 |
| `before_model_call` — 每次 LLM 调用前 | 消费缓冲的诊断信息，将其作为 `UserMessage` 注入消息列表，使 LLM 无需显式调用任何诊断工具即可感知错误 |
| `uninit()` — 智能体停止 | 移除 `LspTool`；关闭所有语言服务器进程 |

智能体只会看到一个名为 `lsp` 的工具。工具的描述及完整的操作 Schema 会自动注入到系统提示词中，无需手动描述。

---

## 快速开始：带 LSP 的智能体

```python
import asyncio
import os
from openjiuwen.core.foundation.llm import init_model
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.runner import Runner
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.rails.lsp_rail import LspRail
from openjiuwen.harness.lsp import shutdown_lsp

_API_KEY = os.getenv("API_KEY", "your api key here")
_MODEL_NAME = os.getenv("MODEL_NAME", "your model here")
_API_BASE = os.getenv("API_BASE", "your api base here")

async def main():
    await Runner.start()

    model = init_model(
        provider="OpenAI",
        model_name=_MODEL_NAME,
        api_key=_API_KEY,
        api_base=_API_BASE,
    )

    agent = create_deep_agent(
        model=model,
        card=AgentCard(
            name="code_navigator",
            description="通过 LSP 导航源码的智能体。",
        ),
        system_prompt="你是一个代码导航助手，请使用 lsp 工具回答有关代码的问题。",
        rails=[LspRail()],          # <-- 一行代码开启 LSP 能力
        workspace="/path/to/repo",
    )

    try:
        result = await Runner.run_agent(
            agent,
            {"query": "src/models.py 中定义了哪些类？"},
        )
        print(result)
    finally:
        await shutdown_lsp()
        await Runner.stop()

asyncio.run(main())
```

`LspRail()` 不传参数时，会自动继承智能体的 `workspace` 路径并使用默认的语言服务器配置。

### 启用自动诊断注入

要激活 `after_tool_call` → `before_model_call` 管道（详见下一节），需同时挂载 `SysOperationRail`：

```python
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
from openjiuwen.harness.rails.lsp_rail import LspRail
from openjiuwen.harness.lsp import InitializeOptions

rails = [
    SysOperationRail(),  # 提供 edit_file / write_file
    LspRail(options=InitializeOptions(cwd="/path/to/repo"), verbose=True),  # LSP + 自动注入
]
```

两个 Rail 同时挂载后，每次 `edit_file` 或 `write_file` 都会自动触发语言服务器重新分析，分析结果将在下一轮 LLM 上下文中自动注入——无需显式调用任何诊断工具。

---

## 自动诊断注入

`LspRail` 新增了两个协同工作的生命周期钩子，在文件编辑后为智能体提供持续的诊断反馈循环：

### `after_tool_call` — 触发重新分析

每当智能体调用 `edit_file` 或 `write_file`，`LspRail.after_tool_call` 会自动触发。它将：

1. 解析被编辑文件的绝对路径。
2. 向语言服务器发送 `textDocument/didOpen`（若文件从未打开过）以及 `textDocument/didChange`。
3. 立即返回——重新分析以 fire-and-forget 方式异步执行；pyright 在后台运行并将 `publishDiagnostics` 通知缓冲到 `LspDiagnosticRegistry`。

无需任何额外配置，只要挂载了 `LspRail`，该钩子即自动生效。

### `before_model_call` — 将诊断注入上下文

在每次 LLM 调用前，`LspRail.before_model_call` 会消费诊断注册表。若有待处理的诊断信息，会将其格式化后追加为 `UserMessage`，使 LLM 在上下文中直接看到错误：

```
[LSP Diagnostics] The following issues were detected after the last file edit:

File: src/models.py
  [Error] line 12, col 15 (reportArgumentType)  Argument of type "str" cannot be assigned to parameter "age" of type "int"

Please review and fix these issues.
```

智能体随即可以修复错误，无需显式调用任何诊断工具。循环持续进行，直到诊断队列清空为止。

### 详细日志（verbose 模式）

向 `LspRail` 传入 `verbose=True`，可将每次 `before_model_call` 的诊断快照写入带时间戳的日志文件：

```python
LspRail(verbose=True)
```

日志文件位于项目根目录下的 `logs/logs/lsp/lsp_YYYYMMDD_HHMMSS.log`，每次运行创建新文件。适用于对多轮自动修复循环进行离线调试。

---

## `lsp` 工具——操作参考

`LspRail` 激活后，LLM 可调用 `lsp` 工具执行以下 **10 种操作**。

所有位置参数（`line`、`character`）均为 **1-based（从 1 开始）**。工具在发送给语言服务器前会自动转换为 0-based。

### 导航操作

| 操作 | LSP 方法 |
|---|---|
| `documentSymbol` | `textDocument/documentSymbol` |
| `goToDefinition` | `textDocument/definition` |
| `findReferences` | `textDocument/references` |
| `workspaceSymbol` | `workspace/symbol` |
| `goToImplementation` | `textDocument/implementation` |
| `prepareCallHierarchy` | `textDocument/prepareCallHierarchy` |
| `incomingCalls` | `callHierarchy/incomingCalls` |
| `outgoingCalls` | `callHierarchy/outgoingCalls` |

### 诊断操作

| 操作 | 用途 |
|---|---|
| `changeFile` | 发送 `textDocument/didChange` 通知服务器文件内容已变更，触发重新分析 |
| `getDiagnostics` | 消费缓冲的 `publishDiagnostics` 通知，返回格式化的错误/警告信息 |

---

### `documentSymbol` — 列出文件中的所有符号

返回单个文件中定义的全部类、函数、方法和变量，无需指定位置。

```python
{
    "operation": "documentSymbol",
    "file_path": "src/models.py"
}
```

### `goToDefinition` — 跳转到符号定义处

```python
{
    "operation": "goToDefinition",
    "file_path": "src/app.py",
    "line": 42,
    "character": 15
}
```

### `findReferences` — 查找符号的所有引用

```python
{
    "operation": "findReferences",
    "file_path": "src/models.py",
    "line": 10,
    "character": 7,
    "include_declaration": True   # 是否在结果中包含定义位置（默认：True）
}
```

### `workspaceSymbol` — 在整个项目中搜索符号

```python
{
    "operation": "workspaceSymbol",
    "file_path": "",              # 跨工作区搜索时可留空
    "query": "UserRepository"
}
```

### `goToImplementation` — 查找抽象方法的具体实现

```python
{
    "operation": "goToImplementation",
    "file_path": "src/base.py",
    "line": 20,
    "character": 9
}
```

> **注意：** 并非所有语言服务器都支持此操作。例如，pyright 不支持 `textDocument/implementation`。若服务器不支持该操作，工具会返回明确的错误信息，而不会静默失败。

### `prepareCallHierarchy` — 将符号解析为调用层级项

```python
{
    "operation": "prepareCallHierarchy",
    "file_path": "src/services.py",
    "line": 55,
    "character": 5
}
```

### `incomingCalls` — 查找调用某函数的所有调用方

工具内部会自动先调用 `prepareCallHierarchy`，因此只需提供位置信息：

```python
{
    "operation": "incomingCalls",
    "file_path": "src/services.py",
    "line": 55,
    "character": 5
}
```

### `outgoingCalls` — 查找某函数调用的所有函数

```python
{
    "operation": "outgoingCalls",
    "file_path": "src/services.py",
    "line": 55,
    "character": 5
}
```

### `changeFile` — 通知服务器文件内容已变更

先发送 `textDocument/didOpen`（若文件尚未打开），再发送 `textDocument/didChange`。语言服务器会对新内容进行重新分析，并发出 `publishDiagnostics` 通知，该通知将被缓冲以供下一次 `getDiagnostics` 调用使用。

```python
{
    "operation": "changeFile",
    "file_path": "src/models.py",
    "content": "class User:\n    name: str\n    age: int\n"   # 文件完整内容
}
```

`content` 为文件的**完整**新内容（全量同步模式，不支持增量 diff）。

### `getDiagnostics` — 获取缓冲的诊断信息

消费自上次调用以来所有待处理的 `publishDiagnostics` 通知，并以格式化、按严重程度排序的列表返回。每次调用会对批次间的重复条目进行去重，避免同一错误被重复显示。

```python
# 获取所有待处理文件的诊断信息
{
    "operation": "getDiagnostics",
    "file_path": ""
}

# 仅获取指定文件的诊断信息
{
    "operation": "getDiagnostics",
    "file_path": "src/models.py"
}
```

---

## 诊断工作流

### 显式工作流（通过 `lsp` 工具）

使用智能体显式调用工具检查代码错误的典型流程：

```
changeFile  →  （等待服务器重新分析）  →  getDiagnostics
```

触发该工作流的智能体提示示例：

```python
result = await Runner.run_agent(
    agent,
    {
        "query": (
            "将 src/models.py 中的 `age` 字段类型从 `int` 改为 `str`，"
            "然后获取诊断信息，查看 pyright 是否报告了类型错误。"
        )
    },
)
```

智能体将依次调用：

1. `lsp(operation="changeFile", file_path="src/models.py", content="...")` — 将新内容发送给 pyright。
2. `lsp(operation="getDiagnostics", file_path="src/models.py")` — 获取所有类型错误。

### 自动工作流（通过 `after_tool_call` + `before_model_call`）

同时挂载 `SysOperationRail` 后，智能体可直接使用 `edit_file` 编辑文件并自动获得诊断反馈——无需调用 `changeFile` 或 `getDiagnostics`：

```python
result = await Runner.run_agent(
    agent,
    {
        "query": (
            "读取 src/models.py 并修复所有类型错误，"
            "持续修改直到不再有错误为止。"
        )
    },
)
```

每次 `edit_file` 会触发 `after_tool_call`，后者发送 `textDocument/didChange`。在下一轮 LLM 调用前，`before_model_call` 将新诊断作为 `UserMessage` 注入上下文。智能体感知错误后持续修复，直到诊断队列清空。

### 数量上限与去重

| 配置项 | 默认值 |
|---|---|
| 每文件最大诊断条数 | 10 |
| 全局最大诊断条数 | 30 |

在应用数量上限之前，诊断信息会按严重程度排序（Error → Warning → Info → Hint）。跨调用去重机制会抑制已在上次响应中返回过的条目。

---

## 自定义语言服务器

向 `LspRail` 传入 `InitializeOptions` 以覆盖默认服务器配置：

```python
from openjiuwen.harness.lsp import InitializeOptions, CustomServerConfig

rail = LspRail(
    options=InitializeOptions(
        cwd="/path/to/repo",
        custom_servers={
            "pyright": CustomServerConfig(
                command="/usr/local/bin/pyright-langserver",
                args=["--stdio"],
                env={"PYRIGHT_PYTHON_PATH": "/usr/bin/python3"},
            )
        },
    ),
    verbose=True,   # 将诊断快照写入 logs/logs/lsp/
)
```

`LspRail` 构造函数参数：

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `options` | `InitializeOptions \| None` | `None` | LSP 初始化选项（cwd、自定义服务器等）。默认使用智能体的 workspace。 |
| `verbose` | `bool` | `False` | 为 `True` 时，将每次 `before_model_call` 的诊断快照写入时间戳日志文件 `logs/logs/lsp/lsp_YYYYMMDD_HHMMSS.log` |

`CustomServerConfig` 字段说明：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `command` | `str \| None` | `None` | 服务器可执行文件路径 |
| `args` | `list[str] \| None` | `None` | 命令行参数 |
| `env` | `dict[str, str] \| None` | `None` | 附加环境变量 |
| `extensions` | `list[str] \| None` | `None` | 处理的文件扩展名（如 `[".py"]`） |
| `language_id` | `str \| None` | `None` | LSP 语言标识符 |
| `initialization_options` | `dict \| None` | `None` | 在 `initialize` 请求中传递给服务器的选项 |
| `disabled` | `bool` | `False` | 设为 `True` 可完全禁用此服务器 |

`InitializeOptions` 字段说明：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `cwd` | `str \| None` | `None` | 工作目录，默认使用智能体的 workspace |
| `custom_servers` | `dict[str, CustomServerConfig] \| None` | `None` | 以服务器 ID 为键的逐服务器配置覆盖 |

---

## 支持的语言服务器

| 服务器 ID | 语言 | 文件扩展名 | 可执行文件 |
|---|---|---|---|
| `pyright` | Python | `.py`、`.pyi` | `pyright-langserver` |
| `typescript` | TypeScript / JavaScript | `.ts`、`.tsx`、`.js`、`.jsx` | `typescript-language-server` |
| `rust` | Rust | `.rs` | `rust-analyzer` |
| `go` | Go | `.go` | `gopls` |
| `java` | Java | `.java` | `jdtls` |

若初始化时未找到某个服务器的可执行文件，该服务器会被静默跳过，其他语言服务器不受影响。
