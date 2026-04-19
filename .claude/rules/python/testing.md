---
description: Pytest markers, fixtures, mocking, and coverage conventions for agent-core Python code.
language: chinese
paths:
  - "tests/**/*.py"
alwaysApply: false
---

# Python Testing (Extended)

Extends `rules/testing.md` with Python/pytest-specific conventions.
See `skills/python-testing` for deep reference.

## Coverage Target

Minimum **80% line coverage** for all modules under `openjiuwen/core/` and
`openjiuwen/harness/`. Run coverage with:

```bash
pytest --cov=openjiuwen --cov-report=term-missing --cov-report=term-missing-rx
```

Coverage gates in CI reject any module that drops below 80%.

## Pytest Markers

Use markers to categorize tests for selective execution:

```python
import pytest

@pytest.mark.unit     # Fast, deterministic, no I/O
class TestAbilityManager:
    ...

@pytest.mark.integration  # Requires external services (may be skipped in CI)
class TestSessionPersistence:
    ...

@pytest.mark.slow     # Long-running; skip routinely with -m "not slow"
class TestFullWorkflow:
    ...
```

Run only fast tests in CI:

```bash
pytest -m "not slow"
```

## Fixtures

Define shared fixtures in `tests/conftest.py`. Use explicit scope
when a fixture should be reused across a module or session:

```python
import pytest
from unittest import mock

@pytest.fixture
def mock_llm_client():
    with mock.patch("openjiuwen.core.foundation.LLMClient") as m:
        yield m

@pytest.fixture(scope="module")
def db_connection():
    # Shared across all tests in the module; close when done
    conn = create_test_connection()
    yield conn
    conn.close()
```

For async fixtures with `pytest-asyncio`:

```python
import pytest
import pytest_asyncio

@pytest_asyncio.fixture
async def async_client():
    client = await create_client()
    yield client
    await client.close()
```

## Mocking

Use `unittest.mock` for mocks and patches:

```python
from unittest import mock
import pytest

def test_it_uses_llm(monkeypatch):
    called = False

    def fake_call(self, prompt: str) -> str:
        nonlocal called
        called = True
        return "mocked response"

    monkeypatch.setattr(
        "openjiuwen.core.foundation.LLMClient.call",
        fake_call
    )
    # ...
    assert called
```

For autospec on classes/methods:

```python
with mock.patch(
    "openjiuwen.harness.DeepAgent.run",
    autospec=True
) as mock_run:
    mock_run.return_value = {"result": "ok"}
    # ...
```

## Async Tests

Use `pytest-asyncio` with `loop_scope="function"` (configured in
`pyproject.toml`). Prefer `IsolatedAsyncioTestCase` for async test classes
that need per-test isolation:

```python
from unittest import IsolatedAsyncioTestCase

class TestRunner(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.runner = await create_runner()

    async def test_it_runs(self) -> None:
        result = await self.runner.run("test task")
        self.assertEqual(result.status, "success")
```

For standalone async test functions:

```python
import pytest

@pytest.mark.asyncio
async def test_agent_invokes_subagent():
    agent = await build_agent()
    result = await agent.run("delegate to planner")
    assert result.subagent_called is True
```

## Credentials in Tests

Never hardcode real API keys. Use `os.getenv` with a safe mock default:

```python
import os

api_key: str = os.getenv("OPENAI_API_KEY", "mock-api-key-for-tests")
```

Mark tests requiring real credentials:

```python
@pytest.mark.skip(reason="requires real API credentials")
async def test_with_real_api():
    ...
```
