---
description: Python-specific coding conventions: immutability, type annotations, toolchain, and anti-patterns.
language: chinese
paths:
  - "openjiuwen/**/*.py"
alwaysApply: false
---

# Python Coding Style (Extended)

Extends `rules/code-style.md` with Python-specific conventions.
See `skills/python-patterns` for deep pattern reference.

## Immutability

Prefer immutable data structures. Use `@dataclass(frozen=True)` for
data-only types (cards, configs, events). Use `NamedTuple` for simple
fixed-length records. See `skills/python-patterns` for complete examples.

## Modern Type Annotations

Use Python 3.9+ built-in generics instead of `typing` module equivalents:

```python
# Preferred (Python 3.9+)
def process(items: list[int], mapping: dict[str, str]) -> set[str]: ...

# Avoid (legacy form)
from typing import List, Dict, Set
def process(items: List[int], mapping: Dict[str, str]) -> Set[str]: ...
```

Use `typing.Protocol` for structural subtyping (duck typing with type hints):

```python
from typing import Protocol

class ResourceAllocator(Protocol):
    def allocate(self, name: str) -> str: ...
    def release(self, name: str) -> None: ...
```

See `skills/python-patterns` for `runtime_checkable` examples.

## Toolchain

- **Formatter**: `black` (line length 120, matches Ruff)
- **Import sorter**: `isort`
- **Linter**: `ruff` (primary — handles both linting and some formatting)
- Run `make fix` to apply all auto-fixes; run `make check` to verify

## Memory Optimization

Use `__slots__` for lightweight classes instantiated frequently:

```python
class Event:
    __slots__ = ("type", "timestamp", "payload")
```

Only use `__slots__` when the class has a fixed set of attributes and
memory efficiency matters. Do not use `__slots__` when the class needs
arbitrary attributes or is subclassed with additional fields.
See `skills/python-patterns` for more examples.

## Anti-Patterns

- **Mutable default arguments** — use `None` and initialize inside function:
  `def f(x: list[str] | None = None)` instead of `def f(x=[])`
- **`type()` checking** — use `isinstance()` instead: `isinstance(x, str)`
- **Bare `except`** — always catch specific exceptions, never bare `except:`

See `skills/python-patterns` for correct patterns and detailed examples.
