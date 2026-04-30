# Building an LSP-Powered Agent

This guide shows how to give a `DeepAgent` code-navigation and diagnostic capabilities using `LspRail` and the Language Server Protocol (LSP). When the rail is attached, the agent gains a built-in `lsp` tool that it can call to navigate source code and retrieve live diagnostics — jumping to definitions, finding all usages of a symbol, listing file symbols, changing file content to trigger re-analysis, and reading back any errors or warnings.

After reading this guide you will understand:

- What `LspRail` is and how it fits into the agent lifecycle.
- How to attach it to a `DeepAgent` with one line.
- How to use `InitializeOptions` and `CustomServerConfig` to tune the LSP subsystem.
- What operations the `lsp` tool exposes and what each one returns.
- How the automatic diagnostic injection pipeline (`after_tool_call` → `before_model_call`) works.
- How to use the lower-level `initialize_lsp` / `call_lsp_tool` API without an agent.

---

## Prerequisites

Install a language server for the languages you want to navigate. For Python, install [pyright](https://github.com/microsoft/pyright) using any of the following methods:

```bash
# npm (recommended)
npm install -g pyright

# pip
pip install pyright

# pipx (isolated install)
pipx install pyright
```

Set the environment variables your LLM requires:

```bash
export API_KEY=...
export API_BASE=https://api.openai.com/v1
export MODEL_NAME=gpt-5.2
export MODEL_PROVIDER=OpenAI
```

---

## Complete Example

See [`examples/lsp/deep_agent_lsp_demo.py`](../../../../examples/lsp/deep_agent_lsp_demo.py) for a runnable end-to-end example covering all 9 demos:

| Demo | Operation | Description |
|---|---|---|
| 1 | `goToDefinition` | Jump to a function's definition |
| 2 | `findReferences` | Find all usages of a symbol |
| 3 | `documentSymbol` | List all symbols in a file |
| 4 | `workspaceSymbol` | Search symbols across the whole project |
| 5 | `goToImplementation` | Find implementations of an abstract method |
| 6 | `prepareCallHierarchy` | Prepare a call hierarchy item |
| 7 | `incomingCalls` | Find all callers of a function |
| 8 | `outgoingCalls` | Find all functions called by a function |
| 9 | `before_model_call` injection | `edit_file` → auto-trigger LSP → auto-inject diagnostics → agent fixes all errors |

```bash
# Requires API_KEY, API_BASE, MODEL_NAME, and pyright installed
uv run python examples/lsp/deep_agent_lsp_demo.py
```

---

## How It Works

`LspRail` is a `DeepAgentRail` that wires the LSP subsystem into an agent's lifecycle:

| Lifecycle event | What `LspRail` does |
|---|---|
| `init()` — agent starts | Initializes `LSPServerManager`; registers `LspTool` on the agent's `ability_manager` |
| Agent runs | LLM calls the `lsp` tool freely; language servers start lazily on first request; `publishDiagnostics` notifications are buffered in `LspDiagnosticRegistry` |
| `after_tool_call` — after `edit_file` / `write_file` | Sends `textDocument/didChange` to the language server so it re-analyses the modified file; fresh diagnostics are buffered asynchronously (fire-and-forget) |
| `before_model_call` — before each LLM call | Drains buffered diagnostics and injects them as a `UserMessage` so the LLM sees errors without calling any diagnostic tool explicitly |
| `uninit()` — agent stops | Removes `LspTool`; shuts down all language server processes |

The agent sees a single tool named `lsp`. Its description and full operation schema are automatically injected into the system prompt — you do not need to describe it manually.

---

## Quickstart: Agent with LSP

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
            description="Navigates source code via LSP.",
        ),
        system_prompt="You are a code-navigation assistant. Use the lsp tool to answer questions.",
        rails=[LspRail()],          # <-- one line to add LSP
        workspace="/path/to/repo",
    )

    try:
        result = await Runner.run_agent(
            agent,
            {"query": "What classes are defined in src/models.py?"},
        )
        print(result)
    finally:
        await shutdown_lsp()
        await Runner.stop()

asyncio.run(main())
```

`LspRail()` with no arguments inherits the `workspace` path from the agent and uses default language server settings.

### Enabling automatic diagnostic injection

To activate the `after_tool_call` → `before_model_call` pipeline (described in the next section), add `SysOperationRail` alongside `LspRail`:

```python
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
from openjiuwen.harness.rails.lsp_rail import LspRail
from openjiuwen.harness.lsp import InitializeOptions

rails = [
    SysOperationRail(),  # provides edit_file / write_file
    LspRail(options=InitializeOptions(cwd="/path/to/repo"), verbose=True),  # LSP + auto-inject
]
```

With both rails active, every `edit_file` or `write_file` call automatically triggers a language server re-analysis, and the resulting diagnostics are injected into the next LLM context — no explicit diagnostic tool calls needed.

---

## Automatic Diagnostic Injection

`LspRail` adds two complementary lifecycle hooks that work together to give the agent a continuous feedback loop after file edits:

### `after_tool_call` — trigger re-analysis

Whenever the agent calls `edit_file` or `write_file`, `LspRail.after_tool_call` fires automatically. It:

1. Resolves the edited file's absolute path.
2. Sends `textDocument/didOpen` (if the file was never opened) followed by `textDocument/didChange` to the language server.
3. Returns immediately — the re-analysis is fire-and-forget; pyright runs asynchronously and publishes `publishDiagnostics` notifications that are buffered in `LspDiagnosticRegistry`.

No configuration is needed — the hook is active whenever `LspRail` is attached.

### `before_model_call` — inject diagnostics into context

Before each LLM call, `LspRail.before_model_call` drains the diagnostic registry. If any diagnostics are pending it formats them and appends a `UserMessage` to the message list so the LLM sees the errors in its context:

```
[LSP Diagnostics] The following issues were detected after the last file edit:

File: src/models.py
  [Error] line 12, col 15 (reportArgumentType)  Argument of type "str" cannot be assigned to parameter "age" of type "int"

Please review and fix these issues.
```

The agent can then fix the errors without ever calling a diagnostic tool explicitly. The loop continues until no diagnostics remain.

### Verbose logging

Pass `verbose=True` to `LspRail` to write every `before_model_call` diagnostic snapshot to a timestamped log file:

```python
LspRail(verbose=True)
```

Log files are created at `logs/logs/lsp/lsp_YYYYMMDD_HHMMSS.log` under the project root, with a fresh file on each run. Useful for offline debugging of multi-turn fix loops.

---

## The `lsp` Tool — Operations Reference

When `LspRail` is active the LLM can call the `lsp` tool with any of the following **10 operations**.

All position arguments (`line`, `character`) are **1-indexed**. The tool converts them to 0-indexed values before sending to the language server.

### Navigation operations

| Operation | LSP Method |
|---|---|
| `documentSymbol` | `textDocument/documentSymbol` |
| `goToDefinition` | `textDocument/definition` |
| `findReferences` | `textDocument/references` |
| `workspaceSymbol` | `workspace/symbol` |
| `goToImplementation` | `textDocument/implementation` |
| `prepareCallHierarchy` | `textDocument/prepareCallHierarchy` |
| `incomingCalls` | `callHierarchy/incomingCalls` |
| `outgoingCalls` | `callHierarchy/outgoingCalls` |

### Diagnostic operations

| Operation | Purpose |
|---|---|
| `changeFile` | Send `textDocument/didChange` to notify the server of new content; triggers re-analysis |
| `getDiagnostics` | Drain buffered `publishDiagnostics` notifications and return formatted errors/warnings |

---

### `documentSymbol` — list all symbols in a file

Returns every class, function, method, and variable defined in a single file. No position needed.

```python
{
    "operation": "documentSymbol",
    "file_path": "src/models.py"
}
```

### `goToDefinition` — jump to a symbol's definition

```python
{
    "operation": "goToDefinition",
    "file_path": "src/app.py",
    "line": 42,
    "character": 15
}
```

### `findReferences` — find all usages of a symbol

```python
{
    "operation": "findReferences",
    "file_path": "src/models.py",
    "line": 10,
    "character": 7,
    "include_declaration": True   # include the definition site in results (default: True)
}
```

### `workspaceSymbol` — search symbols across the whole project

```python
{
    "operation": "workspaceSymbol",
    "file_path": "",              # can be empty for workspace-wide search
    "query": "UserRepository"
}
```

### `goToImplementation` — find concrete implementations of an abstract method

```python
{
    "operation": "goToImplementation",
    "file_path": "src/base.py",
    "line": 20,
    "character": 9
}
```

> **Note:** Not all language servers implement this operation. Pyright, for example, does not support `textDocument/implementation`. The tool returns a clear error message if the server does not support the operation, rather than failing silently.

### `prepareCallHierarchy` — resolve a symbol into a call hierarchy item

```python
{
    "operation": "prepareCallHierarchy",
    "file_path": "src/services.py",
    "line": 55,
    "character": 5
}
```

### `incomingCalls` — find all callers of a function

The tool automatically calls `prepareCallHierarchy` first, so you only need the position:

```python
{
    "operation": "incomingCalls",
    "file_path": "src/services.py",
    "line": 55,
    "character": 5
}
```

### `outgoingCalls` — find all functions called by a function

```python
{
    "operation": "outgoingCalls",
    "file_path": "src/services.py",
    "line": 55,
    "character": 5
}
```

### `changeFile` — notify the server of new file content

Sends `textDocument/didOpen` (if the file has not been opened yet) followed by `textDocument/didChange`. The language server re-analyses the new content and emits a `publishDiagnostics` notification that is buffered for the next `getDiagnostics` call.

```python
{
    "operation": "changeFile",
    "file_path": "src/models.py",
    "content": "class User:\n    name: str\n    age: int\n"   # full file text
}
```

`content` is the **complete** new text of the file (full-sync mode — no diffs).

### `getDiagnostics` — retrieve buffered diagnostics

Drains all pending `publishDiagnostics` notifications emitted since the last call and returns them as a formatted, severity-sorted list. Each call deduplicates entries across batches to avoid showing the same error twice.

```python
# All files with pending diagnostics
{
    "operation": "getDiagnostics",
    "file_path": ""
}

# Filtered to a single file
{
    "operation": "getDiagnostics",
    "file_path": "src/models.py"
}
```

---

## Diagnostic Workflow

### Explicit workflow (via `lsp` tool)

The typical pattern for using the agent to check code for errors with explicit tool calls:

```
changeFile  →  (wait for server re-analysis)  →  getDiagnostics
```

Example agent prompt:

```python
result = await Runner.run_agent(
    agent,
    {
        "query": (
            "Change src/models.py so that the `age` field is typed as `str` instead of `int`, "
            "then get the diagnostics to see if pyright reports any type errors."
        )
    },
)
```

The agent will call:

1. `lsp(operation="changeFile", file_path="src/models.py", content="...")` — sends the new content to pyright.
2. `lsp(operation="getDiagnostics", file_path="src/models.py")` — retrieves any type errors.

### Automatic workflow (via `after_tool_call` + `before_model_call`)

When `SysOperationRail` is also attached, the agent can edit files with `edit_file` and receive diagnostic feedback automatically — no `changeFile` or `getDiagnostics` calls needed:

```python
result = await Runner.run_agent(
    agent,
    {
        "query": (
            "Read src/models.py and fix all type errors. "
            "Keep editing until no errors remain."
        )
    },
)
```

Each `edit_file` call triggers `after_tool_call`, which fires `textDocument/didChange`. Before the next LLM turn, `before_model_call` injects the new diagnostics as a `UserMessage`. The agent sees the errors and continues fixing until the diagnostic queue is empty.

### Caps and deduplication

| Setting | Default |
|---|---|
| Max diagnostics per file | 10 |
| Max diagnostics total | 30 |

Diagnostics are sorted by severity (Error → Warning → Info → Hint) before the cap is applied. Cross-call deduplication suppresses entries that were already delivered in a previous response.

---

## Customizing Language Servers

Pass `InitializeOptions` to `LspRail` to override the default server configuration:

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
    verbose=True,   # write diagnostic snapshots to logs/logs/lsp/
)
```

`LspRail` constructor parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `options` | `InitializeOptions \| None` | `None` | LSP initialization options (cwd, custom servers). Defaults to the agent's workspace. |
| `verbose` | `bool` | `False` | When `True`, writes every `before_model_call` diagnostic snapshot to a timestamped file at `logs/logs/lsp/lsp_YYYYMMDD_HHMMSS.log` |

`CustomServerConfig` fields:

| Field | Type | Default | Description |
|---|---|---|---|
| `command` | `str \| None` | `None` | Path to the server executable |
| `args` | `list[str] \| None` | `None` | Command-line arguments |
| `env` | `dict[str, str] \| None` | `None` | Extra environment variables |
| `extensions` | `list[str] \| None` | `None` | File extensions to handle (e.g. `[".py"]`) |
| `language_id` | `str \| None` | `None` | LSP language identifier |
| `initialization_options` | `dict \| None` | `None` | Passed to the server in the `initialize` request |
| `disabled` | `bool` | `False` | Set `True` to disable this server entirely |

`InitializeOptions` fields:

| Field | Type | Default | Description |
|---|---|---|---|
| `cwd` | `str \| None` | `None` | Working directory; defaults to the agent's workspace |
| `custom_servers` | `dict[str, CustomServerConfig] \| None` | `None` | Per-server overrides keyed by server ID |

---

## Supported Language Servers

| Server ID | Language | File extensions | Executable |
|---|---|---|---|
| `pyright` | Python | `.py`, `.pyi` | `pyright-langserver` |
| `typescript` | TypeScript / JavaScript | `.ts`, `.tsx`, `.js`, `.jsx` | `typescript-language-server` |
| `rust` | Rust | `.rs` | `rust-analyzer` |
| `go` | Go | `.go` | `gopls` |
| `java` | Java | `.java` | `jdtls` |

If a server binary is not found during initialization, that server is skipped silently. Other language servers continue to work.
