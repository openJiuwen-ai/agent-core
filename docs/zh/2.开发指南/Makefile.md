# Makefile

本项目提供了一个**跨平台**的 `Makefile`，用于标准化常见的开发任务，例如安装工具链、运行测试、检查代码质量以及自动修复问题。它被设计为可在 **Linux/macOS**、**Windows**以及类 Unix Shell（Git Bash、MSYS2 等）中运行。

---

## 目录

- [Makefile](#makefile)
  - [目录](#目录)
  - [1. 概述](#1-概述)
  - [2. 安装依赖](#2-安装依赖)
    - [Windows 安装 `make`](#windows-安装-make)
    - [MacOS 安装 `make`](#macos-安装-make)
    - [Linux 安装 `make`](#linux-安装-make)
  - [3. 快速开始](#3-快速开始)
  - [4. 文件选择逻辑](#4-文件选择逻辑)
    - [额外方案：最近 N 次提交](#额外方案最近-n-次提交)
    - [安全检查](#安全检查)
  - [5. Python 与 `uv` 集成](#5-python-与-uv-集成)
  - [6. 支持的 Target](#6-支持的-target)
    - [通用](#通用)
    - [代码质量检查](#代码质量检查)
    - [自动修复](#自动修复)
    - [复合任务](#复合任务)
  - [7. 变量与配置](#7-变量与配置)
    - [常用变量](#常用变量)
  - [8. 依赖检查（`DEP`）](#8-依赖检查dep)
  - [9. 性能分析](#9-性能分析)
    - [火焰图（`FLAME`）](#火焰图flame)
    - [Speedscope 分析（`SPEEDSCOPE`）](#speedscope-分析speedscope)
  - [10. Makefile 自更新](#10-makefile-自更新)
  - [11. 设计说明](#11-设计说明)
    - [跨平台兼容性](#跨平台兼容性)
    - [为什么只处理变更文件？](#为什么只处理变更文件)
    - [为什么用 Python 过滤文件？](#为什么用-python-过滤文件)
  - [示例工作流](#示例工作流)
  - [故障排查](#故障排查)
    - [未选中文件](#未选中文件)
    - [找不到工具](#找不到工具)
  - [支持与问题反馈](#支持与问题反馈)

---

## 1. 概述

该 Makefile 强调**增量检查**：它不会对整个仓库运行 linter/formatter，而只会作用于：

- 当前**已在stage状态**的 Python 文件（`git add` 之后），或者
- 最近 **N 次提交**中发生变化的 Python 文件。

支持的工具包括：

- `ruff`（lint + 格式检查）
- `pylint`
- `mypy`
- `codespell`
- `pytest`
- `pipdeptree`
- `pyinstrument`（性能分析）

这些工具可以通过以下方式运行：

- 使用 [`uv`](https://github.com/astral-sh/uv)（如果已安装），或者
- 使用标准的 `python -m ...` 调用方式。

---

## 2. 安装依赖

### Windows 安装 `make`
- 使用包管理器 [Chocolatey](https://docs.chocolatey.org/en-us/choco/setup/#more-install-options)，执行 `choco install make` 安装 `make`。

### MacOS 安装 `make`
- 若已安装 Xcode，可运行 `xcode-select --install` 安装 Xcode 命令行工具，其中包含 `make`。
- 或使用包管理器 [Homebrew](https://brew.sh)，执行 `brew install make` 安装。

### Linux 安装 `make`
- 运行 `sudo apt update` 更新包列表，然后运行 `sudo apt install make` 安装 `make`；
- 或安装 `sudo apt install build-essential`，它会安装包括 `make` 在内的常用开发工具。

---

## 3. 快速开始

```bash
# 安装所需工具
make install

# stage你的改动
git add ...

# 自动修复 lint + 格式检查问题
make fix

# 对已stage的 Python 文件运行所有检查
make check
```

如果你想对最近 3 次提交的改动运行检查，而不是针对已在stage状态的文件：

```bash
make check COMMITS=3
```

要查看支持的命令，直接运行 `make`，将输出帮助信息：

```
Usage: make [Target] [COMMITS=N] [UV=yes|no]

- 如果指定了 COMMITS=N 且 N > 0，则检查最近 N 次提交中发生变化的 Python 文件
  否则将检查当前已暂存（staged）的变更
- 如果设置了 UV，则其值必须为 yes 或 no；否则 make 会自动检测是否安装了 uv
- 你也可以用这个 Makefile 查看"哪些包依赖某个包"
  例: make DEP=pydantic-core
  语法: make DEP=<包名>
- 这个 Makefile 可以用来为 Python 脚本创建火焰图
  例: make FLAME=test-openai-emb.py
  语法: make FLAME=<脚本名>
- 这个 Makefile 可以用来为 Python 脚本创建 speedscope 火焰图性能分析
  例: make SPEEDSCOPE=test-openai-emb.py
  语法: make SPEEDSCOPE=<脚本名>
  在 https://www.speedscope.app 查看分析结果

可用的 Target: 
    help       - 显示此帮助信息
    install    - 通过 uv 或 pip 安装依赖
    update     - 从 gitcode.com/openJiuwen/agent-core 下载该 Makefile 的最新版本
    test       - 运行 pytest，你可以通过 TESTFLAGS="..." 传入参数
    format     - 使用 ruff 检查所选 Python 文件的格式
    lint       - 使用 ruff 检查所选 Python 文件的 lint
    pylint     - 使用 pylint 检查所选 Python 文件的 lint: 更全面
    spelling   - 使用 codespell 检查所选 Python 文件的拼写
    fix-format - 使用 ruff 自动修复所选 Python 文件的格式错误
    fix-lint   - 使用 ruff 自动修复所选 Python 文件的 lint 错误
    type-check - 使用 mypy 对所选 Python 文件做类型检查
    check      - 运行所有检查: format、spelling、lint、pylint
    fix        - 运行所有自动修复: fix-lint、fix-format
```

> 注：上面这段“Usage/help”是实际输出的中文译文，可以通过运行`make`命令查看英文原文。

---

## 4. 文件选择逻辑

默认情况下，Makefile 会选择：

```
git diff --cached --name-only
```

并使用 Python 做跨平台过滤，只保留：

- `*.py`
- `*.pyi`

### 额外方案：最近 N 次提交

设置变量 `COMMITS`：

```bash
make check COMMITS=3
```

这会检查在下面范围内被修改的文件：

```
HEAD~3..
```

### 安全检查

如果**没有找到任何 Python 文件**，Makefile 会：

- 打印警告
- 显示帮助信息
- 以错误状态退出

这样可以避免误操作导致工具跑完整个仓库。

---

## 5. Python 与 `uv` 集成

Makefile 会自动检测是否存在 `uv`：

| 情况 | 命令形式 |
| --- | --- |
| 找到 `uv` | `uv run ruff ...` |
| 没有 `uv` | `python -m ruff ...` |

你也可以手动覆盖该行为：

```bash
make check UV=yes
make check UV=no
```

---

## 6. 支持的 Target

### 通用

| Target | 说明 |
| --- | --- |
| `help` | 显示用法与可用命令 |
| `install` | 安装所需工具（`ruff`, `pylint`, `mypy` 等） |
| `update` | 下载该 Makefile 的最新版本 |
| `test` | 运行 pytest |
| `dep` | 查看某个包的反向依赖树（谁依赖它） |
| `flame` | 为 Python 脚本生成 HTML 格式的火焰图 |
| `speedscope` | 为 Python 脚本生成 Speedscope 格式的性能分析文件 |

### 代码质量检查

| Target | 工具 | 用途 |
| --- | --- | --- |
| `format` | ruff | 检查格式与 import 顺序 |
| `lint` | ruff | 代码 lint |
| `pylint` | pylint | 更全面的静态分析 |
| `spelling` | codespell | 拼写错误检查 |
| `type-check` | mypy | 类型检查 |

### 自动修复

| Target | 说明 |
| --- | --- |
| `fix-format` | 自动修正格式 + 修复 imports |
| `fix-lint` | 自动修复 ruff lint 问题 |
| `fix` | 同时运行两个修复器 |

### 复合任务

| Target | 说明 |
| --- | --- |
| `check` | 依次运行：format → spelling → lint → pylint |
| `fix` | 依次运行：fix-lint → fix-format |

这些复合任务即使某一步失败也会继续执行，以便输出完整报告。

---

## 7. 变量与配置

所有变量都可以在命令行中传入，例如：

```bash
make check COMMITS=1 PYTHON=python3.12
```

### 常用变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PYTHON` | `python` | Python 可执行文件 |
| `TESTFLAGS` | `.` | 传给 pytest 的参数 |
| `COMMITS` | `0` | 检查最近多少次提交 |
| `UV` | 自动检测 | 强制是否使用 uv（`yes` / `no`） |
| `DEP` | 空 | `make dep` 要查询的包名 |
| `FLAME` | 空 | 要生成火焰图的脚本名 |
| `SPEEDSCOPE` | 空 | 要生成 speedscope 分析的脚本名 |
| `CURL` | 自动检测 | curl 的路径 |

---

## 8. 依赖检查（`DEP`）

你可以查看任意已安装包的**反向依赖**（哪些包依赖它）：

```bash
make DEP=pydantic-core
```

这会执行：

```
pipdeptree --reverse --package pydantic-core
```

并输出类似：

```
What packages depend on [pydantic-core]:
pydantic_core==2.41.5
└── pydantic==2.12.5 [requires: pydantic_core==2.41.5]
    ├── mcp==1.25.0 [requires: pydantic>=2.11.0,<3.0.0]
    │   └── fastmcp==2.14.2 [requires: mcp>=1.24.0,<2.0]
    │       └── openjiuwen==0.1.4 [requires: fastmcp>=2.14.2,<3.0]
    ├── autodoc_pydantic==2.2.0 [requires: pydantic>=2.0,<3.0.0]
    ├── chromadb==1.4.0 [requires: pydantic>=1.9]
    │   └── openjiuwen==0.1.4 [requires: chromadb>=1.3.7]
    ├── pydantic-settings==2.12.0 [requires: pydantic>=2.7.0]
    │   ├── mcp==1.25.0 [requires: pydantic-settings>=2.5.2]
    │   │   └── fastmcp==2.14.2 [requires: mcp>=1.24.0,<2.0]
    │   │       └── openjiuwen==0.1.4 [requires: fastmcp>=2.14.2,<3.0]
    │   └── autodoc_pydantic==2.2.0 [requires: pydantic-settings>=2.0,<3.0.0]
    ├── openai==2.14.0 [requires: pydantic>=1.9.0,<3]
    │   └── openjiuwen==0.1.4 [requires: openai>=1.108.0]
    ├── fastmcp==2.14.2 [requires: pydantic>=2.11.7]
    │   └── openjiuwen==0.1.4 [requires: fastmcp>=2.14.2,<3.0]
    └── openapi-pydantic==0.5.1 [requires: pydantic>=1.8]
        └── fastmcp==2.14.2 [requires: openapi-pydantic>=0.5.1]
            └── openjiuwen==0.1.4 [requires: fastmcp>=2.14.2,<3.0]
```

如果设置了 `DEP`，它会自动成为默认 target。

---

## 9. 性能分析

Makefile 支持使用 `pyinstrument` 工具对 Python 脚本进行性能分析，生成两种格式的性能分析报告。

### 火焰图（`FLAME`）

使用 `FLAME` 变量可以为指定的 Python 脚本生成 HTML 格式的火焰图：

```bash
make FLAME=test-openai-emb.py
```

这会执行：

```
pyinstrument -r html -o test-openai-emb.py.html test-openai-emb.py
```

生成的 HTML 文件可以直接在浏览器中打开，以交互式的方式查看性能分析结果。火焰图可以帮助你：

- 识别代码中的性能瓶颈
- 查看函数调用栈和耗时分布
- 分析程序执行的热点路径

### Speedscope 分析（`SPEEDSCOPE`）

使用 `SPEEDSCOPE` 变量可以为指定的 Python 脚本生成 Speedscope 格式的性能分析文件：

```bash
make SPEEDSCOPE=test-openai-emb.py
```

这会执行：

```
pyinstrument -r speedscope -o test-openai-emb.py.json test-openai-emb.py
```

生成的 JSON 文件可以上传到 [https://www.speedscope.app](https://www.speedscope.app) 进行可视化分析。Speedscope 提供了更丰富的交互式分析功能，包括：

- 时间线视图
- 火焰图视图
- 左侧重视图
- 函数调用频率分析

**注意**：使用性能分析功能前，需要确保已安装 `pyinstrument`。可以通过运行 `make install` 来安装所有依赖，包括 `pyinstrument`。

如果设置了 `FLAME` 或 `SPEEDSCOPE`，它们会自动成为默认 target。

---

## 10. Makefile 自更新

```bash
make update
```

该命令会从上游仓库下载最新版本。

---

## 11. 设计说明

### 跨平台兼容性

Makefile 处理了：

- Windows 与 Unix 的null差异（`NUL` vs `/dev/null`）
- 命令串联方式差异（`&` vs `;`）
- shell 引号规则差异
- 包含空格或引号的路径处理

### 为什么只处理变更文件？

好处包括：

- 更快的反馈循环
- 鼓励小提交
- 与 pre-commit 工作流配合良好
- 避免历史遗留代码带来的噪声

### 为什么用 Python 过滤文件？

Git 可以输出以空字符结尾的路径（`-z`），它：

- 避免引号/转义问题
- 在跨平台场景更可靠

使用 Python 而不是 `grep`/`sed`，可以确保在 Windows 上也有一致行为。

---

## 示例工作流

```bash
make install
git checkout -b feature-x
# 编辑文件
git add openjiuwen/**/*.py
make fix
make check
git commit -m "feat: add feature x"
```

---

## 故障排查

### 未选中文件

当出现：

```
No Python files selected.
```

解决方法：

- 先运行 `git add`，或者
- 使用 `COMMITS=N` 指定检查最近提交。

### 找不到工具

运行：

```bash
make install
```

或确保你的虚拟环境 / uv 环境已激活。

---

## 支持与问题反馈

如果你遇到任何问题、异常行为或有改进建议，可以在以下地址提交 issue：

https://gitcode.com/openJiuwen/agent-core/issues
