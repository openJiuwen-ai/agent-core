---
description: Credentials, sandbox operations, path validation, and shell execution security rules.
language: chinese
paths:
  - "openjiuwen/core/security/**"
  - "openjiuwen/core/sys_operation/**"
  - "openjiuwen/extensions/**"
alwaysApply: false
---

# Security Rules

## Credential Handling

- Never hard-code secrets, API keys, tokens, or real endpoints in source files.
- All credentials must come from environment variables or config loaded at runtime.
- Use `os.getenv("KEY", default)` with a safe default for tests.
- In tests, use `os.getenv(..., "mock-api-key")` — never real credentials.

## .env Files

- `.env` and `.env.*` files must not be committed.
- These are already gitignored; do not override that.
- `Read(./.env)` and `Read(./.env.*)` are denied by permission rules in `settings.json`.

## Sandbox Operations

- `core/sys_operation/` handles shell and filesystem operations.
- Sandboxed operations (`sandbox/`) enforce path scoping and guardrails.
- Never bypass path validation or approval flows in sandbox code.
- Preserve interrupt/confirm semantics for user-facing operations.

## File Path Validation

- Always validate user-supplied file paths before operations.
- Use the `safe_path` utilities in `openjiuwen.core.common.security`.
- Reject paths containing `..` or absolute paths outside the allowed scope.

## Shell Execution

- Never construct shell commands from unsanitized user input.
- Prefer parameterized APIs over string concatenation for command building.
- Review `openjiuwen/core/sys_operation/local/` before modifying shell op code.

## Third-Party Dependencies

- Do not add dependencies without reviewing `pyproject.toml` and understanding the
  security implications.
- New network-facing dependencies require review.

## Security-Sensitive Areas

- `openjiuwen/core/security/` — guardrails, access control
- `openjiuwen/core/sys_operation/` — shell, filesystem, sandbox
- `openjiuwen/extensions/sys_operation/sandbox/` — isolation and provider code

Changes to these areas require extra review and testing.
