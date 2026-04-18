---
name: python-patterns
description: Python idioms, immutability, async patterns, and anti-patterns for agent-core.
---

# Python Patterns

Reference guide for idiomatic Python in agent-core. Covers patterns that
appear repeatedly in the codebase and establishes conventions for new code.

## Immutability

Prefer immutable data structures. Mutable state is a source of bugs in
concurrent and async code.

### Frozen Dataclasses

Use `@dataclass(frozen=True)` for data-only objects (cards, configs, events):

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class ToolCard:
    name: str
    description: str
    parameters: dict[str, str]
    version: str = "1.0"
    tags: tuple[str, ...] = field(default_factory=tuple)
```

Never mutate a frozen dataclass after construction. If you need to create
a modified copy, use `dataclasses.replace()`:

```python
from dataclasses import replace

original = ToolCard(name="ls", description="list files", parameters={})
updated = replace(original, parameters={"path": "str"})
```

### NamedTuple

Use `NamedTuple` for simple fixed-length records:

```python
from typing import NamedTuple

class Point(NamedTuple):
    x: int
    y: int

class Event(NamedTuple):
    type_: str
    timestamp: float
    data: dict
```

### typing.Final

Mark values that should never be reassigned:

```python
from typing import Final

MAX_RETRIES: Final[int] = 3
DEFAULT_TIMEOUT: Final[float] = 30.0
```

## Protocol-Based Duck Typing

Use `typing.Protocol` to define structural interfaces without inheritance:

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class ResourceAllocator(Protocol):
    def allocate(self, name: str) -> str: ...
    def release(self, name: str) -> None: ...

@runtime_checkable
class Runner(Protocol):
    async def run(self, task: str) -> dict: ...

# Usage — any class with the right methods satisfies the Protocol
def execute_with_runner(runner: Runner) -> None:
    ...
```

`Protocol` is especially useful for `AbilityManager`, `Rail` base classes,
and any plugin/extension interface in `openjiuwen/core/` and `openjiuwen/harness/`.

## Custom Exception Hierarchies

Define a project-wide exception hierarchy in `openjiuwen/core/exceptions.py`:

```python
class AgentCoreError(Exception):
    """Base exception for all agent-core errors."""
    pass

class ConfigurationError(AgentCoreError):
    """Raised when configuration is invalid or missing."""
    pass

class SecurityError(AgentCoreError):
    """Raised when a security constraint is violated."""
    pass

class ResourceError(AgentCoreError):
    """Raised when a resource operation fails."""
    pass

class ToolExecutionError(AgentCoreError):
    """Raised when a tool fails to execute."""
    pass
```

Always catch the most specific exception possible. Never use bare `except:`.

## Context Managers

Use context managers for resource acquisition and release.

### Class-Based (for complex cleanup)

```python
class SandboxContext:
    def __init__(self, scope: Path) -> None:
        self.scope = scope
        self._handle: Any = None

    def __enter__(self) -> "SandboxContext":
        self._handle = self._acquire(self.scope)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        self._release(self._handle)
        self._handle = None
```

### Function-Based (for simple cases)

```python
from contextlib import contextmanager

@contextmanager
def temporary_workspace(base: Path) -> Generator[Path, None, None]:
    workspace = base / "tmp"
    workspace.mkdir(exist_ok=True)
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace)
```

## Async Patterns

agent-core is async-heavy. Follow these patterns consistently.

### Running Coroutines Concurrently

```python
import asyncio

# Launch all tasks, gather results (raises if any fails)
results: list[str] = await asyncio.gather(
    run_agent(task) for task in tasks
)

# Gather with return_exceptions (collects all, doesn't raise)
results: list[str | Exception] = await asyncio.gather(
    run_agent(task) for task in tasks,
    return_exceptions=True
)
```

### Create Tasks (fire and forget)

```python
import asyncio

async def process_background(item: str) -> None:
    ...

# Fire and forget — runs concurrently, not awaited
task = asyncio.create_task(process_background(item))

# To await later
await task
```

### Timeout

```python
import asyncio

async def bounded_call(coro: Coroutine, seconds: float) -> Any:
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except asyncio.TimeoutError:
        logger.warning(f"Operation timed out after {seconds}s")
        raise
```

### Async Generators

```python
async def stream_events() -> AsyncGenerator[Event, None]:
    """Yield events as they arrive."""
    while True:
        event = await get_next_event()
        if event is None:
            break
        yield event

async def main():
    async for event in stream_events():
        print(event)
```

## Decorators

Use decorators to add cross-cutting behavior (logging, timing, retry).

### Function Decorators

```python
import functools

def log_calls(logger: Logger):
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger.debug(f"Calling {func.__name__}")
            return func(*args, **kwargs)
        return wrapper  # type: ignore
    return decorator

@log_calls(get_logger(__name__))
async def call_llm(prompt: str) -> str:
    ...
```

### Parameterized Decorators

```python
def retry(max_attempts: int, delay: float):
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        raise
                    logger.warning(f"Retry {attempt + 1}/{max_attempts}: {e}")
                    await asyncio.sleep(delay)
        return wrapper  # type: ignore
    return decorator

@retry(max_attempts=3, delay=1.0)
async def call_llm_with_retry(prompt: str) -> str:
    ...
```

## Package Layout

Follow the `src/` layout for all new packages under `openjiuwen/`:

```
openjiuwen/
  core/
    __init__.py          # Public API exports only
    exceptions.py         # Project-wide exceptions
    single_agent/
      __init__.py
      react_agent.py
      ...
    foundation/
      __init__.py
      llm/
        __init__.py
        client.py
        models.py
```

Keep `__init__.py` minimal — re-export only the public API. Internal
implementation details should not be exported.

## Anti-Patterns

### Mutable Default Arguments

```python
# Bad
def add_tool(tools: list[ToolCard] = []) -> None:
    tools.append(new_tool)

# Good
def add_tool(tools: list[ToolCard] | None = None) -> None:
    if tools is None:
        tools = []
    tools.append(new_tool)
```

### Bare Except

```python
# Bad
try:
    result = await risky_operation()
except:
    pass

# Good
try:
    result = await risky_operation()
except SecurityError:
    raise  # re-raise security errors
except ValueError as e:
    logger.warning(f"Invalid input: {e}")
```

### Type Checking with type()

```python
# Bad
if type(x) is str:

# Good
if isinstance(x, str):
```

## pyproject.toml Toolchain Configuration

```toml
[tool.black]
line-length = 120
target-version = ["py311"]

[tool.isort]
profile = "black"
line_length = 120

[tool.ruff]
line-length = 120
target-version = "py311"

[tool.ruff.lint]
select = [
    "E",     # pycodestyle errors
    "W",     # pycodestyle warnings
    "F",     # Pyflakes
    "I",     # isort
    "B",     # flake8-builtins
    "C4",    # flake8-comprehensions
    "UP",    # pyupgrade
    "ASYNC", # flake8-async
]
ignore = [
    "E501",  # line too long (handled by black)
]

[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
warn_unused_ignores = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"

[tool.bandit]
exclude_dirs = ["tests/", "docs/"]
```

