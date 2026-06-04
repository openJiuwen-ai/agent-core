# ToolRejectExample Rail

Demonstrates tool reject behavior using _skip_tool for both BEFORE and AFTER events.

## Reject Behavior

| Event | When Detected | Reject Action | Agent Behavior |
|-------|--------------|---------------|----------------|
| **BEFORE_TOOL_CALL** | Secret in `tool_args` | `skip_tool` | Agent **continues** (tries other approach) |
| **AFTER_TOOL_CALL** | Secret in `tool_result` | `skip_tool` | Agent **continues** (tries other approach) |

Both BEFORE and AFTER tool reject use `_skip_tool`, returning "blocked for security reason" as the tool result. The agent sees this message and can try an alternative approach.

## Flow Diagrams

### BEFORE Reject Flow

```
┌─────────────────────────────────────────────────────────────────┐
│  User Request                                                   │
│  "glob *sk-secret*"                                             │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  LLM Generates ToolCall                                         │
│  tool_args = "*sk-secret*"                                      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  BEFORE_TOOL_CALL Rail                                          │
│  ✓ Secret detected in args                                      │
│  → Reject                                                        │
│  → _skip_tool (base behavior)                                   │
│  → ToolMessage = "blocked for security reason"                  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  Agent Continues                                                │
│  LLM sees ToolMessage                                           │
│  → "That didn't work, let me ask user for safe filename"       │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  Conversation Continues                                         │
│  User can provide alternative                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

### AFTER Reject Flow

```
┌─────────────────────────────────────────────────────────────────┐
│  User Request                                                   │
│  "Read .env file"                                               │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  Tool Executes (BEFORE passed)                                  │
│  read_file(".env")                                              │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  Tool Result                                                    │
│  "API_KEY=sk-abc123..."                                         │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  AFTER_TOOL_CALL Rail                                           │
│  ✓ Secret detected in result                                    │
│  → Reject                                                        │
│  → _skip_tool (base behavior)                                   │
│  → ToolMessage = "blocked for security reason"                  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  Agent Continues                                                │
│  LLM sees ToolMessage                                           │
│  → "The result was blocked, let me try a different approach"    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Configuration

### Tool Whitelist

Only monitors specific tools:

```python
TOOL_WHITELIST = [
    "read_file",    # Can read files containing secrets
    "bash",         # Can execute commands revealing secrets
    "grep",         # Can search for secrets
    "glob",         # Can find files by secret patterns
    "write_file",   # Can write secrets to files
]
```

### Sensitive Patterns

```python
SENSITIVE_PATTERNS = [
    r"(?:api_key|API_KEY|apikey|APIKEY|secret|SECRET|token|TOKEN|credential|CREDENTIAL)\s*[=:]\s*[\"']?\S+[\"']?",
    r"sk-[a-zA-Z0-9_-]{20,}",  # OpenAI-style keys (supports underscore/hyphen)
    r"Bearer\s+[a-zA-Z0-9\-_]+",
    r"AKIA[0-9A-Z]{16}",        # AWS keys
]
```

---

## Difference from ApiKeyGuardInterrupt

| Feature | ApiKeyGuardInterrupt | ToolRejectExample |
|---------|---------------------|-------------------|
| Interrupt | ✓ HITL approval | ✗ Direct reject |
| User choice | Approve/Reject | No choice |
| BEFORE reject | User rejects → skip | Auto reject → skip |
| AFTER reject | User rejects → skip | Auto reject → skip |

**Use ApiKeyGuardInterrupt** when you want user approval before blocking.
**Use ToolRejectExample** when you want automatic blocking without user interaction.

---

## Installation

Copy this folder to your extensions directory:

```bash
cp -r examples/security_rail_demo/ToolRejectExample ~/guardrail/extensions/
```

---

## Testing

Test scenarios:

1. **BEFORE reject test:**
   - User: "glob *sk-secret123*"
   - Expected: Tool skipped, agent continues

2. **AFTER reject test:**
   - User: "read .env file containing API_KEY=sk-xxx"
   - Expected: Tool result replaced with "blocked for security reason", agent continues

---

## Code Reference

The reject behavior is implemented in `BaseSecurityRail._apply_reject()`:

```python
# Both BEFORE_TOOL_CALL and AFTER_TOOL_CALL reject
if event in (AgentCallbackEvent.BEFORE_TOOL_CALL, AgentCallbackEvent.AFTER_TOOL_CALL):
    self._skip_tool(ctx, tool_call, ...)
    # Agent continues
```