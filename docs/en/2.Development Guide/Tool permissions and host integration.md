# Tool permissions (execution guard)

This document describes how **openjiuwen** enforces **allow / ask / deny** on tool calls and how a product integrates via **`ToolPermissionHost`**.

It complements:

- [Security Notes](./Security%20Notes.md) — transport, SSRF, custom components.
- [Security Guardrail](./Advanced%20Usage/Security%20Guardrail.md) — content-risk backends on LLM/tool I/O.

**Guardrail** is mainly **content safety**; **permissions** are **execution control** (whether a tool may run, confirm interrupts, YAML persistence).

---

## 1. Components

| Piece | Role |
|--------|------|
| **`SecurityRail`** | Default harness rail: safety-oriented prompts / scope hints. |
| **`PermissionInterruptRail`** | Runs on **`before_tool_call`** for **all tools**; `ask` uses the Confirm interrupt flow. |
| **`PermissionEngine`** | Holds `permissions` policy and evaluates decisions. |
| **`ToolPermissionHost`** | Injected by the app or CLI: YAML path, hot snapshot, ACP, persistence, workspace, optional channel gate and scene hook. |

Runnable example (repo root):

```bash
uv run python examples/permissions/permission_demo.py
```

Public API surface: `openjiuwen.harness.security`.

---

## 2. Enabling on DeepAgent

1. Pass a **`permissions`** dict with **`enabled: true`** into **`create_deep_agent(..., permissions=..., permission_host=...)`** (host recommended in production).
2. **`DeepAgent._queue_pending_rails`** calls **`build_permission_interrupt_rail`**, which returns **`None`** unless `permissions.get("enabled")` is true.

Minimal example (evaluated by `tiered_policy.evaluate_tiered_policy`; `schema` is optional documentation):

```python
permissions = {
    "enabled": True,
    "schema": "tiered_policy",
    "permission_mode": "normal",
    "tools": {"read_file": "ask", "write_file": "deny"},
    "defaults": {"*": "allow"},
    "rules": [],
    "approval_overrides": [],
}
```

The engine no longer supports a separate legacy “pattern-only” path; express policy with `tools`, `defaults`, `rules`, and `approval_overrides`.

Package command rules ask before recursive, forced root deletion with `rm`
(including `/`, quoted roots, Windows drive roots and bare drive letters),
PowerShell `Remove-Item` and its deletion aliases, and CMD `rd`/`rmdir`/`del`/`erase /s`.
This includes PowerShell parameter prefixes such as `-Rec`/`-Fo`, attached
CMD switches such as `rd/s/q`, literal dot roots (`/./`, `C:/.`), and Windows
extended/device prefixes (`\\?\C:\`, `\\.\C:\`).
Root-content wildcards such as `/*`, `C:\*`, and `C:\*.*` are also covered. Ordinary
file or directory deletion is left to the host's product rules. These are
command-text checks; they do not resolve variables or inspect script bodies.
Hosts must keep permission checks and package command rules enabled for this
protection to apply. Existing explicit approval grants retain their semantics.

---

## 3. `ToolPermissionHost` (summary)

See **`openjiuwen.harness.security.host.ToolPermissionHost`**.

| Field | Purpose |
|--------|---------|
| **`get_permissions_snapshot`** | Returns a dict shaped like the **`permissions`** section; rail may **`update_config`** before each check. |
| **`request_permission_confirmation`** | Async **ASK** confirmation hook: returns **`PermissionConfirmResponse`**, the literal **`"interrupt"`** (fall back to built-in Confirm), or **`None`** (failure). “Always allow” is **`approved: true` with `auto_confirm: true`** (merge `permissions`, update memory, then persist). Products may implement ACP; see **`RequestPermissionConfirmationHook`**. |
| **`persist_allow_rule`** | ``(permissions: dict) -> bool``: called after the rail merges and `update_config`; persist the given full `permissions` dict; return `False` to roll back memory. If unset, YAML write uses **`write_permissions_section_to_agent_config_yaml`**. |
| **`resolve_workspace_dir`** | Workspace root for path checks. |
| **`permission_yaml_path`** | Agent **config YAML** path; **`persist_*`** writes the **`permissions:`** subtree. File may not exist yet if the **parent directory** exists; first write can bootstrap from the global engine config. |
| **`tool_permission_checks_active`** | `Callable[[], bool]`. If it returns **False**, tool permission checks are **skipped** (allow). If **unset**, checks always run. The host decides what “active” means (e.g. product-specific entry routing). |
| **`permission_scene_hook`** | Optional async hook before tiered evaluation; product-specific (e.g. digital-avatar flows in a host repo). |

---

## 4. What is “read”?

1. **In-memory `permissions`** passed at **`create_deep_agent`** (caller may load YAML first).
2. Optional **`get_permissions_snapshot()`** to refresh from disk or config service before a tool call.
3. **`PermissionEngine.config`** — authoritative for evaluation after updates.

There is **no** framework-fixed filename; the host chooses paths via **`permission_yaml_path`** and snapshots.

---

## 5. What is “written”?

Persistence targets the full **`permissions`** policy dict after merges and in-memory **`update_config`**. **Exactly one** sink is used:

- **`ToolPermissionHost.persist_allow_rule` is set**: after “always allow” merges succeed, the rail calls **only** this hook with the full merged **`permissions`** dict (host writes DB, remote config, etc.). The built-in **`write_permissions_section_to_agent_config_yaml`** is **not** invoked. If the hook returns **`False`**, the rail **rolls back** `PermissionEngine.config` in memory.
- **Hook unset**: the rail uses **`write_permissions_section_to_agent_config_yaml`** to write the **`permissions:`** subtree into the **root agent YAML**.

For the built-in YAML writer, **`_resolve_agent_config_yaml_path`** uses **only** the explicit **`config_yaml_path`** argument (typically **`ToolPermissionHost.permission_yaml_path`** passed by the rail). If missing, persistence cannot locate a file. Environment variables such as **`OPENJIUWEN_AGENT_CONFIG`** are **not** used for this resolution.

Permission interrupt metadata includes `authorization_scopes`: `exact` plus the applicable `parent` (files), `domain` (URLs), or `subcommands` (reliably parsed shell calls). When confirmation fields are omitted, the server selects the applicable broader scope, falling back to `exact`; client changes are not required. Explicit `exact`, or `authorization_mode: allow` alone, retains exact authorization. A scope-only reply infers the matching mode. Split shell approval creates multiple `shell_subcommand` records, binding each literal child to the original invocation directory, tool and interpreter. Both children of `cd repo && git status` retain that initial directory. They lift shell ASK/undetermined results only, preserving structural checks and other guards. Existing exact grants are not converted; allow-once still saves no grant.

Persistence hooks accepting `approval_grants` receive the current shell approval batch and should save only that batch, without promoting unrelated session grants. Single-object approvals still pass `approval_grant`. Session approvals use `persist_session_allow_rule`; allow-once saves no grant.

---

## 6. Source index

| Path | Notes |
|------|--------|
| `openjiuwen/harness/security/core.py` | `PermissionEngine` |
| `openjiuwen/harness/security/host.py` | `ToolPermissionHost` |
| `openjiuwen/harness/security/factory.py` | `build_permission_interrupt_rail` |
| `openjiuwen/harness/rails/security/tool_security_rail.py` | `PermissionInterruptRail` |
| `openjiuwen/harness/security/patterns.py` | Matching + YAML **`persist_*`** |
| `openjiuwen/harness/deep_agent.py` | Queues permission rail when enabled |
| `examples/permissions/permission_demo.py` | Demo script |
