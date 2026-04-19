---
description: Python code style, formatting, naming, imports, and async safety rules for agent-core.
language: chinese
paths:
  - "openjiuwen/**/*.py"
alwaysApply: false
---

# Code Style Rules

## Language and Formatting

- Python 3.11+ required.
- Ruff line length: 120 characters.
- Ruff is the primary formatter/linter. Run `make fix` for auto-fix.
- Match surrounding module style before introducing new patterns.
- Add type hints for new public APIs; keep docstrings aligned with the surrounding module.

## Async Safety

- Keep library code async-safe. Avoid blocking calls in async paths unless the module already does so deliberately.
- For async file I/O, prefer `aiofiles` or `asyncio.to_thread()` over synchronous `open()`.

## Logging

- Do not use `print()` in library code; use project logging (`from openjiuwen.core.common.logging import get_logger`).
- Prefer `logger.opt(...)` over `logger.debug(f"...")` for structured logging.

## Naming Conventions

- Follow PEP 8 with Ruff defaults.
- Type aliases and schemas go in `schema/` or `types/` subdirectories.
- Card types (identity/metadata): `AgentCard`, `ToolCard`, `WorkflowCard`, `SysOperationCard`.
- Config/manager/runtime types: `<Feature>Config`, `<Feature>Manager`, `<Feature>Runner`.

## Imports

- Use absolute imports within the `openjiuwen` package.
- Do not use wildcard imports (`from module import *`) in library code.
- Group imports: stdlib, third-party, local/relative (Ruff handles this).

## File Organization

- One public class per module preferred; small related utilities may share a module.
- Private implementation details start with `_` or `__`.
- `__init__.py` exports the public surface only; keep it minimal.
