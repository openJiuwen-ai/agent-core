# ModelCallGuard Rail

Blocks API keys/secrets in LLM input/output with automatic history cleanup.

## Behavior

| Event | Check | Action |
|-------|-------|--------|
| BEFORE_MODEL_CALL | Scan entire history | Pop messages with secrets + Reject |
| AFTER_MODEL_CALL | Check response + history | Pop messages with secrets + Reject |

## Critical: Check and Pop Must Match

**判断条件和 Pop 条件必须一致！**

If check and pop use different patterns or scopes, you'll have inconsistent behavior:

| Scenario | Problem |
|----------|---------|
| Check uses pattern A, Pop uses pattern B | Message detected but not popped |
| Check scans `with_history=True`, Pop uses `with_history=False` | Secret in history remains |
| Check pattern too broad (e.g. `\.env\b`) | Innocent messages incorrectly rejected |

### Correct Implementation

```python
# Use SAME patterns for check and pop
if self._contains_secret(content):  # uses self._compiled_patterns
    self._pop_matching_messages(ctx, self._compiled_patterns, ...)  # SAME patterns

# Use SAME scope for check and pop
messages = ctx.context.get_messages(with_history=True)  # check scope
self._pop_matching_messages(ctx, patterns, with_history=True)  # SAME scope
```

### Incorrect Implementation (Examples)

```python
# WRONG: Different patterns
patterns_check = [r"sk-\w+", r"API_KEY=\S+"]
patterns_pop = [r"sk-\w+"]  # Missing API_KEY pattern!
# Result: API_KEY detected but not popped, remains in history

# WRONG: Different scopes
messages = ctx.context.get_messages(with_history=True)  # Check all history
self._pop_matching_messages(ctx, patterns, with_history=False)  # Pop only current turn
# Result: Secret in old history remains, triggers again next turn

# WRONG: Overly broad pattern
patterns = [r"\.env\b"]  # Matches filename, not secret
# Result: "read .env file" incorrectly rejected
```

## Pattern Configuration

Current patterns in `rail.py`:

```python
API_KEY_PATTERNS = [
    r"(?:api_key|API_KEY|apikey|APIKEY|secret|SECRET|token|TOKEN|credential|CREDENTIAL)\s*[=:]\s*[\"']?\S+[\"']?",
    r"sk-[a-zA-Z0-9]{20,}",
    r"Bearer\s+[a-zA-Z0-9\-_]+",
    r"AKIA[0-9A-Z]{16}",
]
```

**Warning:** Avoid patterns that match non-secrets (e.g., filenames, common words).

## Conversation Flow After Rejection

| Turn | Event | History | Result |
|------|-------|---------|--------|
| 1a | BEFORE iter 1 | [User("read config")] | Allow → Tool executes |
| 1b | Tool adds result | [User, ToolMessage(secret)] | - |
| 1c | BEFORE iter 2 | [User, ToolMessage(secret)] | Secret found → Pop ToolMessage → Reject |
| 2 | BEFORE | [User("clean")] | Allow → Normal response |

## Helper Methods

`BaseSecurityRail` provides helpers that ensure consistency:

### Pop/Sanitize Helpers

| Helper | Purpose | Returns |
|--------|---------|---------|
| `_pop_matching_messages(ctx, patterns, with_history)` | Remove messages with secrets | List of popped messages |
| `_sanitize_matching_messages(ctx, patterns, replacement, with_history)` | Replace secrets with `[REDACTED]` | List of sanitized messages |
| `_extract_message_content(msg)` | Extract content from message types | String content |
| `_contains_any_pattern(text, patterns)` | Check text against patterns | Boolean |

### Interrupt Helpers

| Helper | Purpose | Returns |
|--------|---------|---------|
| `_handle_interrupt_resume(security_ctx, auto_confirm_key)` | Handle interrupt resume flow | `SecurityAllow`, `SecurityReject`, or `None` |
| `_is_auto_confirmed(config, key)` | Check if auto-approved | Boolean |
| `_store_auto_confirm(ctx, key)` | Store auto-approval for future | None |

### Interrupt Flow

```python
# Typical interrupt flow in run_security_check
auto_confirm_key = f"rail_name:{tool_name}"

# Handle resume (auto-confirm + user_input)
resume_decision = self._handle_interrupt_resume(security_ctx, auto_confirm_key)
if resume_decision is not None:
    return resume_decision  # Allow or Reject

# First call - check and interrupt if needed
if self._contains_secret(content):
    return self.interrupt(
        InterruptRequest(
            message="Approve execution?",
            payload_schema={...},
            auto_confirm_key=auto_confirm_key,
        ),
        subject_id=tool_call_id,
    )
```

### Single Event Mode

Rails can be configured to handle only one event:

```python
# Only check input (before model call)
rail.supported_events = {AgentCallbackEvent.BEFORE_MODEL_CALL}

# Only check output (after model call)
rail.supported_events = {AgentCallbackEvent.AFTER_MODEL_CALL}
```

## Installation

Copy this folder to your extensions directory:

```bash
cp -r examples/security_rail_demo/ModelCallGuard ~/guardrail/extensions/
```

## Testing

```bash
uv run pytest tests/unit_tests/harness/rails/test_model_call_guard.py -v
```