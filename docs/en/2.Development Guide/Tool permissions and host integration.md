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

Native FileGuard extracts Shell paths per segment: `cd child && node a.js` checks the script's `exec` permission
under the changed directory. Bare script names, common interpreter options and Node file preload options are supported.
Redirects use their segment's directory. On Windows Bash/Sh, `/e/code/x` maps to `E:/code/x`; `auto` reuses
the executor's shell selection predicates, so cmd switches `/b`, `/s` and `/d` are not drive roots.
Bash/Sh expands unquoted `~`; unresolved variables are not literal directories. Only a single valid `cd` operand
advances cwd. Conditional branches and pipelines retain possible directories and can be more conservative than
execution. Extraction is bounded to 128 cwd candidates and 1024 segment/cwd checks, shared across literal Bash/Sh
`-c` recursion (maximum depth 4); a child shell does not change its parent's cwd. Limits raise a tagged framework
error that `check_permission()` converts to DENY with rule `file_guard:analysis_limit`. Operands following unknown
options are conservatively checked as possible scripts. Dynamic inline code, module resolution and file access
inside scripts still require appropriate runtime protection.

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
| **`permissions_snapshot_required`** | Optional bool or callable, default False. When enabled, a missing/failed snapshot rejects the check instead of using stale static policy. |
| **`request_permission_confirmation`** | Async **ASK** confirmation hook: returns **`PermissionConfirmResponse`**, the literal **`"interrupt"`** (fall back to built-in Confirm), or **`None`** (failure). “Always allow” is **`approved: true` with `auto_confirm: true`** (merge `permissions`, update memory, then persist). Products may implement ACP; see **`RequestPermissionConfirmationHook`**. |
| **`persist_allow_rule`** | ``(permissions: dict, *, approval_grants: list[dict]) -> bool``: called once after the rail merges and `update_config`; persist only the supplied approval batch; return `False` to roll back memory. If unset, YAML write uses **`write_permissions_section_to_agent_config_yaml`**. |
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

Permission interrupt metadata includes `authorization_scopes`: `exact` plus the applicable `parent` (files), `domain` (URLs), or `subcommands` (reliably parsed shell calls, including single commands). When confirmation fields are omitted, the server selects the applicable broader scope, falling back to `exact`; client changes are not required. Explicit `exact`, or `authorization_mode: allow` alone, retains exact authorization. A scope-only reply infers the matching mode. Split shell approval creates `shell_subcommand` records, binding each literal child to the original invocation directory, tool and interpreter. Both children of `cd repo && git status` retain that initial directory. Approved children can be reused independently or in new combinations and cover Shell ASK on the combined text; DENY, structural checks and other guards remain effective.

When a remembered `subcommands` approval contains one actual command, the rail also includes an `exact_operation` record for that complete invocation. For example, remembering `cat ./secret.txt` preserves combination reuse while also satisfying FileGuard ASK on the identical command. The exact record remains bound to the command, invocation directory, tool and interpreter; it does not cover changed calls or newly composed commands. Multiple commands (including repeated identical children) do not receive this extra whole-command record. No broad session auto-confirm shortcut is written for `subcommands`; allow-once still saves no grant, and DENY still wins over either record type. Existing exact grants are not converted.

All persistence hooks receive `approval_grants: list[dict]` and are called once per remembered approval. This applies to exact/file/URL grants as well as shell batches; a single-object grant is a one-element list. Save only this delta, without promoting unrelated snapshot/session grants. There is no singular delta parameter or per-record callback fallback. Session approvals use `persist_session_allow_rule` with the same batch and the session ID; an exception or `False` return rolls back in-memory configuration. Hosts implementing these hooks must accept the list keyword.

Shell grant splitting validates source spans before slicing UTF-8 bytes. Invalid spans or decoding failures return no reusable children, so a non-ASCII command on the conservative parser fallback receives ordinary exact-command confirmation instead of crashing or producing partial grants.

---

## 6. Composed source snapshots

Hosts can supply a native `permissions.composition` snapshot instead of letting
the engine inject package rules. Core supports schema versions 1 and 2;
`COMPOSITION_SCHEMA_VERSION` remains `1` for older hosts, while
`SUPPORTED_COMPOSITION_SCHEMA_VERSIONS` declares `(1, 2)`.
The legacy `DEFAULT_SOURCE_ORDER` remains `user, cloud, builtin`.

Version 2 also accepts `user_group`, `cloud_group` and `builtin_group` layers.
`DEFAULT_LAYER_ORDER` pairs each owner with its group layer. The host selects
enabled layers and provides `source_order` in priority order. A group layer
requires its owner in that list. Core does not store or manage groups: the host
expands them into native paths, commands, tool values and URLs before ingestion.
Within each native policy module, any matching enabled deny wins; otherwise
the existing source priority and matching rules apply. Cross-module strict
merging and remembered approval behavior remain unchanged.

```yaml
composition:
  schema_version: 2
  revision: sec-example
  source_order: [user, user_group, cloud, cloud_group, builtin, builtin_group]
  workspace_layer: user_group
file_guard:
  enabled: true
  defaults: {read: ask, write: ask, exec: deny}
  workspace: {read: allow, write: allow}
```

`workspace_layer` is v2 host metadata and accepts `user` or `user_group`.
Its default is `user`; a workspace rule contributes only when that layer is
enabled. The actual path comes from the engine's `workspace_root`, so separate
engines can bind the same snapshot to different roots. A missing root contributes
no workspace rule. Version 2 uses only explicitly configured workspace axes
(read deny still denies all axes); the example does not grant exec. Version 1
keeps its historical implicit ask for omitted workspace axes. This metadata does
not itself connect a product session to its root or allow Shell deletion.

On a v2 rail refresh, `resolve_workspace_dir` is evaluated again and may return
`None` to revoke a previously bound root. `PermissionEngine.update_workspace_root`
is also available to hosts managing the engine directly. The host must return
its locked project root rather than the changing execution directory.

V2 resolves relative operands against Core's actual execution CWD and a Shell
call's explicit workdir. CWD supplies no workspace authorization. When the root
is unavailable, FileGuard still checks static paths and defaults using execution
CWD; it does not grant CWD as an implicit workspace. V1 retains its previous
extraction and missing-root behavior.

### 6.1 Finite workspace file deletion

V2 hosts may publish backend-derived `workspace_delete_defaults` and
`tool_layers` metadata in composition. This metadata records controlled product
ASK contributions; it is not an input to ordinary rule or group editing APIs.
An ordinary rule ID, including a copied product ID, establishes no such identity.
Hosts must revoke the identity on explicit edits, including same-value edits.
Unknown legacy ASK values remain ASK until explicit backend migration.

The rail verifies the actual manager-owned `BashTool`/`PowerShellTool` instance
and its native local `SysOperation` before supplying a `LocalDeleteExecution`
to the engine. A tool name or model-provided `shell_type` alone is insufficient.
Hosts that wrap tools or own a bound command adapter can provide
`ToolPermissionHost.resolve_shell_execution(ctx, tool_call, args)`. It returns
`LocalDeleteExecution` or None, synchronously or asynchronously. A configured
hook replaces native inference; failure, None or an untyped result never falls
back to a less-specific executor. Verification finishes before the final live
snapshot/root is bound, so an asynchronous verifier cannot reuse another
session's refresh. Swarm uses this for its fixed-shell wrappers and its owned
local `mcp_exec_command`; external MCP names establish no executor identity.
Currently the finite subset is `rm -- <literal files>` on native POSIX shells,
or `Remove-Item -LiteralPath <one literal file>` on Windows PowerShell. Remote
providers, MCP terminals, Windows POSIX path translation, recursive deletion,
globs, expressions, scripts, and compound commands retain ordinary checks.

Every target must be strictly below the bound workspace, with an active explicit
workspace write-allow grant. Existing directories, symlinks, NT reparse points,
provider/device paths, and uncertain boundaries do not qualify. The complete
recognized targets are also checked by normal FileGuard rules, including bare
filenames. Missing literal targets may be recognized; this is not an existence
condition in the policy language. Path observations are not an atomic sandbox.

Only controlled default ASK contributions are removed from the per-operation
policy input. Normal source ordering, explicit ASK/DENY, immutable control roots,
network/host evaluation and deletion thresholds still apply. Batch counting uses
the verified execution cwd. The rail also binds this cwd into the actual finite
ToolCall's workdir, so a parallel cwd change cannot move the checked operation.
This is neither a global Shell allow nor a remembered
approval, and it never rewrites a final ASK to ALLOW. Disabling the workspace or
its source removes this contribution on refresh. V1 has no such exception.

## 7. Source index

| Path | Notes |
|------|--------|
| `openjiuwen/harness/security/core.py` | `PermissionEngine` |
| `openjiuwen/harness/security/host.py` | `ToolPermissionHost` |
| `openjiuwen/harness/security/factory.py` | `build_permission_interrupt_rail` |
| `openjiuwen/harness/rails/security/tool_security_rail.py` | `PermissionInterruptRail` |
| `openjiuwen/harness/security/patterns.py` | Matching + YAML **`persist_*`** |
| `openjiuwen/harness/deep_agent.py` | Queues permission rail when enabled |
| `examples/permissions/permission_demo.py` | Demo script |
