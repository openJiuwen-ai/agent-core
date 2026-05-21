# ApiKeyGuardInterrupt Rail

Requires human approval when API key/secret detected (HITL).

## Events

| Event | Check | Reject Behavior |
|-------|-------|-----------------|
| BEFORE_TOOL_CALL | `tool_args` (arguments) | Skip tool, agent continues |
| AFTER_TOOL_CALL | `tool_result` (result) | Force finish agent |

## Reject Behavior Difference

| Event | Why Different Behavior |
|-------|------------------------|
| BEFORE reject | Secret in arguments - tool not executed yet, agent can try other approach |
| AFTER reject | Secret in result - data already leaked, must terminate agent |

## Auto-Confirm Keys

Format: `api_key_guard:{tool_name}:{event}`

| Event | Key Example |
|-------|-------------|
| BEFORE | `api_key_guard:read_file:before` |
| AFTER | `api_key_guard:read_file:after` |

Separate keys allow user to approve different events independently.

## Flow Examples

### BEFORE Interrupt (arguments contain secret)

```
Turn 1:
  User: "read file with path containing secret"
  LLM: ToolCall(args="path=sk-secret123...")
  Rail: Interrupt "Secret in arguments. Approve?"
  User: Reject
  Rail: Skip tool (agent continues)
  LLM: Try different approach (e.g., ask user for safe path)
```

### AFTER Interrupt (result contains secret)

```
Turn 1:
  User: "read .env file"
  Tool: Returns "API_KEY=sk-abc123..."
  Rail: Interrupt "Secret in result. Approve?"
  User: Reject
  Rail: Force finish (agent terminates)
  Result: Error "Rejected by user"
```

## Tool Filtering

Only monitors FILE_READING_TOOLS:

```python
FILE_READING_TOOLS = {
    "read_file",
    "bash",
    "grep",
    "glob",
}
```

## Pattern Configuration

```python
API_KEY_PATTERNS = [
    r"(?:api_key|API_KEY|apikey|APIKEY|secret|SECRET|token|TOKEN|credential|CREDENTIAL)\s*[=:]\s*[\"']?\S+[\"']?",
    r"sk-[a-zA-Z0-9]{20,}",
    r"Bearer\s+[a-zA-Z0-9\-_]+",
    r"AKIA[0-9A-Z]{16}",
]
```

**Warning:** Previously had `r"\.env\b"` pattern which was removed (over-broad, matches filenames).

## Installation

Copy this folder to your extensions directory:

```bash
cp -r examples/security_rail_demo/ApiKeyGuardInterrupt ~/guardrail/extensions/
```

## Testing

```bash
uv run pytest tests/unit_tests/harness/rails/test_model_call_guard.py::TestToolInterruptReject -v
uv run pytest tests/unit_tests/harness/rails/test_model_call_guard.py::TestApiKeyGuardInterruptAutoConfirm -v
```