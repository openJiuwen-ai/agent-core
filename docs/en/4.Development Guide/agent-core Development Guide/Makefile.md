# Makefile

This project provides a cross‑platform `Makefile` to standardize common development tasks such as installing tooling, running tests, checking code quality, and applying automatic fixes. It is designed to work on **Linux/macOS**, **Windows (cmd.exe / PowerShell)**, and Unix‑like shells (Git Bash, MSYS2, etc.).

---

## Table of Contents

- [Makefile](#makefile)
  - [Table of Contents](#table-of-contents)
  - [1. Overview](#1-overview)
  - [2. Requirements](#2-requirements)
  - [3. Quick Start](#3-quick-start)
  - [4. File Selection Logic](#4-file-selection-logic)
    - [Alternative: last N commits](#alternative-last-n-commits)
    - [Safety check](#safety-check)
  - [5. Python \& `uv` integration](#5-python--uv-integration)
  - [6. Supported Targets](#6-supported-targets)
    - [General](#general)
    - [Code quality checks](#code-quality-checks)
    - [Automatic fixes](#automatic-fixes)
    - [Combination Tasks](#combination-tasks)
  - [7. Variables \& Configuration](#7-variables--configuration)
    - [Common variables](#common-variables)
  - [8. Dependency inspection (`DEP`)](#8-dependency-inspection-dep)
  - [9. Self‑updating the Makefile](#9-selfupdating-the-makefile)
  - [10. Design Notes](#10-design-notes)
    - [Cross‑platform compatibility](#crossplatform-compatibility)
    - [Why only changed files?](#why-only-changed-files)
    - [Why Python for filtering files?](#why-python-for-filtering-files)
  - [Example workflow](#example-workflow)
  - [Troubleshooting](#troubleshooting)
    - [No files selected](#no-files-selected)
    - [Tools not found](#tools-not-found)
  - [Support \& Issues](#support--issues)

---

## 1. Overview

The Makefile focuses on **incremental checks**: instead of running linters and formatters on the entire repository, it only operates on:

- Python files that are **currently staged** (`git add`), or
- Python files changed in the **last N commits**.

It supports the following tools:

- `ruff` (linting + formatting)
- `pylint`
- `mypy`
- `codespell`
- `pytest`
- `pipdeptree`

It can run them either via:

- [`uv`](https://github.com/astral-sh/uv) (if installed), or
- standard `python -m ...` invocations

---

## 2. Requirements

Install `make` on Windows:
- With the package manager [Chocolatey](https://docs.chocolatey.org/en-us/choco/setup/#more-install-options), you can run the `choco install make` command to install `make`.

Install `make` on MacOS:
- If you already have Xcode, the `xcode-select --install` command installs the Xcode command-line tools which include a version of `make`.
- Alternatively, with the package manager [Homebrew](https://brew.sh), install via `brew install make`

Install `make` on Linux:
- Run `sudo apt update` to update the package list, then run `sudo apt install make` to install `make` on itself
- Alternatively, you can also run `sudo apt install build-essential`, which installs development tools including `make`.

---

## 3. Quick Start

```bash
# Install required tooling
make install

# Stage your changes
git add ...

# Automatically fix lint + formatting issues
make fix

# Run all checks on staged Python files
make check
```

To run checks on the last 3 commit instead of staged files:

```bash
make check COMMITS=3
```

To check commands supported, simply run `make` which will print out this help message:
```
Usage: make [target] [COMMITS=N] [UV=yes|no]

- If COMMITS=N is specified and greater than 0, check Python files changed in last N commits
  Otherwise the currently staged changes are checked
- If UV is set, it must be either yes or no, otherwise make will detect if uv is available
- You can also use this Makefile to check what packages depends on a specific package
  Example: make DEP=pydantic-core
  Syntax:  make DEP=<package-name>

Available targets:
    help       - Show this help message
    install    - Install dependencies via uv or pip: ruff, pylint, mypy, codespell, pipdeptree
    update     - Download latest version of this Makefile from gitcode.com/openJiuwen/agent-core
    test       - Execute pytest, you can supply arguments via TESTFLAGS="..."
    format     - Check formatting of selected Python files via ruff
    lint       - Check linting of selected Python files via ruff
    pylint     - Check linting of selected Python files via pylint: more comprehensive
    spelling   - Check spelling of selected Python files via codespell
    fix-format - Auto-fix formatting errors in selected Python files via ruff
    fix-lint   - Auto-fix linting errors in selected Python files via ruff
    type-check - Type-check selected Python files via mypy
    check      - Run all checks: format, spelling, lint, pylint
    fix        - Run all auto-fixes: fix-lint, fix-format
```

---

## 4. File Selection Logic

By default, the Makefile selects:

```
git diff --cached --name-only
```

and filters to:

- `*.py`
- `*.pyi`

using Python for cross‑platform compatibility.

### Alternative: last N commits

Set the variable `COMMITS`:

```bash
make check COMMITS=3
```

This checks files modified in:

```
HEAD~3..
```

### Safety check

If **no Python files** are found, the Makefile:

- prints a warning
- shows the help message
- exits with an error

This prevents accidentally running tools on the entire repository.

---

## 5. Python & `uv` integration

The Makefile automatically detects whether `uv` is available:

| Case       | Command style        |
| ---------- | -------------------- |
| `uv` found | `uv run ruff ...`    |
| no `uv`    | `python -m ruff ...` |

You can override this behavior:

```bash
make check UV=yes
make check UV=no
```

---

## 6. Supported Targets

### General

| Target    | Description                                               |
| --------- | --------------------------------------------------------- |
| `help`    | Show usage and available commands                         |
| `install` | Install required tooling (`ruff`, `pylint`, `mypy`, etc.) |
| `update`  | Download latest version of this Makefile                  |
| `test`    | Run pytest                                                |
| `dep`     | Show reverse dependency tree for a package                |

---

### Code quality checks

| Target       | Tool      | Purpose                         |
| ------------ | --------- | ------------------------------- |
| `format`     | ruff      | Check formatting & import order |
| `lint`       | ruff      | Lint code                       |
| `pylint`     | pylint    | Deep static analysis            |
| `spelling`   | codespell | Spelling mistakes               |
| `type-check` | mypy      | Type checking                   |

---

### Automatic fixes

| Target       | Description               |
| ------------ | ------------------------- |
| `fix-format` | Auto‑format + fix imports |
| `fix-lint`   | Auto‑fix ruff lint issues |
| `fix`        | Run both fixers           |

---

### Combination Tasks

| Target  | Description                            |
| ------- | -------------------------------------- |
| `check` | Run: format → spelling → lint → pylint |
| `fix`   | Run: fix-lint → fix-format             |

These targets continue running even if one step fails, to provide a full report.

---

## 7. Variables & Configuration

All variables can be passed on the command line:

```bash
make check COMMITS=1 PYTHON=python3.12
```

### Common variables

| Variable    | Default  | Description                   |
| ----------- | -------- | ----------------------------- |
| `PYTHON`    | `python` | Python executable             |
| `TESTFLAGS` | `.`      | Arguments passed to pytest    |
| `COMMITS`   | `0`      | Number of commits to inspect  |
| `UV`        | auto     | Force uv usage (`yes` / `no`) |
| `DEP`       | empty    | Package name for `make dep`   |
| `CURL`      | auto     | Path to curl binary           |

---

## 8. Dependency inspection (`DEP`)

You can inspect reverse dependencies of any installed package:

```bash
make DEP=pydantic-core
```

This runs:

```
pipdeptree --reverse --package pydantic-core
```
and produces the following output:
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

If `DEP` is set, it becomes the default target automatically.

---

## 9. Self‑updating the Makefile

```bash
make update
```

Downloads the latest version from the upstream repository.

---

## 10. Design Notes

### Cross‑platform compatibility

The Makefile handles:

- Windows vs Unix null devices (`NUL` vs `/dev/null`)
- command chaining (`&` vs `;`)
- shell quoting differences
- file paths with spaces or quotes

### Why only changed files?

Benefits:

- Faster feedback loop
- Encourages small commits
- Works well with pre‑commit workflows
- Avoids noise from legacy code

### Why Python for filtering files?

Git outputs null‑terminated paths (`-z`), which:

- avoids quoting issues
- is reliable across platforms

Python is used instead of `grep`/`sed` to ensure consistent behavior on Windows.

---

## Example workflow

```bash
make install
git checkout -b feature-x
# edit files
git add openjiuwen/**/*.py
make fix
make check
git commit -m "feat: add feature x"
```

---

## Troubleshooting

### No files selected

```
No Python files selected.
```

Fix:

- Run `git add` first, or
- Use `COMMITS=N`

### Tools not found

Run:

```bash
make install
```

or ensure your virtual environment / uv environment is active.

---

## Support & Issues

If you encounter any problems, unexpected behavior, or have suggestions for improvements, feel free to open an issue at:

[https://gitcode.com/openJiuwen/agent-core/issues](https://gitcode.com/openJiuwen/agent-core/issues)
