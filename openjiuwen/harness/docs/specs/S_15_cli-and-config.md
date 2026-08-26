# S_15 CLI 与配置

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/cli/`（24 文件） |
| 最近一次修订日期 | 2026-08-23 |
| 关联 feature | N/A |

## 范围 / 边界

本规约定义 harness 的 CLI 子系统：Click 入口、配置三层优先级、agent 装配、
存储、UI。`cli/` 是 OpenJiuWen 命令行前端的独立包（`openjiuwen` / `openjiuwen chat` /
`openjiuwen run PROMPT`）。

具体覆盖：

- `cli/cli.py`：Click 入口（`openjiuwen` REPL 默认 / `chat` REPL 显式 / `run PROMPT`
  非交互单跑）；`_bootstrap_logging`（CLI 模式静音 SDK 日志）。
- `cli/agent/config.py`：配置管理（`SETTINGS_PATH = ~/.openjiuwen/settings.json`，
  **三层优先级：settings.json < 环境变量 < CLI 参数**）、`load_settings_json` /
  `_default_workspace`（`~/.openjiuwen/workspace`）。
- `cli/agent/factory.py`：agent 装配（消费 `S_01` 的 `create_deep_agent`）。
- `cli/prompts/`：CLI 侧 prompt builder。
- `cli/rails/`：`token_tracker.py` / `tool_tracker.py`（CLI 侧 rail）。
- `cli/storage/session_store.py`：会话存储。
- `cli/ui/`：`renderer.py` / `repl.py` / `runner.py` / `todo_render.py` / `tool_display.py`。
- `cli/__main__.py` / `install.sh` / `install.ps1` / `README.md`。

不在本规约范围内：
- 交互 supervisor（`S_02`）与输出租约模型——CLI 只是消费者。
- 工具 / rail / prompt 内部实现——`S_05` / `S_04` / `S_06`。
- agent_teams 的交互式 TUI（`agent_teams/cli/`）——另一子系统。

## 不变量

1. **入口唯一**：`__main__.py` → `cli.py` 的 Click 命令组；命令：隐式 `openjiuwen`（REPL）、
   `chat`（REPL）、`run PROMPT`（单轮非交互）。
2. **配置三层优先级**：`settings.json < 环境变量 < CLI 参数`（`cli/agent/config.py` 模块
   docstring 明示）。`SETTINGS_PATH` 固定 `~/.openjiuwen/settings.json`；
   `_default_workspace` 固定 `~/.openjiuwen/workspace`。
3. **CLI 日志静音**：`_bootstrap_logging` 在 standalone 模式把 `openjiuwen` logger 压到
   CRITICAL、根 logger 压到 WARNING，用 `_NullLogger` 兜底 `LogManager` 未初始化路径；
   pytest 运行时跳过（`"pytest" in sys.modules`）。
4. **装配统一走 harness 构造流**：`cli/agent/factory.py` 调 `create_deep_agent`（`S_01`），
   不在 CLI 另造装配路径。
5. **会话存储独立**：`cli/storage/session_store.py` 管理 CLI 侧会话记录；repl / runner
   经它恢复上一会话（`S_02` session 语义的 CLI 落地）。
6. **渲染面**：`ui/renderer.py`（结果渲染）、`ui/repl.py`（交互循环）、`ui/runner.py`
   （run 驱动）、`ui/todo_render.py` / `ui/tool_display.py`（todo / 工具输出展示）——
   CLI 是 `S_02` 输出租约 / `S_10` 投影的唯一宿主面之一。
7. **CLI rail**：`cli/rails/token_tracker.py` / `tool_tracker.py` 负责 CLI 的 token /
   工具调用追踪，经 `S_04` rail 机制挂载（CLI 专用 rail，不进 `harness/rails/__init__`）。

## 接口契约

```python
# cli/cli.py（Click）
openjiuwen           # 交互式 REPL（默认）
openjiuwen chat      # 交互式 REPL（显式）
openjiuwen run PROMPT  # 非交互单轮

# cli/agent/config.py
SETTINGS_PATH = Path.home() / ".openjiuwen" / "settings.json"
def load_settings_json(path: Optional[Path] = None) -> dict[str, Any]
def _default_workspace() -> str  # ~/.openjiuwen/workspace
```

错误 / 返回语义：

- `_bootstrap_logging` 缺 `extensions.common.configs.log_config` entry-point → 回退
  stdlib logger（不抛）。
- `run PROMPT` 非零退出（agent 失败）→ Click 错误出口。
- 配置解析：settings.json 缺失 → 空 dict 合并（不抛）。

## 数据结构

### CLI 目录（用户主目录）

| 路径 | 用途 |
|---|---|
| `~/.openjiuwen/settings.json` | 用户 settings（第三层优先） |
| `~/.openjiuwen/workspace` | 默认工作区（`_default_workspace`） |
| session store | CLI 会话记录（`cli/storage/session_store.py`） |

### 优先级解析

| 层 | 来源 | 优先级 |
|---|---|---|
| 1 | `settings.json` | 最低 |
| 2 | 环境变量 | 中 |
| 3 | CLI 参数 | 最高 |

## 与其它 spec 的关系

- 装配经 `create_deep_agent` —— `S_01`；运行经交互 supervisor —— `S_02`。
- CLI rail 走 rail 机制 —— `S_04`；prompt 面走 `S_06`；todo/工具渲染对应 `S_05` 的
  todo 工具族与 `S_10` 投影。
- `run PROMPT` 的任务计划输出消费 `TaskPlan` render —— `S_05`。
- 与 `agent_teams/cli/` 的 TUI 互操作（`Runner.interact_agent_team`）是仓库级集成点，
  不在本规约展开。
