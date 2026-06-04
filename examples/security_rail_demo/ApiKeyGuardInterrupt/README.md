# ApiKeyGuardInterrupt Rail

Requires human approval when API key/secret detected (HITL).

## Detection Type

This rail uses **Detection Type** to distinguish different categories of sensitive data.

Auto-confirm key format: `apikeyguardinterrupt:{detection_type}:{event}`

### Supported Detection Types

| Detection Type | Pattern | Description |
|---------------|---------|-------------|
| `api_key_openai` | `sk-[a-zA-Z0-9_-]{20,}` | OpenAI API key |
| `api_key_aws` | `AKIA[0-9A-Z]{16}` | AWS access key ID |
| `bearer_token` | `Bearer\s+[a-zA-Z0-9\-_]+` | Bearer token |
| `secret_generic` | `(?:api_key|secret|token)\s*[=:]\s*\S+` | Generic secret/credential |

### Auto-confirm Behavior

When user selects "Always Allow":
- Key stored: `apikeyguardinterrupt:{detection_type}:{event}`
- Only same detection_type auto-approved
- Different detection_type requires new approval

Example:
- User approves `api_key_openai` -> subsequent OpenAI key detections auto-approved
- User encounters `api_key_aws` -> still requires approval (different type)

### Multi-Type Detection (Security Guarantee)

**Content may contain multiple detection types simultaneously.**

This rail detects **ALL matching types**, not just the first one:

```
Content: "sk-openai123... and AKIAAWSKEY123..."
Detected: ["api_key_openai", "api_key_aws"]
```

**Handling flow:**
1. Detect all types -> `["api_key_openai", "api_key_aws"]`
2. Check auto_confirm status for each
3. Unconfirmed types -> batch interrupt (show all)
4. User approves -> ALL unconfirmed types auto-confirm

**Example scenario:**

```
Turn 1: Content contains OpenAI Key + AWS Secret
  Rail: Interrupt "检测到 api_key_openai, api_key_aws 类敏感信息"
  User: Always Allow
  Result: Both types auto-confirmed

Turn 2: Content contains OpenAI Key + AWS Secret
  Rail: Auto-confirm (both types already approved)
  
Turn 3: Content contains OpenAI Key + Bearer Token (bearer not confirmed)
  Rail: Interrupt "检测到 bearer_token 类敏感信息"
  (OpenAI already confirmed, Bearer needs approval)
```

**Security guarantee:**
- No detection type is missed
- User explicitly approves ALL types
- Partial auto-confirm (some confirmed, some not) triggers interrupt for unconfirmed only

## Events

| Event | Check | Reject Behavior |
|-------|-------|-----------------|
| BEFORE_TOOL_CALL | `tool_args` (arguments) | Skip tool, agent continues |
| AFTER_TOOL_CALL | `tool_result` (result) | Skip tool, agent continues |

Both BEFORE and AFTER tool reject use `_skip_tool`, returning "blocked for security reason" as the tool result. The agent sees this message and can try an alternative approach.

## Auto-Confirm Keys

Format: `apikeyguardinterrupt:{detection_type}:{event}`

| Event | Key Example |
|-------|-------------|
| BEFORE | `apikeyguardinterrupt:api_key_openai:before` |
| AFTER | `apikeyguardinterrupt:api_key_aws:after` |

Detection type-based keys allow granular approval per secret category.

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
  Rail: Skip tool (agent continues)
  LLM: Try different approach
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

Detection rules with named types:

```python
DETECTION_RULES = {
    "api_key_openai": r"sk-[a-zA-Z0-9_-]{20,}",
    "api_key_aws": r"AKIA[0-9A-Z]{16}",
    "bearer_token": r"Bearer\s+[a-zA-Z0-9\-_]+",
    "secret_generic": r"(?:api_key|secret|token)\s*[=:]\s*\S+",
}
```

Each rule has a `detection_type` name used for auto-confirm key generation.

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