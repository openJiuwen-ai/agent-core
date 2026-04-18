---
name: security-review
description: 10-category security checklist for agent-core: secrets, input validation, sandbox, and prompt injection.
disable-model-invocation: true
---

# Security Review

Comprehensive security checklist for agent-core. Run through all 10
categories before any security-sensitive change or PR.

See `.claude/rules/python/security.md` for tool-specific guidance (bandit, pip-audit).
See `.claude/rules/security.md` for credential, sandbox, and shell execution rules.

## 1. Secrets Management

**Rule:** Credentials never enter source code.

- [ ] No API keys, tokens, or passwords hardcoded in `.py` files
- [ ] All secrets loaded from environment variables via `os.getenv()`
- [ ] `.env` files not committed (already in `.gitignore` — do not remove)
- [ ] `settings.json` deny rules block `Read(./.env)` and `Read(./**/secrets/**)`
- [ ] Test files use mock defaults: `os.getenv("KEY", "mock-key-for-tests")`

```python
# Bad
API_KEY = "sk-1234567890abcdef"

# Good
import os
API_KEY = os.getenv("OPENAI_API_KEY")  # Must be set in environment
```

## 2. Input Validation

**Rule:** Validate all external input before use.

- [ ] User-supplied file paths checked with `safe_path` utilities
- [ ] Paths rejected if they contain `..` or resolve outside allowed scope
- [ ] Shell commands constructed via parameterized APIs, not string concatenation
- [ ] URL parameters and query strings sanitized before use

For `sys_operation`:

```python
from openjiuwen.core.common.security import safe_path

def execute_command(user_path: str, working_dir: Path) -> None:
    validated = safe_path(user_path, allowed_base=working_dir)
    if validated is None:
        raise SecurityError(f"Path outside allowed scope: {user_path}")
    # Proceed with validated path
```

## 3. SQL Injection

**Rule:** Use parameterized queries for all database operations.

- [ ] No string interpolation in SQL: `f"SELECT * FROM {table}"` is forbidden
- [ ] All SQL uses parameterized placeholders: `"WHERE id = ?", (id,)`
- [ ] Table/column names validated against an allowlist if dynamic

```python
# Bad
cursor.execute(f"SELECT * FROM {table_name} WHERE id = {user_id}")

# Good
cursor.execute(
    "SELECT * FROM sessions WHERE id = ?",
    (session_id,)
)
```

## 4. Authentication and RBAC

**Rule:** Access control must be enforced server-side, not just client-side.

- [ ] All agent capabilities gated behind permission checks in `core/security/`
- [ ] Guardrails in `openjiuwen/core/security/` verify permissions before execution
- [ ] No capability bypassed by missing checks on alternate code paths
- [ ] Resource limits enforced (rate limiting, concurrent request limits)

## 5. Prompt Injection

**Rule:** `openjiuwen/harness/prompts/` must guard against injected user content.

- [ ] User-provided strings never concatenated directly into system prompts without sanitization
- [ ] Prompt templates use placeholder isolation (separate user content from instruction)
- [ ] Rail outputs validated before being passed to downstream components
- [ ] The security rail (`openjiuwen/harness/rails/`) correctly blocks dangerous patterns

Prompt injection is agent-core's most unique security concern. Attackers may
try to inject instructions into conversation history to manipulate agent behavior:

```python
# Bad — user content injected into system prompt
system_prompt = f"You are a helpful assistant. User said: {user_message}"

# Good — user content kept in separate context slot
system_prompt = SYSTEM_INSTRUCTIONS
context = {
    "user_message": sanitize_for_display(user_message),
    "conversation_history": conversation,
}
```

## 6. CSRF / Request Validation

**Rule:** All mutating requests include validation.

- [ ] `core/session/` validates that requests originate from legitimate sessions
- [ ] Session IDs are non-guessable (use `secrets.token_urlsafe()`)
- [ ] Session state changes are idempotent or protected by transaction semantics
- [ ] No state-modifying operations accessible without session context

## 7. Rate Limiting

**Rule:** Protect `core/runner/` and `core/runner/` resources from exhaustion.

- [ ] `Runner.resource_mgr` enforces limits on concurrent agent executions
- [ ] Memory usage bounded for long-running sessions
- [ ] Compaction triggers prevent unbounded context growth
- [ ] Tool call frequency limits enforced per session

## 8. Sensitive Data in Logs

**Rule:** Logs must not expose credentials, tokens, or sensitive data.

- [ ] No API keys, tokens, or passwords in log output
- [ ] Use structured logging with explicit field names, not f-string interpolation
- [ ] Error messages do not include sensitive user data
- [ ] `openjiuwen.core.common.logging` used instead of `print()`

```python
# Bad
logger.info(f"Authenticated user {user_id} with token {token}")

# Good
logger.info("User authenticated", extra={"user_id": user_id})
```

## 9. Dependency Security

**Rule:** All dependencies scanned before merging PRs.

- [ ] New dependencies reviewed for known CVEs: `pip-audit`
- [ ] New network-facing dependencies reviewed for security implications
- [ ] `bandit -r openjiuwen/ -ll` passes (no HIGH/CRITICAL findings)
- [ ] Third-party code in `core/sys_operation/` and `core/security/` minimized

```bash
# Run before merging dependency changes
pip-audit
bandit -r openjiuwen/ -ll
```

## 10. Sandbox Isolation

**Rule:** `core/sys_operation/sandbox/` must provide genuine isolation.

- [ ] File operations respect path scoping (no escape via `../`)
- [ ] Shell execution runs in a restricted environment
- [ ] Network access is explicitly allowed/denied, not default-open
- [ ] Cleanup runs after every operation, even on failure
- [ ] Interrupt/confirm flows preserved for user-facing operations

For sandbox implementations, verify:

```python
# Path isolation
def sandbox_read(path: Path, allowed_base: Path) -> str:
    resolved = (allowed_base / path).resolve()
    if not resolved.is_relative_to(allowed_base):
        raise SecurityError(f"Escape attempt: {path}")
    return resolved.read_text()
```

## Pre-Review Checklist

Before marking a security-sensitive PR as ready for review, run through
all 10 categories above. Document the review in the PR description:

```
Security Review
===============
Secrets:        PASS (no hardcoded credentials)
Input Val:      PASS (safe_path used for all user paths)
SQL Injection:  PASS (parameterized queries only)
Auth/RBAC:      PASS (guardrails enforce permissions)
Prompt Inject:  PASS (user content isolated from system prompts)
CSRF:           PASS (session IDs are non-guessable)
Rate Limiting:  PASS (resource_mgr enforces limits)
Log Safety:     PASS (no credentials in structured logs)
Dependencies:   PASS (bandit + pip-audit clean)
Sandbox:        PASS (path scoping verified)
```

For changes to `core/security/`, `core/sys_operation/`, or
`openjiuwen/extensions/sys_operation/sandbox/`, request a dedicated
security review from a second reviewer.
