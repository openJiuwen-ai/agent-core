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
    self._pop_matching_messages_local(ctx, with_history=True)  # SAME patterns + SAME scope

# Use SAME scope for check and pop
messages = ctx.context.get_messages(with_history=True)  # check scope
self._pop_matching_messages_local(ctx, with_history=True)  # SAME scope
```

### Incorrect Implementation (Examples)

```python
# WRONG: Different scopes
messages = ctx.context.get_messages(with_history=True)  # Check all history
self._pop_matching_messages_local(ctx, with_history=False)  # Pop only current turn
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

This rail implements its own message filtering logic, then uses `_replace_messages` from `BaseSecurityRail` to apply changes:

| Method | Purpose |
|--------|---------|
| `_pop_matching_messages_local(ctx, with_history)` | Pop messages containing secrets, call `_replace_messages` with kept messages |
| `_extract_message_content(msg)` | Extract content from message types |
| `_contains_secret(text)` | Check text against compiled patterns |

`_replace_messages(ctx, messages, with_history)` from `BaseSecurityRail` handles the final message list replacement.

## Installation

Copy this folder to your extensions directory:

```bash
cp -r examples/security_rail_demo/ModelCallGuard ~/guardrail/extensions/
```

## Testing

```bash
uv run pytest tests/unit_tests/harness/rails/test_model_call_guard.py -v
```