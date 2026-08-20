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

## Huawei Codecheck 硬规则(G.*)

`make check`(ruff)之外还有华为 codecheck 工具在本地/CI 跑。以下规则反复违反过,
写代码时直接遵守,不要等 codecheck 报告:

- **G.CLS.07 — 方法不用实例就加 `@staticmethod`**:方法体内不访问 `self`(只用参数 /
  模块函数 / 类常量)时必须加 `@staticmethod` 装饰器并删掉 `self` 形参;调用点
  `self.method(...)` 无需改动。反例历史:assembler.py / workspace_store.py 曾 9 处违反。
- **G.CLS.11 — 不要访问其他实例的受保护成员**:同类内只允许访问 `self._xxx`;
  访问 `other._xxx`(另一实例的 protected 字段)必须改走公开方法 / property。
  范例:`AgentConfigurator.workspace_manager`(property+setter)、
  `TeamAgent.attach_workspace_manager` —— 跨实例共享一律经公开表面。
- **G.VAR.03 — 禁止覆盖外层标识符**(严重):参数 / 局部变量不得遮蔽外层名字。
  高发场景:`from functools import cache` 之后又用 `cache` 做参数名或局部变量
  (loader.py / locales/__init__.py 都犯过)。与 `functools.cache` 冲突的参数一律改名
  (如 `ws_cache`),并同步所有 keyword 调用点。
- **G.FMT.04 — 冒号前不留空格**:切片 / 注解 / 字典一律 `x[a:b]`、`x: int`,
  不要写 `x : int`、`[a : b]`。
- **G.FMT.02 — 行宽 120**:docstring / 注释里的长行同样算,超 120 字符就换行或缩短。
