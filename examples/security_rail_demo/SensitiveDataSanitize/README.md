# SensitiveDataSanitize Rail

Sanitizes sensitive data (API keys, secrets, tokens) in conversation history without blocking execution.

## Behavior

| Event | Check | Action |
|-------|-------|--------|
| BEFORE_MODEL_CALL | Scan history | Replace secrets with `[REDACTED]` |
| AFTER_MODEL_CALL | Scan response + history | Replace secrets with `[REDACTED]` |

**Key difference from ModelCallGuard:**

| Rail | Pop | Sanitize | Reject |
|------|-----|----------|--------|
| ModelCallGuard | ✓ | - | ✓ |
| SensitiveDataSanitize | - | ✓ | - |

ModelCallGuard **removes** messages with secrets and **rejects** execution.
SensitiveDataSanitize **replaces** secrets with `[REDACTED]` and **allows** execution to continue.

## Use Cases

1. **Logging/Audit**: Redact secrets before logging conversation history
2. **Partial Protection**: Mask secrets but allow user to see structure
3. **Pipeline Processing**: Use before reject rail for visibility
4. **Debug Mode**: Allow execution to continue while masking secrets

## Configuration

```python
# Default replacement string
rail = SensitivedatasanitizeRail(replacement="[REDACTED]")

# Custom replacement
rail = SensitivedatasanitizeRail(replacement="<SECRET>")
```

## Pattern Configuration

Current patterns:

```python
SENSITIVE_PATTERNS = [
    r"(?:api_key|API_KEY|apikey|APIKEY|secret|SECRET|token|TOKEN|credential|CREDENTIAL)\s*[=:]\s*[\"']?\S+[\"']?",
    r"sk-[a-zA-Z0-9]{20,}",
    r"Bearer\s+[a-zA-Z0-9\-_]+",
    r"AKIA[0-9A-Z]{16}",
]
```

## Combination with Other Rails

SensitiveDataSanitize can be combined with reject rails:

```json
{
  "rails": [
    {"SensitiveDataSanitize": {"priority": 85}},
    {"ModelCallGuard": {"priority": 90}}
  ]
}
```

Execution order:
1. SensitiveDataSanitize (priority 85) - Replace secrets with `[REDACTED]`
2. ModelCallGuard (priority 90) - Check if `[REDACTED]` present, pop and reject

**Warning:** If you want ModelCallGuard to reject after sanitization, you need to add a pattern for `[REDACTED]` or check sanitized state.

## Helper Method

Uses `BaseSecurityRail._sanitize_matching_messages()`:

```python
sanitized = self._sanitize_matching_messages(
    ctx,
    patterns,
    replacement="[REDACTED]",
    with_history=True,
)
```

## Installation

Copy this folder to your extensions directory:

```bash
cp -r examples/security_rail_demo/SensitiveDataSanitize ~/guardrail/extensions/
```