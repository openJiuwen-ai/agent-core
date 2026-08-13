# akernel-sdk

Python SDK for AKernel remote sandboxes.

## Installation

Install the pre-built package directly:

```bash
pip install akernel-sdk
```

For **Ant Group** internal users (will be removed after open-source release):

```bash
pip install akernel-sdk --extra-index-url https://artifacts.antgroup-inc.cn/artifact/repositories/simple-dev/
```

Or install from source:

```bash
cd sdk/python
pip install .
```

## Environment Variables

Set the following environment variables before use:

```bash
export AKERNEL_SERVER_ADDRESS="<server_address>:<port>"
export AKERNEL_TOKEN="<your_token>"
```

---

## Quick Start

```python
from akernel_sdk import Sandbox

with Sandbox(cpu=2000, memory=4096) as sb:
    # Stateless command (new process each call)
    result = sb.commands.run("echo hello")
    print(result.stdout)   # "hello\n"

    # Filesystem operations
    sb.files.write("/tmp/hello.txt", "hello world")
    content = sb.files.read("/tmp/hello.txt")
    print(content)  # "hello world"
```

### Persistent Shell

Create persistent shell sessions where cwd, env vars, and shell functions are preserved across calls:

```python
import asyncio
from akernel_sdk import Sandbox

async def main():
    with Sandbox(cpu=2000, memory=4096) as sb:
        sh = await sb.shells.create(cwd="/testbed")

        await sh.run("export MY_VAR=hello")
        result = await sh.run("echo $MY_VAR")   # → hello
        result = await sh.run("pwd")             # → /testbed

        # Multiple independent shells
        sh2 = await sb.shells.create(cwd="/tmp")
        result = await sh2.run("pwd")            # → /tmp

        await sh.kill()
        await sh2.kill()

asyncio.run(main())
```

---

## Creating a Sandbox

```python
sb = Sandbox(
    image="your-registry/python:3.12-slim",  # Docker image URL (optional)
    cpu=2000,           # CPU request in milli-cores (default: 1000)
    memory=4096,        # Memory request in MiB (default: 4096)
    cpu_limit=0,        # CPU limit in milli-cores (default: 0, see below)
    mem_limit=0,        # Memory limit in MiB (default: 0, see below)
    idle_timeout=600,   # seconds before auto-termination (default: 300)
    env={"MY_VAR": "value"},  # environment variables (optional)
    name="my-sandbox",  # instance name (optional)
    cwd="/workspace",   # initial working directory (optional)
)
```

If no `image` is specified, the default runtime image is used.

### Resource Requests vs. Limits

`cpu` / `memory` are **requests** (guaranteed allocation used for scheduling).
`cpu_limit` / `mem_limit` are **cgroup limits** (hard ceiling at runtime):

- `0` (default): limit equals the request — Guaranteed QoS.
- Positive value: must be `>= request`. The sandbox may burst up to this ceiling when spare capacity is available on the node.

---

## Stateless Command Execution (`sb.commands`)

### Foreground Commands

Run a command and wait for it to complete. Returns a `CommandResult` with `stdout`, `stderr`, and `exit_code`.

```python
result = sb.commands.run("echo hello && echo world")
print(result.stdout)     # "hello\nworld\n"
print(result.exit_code)  # 0

# With environment variables
result = sb.commands.run("echo $MY_VAR", envs={"MY_VAR": "hello"})

# With working directory
result = sb.commands.run("ls -la", cwd="/workspace")

# With custom timeout (default: 60 seconds)
result = sb.commands.run("long-running-task", timeout=300)
```

### Background Commands

Start a command in the background. Returns a `CommandHandle` for managing the process.

```python
handle = sb.commands.run("python3 server.py", background=True)
print(handle.pid)

# Wait for it to finish
result = handle.wait(timeout=30)

# Or kill it
handle.kill()
```

### Process Management

```python
# List all tracked processes
processes = sb.commands.list()
for proc in processes:
    print(f"PID {proc['pid']}: {proc['cmd']} (running={proc['running']})")

# Kill a process by PID
sb.commands.kill(pid=12345)

# Send stdin to a process by PID
sb.commands.send_stdin(pid=12345, data="input\n")
```

---

## Persistent Shell Sessions (`sb.shells`)

Persistent shells maintain a long-lived bash process inside the container. State (cwd, env vars, shell functions) is preserved across `run()` calls. Communication uses short-lived yr RPC calls (submit + long-poll), so it is immune to gateway idle-connection timeouts.

### Creating a Shell

```python
# Default /bin/bash
sh = await sb.shells.create()

# With initial working directory and environment variables
sh = await sb.shells.create(cwd="/testbed", envs={"DEBUG": "1"})

# Different shell binary
sh = await sb.shells.create(shell="/bin/zsh")
```

### Running Commands

```python
# Commands preserve state across calls
await sh.run("cd /testbed")
result = await sh.run("pwd")         # → /testbed

await sh.run("export FOO=bar")
result = await sh.run("echo $FOO")   # → bar

# One-shot cwd/envs (does not persist to subsequent calls)
result = await sh.run("pwd", cwd="/tmp")            # → /tmp
result = await sh.run("pwd")                         # → /testbed (unchanged)

# Custom timeout (default: 60s)
result = await sh.run("make test", timeout=300)
```

### Destroying a Shell

```python
await sh.kill()
```

All shells are automatically destroyed when `sb.kill()` is called or the `with` block exits.

> **stdout / stderr note:** Since the shell uses a pty, stderr is merged into stdout. `CommandResult.stderr` is always an empty string. This differs from `commands.run()` which starts an independent process per call and can separate the two streams.

---

## Filesystem Operations (`sb.files`)

All filesystem operations execute inside the remote sandbox container. Data is transferred via the RPC gateway, so they are best suited for small files (configs, scripts, patches, etc.).

> **Note:** For large files (datasets, model weights, build artifacts, etc.), prefer using external storage services (OSS, S3, etc.) and downloading them inside the sandbox via `sb.commands.run("wget ...")` or similar.

```python
# Write / read files
sb.files.write("/workspace/script.py", "print('hello')")
content = sb.files.read("/workspace/script.py")

# Binary data
sb.files.write("/workspace/data.bin", b"\x00\x01\x02\x03")
data = sb.files.read("/workspace/data.bin", format="bytes")

# Check existence
sb.files.exists("/workspace/script.py")  # True

# File info (returns EntryInfo)
info = sb.files.get_info("/workspace/script.py")
print(f"{info.name} {info.type} {info.size}B")

# List directory (returns List[EntryInfo])
entries = sb.files.list("/workspace")
entries = sb.files.list("/workspace", depth=3)  # recursive

# Rename / move
sb.files.rename("/workspace/old.txt", "/workspace/new.txt")

# Create directory
sb.files.make_dir("/workspace/nested/dir")

# Remove file or directory
sb.files.remove("/workspace/script.py")
```

---

## Port Forwarding

Expose sandbox ports via a gateway tunnel URL. Two access modes are available:

### External Access (default)

Use the VIP gateway URL. Recommended for accessing sandbox services **from outside the cluster** (e.g., local machine, CI/CD pipelines).

```python
with Sandbox(cpu=2000, memory=4096, port_forwardings=[8080]) as sb:
    sb.commands.run("python3 -m http.server 8080", background=True)
    url = sb.get_port_url(8080)
    print(url)  # "http://<vip>:8888/<instance_id>/8080"
```

### Internal Access (cluster-internal)

Use `internal=True` to resolve the traefik pod IP directly, bypassing the VIP. Recommended for **sandbox-to-sandbox** or **in-cluster** communication where both the caller and the target sandbox are in the same AKernel cluster. This provides lower latency and avoids VIP overhead.

```python
with Sandbox(cpu=2000, memory=4096, port_forwardings=[8080]) as sb:
    sb.commands.run("python3 -m http.server 8080", background=True)
    url = sb.get_port_url(8080, internal=True)
    print(url)  # "http://<traefik_pod_ip>:8888/<instance_id>/8080"
```

> **Note:** The `internal` mode fetches the traefik pod IP via the `/internal-stats` endpoint on first call and caches it for the process lifetime.

---

## Reverse Tunnel

Allow sandbox code to access services running on your **local machine** via a reverse tunnel. This is useful when your sandbox needs to call local APIs, databases, or dev servers.

```
[Local Machine]                     [Cloud Sandbox]
  Local Service :8000          Port B :8766 (loopback HTTP proxy)
       ^                             |
  TunnelClient ←── WSS/Traefik ──── Port A :8765 (WebSocket endpoint)
```

```python
with Sandbox(
    cpu=2000,
    memory=4096,
    upstream="127.0.0.1:8000",   # local service address
    proxy_port=8766,             # HTTP proxy port inside sandbox (default)
) as sb:
    tunnel_url = sb.get_tunnel_url()  # "http://127.0.0.1:8766"

    # Sandbox code reaches your local service through the tunnel
    result = sb.commands.run(
        f"python3 -c \"import urllib.request; "
        f"print(urllib.request.urlopen('{tunnel_url}/api/data').read().decode())\""
    )
    print(result.stdout)
```

- `upstream`: The local service address to tunnel to (e.g., `"127.0.0.1:8000"` or `"192.168.1.100:3000"`).
- `proxy_port`: The HTTP proxy port inside the sandbox (default `8766`). Sandbox code calls `http://127.0.0.1:{proxy_port}/...` to reach your local service.
- The WebSocket tunnel port (`proxy_port - 1`, default `8765`) is automatically registered with Traefik.

> **Note:** For test clusters with self-signed certificates, set `TUNNEL_SSL_VERIFY=0` before importing the SDK.

---

## Lifecycle Management

```python
sb.is_running()     # → True / False
sb.get_info()       # → SandboxInfo(sandbox_id, state, cpu, memory, image)
sb.sandbox_id       # → "97918efb2d4d03590000"
sb.kill()           # Terminate sandbox (destroys all shells)
```

Context manager for automatic cleanup:

```python
with Sandbox(cpu=1000, memory=2048) as sb:
    result = sb.commands.run("echo hello")
# Sandbox is automatically killed when exiting the with block
```

---

## API Reference

### Sandbox

| Constructor Parameter | Type | Default | Description |
|---|---|---|---|
| `image` | `str` | `None` | Docker image URL |
| `cpu` | `int` | `1000` | CPU request (milli-cores) |
| `memory` | `int` | `4096` | Memory request (MiB) |
| `cpu_limit` | `int` | `0` | CPU cgroup limit (milli-cores). `0` = equal to `cpu`; otherwise must be `>= cpu` |
| `mem_limit` | `int` | `0` | Memory cgroup limit (MiB). `0` = equal to `memory`; otherwise must be `>= memory` |
| `idle_timeout` | `int` | `300` | Idle timeout (seconds) |
| `env` | `dict` | `None` | Environment variables |
| `name` | `str` | `None` | Instance name |
| `cwd` | `str` | `None` | Initial working directory |
| `port_forwardings` | `List[int]` | `None` | Ports to expose |
| `upstream` | `str` | `None` | Local service address for reverse tunnel |
| `proxy_port` | `int` | `8766` | HTTP proxy port inside sandbox (for reverse tunnel) |

| Property / Method | Returns | Description |
|---|---|---|
| `sb.files` | `Filesystem` | Filesystem operations |
| `sb.commands` | `Commands` | Stateless command execution |
| `sb.shells` | `Shells` | Persistent shell factory |
| `sb.sandbox_id` | `str` | Instance ID |
| `sb.is_running()` | `bool` | Health check |
| `sb.get_info()` | `SandboxInfo` | Status info |
| `sb.get_port_url(port, internal=False)` | `str` | Tunnel URL for a forwarded port (`internal=True` for direct pod IP) |
| `sb.get_tunnel_url()` | `str` | Reverse tunnel proxy URL (e.g., `http://127.0.0.1:8766`) |
| `sb.kill()` | `None` | Terminate sandbox |

### Shells

| Method | Returns | Description |
|---|---|---|
| `await shells.create(cwd=None, envs=None, shell="/bin/bash")` | `Shell` | Create a new persistent shell |

### Shell

| Method | Returns | Description |
|---|---|---|
| `await sh.run(cmd, envs=None, cwd=None, timeout=60)` | `CommandResult` | Execute command (state preserved) |
| `await sh.kill()` | `None` | Destroy this shell |
| `sh.close()` | `None` | Sync destroy (for `Sandbox.kill()`) |
| `sh.session_id` | `str` | Session identifier |

### Commands

| Method | Returns | Description |
|---|---|---|
| `run(cmd, background=False, envs=None, cwd=None, timeout=60)` | `CommandResult` / `CommandHandle` | Run a command |
| `list()` | `List[dict]` | List processes |
| `kill(pid)` | `bool` | Kill a process |
| `send_stdin(pid, data)` | `None` | Send stdin |

### Filesystem

| Method | Returns | Description |
|---|---|---|
| `read(path, format="text")` | `str` / `bytes` | Read file |
| `write(path, data)` | `EntryInfo` | Write file |
| `list(path, depth=1)` | `List[EntryInfo]` | List directory |
| `exists(path)` | `bool` | Check existence |
| `remove(path)` | `None` | Remove file/directory |
| `rename(old, new)` | `EntryInfo` | Rename/move |
| `make_dir(path)` | `bool` | Create directory |
| `get_info(path)` | `EntryInfo` | Get metadata |

### Data Types

| Type | Fields | Description |
|------|--------|-------------|
| `EntryInfo` | `name`, `path`, `type`, `size`, `permissions`, `modified_time` | File/directory metadata |
| `CommandResult` | `stdout`, `stderr`, `exit_code` | Command result |
| `CommandHandle` | `pid`, `wait()`, `kill()`, `send_stdin()` | Background process handle |
| `SandboxInfo` | `sandbox_id`, `state`, `cpu`, `memory`, `image` | Sandbox status |

---

## Deprecated APIs

`SimpleSandbox`, `PersistentBashSandbox`, and `create_persistent()` are deprecated. Use `Sandbox` with `shells.create()` instead. They remain importable for backward compatibility but will emit `DeprecationWarning` on use.
