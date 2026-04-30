# sys_operation/local — Agent Notes

## Module Overview

This directory implements **local system operations** for code execution, filesystem manipulation, and shell commands. Three operation classes handle different domains:

- **CodeOperation** — Execute code (Python, JavaScript) with language-aware runtime setup
- **FsOperation** — File system operations with async I/O, concurrent access control, and sandbox enforcement
- **ShellOperation** — Shell command execution with platform-specific shell detection and safety guardrails

All operations support both **single-shot** (`.execute_*`) and **streaming** (`.execute_*_stream`) modes.

## CodeOperation

### CLI vs File Execution

Code is executed via one of two strategies based on length:

- **CLI mode** (inline): `[python_exe, "-u", "-c", code]` — used when code ≤ `_UNIX_CMD_LIMIT` (100KB) or `_WINDOWS_CMD_LIMIT` (8KB)
- **File mode** (temp file): `[python_exe, "-u", path_to_temp_file]` — used for long code or when `force_file=True`

Temp files are created in system temp dir and cleaned up in the finally block, even on timeout.

### Language Support

Supported languages (extensible via `_SUPPORT_LANGUAGE_CONFIG_DICT`):

| Language | CLI Wrapper | File Suffix | Special Setup |
|----------|-------------|-------------|---------------|
| Python | `python -u -c` | `.py` | `PYTHONIOENCODING=utf-8`, `PYTHONUTF8=1` |
| JavaScript | `node -e` | `.js` | `NODE_DISABLE_COLORS=1` |

Environment variables are merged with user-supplied `environment` dict before subprocess creation.

### Streaming vs Non-streaming

- **`execute_code()`** — Returns `ExecuteCodeResult` with complete stdout/stderr after execution
- **`execute_code_stream()`** — Yields `ExecuteCodeStreamResult` chunks (one per stdout/stderr line) + final exit code

Streaming is useful for long-running code where output is needed incrementally; non-streaming is simpler and safer for short operations.

## FsOperation

### Concurrency: Read-Write Locks

`FsOperation` uses per-file async read-write locks (`_AsyncReadWriteLock`) stored in `_rw_locks` WeakValueDictionary:

- **Multiple readers** allowed simultaneously
- **Exclusive writer** blocks readers and other writers
- **Writer priority** — waiting writers block new readers to prevent starvation

Lock acquisition happens in `_maybe_read_lock()`, `_file_lock()`, and `_ordered_file_locks()`. All take a `timeout` (default 300s from options).

**Ordered locking:** When operations touch multiple files (e.g., `upload_file` reads src, writes dst), use `_ordered_file_locks()` which acquires locks in sorted path order to prevent deadlocks.

### Path Resolution: CWD and Sandbox Independent

`_resolve_path(path, create_parent=False)` resolves relative paths against `get_cwd()` (ContextVar) and enforces sandbox if configured:

1. **Base resolution** — relative paths resolve from `get_cwd()`, absolute paths used as-is
2. **Normalization** — `os.path.normpath()` + `Path.resolve(strict=False)` handles `..`, symlinks
3. **Sandbox check** — if `restrict_to_sandbox=True`, path must be within one of:
   - `sandbox_root` (from config), or
   - `[get_workspace(), get_project_root()]` (from ContextVar defaults)

CWD and sandbox are **independent** — CWD can move (e.g., worktree enter/exit) without changing the sandbox boundary.

### Streaming File Operations

Read, upload, download operations all support streaming:

- **`read_file_stream()`** — yields `ReadFileStreamResult` per line (text) or per chunk (bytes)
- **`upload_file_stream()` / `download_file_stream()`** — yield progress chunks with `chunk_index` and `is_last_chunk` flag

Streaming uses a peek-ahead pattern to detect the last chunk before yielding it. For text mode with `tail`, all lines are collected before streaming (to ensure proper `is_last_chunk` detection).

### Encoding and Permission Edge Cases

- **Text mode** — `encoding` parameter (default `utf-8`) used for all text read/write. Encoding errors in binary-to-text conversion use `errors="replace"` as fallback
- **Permissions** — `_apply_permissions()` and `_copy_permissions()` are best-effort on Unix; failures are logged but don't fail the operation
- **Head/Tail/Line Range** — only available in text mode; binary mode rejects these parameters

## ShellOperation

### Shell Type Detection

Three shell types supported via `ShellType` enum:

- **AUTO** (default) — Detects PowerShell on Windows (via `_looks_like_powershell()`), uses system shell otherwise
- **SH** — Forces `/bin/sh` (Unix) or `cmd.exe` (Windows)
- **POWERSHELL** — Forces PowerShell (`pwsh` or `powershell.exe`)

**PowerShell heuristics** (`_looks_like_powershell()`):

- Keywords: `powershell`, `get-childitem`, `set-location`, `remove-item`, `invoke-webrequest`, etc.
- Variables: `$env:`, `$psversiontable`, `$null`, `$true`, `$false`
- Syntax: `@'...'@` and `@"..."@` (here-strings)
- Regex: `\$[A-Za-z_][A-Za-z0-9_]*` pattern matches PS variables

### Dangerous Command Patterns

`ShellOperation` scans commands for dangerous patterns before execution:

| Pattern | Explanation |
|---------|-------------|
| `rm -rf` | Recursive force delete (Unix) |
| `del /f /s /q` | Force recursive delete (Windows) |
| `rd /s /q` | Remove directory tree |
| `format [drive]:` | Format disk |
| `shutdown`, `reboot` | System power commands |
| `diskpart` | Disk partitioning tool |
| `mkfs` | Filesystem format (Unix) |
| `reg delete` | Windows registry deletion |
| `Remove-Item -Recurse -Force` | PowerShell recursive delete |

Detection is **regex-based** and **case-insensitive**. No blocking happens; warnings are logged. Actual approval/blocking is handled by upstream guardrails.

### PTY and Buffering Wrappers

On macOS/Linux, shell commands may be wrapped to force unbuffered output:

| Platform | Wrapper |
|----------|---------|
| Windows | none (pass through) |
| Linux | `stdbuf -oL -eL /bin/sh -c ...` |
| macOS | `script -q /dev/null /bin/sh -c ...` |

**Critical:** The macOS wrapper creates a PTY, causing interactive programs to detect a terminal and enable paging/prompts. Use only for streaming; avoid for one-shot operations.

### stdin Policy

Non-background subprocesses use `stdin=DEVNULL`. Never set `stdin=None` (which inherits parent stdin) — in non-interactive agent contexts, programs waiting for stdin will hang indefinitely.

## CWD Architecture

CWD is managed by `openjiuwen/core/sys_operation/cwd.py` via ContextVars,
**not** by LocalWorkConfig or SysOperation. All tools and operations read
CWD through `get_cwd()`.

Three layers:

- `_project_root` — project identity anchor, set once
- `_original_cwd` — session start point, changes on worktree enter/exit
- `_cwd` — current working directory, changes after shell commands

LocalWorkConfig only holds the security boundary (`sandbox_root` +
`restrict_to_sandbox`). The sandbox is independent of CWD — CWD can
move freely while the sandbox stays fixed.

## Common Gotchas

1. **Encoding mismatches** — Always set matching encoding in read/write operations. UTF-8 is default but verify script output matches
2. **Temp file cleanup** — CodeOperation cleans up temp files in finally block, even on timeout; don't assume temp paths persist
3. **File lock timeouts** — Default 300s; long-held locks block subsequent operations. Adjust via `options={'lock_timeout': 60}` if needed
4. **Streaming vs non-streaming** — Streaming chunks may arrive out-of-order on stderr; don't assume order matches wall-clock time
5. **Permission preservation** — `preserve_permissions=True` is best-effort; failures don't error, only log warnings
6. **Sandbox enforcement** — `_resolve_path()` enforces sandbox independently of CWD; moving CWD doesn't bypass sandbox
