# ApiKeyGuardAlert

API Key Guard Rail - **ALERT mode**.

Allows execution but alerts user when API key detected. Demonstrates three display_mode options.

## Purpose

Unlike `ApiKeyGuardInterrupt` which blocks execution for user approval, `ApiKeyGuardAlert`:

1. **Allows execution to continue** (tool result passes to model)
2. **Alerts user** via frontend stream
3. **Logs warning** for observability

This is useful for:
- Monitoring without blocking workflow
- Low-risk detections (e.g., public test keys)
- Gradual rollout of security policies

## Display Modes

The `display_mode` parameter hints frontend on presentation style (stored in metadata).

**Note:** Backend uses `type="message"` for frontend compatibility (JiuClawStreamEventRail).
Frontend can identify security alerts via `payload.metadata.is_security_alert=True`.

| Mode | Frontend Behavior | Metadata |
|------|-------------------|----------|
| `popup` | Toast/popup notification | `display_mode: "popup"` |
| `history` | Insert into chat history | `display_mode: "history"` |
| `inline` | Stream output in real-time | `display_mode: "inline"` |

## Configuration

```python
from examples.security_rail_demo.ApiKeyGuardAlert.rail import ApikeyguardalertRail
from openjiuwen.harness.rails.security import SecurityAlertLevel

# Default: popup notification, WARNING level
rail = ApikeyguardalertRail()

# Custom display mode
rail_popup = ApikeyguardalertRail(display_mode="popup")
rail_history = ApikeyguardalertRail(display_mode="history")
rail_inline = ApikeyguardalertRail(display_mode="inline")

# Custom alert level
rail_critical = ApikeyguardalertRail(
    display_mode="popup",
    alert_level=SecurityAlertLevel.CRITICAL,
)
```

## Detected Patterns

Same patterns as `ApiKeyGuardInterrupt`:

- OpenAI-style keys: `sk-xxx` (20+ chars, supports underscore/hyphen)
- Generic secrets: `api_key=xxx`, `SECRET=xxx`, `token=xxx`
- AWS keys: `AKIA...` (16 chars)
- Bearer tokens: `Bearer xxx`

## Example Output

When `sk-abc123...` detected in tool result:

**Backend stream:**
```python
OutputSchema(
    type="message",  # Frontend compatibility
    index=0,
    payload={
        "role": "system",
        "content": "[WARNING] API key/secret detected in read_file result. Execution allowed but flagged.",
        "metadata": {
            "is_security_alert": True,
            "level": "warning",
            "alert_type": "api_key_leakage",
            "display_mode": "popup",
            "rail": "ApikeyguardalertRail",
        },
    },
)
```

**Frontend receives:**
- System message in chat history (using `role="system"`)
- Content prefixed with level: `[WARNING] message...`
- `metadata.is_security_alert` allows frontend to add special styling (red border, warning icon)

## vs Other Rails

| Rail | Behavior | Execution |
|------|----------|-----------|
| `ModelCallGuard` | Pop secret + force_finish | **Blocks** |
| `ApiKeyGuardInterrupt` | Interrupt for approval | **Blocks until approved** |
| `ApiKeyGuardAlert` | Alert + continue | **Allows** |
| `SensitiveDataSanitize` | Redact + continue | **Allows** |
| `ToolRejectExample` | Reject | **Blocks** |

## Copy to Extensions

```bash
cp -r examples/security_rail_demo/ApiKeyGuardAlert ~/guardrail/extensions/
```

Then register in `jiuwenclaw` config.