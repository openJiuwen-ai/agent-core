# OpenJiuWen CLI

> 终端交互式 AI 编程助手 — 说一句话，Agent 自动完成文件读写、代码搜索、Shell 执行

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-64%20passed-brightgreen.svg)](../../tests/cli/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)

## 项目简介

**OpenJiuWen CLI** 是基于 [openJiuwen agent-core SDK](https://gitcode.com/openJiuwen/agent-core) 构建的终端 AI 编程助手。在终端中启动 `openjiuwen`，即可进入智能编程对话环境：

- 用自然语言描述需求，Agent 自动调用工具完成任务
- 支持多轮对话，Agent 保持上下文理解
- 支持流式输出，逐字渲染到终端
- 支持 OPENJIUWEN.md 项目记忆文件
- 支持交互式（REPL）和非交互式两种模式

```
┌─── OpenJiuWen CLI v0.1.0 ──────────────────────────────────────────────────┐
│                                │ Tips for getting started                   │
│         Welcome to             │ Create an OPENJIUWEN.md for project rules  │
│                                │ ──────────────────────────────────────     │
│           ████                 │ Commands                                   │
│          ██  ██                │   /help      Show available commands       │
│           ████                 │   /status    Token usage & model info      │
│                                │   /exit      Exit OpenJiuWen              │
│   GLM-5 (OpenAI)              │   ! <cmd>    Run a shell command           │
│   ~/my-project                │                                            │
╰────────────────────────────────────────────────────────────────────────────╯
```

## 安装

### 方式 1: 一键安装（推荐）

自动安装 `openjiuwen[cli]` 并配置全局 `openjiuwen` 命令：

```bash
# Linux / macOS
curl -fsSL https://gitcode.com/openJiuwen/agent-core/raw/main/openjiuwen/harness/cli/install.sh | bash

# Windows（以管理员权限运行 PowerShell）
irm https://gitcode.com/openJiuwen/agent-core/raw/main/openjiuwen/harness/cli/install.ps1 | iex
```

### 方式 2: pip install

```bash
# 从 PyPI 安装（含 CLI 依赖）
pip install -U "openjiuwen[cli]"

# 或从源码安装（开发模式）
git clone https://gitcode.com/openJiuwen/agent-core.git
cd agent-core
pip install -e ".[cli]"

# 验证安装
openjiuwen --version
```

### 方式 3: 直接运行（无需安装）

```bash
# 在 agent-core 项目根目录
pip install click rich prompt-toolkit

# 通过 python -m 运行
python -m openjiuwen.harness.cli --version
python -m openjiuwen.harness.cli chat
python -m openjiuwen.harness.cli run "What is 2+2?"
```

### 系统要求

| 要求 | 说明 |
|------|------|
| **Python** | 3.11+ (推荐 3.11.4) |
| **操作系统** | Linux / macOS / Windows 10+ |
| **终端** | 支持 ANSI 颜色（Windows 推荐使用 Windows Terminal） |

## 配置

配置从 `~/.openjiuwen/settings.json` 加载，首次启动时交互式向导会自动引导完成配置。

### settings.json（推荐）

配置文件位于 `~/.openjiuwen/settings.json`：

```json
{
  "provider": "OpenAI",
  "model": "gpt-4o",
  "apiKey": "sk-...",
  "apiBase": "https://api.openai.com/v1",
  "maxTokens": 8192,
  "maxIterations": 30,
  "workspace": "~/.openjiuwen/workspace"
}
```

首次运行 `openjiuwen` 时，如果未检测到 API Key，会自动启动交互式配置向导，将配置写入此文件。

### 配置项说明

| settings.json 字段 | 环境变量 | 说明 | 默认值 |
|---|---|---|---|
| `apiKey` | `OPENJIUWEN_API_KEY` | API 密钥（必需） | — |
| `model` | `OPENJIUWEN_MODEL` | 模型名称 | `gpt-4o` |
| `provider` | `OPENJIUWEN_PROVIDER` | LLM 提供商 | `OpenAI` |
| `apiBase` | `OPENJIUWEN_API_BASE` | API 基础地址 | `https://api.openai.com/v1` |
| `maxTokens` | `OPENJIUWEN_MAX_TOKENS` | 最大 token 数 | `8192` |
| `maxIterations` | `OPENJIUWEN_MAX_ITERATIONS` | 最大迭代次数 | `30` |
| `serverUrl` | `OPENJIUWEN_SERVER_URL` | 远程 Agent Server 地址 | — |
| `workspace` | `OPENJIUWEN_WORKSPACE` | 工作空间目录 | `~/.openjiuwen/workspace` |

### 配置优先级

```
CLI 参数 > 环境变量 > ~/.openjiuwen/settings.json > 默认值
```

### CLI 参数（优先级最高）

```bash
openjiuwen --model GLM-5 --provider OpenAI --api-key "your-key" --api-base "https://api.example.com/v1"
```

### `~/.openjiuwen/` 目录结构

| 路径 | 说明 |
|------|------|
| `settings.json` | 主配置文件（provider、model、apiKey 等） |
| `mcp.json` | MCP 服务器配置 |
| `OPENJIUWEN.md` | 用户级系统提示词 / 记忆文件 |
| `sessions/` | 会话历史持久化（JSON） |
| `workspace/` | 默认工作空间根目录 |
| `workspace/skills/` | 技能目录 |

## 使用方式

### 交互式模式（默认）

```bash
$ openjiuwen
# 或
$ openjiuwen chat

You> 这个项目的目录结构是什么？
  ⚙ GlobTool: **/*
  项目包含以下目录：
  - src/       — 源代码
  - tests/     — 测试
  ...

You> 帮我在 src/utils.py 中添加一个 format_date 函数
  ⚙ ReadFileTool: src/utils.py
  ⚙ EditFileTool: src/utils.py
  已添加 format_date 函数。

You> /exit
Goodbye!
```

### 非交互式模式

```bash
# 直接运行
openjiuwen run "分析 src/ 下所有 Python 文件，列出未使用的 import"

# 管道模式
echo "检查代码风格问题" | openjiuwen run -

# JSON 输出（适合 CI/CD 集成）
openjiuwen run -f json "What is 2+2?"

# 流式 JSONL
openjiuwen run -f stream-json "分析这个项目" >> build.jsonl
```

### 项目记忆（OPENJIUWEN.md）

在项目根目录创建 `OPENJIUWEN.md`，Agent 会自动加载其中的规则：

```markdown
# 项目约定
- 使用 pytest 进行测试
- 所有函数必须有类型注解
- 提交消息使用 conventional commits 格式
- 代码风格遵循 ruff 规范
```

支持两层记忆：
1. **用户级**: `~/.openjiuwen/OPENJIUWEN.md` — 全局偏好
2. **项目级**: `{project_root}/OPENJIUWEN.md` — 项目规范（优先级更高）

## 能力全景

CLI Agent 集成了丰富的工具、Rail 插件和子 Agent，开箱即用。

### 5.1 内置工具（Tools）

#### 文件系统工具（FileSystemRail 提供）

| 工具名 | 功能 | 说明 |
|--------|------|------|
| `bash` | Shell 执行 | 运行 shell 命令，支持超时控制 |
| `read_file` | 读取文件 | 读取文件内容，支持行号范围 |
| `write_file` | 写入文件 | 完全覆盖写入，文件不存在时自动创建 |
| `edit_file` | 智能编辑 | 基于字符串替换的精确编辑，保留格式 |
| `glob` | 文件匹配 | 支持 `**/*` 等 glob 模式查找文件 |
| `grep` | 内容搜索 | 支持正则表达式搜索文件内容 |
| `list_files` | 目录列表 | 列出指定目录下的文件和子目录 |
| `code` | 代码执行 | 执行 Python 或 JavaScript 代码片段 |

#### 网络工具（手动注册）

| 工具名 | 功能 | 说明 |
|--------|------|------|
| `free_search` | 网络搜索 | 通过 DuckDuckGo 免费搜索，返回排序 URL 和摘要 |
| `fetch_webpage` | 网页抓取 | 获取网页文本内容，返回状态码/标题/正文 |

#### Todo 工具（DeepAgent 内置）

| 工具名 | 功能 | 说明 |
|--------|------|------|
| `todo_create` | 创建待办 | 创建 Todo 列表 |
| `todo_list` | 查看待办 | 获取并展示所有 Todo 项 |
| `todo_modify` | 修改待办 | 更新、删除、取消、追加、插入 Todo 项 |

#### 任务调度工具（DeepAgent 内置）

| 工具名 | 功能 | 说明 |
|--------|------|------|
| `task` | 子任务派发 | 启动临时子 Agent 处理复杂多步独立任务，隔离上下文窗口 |
| `cron` | 定时任务 | 使用 cron 表达式调度周期或一次性任务 |

#### 工具发现（DeepAgent 内置）

| 工具名 | 功能 | 说明 |
|--------|------|------|
| `search_tools` | 搜索工具 | 按能力、名称、描述或参数搜索候选工具（仅发现，不直接调用） |
| `load_tools` | 加载工具 | 动态加载/注册新工具 |

#### 多模态工具（需环境变量配置）

| 工具名 | 功能 | 前置条件 |
|--------|------|----------|
| `image_ocr` | 图片 OCR | 设置 `VISION_API_KEY` 或复用主模型凭据 |
| `visual_question_answering` | 图片问答 | 同上 |
| `audio_transcription` | 语音转文字 | 设置 `AUDIO_API_KEY` 或复用主模型凭据 |
| `audio_question_answering` | 音频问答 | 同上 |
| `audio_metadata` | 音频元数据 | 同上 |
| `video_understanding` | 视频理解 | 同上 |

### 5.2 Rail 插件

Rail 是 Agent 运行时的拦截器/增强器，在工具调用前���自动执行。

| Rail | 功能 | 注入的工具 |
|------|------|-----------|
| **FileSystemRail** | 注册文件系统工具集（bash、read_file 等） | 8 个文件系统工具 |
| **TokenTrackingRail** | 统计每轮 LLM 调用的 Token 用量 | 无 |
| **ToolTrackingRail** | 追踪工具调用事件，生成 chunk 供 UI 渲染 | 无 |
| **AskUserRail** | 中断执行流，向用户提问并等待回答 | `ask_user` |
| **ConfirmInterruptRail** | 对危险工具（bash、write_file、edit_file）进行人工确认拦截 | 无 |
| **SkillUseRail** | 扫描并加载 `~/.openjiuwen/workspace/skills/` 下的技能 | `list_skill` |
| **ContextEngineeringRail** | 上下文窗口管理，含 DialogueCompressor 支持 `/compact` | 无 |
| **MemoryRail** | 向量记忆工具（需 Embedding 模型配置） | `memory_search`、`memory_get`、`write_memory`、`edit_memory`、`read_memory` |
| **SessionRail** | 异步子 Agent 任务管理（有子 Agent 时自动注入） | `sessions_list`、`sessions_spawn`、`sessions_cancel` |

#### MemoryRail 配置

MemoryRail 需要 Embedding 模型支持，通过环境变量配置：

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `EMBEDDING_MODEL_NAME` | Embedding 模型名 | `text-embedding-3-small` |
| `EMBEDDING_BASE_URL` | Embedding API 地址 | 复用主模型 `apiBase` |
| `EMBEDDING_API_KEY` | Embedding API 密钥 | 复用主模型 `apiKey` |

### 5.3 子 Agent（Subagents）

CLI Agent 可以将复杂任务委派给专用子 Agent，通过 SessionRail 在后台异步执行。

| 子 Agent | 功能 | 说明 |
|----------|------|------|
| **code_agent** | 软件工程 / 编码任务 | 资深软件工程师，擅长把任务落到可运行的代码与可验证的结果。自带 FileSystemRail 工具集 |
| **research_agent** | 研究调查任务 | 专注研究与调查，每次给出一个主题进行深度分析。自带 FileSystemRail 工具集 |
| **browser_agent** | 浏览器自动化 | 使用 Playwright 执行网页操作（点击、填表、截图等）。需安装额外依赖 |

### 5.4 Browser 子 Agent

**前置条件：** 需要安装 Playwright 相关依赖。

```bash
pip install playwright
playwright install chromium
```

> **注意：** 如果未安装 Playwright，browser_agent 会被自动跳过，不影响其他功能使用。

### 5.5 MCP 服务器扩展

通过 `~/.openjiuwen/mcp.json` 配置外部 MCP（Model Context Protocol）服务器，动态扩展 Agent 工具集。格式兼容 Claude Code：

```json
{
  "mcpServers": {
    "my-server": {
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@mcp/my-server"],
      "env": {}
    }
  }
}
```

支持的传输方式：`stdio`、`sse`、`streamable-http`。

### 5.6 技能系统（Skills）

SkillUseRail 会扫描以下目录（优先级从高到低）：

1. `~/.openjiuwen/workspace/skills/`
2. `~/.claude/skills/`
3. `~/.codex/skills/`
4. `~/.jiuwenclaw/workspace/skills/`

在技能目录中放置 Markdown 文件即可被 Agent 自动发现和调用。

## 命令参考

### CLI 命令

```bash
openjiuwen                          # 交互式 REPL（默认）
openjiuwen chat                     # 交互式 REPL（显式）
openjiuwen run "prompt"             # 非交互式运行
openjiuwen run -f json "prompt"     # JSON 输出
openjiuwen run -f stream-json "p"   # 流式 JSONL
openjiuwen run -                    # 从 stdin 读取 prompt
openjiuwen --version                # 显示版本
openjiuwen --help                   # 显示帮助
```

### REPL 命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助信息 |
| `/exit` | 退出 OpenJiuWen |
| `/quit` | `/exit` 别名 |
| `/clear` | 清屏 |
| `/status` | 显示 Token 用量和模型信息 |
| `/cost` | 显示 Token 费用统计 |
| `/compact` | 压缩对话历史 |
| `/sessions` | 列出历史会话 |
| `! <cmd>` | 直接执行 shell 命令（不经过 Agent） |

### 快捷键

| 快捷键 | 行为 |
|--------|------|
| `Ctrl+C` (1次) | 中止当前流式输出 |
| `Ctrl+C` (2次, 2秒内) | 提示即将退出 |
| `Ctrl+C` (3次, 2秒内) | 退出程序 |

## 支持的 LLM 提供商

| 提供商 | `provider` 值 | 说明 |
|--------|---------------|------|
| OpenAI | `OpenAI` | GPT-4o, GPT-4o-mini 等 |
| OpenRouter | `OpenRouter` | 多模型路由 |
| DashScope | `DashScope` | 通义千问系列 |
| SiliconFlow | `SiliconFlow` | GLM, DeepSeek 等 |
| ModelArts | `OpenAI` | 华为云 ModelArts（OpenAI 兼容） |

## 项目结构

```
openjiuwen/harness/cli/
├── __init__.py              # 包定义 + 版本号
├── __main__.py              # python -m 入口
├── cli.py                   # Click CLI 入口（chat / run）
├── install.sh               # Linux/macOS 一键安装脚本
├── install.ps1              # Windows 一键安装脚本
├── agent/
│   ├── config.py            # 配置管理（settings.json + 三层优先级）
│   └── factory.py           # Agent 工厂 + LocalBackend
├── prompts/
│   └── builder.py           # 系统提示词构建
├── rails/
│   ├── token_tracker.py     # Token 用量追踪
│   └── tool_tracker.py      # 工具调用追踪
├── storage/
│   └── session_store.py     # JSON 会话持久化
└── ui/
    ├── renderer.py          # 流式渲染器（8 种 chunk 类型）
    ├── repl.py              # 交互式 REPL + slash 命令
    ├── runner.py            # 非交互模式（text/json/stream-json）
    ├── tool_display.py      # 工具名称映射 + 参数格式化
    └── todo_render.py       # Todo 渲染
```

## 测试

```bash
# 运行全部 CLI 测试
pytest tests/cli/ -v -o "addopts="

# 仅单元测试（快速，无需 API Key）
pytest tests/cli/unit/ -v -o "addopts="

# 仅集成测试
pytest tests/cli/integration/ -v -o "addopts="

# E2E 测试（需要 API Key）
pytest tests/cli/e2e/ -v -o "addopts="
```

## 架构

```
用户终端
    │
    ▼
cli.py (Click)  →  chat / run 子命令
    │
    ├── repl.py (交互式)     →  prompt_toolkit + rich
    └── runner.py (非交互式)  →  text / json / stream-json
            │
            ▼
    AgentBackend (Protocol)
    └── LocalBackend  →  SDK Runner.run_agent_streaming()
            │
            ▼
    agent-core SDK
    ├── DeepAgent + ReActAgent
    ├── 内置工具 (Bash/File/Grep/Web)
    └── Rail 插件 (Security/TokenTracking)
            │
            ▼
    LLM Provider (OpenAI / DashScope / SiliconFlow / ...)
```
