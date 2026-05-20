# Security Rail Demo for jiuwenclaw

This example demonstrates how to create and register custom security rails in jiuwenclaw using the `BaseSecurityRail` framework from agent-core.

## Overview

`BaseSecurityRail` provides a structured pattern for implementing security checks that intercept agent operations:

- **SecurityAllow** - Allow the operation to continue
- **SecurityReject** - Block the operation and return an error message
- **SecurityInterrupt** - Pause for human approval (HITL)

## Quick Installation

Copy the extension folders to your jiuwenclaw extensions directory:

```bash
# Copy Reject and Interrupt examples
cp -r examples/security_rail_demo/ApiKeyGuardReject ~/guardrail/extensions/
cp -r examples/security_rail_demo/ApiKeyGuardInterrupt ~/guardrail/extensions/

# Copy config
cp examples/security_rail_demo/example_config.json ~/guardrail/extensions/extensions_config.json
```

Then restart jiuwenclaw. Enable one extension at a time in the config to test each mode.

## Extension Folders

| Folder | Mode | Class Name | Behavior |
|--------|------|------------|----------|
| `ApiKeyGuardReject` | REJECT | `ApikeyguardrejectRail` | Blocks execution completely |
| `ApiKeyGuardInterrupt` | INTERRUPT | `ApikeyguardinterruptRail` | Requires human approval |

## Testing Each Mode

### Step 1: Enable One Extension

Edit `~/guardrail/extensions/extensions_config.json`:

```json
{
  "ApiKeyGuardReject": {"enabled": true},   // Enable to test REJECT
  "ApiKeyGuardInterrupt": {"enabled": false}
}
```

### Step 2: Restart jiuwenclaw

The rail will be auto-loaded via `RailManager`.

### Step 3: Test with API Key Pattern

Create a file with API key content and read it:
```
API_KEY=sk-test1234567890abcdef
SECRET_TOKEN=mysecret123
```

### Expected Behavior

| Mode | What Happens |
|------|--------------|
| **REJECT** | Tool result replaced with error message: "API key/secret detected... Operation blocked" |
| **INTERRUPT** | Execution pauses, UI shows approval dialog, user must approve/reject |

## File Structure and Naming Convention

**CRITICAL**: jiuwenclaw's RailManager requires specific naming conventions:

### Directory Structure

```
~/guardrail/extensions/ApiKeyGuardReject/   # Extension directory (CapitalCamelCase)
├── rail.py                           # MANDATORY: must be named "rail.py"
└── (optional: __init__.py)          # For relative imports support
```

### Naming Rules

| Element | Rule | Example |
|---------|------|---------|
| **Directory name** | CapitalCamelCase | `ApiKeyGuardReject`, `MyCustom` |
| **File name** | Must be `rail.py` | `rail.py` (not `api_key_guard_rail.py`) |
| **Class name** | `{folder_first_cap}{folder_rest_lower}Rail` | `ApiKeyGuardReject` → `ApikeyguardrejectRail` |
| **Config key** | Must match folder name | `"ApiKeyGuardReject"` |

### Class Naming Pattern

```python
# Folder: ApiKeyGuardReject → Class: ApikeyguardrejectRail
# Folder: MyCustom → Class: MycustomRail
# Pattern: capitalize first letter, lowercase rest, add "Rail"
class ApikeyguardrejectRail(BaseSecurityRail):
    ...
```

### Required Import (for Validation)

**Must** include this import even if your class inherits from `BaseSecurityRail`:

```python
from openjiuwen.harness.rails.base import DeepAgentRail  # Required for RailManager validation
```

## Creating Custom Security Rails

### Template

```python
from __future__ import annotations

import re
from typing import Set

# Required import for validation (even if not used)
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
from openjiuwen.harness.rails.security.base_security_rail import (
    BaseSecurityRail,
    SecurityAllow,
    SecurityReject,
    SecurityInterrupt,
    SecurityCheckContext,
)

FILE_READING_TOOLS: Set[str] = {"read_file", "bash", "grep", "glob"}

# Class name: capitalize first letter, lowercase rest, add "Rail"
# Example: folder "MyCustom" → class "MycustomRail"
class MycustomRail(BaseSecurityRail):
    """Custom security rail."""
    
    priority = 85
    supported_events = {AgentCallbackEvent.AFTER_TOOL_CALL}
    
    async def run_security_check(self, security_ctx: SecurityCheckContext):
        ctx = security_ctx.callback_ctx
        inputs = ctx.inputs
        
        tool_result = getattr(inputs, "tool_result", None)
        if self._is_sensitive(tool_result):
            return self.reject(message="Sensitive data blocked")
        
        return self.allow()
    
    async def apply_security_decision(self, security_ctx, decision):
        if isinstance(decision, SecurityAllow):
            return
        
        if isinstance(decision, SecurityReject):
            ctx = security_ctx.callback_ctx
            inputs = ctx.inputs
            tool_call = getattr(inputs, "tool_call", None)
            
            inputs.tool_result = decision.message
            inputs.tool_msg = ToolMessage(
                content=decision.message,
                tool_call_id=tool_call.id if tool_call else "",
            )
            return
        
        if isinstance(decision, SecurityInterrupt):
            ctx = security_ctx.callback_ctx
            user_input = security_ctx.user_input
            
            if user_input is None:
                self._raise_tool_interrupt(
                    tool_name=ctx.inputs.tool_name,
                    tool_call=ctx.inputs.tool_call,
                    request=decision.request,
                )
            
            approved = user_input.get("approved", False) if isinstance(user_input, dict) else False
            if not approved:
                ctx.inputs.tool_result = "Rejected by user"
            return
        
        await super().apply_security_decision(security_ctx, decision)
    
    def _is_sensitive(self, result) -> bool:
        return False

__all__ = ["MycustomRail"]
```

### Supported Events

| Event | When it fires | Supports Interrupt? |
|-------|---------------|---------------------|
| `BEFORE_INVOKE` | Before agent invoke starts | Yes |
| `BEFORE_MODEL_CALL` | Before LLM call | **No** (auto-reject) |
| `AFTER_MODEL_CALL` | After LLM response | **No** (auto-reject) |
| `BEFORE_TOOL_CALL` | Before tool execution | Yes |
| `AFTER_TOOL_CALL` | After tool execution | Yes |
| `ON_MODEL_EXCEPTION` | When LLM call fails | **No** (auto-reject) |
| `ON_TOOL_EXCEPTION` | When tool execution fails | Yes |

### Priority Guide

| Priority | Typical Use |
|----------|-------------|
| 100+ | Highest priority, final checks |
| 85-95 | Security rails |
| 50-70 | Processing rails |
| 10-30 | Logging/telemetry rails |

## Testing Your Rail

### Quick Test

```bash
# In agent-core
PYTHONPATH=. uv run pytest \
  tests/unit_tests/harness/rails/test_base_security_rail.py \
  tests/system_tests/rail/test_base_security_rail_integration.py \
  -v
```

## Files in This Demo

| Folder/File | Description |
|-------------|-------------|
| `ApiKeyGuardReject/rail.py` | REJECT mode - blocks execution |
| `ApiKeyGuardInterrupt/rail.py` | INTERRUPT mode - HITL approval |
| `example_config.json` | Extension config for Reject + Interrupt |

## Related Documentation

- `openjiuwen/harness/rails/security/base_security_rail.py` - Core implementation
- `tests/system_tests/rail/test_base_security_rail_integration.py` - Integration test examples