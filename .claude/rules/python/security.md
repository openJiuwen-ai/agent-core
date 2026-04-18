---
description: Bandit scanning, dependency review, and secret management for agent-core Python code.
language: chinese
paths:
  - "openjiuwen/core/security/**"
  - "openjiuwen/core/sys_operation/**"
  - "openjiuwen/extensions/**"
alwaysApply: false
---

# Python Security (Extended)

Extends `rules/security.md` with Python-specific tooling (bandit, pip-audit).
See `skills/security-review` for the full checklist.

## Bandit Security Scanning

Run `bandit` for static security analysis on all Python source:

```bash
bandit -r openjiuwen/ -f screen
```

Integrate into CI (`Makefile` or CI pipeline) — fail on HIGH or CRITICAL
severity findings:

```bash
bandit -r openjiuwen/ -ll  # -ll = only HIGH/CRITICAL
```

Bandit covers: hardcoded passwords, risky imports (`pickle`, `eval`,
`subprocess` with shell=True), SQL injection patterns, unsafe YAML load,
and more.

## Secret Management

Use `python-dotenv` for local development secrets. All secrets must
be loaded from environment variables at runtime:

```python
import os
from dotenv import load_dotenv

load_dotenv()  # Load .env for local dev only

api_key: str = os.getenv("OPENAI_API_KEY")  # Must be set in production
```

**Never** commit `.env` files. They are already in `.gitignore` — do not
override that. The deny rules in `settings.json` prevent the agent from
reading `Read(./.env)` or `Read(./.env.*)`.

For test credentials, always use safe mock defaults:

```python
api_key: str = os.getenv("OPENAI_API_KEY", "mock-api-key-for-tests")
```

## Dependency Security

Before adding a new dependency, especially one with network access:

1. Review the package's own dependencies (check PyPI page, GitHub security tab)
2. Run `pip-audit` on the new dependency:

```bash
pip install pip-audit
pip-audit -r requirements.txt
```

3. Check for known CVEs against `pyproject.toml` transitive deps:

```bash
pip-audit
```

New network-facing dependencies require an additional security review.
Document the review decision in the PR.

## Sandbox-Sensitive Code

The following areas are security-sensitive — changes require extra review:

- `openjiuwen/core/security/` — guardrails, access control
- `openjiuwen/core/sys_operation/` — shell, filesystem, sandbox
- `openjiuwen/extensions/sys_operation/sandbox/` — isolation and provider code

In these areas, prefer allowlist over denylist for permissions. Never
construct shell commands from unsanitized user input. Use the `safe_path`
utilities in `openjiuwen.core.common.security` for all file path validation.
