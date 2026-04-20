---
description: Test location, style, async patterns, mocking, and running conventions for agent-core.
language: chinese
paths:
  - "tests/**/*.py"
alwaysApply: false
---

# Testing Rules

## Test Location

- Prefer targeted unit tests that mirror the source path.
  Example: `openjiuwen/harness/deep_agent.py` -> `tests/unit_tests/harness/test_deep_agent.py`
- `tests/unit_tests/`: fast deterministic coverage, used in CI.
- `tests/system_tests/`: higher-level E2E; may require real model/API credentials and are commonly skipped in CI.

## Choosing Test Patterns

| Pattern | When to Use | Characteristics |
|---------|-------------|----------------|
| **Unit test** | Isolated logic, no I/O, fast feedback | Mock all dependencies |
| **Integration test** | Component interaction, shared state | May use real storage, skip in CI |
| **System/E2E test** | Full workflow, end-to-end correctness | Requires credentials, skipped in CI |

**Decision rules**:
- If it touches the filesystem, network, or external service -> integration or system test
- If it tests a single class/function in isolation -> unit test
- If the harness subsystem changes -> ensure both unit and integration coverage
- Coverage gate: 80% minimum for `openjiuwen/core/` and `openjiuwen/harness/`

For pytest-specific conventions (markers, fixtures, async patterns), see
`python/testing.md` and `skills/python-testing`.

## Test Style

- This repo uses both `pytest` and `unittest.IsolatedAsyncioTestCase`.
- Use `pytest` fixtures for shared setup; use `IsolatedAsyncioTestCase` for async test classes.
- Test class naming: `Test<Feature>` or `Test<FeatureName>`.

## Credentials and Mocks

- Use mock defaults for credentials in tests, for example: `os.getenv(..., "mock-api-key")`.
- Never hard-code real API keys in test files.
- Mark tests that require real credentials with `@pytest.mark.skip(reason="requires real credentials")`.

## Async Tests

- Use `pytest-asyncio` with `loop_scope="function"` (set in `pyproject.toml`).
- Example async test class:

```python
import pytest
from unittest import IsolatedAsyncioTestCase

class TestMyFeature(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.fixture = await setup_fixture()

    async def test_it_works(self) -> None:
        result = await my_async_fn()
        self.assertEqual(result, expected)
```

## Assertions and Coverage

- Use descriptive assertion messages for non-obvious conditions.
- New public API changes require corresponding test updates.
- When behavior changes are user-visible, update `docs/` and `examples/` alongside tests.

## Running Tests

- Run all tests: `make test`
- Run targeted test: `make test TESTFLAGS="tests/unit_tests/harness/test_deep_agent.py"`
- `make check` runs linting; it does NOT run tests.
