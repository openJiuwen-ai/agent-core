# FTPComponent Testing Guide

**Component:** `openjiuwen.core.workflow.components.tool.ftp.FTPComponent`
**Target audience:** QA engineers and developers testing the FTP workflow component
**Last reviewed:** 2026-05-07

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture Primer for Testers](#architecture-primer-for-testers)
3. [Key Difference from BrowserComponent](#key-difference-from-browsercomponent)
4. [Test Infrastructure Setup](#test-infrastructure-setup)
5. [Priority Framework](#priority-framework)
6. [Known Limitations](#known-limitations)
7. [What NOT to Test (and Why)](#what-not-to-test-and-why)
8. [Test Cases — Protocol & Authentication](#test-cases--protocol--authentication)
9. [Test Cases — UPLOAD Operation](#test-cases--upload-operation)
10. [Test Cases — DOWNLOAD Operation](#test-cases--download-operation)
11. [Test Cases — LIST Operation](#test-cases--list-operation)
12. [Test Cases — DELETE Operation](#test-cases--delete-operation)
13. [Test Cases — RENAME Operation](#test-cases--rename-operation)
14. [Test Cases — CREATE_DIRECTORY Operation](#test-cases--create_directory-operation)
15. [Test Cases — GET_INFO Operation](#test-cases--get_info-operation)
16. [Test Cases — Connection Management & Retry](#test-cases--connection-management--retry)
17. [Test Cases — Error Handling](#test-cases--error-handling)
18. [Test Cases — Security Configuration](#test-cases--security-configuration)
19. [Test Cases — Template Variables & Workflow Integration](#test-cases--template-variables--workflow-integration)
20. [Output Contract Tests](#output-contract-tests)
21. [Multi-Operation Batching Tests](#multi-operation-batching-tests)
22. [Recommended Test Server Setup](#recommended-test-server-setup)
23. [Execution Checklist](#execution-checklist)

---

## Overview

`FTPComponent` is a deterministic, protocol-level workflow component that executes file transfer operations directly via `aioftp` (FTP/FTPS) and `asyncssh` (SFTP). Unlike `BrowserComponent`, **there is no LLM intermediary** — every operation maps directly to a protocol-level call. This allows precise assertions on transfer byte counts, file metadata, and operation results.

The component supports three protocols, seven operation types, and two authentication methods. It integrates into the openJiuwen workflow engine as a standard `ComponentComposable`.

---

## Architecture Primer for Testers

```
Your test code
     ↓  FTPComponentConfig(credentials=..., operations=[...])
FTPComponent.invoke() / stream()
     ↓  opens connection via FTPService
     ↓  protocol dispatch: FTP/FTPS → aioftp client
     ↓                     SFTP     → asyncssh client
     ↓  executes each FTPOperationConfig sequentially
     ↓  connection pooled by (host, port, username, protocol)
Returns structured output dict (never raises exceptions)
```

**Connection lifecycle per invoke():**
- A connection is established (or reused from pool) once.
- All operations in the list share that single connection.
- On completion or error, the pool entry is either kept or evicted.

---

## Key Difference from BrowserComponent

| Aspect | BrowserComponent | FTPComponent |
|---|---|---|
| Execution engine | LLM + Playwright MCP (non-deterministic) | Direct protocol client (deterministic) |
| Assertion style | Schema + intent only | Exact values (byte counts, file lists) |
| Test isolation | Public websites | Requires a dedicated test FTP server |
| Failure mode | LLM may succeed via alternate path | Binary pass/fail at protocol level |
| Credential handling | Model API keys | FTP/SFTP server credentials |

---

## Test Infrastructure Setup

### Python Dependencies

```bash
uv sync
```

Ensure `aioftp` and `asyncssh` are in the resolved environment:

```bash
python -c "import aioftp, asyncssh; print('OK')"
```

### Required: Local Test FTP/SFTP Server

**All live tests require a running FTP/SFTP server.** Tests that run against non-existent servers are only useful for error-path validation (authentication failure, connection refused).

**Recommended: Docker-based test servers**

```bash
# FTP server (vsftpd) — plain FTP
docker run -d --name ftp-test \
  -p 21:21 -p 21100-21110:21100-21110 \
  -e FTP_USER=testuser -e FTP_PASS=testpass \
  -e PASV_ADDRESS=127.0.0.1 -e PASV_MIN_PORT=21100 -e PASV_MAX_PORT=21110 \
  fauria/vsftpd

# SFTP server (OpenSSH-based)
docker run -d --name sftp-test \
  -p 22:22 \
  atmoz/sftp testuser:testpass:::upload

# FTPS: use vsftpd with SSL enabled or an equivalent image with a self-signed cert
```

Set environment variables pointing at these servers:

```bash
export FTP_HOST=127.0.0.1
export FTP_PORT=21
export FTP_USERNAME=testuser
export FTP_PASSWORD=testpass

export SFTP_HOST=127.0.0.1
export SFTP_USERNAME=testuser
export SSH_KEY_PATH=/path/to/test_id_rsa  # for key-auth tests
```

### Server Pre-conditions

Before running any test session, verify the server is accessible:

```bash
# FTP
curl -v --user testuser:testpass ftp://127.0.0.1/

# SFTP
sftp -P 22 testuser@127.0.0.1
```

If either fails, investigate server setup before running component tests.

---

## Priority Framework

| Priority | Meaning | Must pass before... |
|---|---|---|
| **P0** | Blocking — breaks fundamental guarantees | Any release |
| **P1** | Important — covers all primary use cases | Any public release |
| **P2** | Edge cases, less common configurations | Best effort |

---

## Known Limitations

### L1 — Live Server Required for All Real-Transfer Tests
The component makes actual network calls. Tests that need to assert `bytes_transferred`, file existence, or directory listings must run against a real (or Dockerised) FTP/SFTP server. Tests without a server are limited to error-path and configuration validation.

### L2 — Connection Pool is Process-Global
The connection pool is keyed by `(host, port, username, protocol)`. Concurrent tests using the same credentials may share a connection and interfere with each other. Run concurrent tests with different usernames or sequential isolation when possible.

### L3 — Pattern Filtering Semantics Differ by Protocol
For **FTP/FTPS**, pattern filtering in `LIST` is server-side and behaviour is server-dependent. For **SFTP**, pattern filtering is client-side (glob matching). Do not assume identical filtering behaviour across protocols without explicit verification.

### L4 — SSH Key File Permissions are OS-Enforced
On POSIX systems, `asyncssh` will reject an SSH private key whose file permissions are not `600`. A test using a world-readable key file (`644`) will fail with a permissions error — this is correct and expected behaviour.

### L5 — FTPS Certificates in Test Environments
Self-signed certificates used in test FTPS servers will fail `verify_ssl=True`. Use `verify_ssl=False` in test environments. Never use `verify_ssl=False` in a production scenario.

### L6 — Download to Memory Has No Size Guardrail
`FTPDownloadConfig` with no `local_path` returns file content as `bytes` in the result dict. There is no maximum size limit. Downloading very large files to memory in this mode can cause OOM. Do not test with files > a few MB in memory-download mode.

### L7 — Transfer Mode (Binary vs ASCII) is Not Directly Configured
The API does not expose a binary/ASCII transfer mode toggle as a top-level config field. Assume binary mode unless documentation or implementation clarifies otherwise. ASCII mode tests for text files with line-ending normalisation are out of scope.

### L8 — `content` Upload Encodes as UTF-8
When `FTPUploadConfig.content` (a string) is used instead of `local_path`, the content is encoded to bytes before transfer. The encoding used is the component's configured `encoding` (default: UTF-8). Uploading strings containing characters not representable in the configured encoding is undefined behaviour.

### L9 — Retry Timing is Not Precisely Testable
The retry mechanism uses exponential backoff. Asserting exact timing of retries (e.g., "second attempt happened after exactly 2s") is not reliable in CI. Assert only that the final result is correct, not timing of intermediate attempts.

### L10 — Template Variables Resolved at Workflow Layer
Variables like `${env.FTP_HOST}` or `${start.backup_file}` are resolved by the workflow engine before the `FTPComponent` sees the configuration. The FTP component itself performs no variable substitution. Test template resolution through `workflow.invoke()`, not by calling the component directly.

---

## What NOT to Test (and Why)

| Do NOT test | Reason |
|---|---|
| Remote FTP server speed or throughput | Not the component's responsibility |
| `aioftp` / `asyncssh` library internals | External dependencies with their own test suites |
| OS-level permissions on the remote server | Server admin concern, not component behaviour |
| Exact retry timing / exponential backoff delays | Non-deterministic in CI environments (see L9) |
| FTP server-side directory permission enforcement | Server configuration, not component logic |
| TLS handshake protocol negotiation details | Handled by the OS TLS stack, not the component |
| Behaviour of `passive_mode=False` in NAT environments | Network topology concern, not component logic |

---

## Test Cases — Protocol & Authentication

**Priority:** P0 — the component must connect and authenticate correctly for all supported protocols.

| ID | Test Case | Setup | Expected Result |
|---|---|---|---|
| AUTH-01 | FTP password login (success) | Valid `host`, `username`, `password`, `protocol=FTP` | `result["ok"] is True`; `connection.connected is True` |
| AUTH-02 | FTPS password login (success) | Valid FTPS server; `protocol=FTPS`, `verify_ssl=False` | `result["ok"] is True`; `connection.protocol == "ftps"` |
| AUTH-03 | SFTP password login (success) | Valid SFTP server; `protocol=SFTP`, valid password | `result["ok"] is True`; `connection.protocol == "sftp"` |
| AUTH-04 | SFTP SSH key login (success) | Valid key file (permissions 600); `private_key` set | `result["ok"] is True` |
| AUTH-05 | SFTP SSH key with passphrase | Key encrypted with passphrase; `private_key_passphrase` set | `result["ok"] is True` |
| AUTH-06 | Wrong password (FTP) | Invalid `password` | `result["ok"] is False`; `error` field populated; no Python exception |
| AUTH-07 | Wrong password (SFTP) | Invalid `password` | `result["ok"] is False`; no Python exception |
| AUTH-08 | Wrong SSH key (SFTP) | Key not authorised on server | `result["ok"] is False`; error mentions authentication |
| AUTH-09 | SSH key with wrong passphrase | Correct key but wrong `private_key_passphrase` | `result["ok"] is False`; no Python exception |
| AUTH-10 | SSH key with wrong file permissions | Key file permissions `644` on POSIX | `result["ok"] is False`; error references permissions |
| AUTH-11 | Unreachable host | `host` set to `192.0.2.1` (TEST-NET, guaranteed unreachable) | `result["ok"] is False`; error mentions connection refused or timeout |
| AUTH-12 | Wrong port | Correct host, wrong port number | `result["ok"] is False`; no Python exception |
| AUTH-13 | Default port for FTP | No `port` specified, `protocol=FTP` | Component uses port 21 by default |
| AUTH-14 | Default port for SFTP | No `port` specified, `protocol=SFTP` | Component uses port 22 by default |

---

## Test Cases — UPLOAD Operation

**Priority:** P0 for basic upload; P1 for advanced options.

| ID | Test Case | Steps | Expected Result |
|---|---|---|---|
| UPL-01 | Upload local file | `local_path` = path to an existing file; `remote_path` = `/test_upload.txt` | `ok=True`; `data.bytes_transferred > 0`; file exists on server |
| UPL-02 | Upload to nested path with `create_directories=True` | `remote_path="/a/b/c/file.txt"`, `create_directories=True` | `ok=True`; directories `a/b/c` created; file present |
| UPL-03 | Upload to nested path with `create_directories=False` (default) | `remote_path="/nonexistent_dir/file.txt"`, `create_directories=False` | `ok=False`; error mentions missing directory |
| UPL-04 | Upload with `overwrite=True` (file exists) | Upload same `remote_path` twice | Second upload: `ok=True`; file replaced; no error |
| UPL-05 | Upload with `overwrite=False` (file exists) | Upload same `remote_path` twice with `overwrite=False` | Second upload: `ok=False`; error indicates file already exists |
| UPL-06 | Upload string content directly | `content="hello world"`, no `local_path` | `ok=True`; remote file contains the uploaded string; `bytes_transferred > 0` |
| UPL-07 | Upload content with non-ASCII characters | `content` contains Unicode (e.g., `"café"`) | `ok=True`; remote file readable when decoded with configured encoding |
| UPL-08 | Upload non-existent local file | `local_path="/tmp/does_not_exist_abc123.txt"` | `ok=False`; error references file not found; no Python exception |
| UPL-09 | `bytes_transferred` accuracy | Upload a known-size file (e.g., exactly 1024 bytes) | `data.bytes_transferred == 1024` (or within acceptable tolerance) |
| UPL-10 | `duration_ms` present | Any successful upload | `data.duration_ms` is an integer `>= 0` |
| UPL-11 | Both `content` and `local_path` set | Provide both fields simultaneously | Document the observed behaviour (which takes precedence, or error) |

---

## Test Cases — DOWNLOAD Operation

**Priority:** P0 for basic download; P1 for memory-mode and overwrite.

| ID | Test Case | Steps | Expected Result |
|---|---|---|---|
| DWN-01 | Download to local path | `remote_path` = existing file; `local_path` = writable local path | `ok=True`; `data.bytes_transferred > 0`; file exists locally |
| DWN-02 | Download to memory (no `local_path`) | `local_path` omitted | `ok=True`; `data.content` is `bytes` with correct content; `data.bytes_transferred > 0` |
| DWN-03 | Downloaded content integrity | Upload a known file, then download it to memory | `data.content` decoded equals original uploaded string |
| DWN-04 | Download with `overwrite=True` (file exists locally) | Download to same `local_path` twice | Second download: `ok=True`; local file updated |
| DWN-05 | Download with `overwrite=False` (file exists locally) | Download to same `local_path` twice with `overwrite=False` | Second download: `ok=False`; error indicates local file already exists |
| DWN-06 | Download non-existent remote file | `remote_path="/this_file_does_not_exist.xyz"` | `ok=False`; error populated; no Python exception |
| DWN-07 | `local_path` in result | Download with a `local_path` specified | `data.local_path` equals the specified path |
| DWN-08 | Large file (>1 MB) to local path | Upload then download a 2 MB file | `ok=True`; `data.bytes_transferred` approximately matches file size |

---

## Test Cases — LIST Operation

**Priority:** P1

| ID | Test Case | Steps | Expected Result |
|---|---|---|---|
| LST-01 | List root directory | `remote_path="/"`, no pattern | `ok=True`; `data.items` is a list; each item has `name`, `type`, `size`, `modified`, `path` keys |
| LST-02 | List with glob pattern | Upload several files; `pattern="*.txt"` | `data.items` contains only `.txt` files |
| LST-03 | Pattern with no matches | `pattern="*.nonexistentextension"` | `ok=True`; `data.items == []` (not an error) |
| LST-04 | Recursive listing | Directory with nested subdirs; `recursive=True` | `data.items` includes files from all subdirectories |
| LST-05 | Non-recursive listing | Directory with nested subdirs; `recursive=False` | `data.items` includes only top-level entries |
| LST-06 | `include_hidden=True` | Server has hidden files (`.dotfile`) | `data.items` includes entries starting with `.` |
| LST-07 | `include_hidden=False` | Server has hidden files; `include_hidden=False` | `.dotfile` entries absent from `data.items` |
| LST-08 | List non-existent directory | `remote_path="/dir_that_does_not_exist"` | `ok=False`; error populated; no Python exception |
| LST-09 | Item type field | Directory with both files and subdirectories | Each item's `type` field is `"file"` or `"directory"` |
| LST-10 | Pattern semantics (FTP vs SFTP) | Same pattern on FTP and SFTP | Document any differences in results (see L3) |

---

## Test Cases — DELETE Operation

**Priority:** P1

| ID | Test Case | Steps | Expected Result |
|---|---|---|---|
| DEL-01 | Delete an existing file | Upload file, then `DELETE` it | `ok=True`; `data.deleted is True`; file no longer present on server |
| DEL-02 | Delete non-existent file with `ignore_missing=True` | `remote_path` points to missing file; `ignore_missing=True` | `ok=True`; no error |
| DEL-03 | Delete non-existent file with `ignore_missing=False` | `remote_path` points to missing file; `ignore_missing=False` (default) | `ok=False`; error mentions file not found; no Python exception |
| DEL-04 | Delete an empty directory | Create dir, then delete it | `ok=True`; directory no longer present |
| DEL-05 | Delete non-empty directory (recursive) | Create dir with files; `DELETE` with `recursive=True` (if supported) | `ok=True`; directory and all contents removed — verify against actual API |
| DEL-06 | Attempt delete without permissions | Target a path the user cannot write | `ok=False`; error references permission denied; no Python exception |

> **DEL-05 note:** Verify whether `FTPDeleteConfig` exposes a `recursive` field. If not, attempting to delete a non-empty directory should fail with an appropriate error. Document actual behaviour.

---

## Test Cases — RENAME Operation

**Priority:** P1

| ID | Test Case | Steps | Expected Result |
|---|---|---|---|
| REN-01 | Rename a file | Upload file; `RENAME` it to a new name in same directory | `ok=True`; `data.old_path` and `data.new_path` present; new name exists; old name absent |
| REN-02 | Move a file (different directory) | `old_path="/a/file.txt"`, `new_path="/b/file.txt"` | `ok=True`; file at new path; absent at old path |
| REN-03 | Rename a directory | Create dir; rename it | `ok=True`; old name absent; new name exists |
| REN-04 | Rename to existing path (overwrite) | Target of rename already exists; `overwrite=True` | `ok=True`; destination replaced — document if `overwrite` is supported |
| REN-05 | Rename non-existent source | `old_path` does not exist | `ok=False`; error populated; no Python exception |
| REN-06 | `old_path` and `new_path` in result | Any successful rename | `data.old_path` and `data.new_path` match inputs |

---

## Test Cases — CREATE_DIRECTORY Operation

**Priority:** P1

| ID | Test Case | Steps | Expected Result |
|---|---|---|---|
| MKD-01 | Create a single directory | `remote_path="/new_test_dir"` | `ok=True`; `data.created is True`; `data.path` matches; directory exists on server |
| MKD-02 | Create nested directories with `recursive=True` | `remote_path="/a/b/c/d"`, `recursive=True` | `ok=True`; entire path `/a/b/c/d` created |
| MKD-03 | Create nested directories with `recursive=False` | `remote_path="/x/y/z"` when `/x/y` doesn't exist, `recursive=False` | `ok=False`; error mentions missing parent; no Python exception |
| MKD-04 | Create already-existing dir with `ignore_exists=True` | Directory already exists; `ignore_exists=True` | `ok=True`; no error; directory still present |
| MKD-05 | Create already-existing dir with `ignore_exists=False` | Directory already exists; `ignore_exists=False` | `ok=False`; error mentions already exists; no Python exception |
| MKD-06 | `path` field in result | Any successful creation | `data.path` equals `remote_path` from config |

---

## Test Cases — GET_INFO Operation

**Priority:** P1

| ID | Test Case | Steps | Expected Result |
|---|---|---|---|
| INF-01 | Get info on an existing file | Upload file; `GET_INFO` on its path | `ok=True`; `data.type == "file"`; `data.size > 0`; `data.modified` is a non-empty string |
| INF-02 | Get info on an existing directory | `GET_INFO` on a known directory path | `ok=True`; `data.type == "directory"` |
| INF-03 | File size accuracy | Upload a known-size file (e.g., 512 bytes); `GET_INFO` | `data.size == 512` |
| INF-04 | Modified timestamp format | `GET_INFO` on any file | `data.modified` parseable as ISO 8601 datetime (or at minimum a non-empty string) |
| INF-05 | Permissions field | `GET_INFO` on a file | `data.permissions` is a non-empty string (e.g., `"0644"`) |
| INF-06 | Get info on non-existent path | `remote_path="/does_not_exist.txt"` | `ok=False`; error populated; no Python exception |

---

## Test Cases — Connection Management & Retry

**Priority:** P1 for timeouts; P2 for retry timing.

| ID | Test Case | Steps | Expected Result |
|---|---|---|---|
| CON-01 | Connection timeout respected | `timeout=1` (1 second) with a slow/unreachable host | `ok=False`; error mentions timeout; completes within ~5s |
| CON-02 | Default timeout value | No `timeout` set; connect to reachable server | Connects; `ok=True` (confirms 30s default is not triggered on a fast server) |
| CON-03 | Passive mode (default) | `passive_mode=True`; run behind NAT | Connection succeeds |
| CON-04 | Max retries respected | `max_retries=1`; connect to a server that refuses the first attempt | At most 1 retry; `ok=False` if all attempts fail; no Python exception |
| CON-05 | `max_retries=0` (no retry) | Force connection to fail once; `max_retries=0` | Fails immediately on first failure; no retry attempt |
| CON-06 | Connection reuse across operations | Multiple operations in a single config | Only one TCP connection opened (verify via server access log if possible) |
| CON-07 | Keepalive interval | Long-running session; `keepalive_interval=10` | Connection stays alive between operations without timeout |
| CON-08 | Custom encoding | Server with non-UTF-8 filenames; `encoding="latin-1"` | Filenames decoded correctly; no UnicodeDecodeError |

---

## Test Cases — Error Handling

**Priority:** P0 — the "no exceptions" contract is fundamental.

| ID | Test Case | Steps | Expected Result |
|---|---|---|---|
| ERR-01 | No Python exception on any failure | Any of AUTH-06–12 or failed operations above | Zero unhandled exceptions; all errors in `result["error"]` or per-operation `error` field |
| ERR-02 | Top-level `error` is first error | Three operations; second fails | `result["error"]` matches `result["operations"][1]["error"]` |
| ERR-03 | Per-operation `ok` is independent | Three operations; middle one fails | `operations[0]["ok"]` and `operations[2]["ok"]` have their own values; not auto-set to False |
| ERR-04 | Empty operations list | `FTPComponentConfig(credentials=..., operations=[])` | `ok=True`; `operations == []`; no exception |
| ERR-05 | Mismatched operation type / config | `operation_type=UPLOAD` but `upload=None` | `ok=False` with descriptive error; no Python exception |
| ERR-06 | Server closes connection mid-transfer | Simulate by killing server during large upload (if possible) | `ok=False`; error populated; no Python exception |

---

## Test Cases — Security Configuration

**Priority:** P1 for FTPS; P2 for client certificates.

| ID | Test Case | Steps | Expected Result |
|---|---|---|---|
| SEC-01 | FTPS with `verify_ssl=True` (valid cert) | Server with a CA-signed certificate | `ok=True`; TLS handshake succeeds |
| SEC-02 | FTPS with `verify_ssl=True` (self-signed cert) | Server with self-signed cert | `ok=False`; error mentions certificate verification; no Python exception |
| SEC-03 | FTPS with `verify_ssl=False` (self-signed cert) | `verify_ssl=False`; self-signed server | `ok=True`; connection proceeds without cert validation |
| SEC-04 | FTPS with explicit TLS version | `ssl_version="TLSv1.3"` on a server that supports it | `ok=True` |
| SEC-05 | FTPS with unsupported TLS version | `ssl_version="TLSv1.0"` on a modern server | `ok=False`; error mentions TLS version; no Python exception |
| SEC-06 | FTPS with client certificate | `cert_file` and `key_file` set; server requires client cert | `ok=True` if cert is accepted |
| SEC-07 | Credentials not logged | Run any test with a DEBUG logger; inspect log output | `password` and `private_key_passphrase` do not appear in any log line |
| SEC-08 | `${env.FTP_PASSWORD}` template in credentials | Set `password="${env.FTP_PASSWORD}"` via workflow; env var set | Credential resolved correctly; no literal `${env.FTP_PASSWORD}` string sent to server |

---

## Test Cases — Template Variables & Workflow Integration

**Priority:** P1

| ID | Test Case | Steps | Expected Result |
|---|---|---|---|
| TPL-01 | `${env.VAR}` in `host` | `host="${env.FTP_HOST}"`, env var set | Component connects to correct host |
| TPL-02 | `${env.VAR}` in `password` | `password="${env.FTP_PASSWORD}"`, env var set | Authentication succeeds with resolved value |
| TPL-03 | `${start.field}` in `local_path` | `local_path="${start.backup_file}"`, `inputs={"backup_file": "/tmp/test.txt"}` | File at resolved path is uploaded |
| TPL-04 | `${start.field}` in `remote_path` | `remote_path="/backups/${start.backup_filename}"` | File uploaded to the resolved path |
| TPL-05 | Unresolved variable (missing env var) | `host="${env.FTP_HOST}"`, env var NOT set | `ok=False`; error mentions unresolved variable or connection failure; no Python exception |
| WFL-01 | Component as single workflow node | `Start → FTPComponent → End` | `result["ftp_upload"]` contains component output |
| WFL-02 | Output mapped to End schema | `End(inputs_schema={"bytes": "${ftp_upload.operations[0].data.bytes_transferred}"})` | `result["bytes"]` equals the actual bytes transferred |
| WFL-03 | Multi-operation result indexing | Three operations; `${comp.operations[2].data}` | Correct per-operation data returned at index 2 |
| WFL-04 | Two FTP components in sequence | `Start → Upload → Verify (GET_INFO) → End` | Both components execute; second sees the file uploaded by the first |

---

## Output Contract Tests

**Priority:** P0 — output schema must be consistent regardless of success or failure.

### Top-level Result

Verify these fields are **always present**:

```
result["ok"]          → bool
result["operations"]  → list (may be empty)
result["connection"]  → {"host": str, "protocol": str, "connected": bool}
result["error"]       → str or None
```

### Per-Operation Result

Each entry in `result["operations"]` must always contain:

```
entry["ok"]        → bool
entry["operation"] → str  (e.g., "upload", "download", "list", ...)
entry["data"]      → dict or None
entry["error"]     → str or None
```

| ID | Scenario | Assertion |
|---|---|---|
| OUT-01 | Successful single UPLOAD | All top-level keys present; `ok=True`; `error=None` |
| OUT-02 | Failed authentication | All top-level keys present; `ok=False`; `error` is a string |
| OUT-03 | Mixed success/failure | `result["error"]` is the **first** error; other per-operation `ok` values independent |
| OUT-04 | Operations count matches config | 3-operation config → `len(result["operations"]) == 3` |
| OUT-05 | `connection.protocol` matches config | `protocol=FTPS` in config → `connection.protocol == "ftps"` |
| OUT-06 | `connection.connected` on success | Any successful operation | `connection.connected is True` |
| OUT-07 | `connection.connected` on auth failure | Wrong credentials | `connection.connected is False` |

---

## Multi-Operation Batching Tests

**Priority:** P1 — verifies the sequential execution and connection reuse model.

| ID | Test Case | Steps | Expected Result |
|---|---|---|---|
| BAT-01 | Upload then LIST in one component | Two operations in one config | LIST result shows the uploaded file; single connection used |
| BAT-02 | Upload → GET_INFO (verify upload) | Upload then immediately GET_INFO on same path | `GET_INFO` data reflects the uploaded file's size |
| BAT-03 | CREATE_DIR then UPLOAD into it | Create directory then upload a file into it | Both `ok=True`; file present in the created directory |
| BAT-04 | UPLOAD → RENAME → DELETE | Three-step file lifecycle in one component | Each step `ok=True`; file absent after DELETE |
| BAT-05 | First operation fails; remaining operations | First op targets a bad path; second op is independent | First `ok=False`; second proceeds (verify: does the component short-circuit or continue?) — document behaviour |
| BAT-06 | Ten operations in one config | 10 sequential LIST operations | All 10 complete; `len(result["operations"]) == 10` |

> **BAT-05 is critical:** Determine whether the component aborts on first failure or continues executing all operations. The documentation implies errors propagate to the top level but does not explicitly state whether subsequent operations are skipped. Test and document the actual behaviour.

---

## Recommended Test Server Setup

### Docker Compose (for full test coverage)

```yaml
version: "3.9"
services:
  ftp:
    image: fauria/vsftpd
    ports:
      - "21:21"
      - "21100-21110:21100-21110"
    environment:
      FTP_USER: testuser
      FTP_PASS: testpass
      PASV_ADDRESS: 127.0.0.1
      PASV_MIN_PORT: 21100
      PASV_MAX_PORT: 21110

  sftp:
    image: atmoz/sftp
    ports:
      - "2222:22"
    command: "testuser:testpass:::upload"
```

### What Each Server Covers

| Server | Protocol | Tests Covered |
|---|---|---|
| `fauria/vsftpd` | FTP | AUTH-01, UPL-*, DWN-*, LST-*, DEL-*, REN-*, MKD-*, INF-*, BAT-* |
| `atmoz/sftp` (password) | SFTP (password) | AUTH-03, and all operation tests repeated for SFTP |
| `atmoz/sftp` (key) | SFTP (SSH key) | AUTH-04, AUTH-05, AUTH-08–10 |
| FTPS-capable server | FTPS | AUTH-02, SEC-01–07 |

> **FTPS note:** Getting a proper FTPS Docker setup requires a server image with an SSL certificate. For basic FTPS testing, use `SEC-03` (`verify_ssl=False`) with a self-signed cert. For `SEC-01`, use a Let's Encrypt certificate on a real server.

---

## Execution Checklist

Use this checklist before signing off a testing cycle:

### P0 Checks (blocking)
- [ ] AUTH-01 (FTP password login) passes against Docker test server
- [ ] AUTH-06 (wrong password) returns `ok=False` with no Python exception
- [ ] AUTH-11 (unreachable host) returns `ok=False` with no Python exception
- [ ] UPL-01 (basic upload) passes; `bytes_transferred > 0`
- [ ] DWN-01 (basic download to path) passes
- [ ] ERR-01 verified: zero unhandled Python exceptions in any test
- [ ] ERR-02 verified: top-level `error` is first error in sequence
- [ ] OUT-01 through OUT-04 (output schema contract) pass

### P1 Checks
- [ ] All AUTH-* cases for at least FTP and SFTP (password) documented
- [ ] UPL-02 (`create_directories=True`) and UPL-04/05 (`overwrite`) verified
- [ ] DWN-02 (download to memory) verified with content integrity check (DWN-03)
- [ ] LST-01 through LST-05 (list with pattern, recursive) verified
- [ ] DEL-02/03 (`ignore_missing` variants) verified
- [ ] MKD-04/05 (`ignore_exists` variants) verified
- [ ] CON-01 (timeout) verified
- [ ] BAT-05 (behaviour on first-operation failure) documented
- [ ] SEC-07 (credentials not in logs) verified
- [ ] WFL-01/02 (workflow integration + output mapping) verified

### P2 Checks
- [ ] SFTP SSH key tests (AUTH-04/05/08–10) documented
- [ ] FTPS SSL tests (SEC-01–06) documented
- [ ] LST-10 (FTP vs SFTP pattern semantics) cross-protocol comparison documented
- [ ] CON-07/08 (keepalive, custom encoding) behaviour documented
- [ ] TPL-05 (unresolved template variable) error path verified

### Notes to File with Results
1. FTP server software and version used in testing
2. Any L1–L10 limitations that manifested during testing
3. BAT-05 actual behaviour: does the component short-circuit on first failure?
4. DEL-05 actual behaviour: does `FTPDeleteConfig` support recursive deletion?
5. LST-10 findings: any pattern filtering differences between FTP and SFTP
