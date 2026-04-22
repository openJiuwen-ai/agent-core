---
description: StatusCode / BaseError usage conventions for agent-core. Enforces the unified error code system.
language: english
paths:
  - "openjiuwen/**/*.py"
---

# Error Code Rules

All exceptions raised inside the framework **must** go through the unified
`openjiuwen.core.common.exception` system. See
`openjiuwen/core/common/exception/CLAUDE.md` for design details — this file
only lists enforceable hard rules.

## The Only Legal Way to Raise

Every exception must carry a `StatusCode`. Business code **must not** raise
bare `Exception / RuntimeError / ValueError / TypeError`.

```python
# Preferred: raise_error / build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error, build_error

raise_error(StatusCode.WORKFLOW_EXECUTION_ERROR, reason=str(e), workflow=wf_id)
err = build_error(StatusCode.TOOL_EXECUTION_ERROR, cause=e, reason=str(e))

# Direct raise is allowed only when the concrete class needs extra constructor args
# (currently only ToolError.card / RunnerTermination.reason)
raise ToolError(StatusCode.TOOL_EXECUTION_ERROR, card=tool_card, reason=str(e))

# Forbidden: bare raise, or BaseError without a StatusCode
raise RuntimeError("workflow broken")
raise BaseError("some message")
```

**Exceptions from external dependencies** must be caught at the module
boundary and re-raised as a `BaseError` subclass with the original exception
attached via `cause=`. Do not let third-party exception types propagate up
the call stack.

## StatusCode vs Exception Class Are Orthogonal

- **StatusCode** identifies the error (which module, which failure).
- **Exception class** encodes control-flow semantics (retry / abort / terminate).

`status_mapping.py` binds StatusCode to exception class.
`raise_error` / `build_error` resolve the class automatically. **Do not guess
the mapping** — get the naming right and the classification follows.

## Adding a New StatusCode

New entries go into `openjiuwen/core/common/exception/codes.py` and must
satisfy all of the following:

1. **Code range** must fall in the numeric segment of the corresponding scope
   (see the range table in CLAUDE.md — kept in sync with
   `code_template._code_range_by_scope` and `status_mapping.RANGE_RULES`).
   Cross-segment values break the range-based fallback classification.
2. **Name** follows `{SCOPE}_{SUBJECT}_{FAILURE_TYPE}`:
   - `SCOPE` must be in `code_template.ALLOWED_SCOPES`
   - `FAILURE_TYPE` must be in `code_template.ALLOWED_FAILURE_TYPES`
     (`INVALID / NOT_FOUND / PARAM_ERROR / CONFIG_ERROR / INIT_FAILED /
     CALL_FAILED / EXECUTION_ERROR / RUNTIME_ERROR / TIMEOUT / INTERRUPTED`, etc.)
3. **Message template** uses `{name}` placeholders — never `{0}` or `%s`.
   TIMEOUT templates must include `{timeout}`; generic errors should carry
   `{reason}` or `{error_msg}`.
4. **Code values must be unique.** Python `Enum` silently aliases duplicates
   instead of raising — grep the numeric value before adding.
5. **Preserve the section comments.** The block-comment segmentation in
   `codes.py` is the file's only human index. Do not scramble it.

No manual mapping registration is needed —
`status_mapping.build_status_exception_map()` resolves based on name keywords
and code range at import time. If the default classification is wrong, prefer
**fixing the name** so it matches a keyword rule. Only fall back to adding
an entry in `_MANUAL_OVERRIDES_RAW` for genuine special cases.

## Don'ts

- **Don't branch on `e.status.code`** in business code. For control-flow
  decisions use `isinstance(e, ExecutionError)` or read `e.recoverable /
  e.fatal`.
- **Don't stuff runtime data into `StatusCode`.** The enum is an immutable
  constant. Dynamic fields go through the `details=` argument or
  `**kwargs → params`.
- **Don't raise new exceptions on the error path.** `_render_message` and
  `_format_template` are lazy-safe by design — missing keys render as
  `<missing:key>` rather than raising `KeyError`. Any extension to rendering
  must preserve this invariant.
- **Don't swallow exceptions with `except BaseError: pass`.** Either let it
  propagate or convert it to an explicit StatusCode and re-raise.
- **Don't lift the lazy import in `status_mapping`** to module level — it
  immediately reintroduces the `errors ↔ status_mapping` circular import.
