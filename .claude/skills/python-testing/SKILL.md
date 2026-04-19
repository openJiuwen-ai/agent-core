---
name: python-testing
description: Deep pytest guide for agent-core: fixtures, mocking, async tests, and TDD workflow.
---

# Python Testing

Comprehensive pytest patterns for agent-core. This skill extends
`.claude/rules/python/testing.md` and `.claude/rules/testing.md`.

## TDD Workflow

Write tests before implementation. Follow the red-green-refactor cycle:

1. **RED** — Write a failing test that describes the desired behavior
2. **GREEN** — Write the minimal implementation to make the test pass
3. **REFACTOR** — Improve code quality while keeping tests green

For agent-core, this means:

```python
# RED: Write the test first
class TestAbilityManager:
    @pytest.mark.asyncio
    async def test_registers_tool(self):
        manager = AbilityManager()
        card = ToolCard(name="echo", description="echo input", parameters={})

        await manager.register_tool(card, echo_handler)

        assert "echo" in manager.list_tools()

    # GREEN: Minimal implementation
    # ...

    # REFACTOR: Clean up, add edge cases
    @pytest.mark.asyncio
    async def test_raises_on_duplicate_registration(self):
        manager = AbilityManager()
        card = ToolCard(name="echo", description="echo input", parameters={})
        await manager.register_tool(card, echo_handler)

        with pytest.raises(ConfigurationError, match="already registered"):
            await manager.register_tool(card, echo_handler)
```

## Fixtures

### conftest.py Organization

Define fixtures in `tests/conftest.py` for project-wide fixtures, or in
`tests/unit_tests/<module>/conftest.py` for module-specific fixtures.

```python
# tests/conftest.py
import pytest
from unittest import mock

@pytest.fixture
def mock_llm_client():
    """Provides a mocked LLM client for all tests."""
    with mock.patch("openjiuwen.core.foundation.LLMClient") as cls:
        instance = mock.MagicMock()
        instance.call.return_value = '{"result": "ok"}'
        cls.return_value = instance
        yield instance

@pytest.fixture(scope="module")
def sample_agent_card():
    """Module-scoped: created once per test module."""
    return AgentCard(id="test-agent", name="TestAgent", version="1.0")

@pytest.fixture
def temp_workspace(tmp_path):
    """Provides a clean temporary directory for each test."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    yield workspace
    # Cleanup happens automatically via tmp_path
```

### Parametrized Fixtures

```python
@pytest.fixture(params=["haiku", "sonnet", "opus"])
def llm_model(request: pytest.FixtureRequest):
    return request.param

async def test_llm_client(llm_model: str):
    client = LLMClient(model=llm_model)
    result = await client.call("hello")
    assert result is not None
```

### Factory Fixtures

```python
@pytest.fixture
def make_tool_card():
    """Factory fixture: creates ToolCards with defaults."""
    def _make(name: str = "test-tool", **kwargs) -> ToolCard:
        defaults = {
            "description": "a test tool",
            "parameters": {},
            "version": "1.0",
        }
        defaults.update(kwargs)
        return ToolCard(name=name, **defaults)
    return _make

async def test_registers_custom_tool(make_tool_card):
    card = make_tool_card(name="custom")
    manager = AbilityManager()
    await manager.register_tool(card, handler)
    assert "custom" in manager.list_tools()
```

### autouse Fixtures

Use `autouse` sparingly — only for global setup that must happen for every
test in the scope:

```python
@pytest.fixture(autouse=True)
def reset_global_state():
    """Reset global state before each test to ensure isolation."""
    original = Runner.resource_mgr
    Runner.resource_mgr = ResourceManager()
    yield
    Runner.resource_mgr = original
```

## Pytest Marks

### Defining Custom Marks

In `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
    "unit: fast, deterministic tests with no I/O",
    "integration: tests requiring external services",
    "slow: long-running tests (skipped by default)",
    "e2e: end-to-end workflow tests",
]
```

### Selective Execution

```bash
# Run only fast unit tests (skip slow + integration)
pytest -m "unit"

# Run unit + integration, skip slow
pytest -m "not slow"

# Run everything including slow tests
pytest -m ""

# Run only e2e tests
pytest -m "e2e"
```

## Mocking

### Monkeypatch

Use `monkeypatch` for simple cases — prefer it over `@patch` decorators:

```python
def test_reads_config(monkeypatch, tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text('{"timeout": 30}')

    monkeypatch.setattr(
        "openjiuwen.core.config.CONFIG_PATH",
        config_file
    )

    config = load_config()
    assert config.timeout == 30
```

### @patch Decorator

Use `@patch` when mocking class methods or when the mock needs to be
accessed inside the test:

```python
from unittest import mock

@mock.patch("openjiuwen.harness.DeepAgent._run_step", new_callable=mock.AsyncMock)
async def test_delegates_to_subagent(mock_run_step):
    mock_run_step.return_value = StepResult(
        status="success",
        subagent_called=True,
    )

    agent = DeepAgent()
    result = await agent.run("complex task")

    assert result.subagent_called
    mock_run_step.assert_called_once()
```

### Autospec

Use `autospec=True` to automatically match the signature of the real method:

```python
with mock.patch(
    "openjiuwen.core.foundation.LLMClient.call",
    autospec=True
) as mock_call:
    mock_call.return_value = '{"choices": [{"message": {"content": "ok"}}]}'
    # mock_call.assert_called_once_with("prompt")  # Enforces correct signature
```

### AsyncMock

For async methods:

```python
from unittest import mock

with mock.patch(
    "openjiuwen.harness.DeepAgent._run_step",
    new_callable=mock.AsyncMock
) as mock_step:
    mock_step.return_value = StepResult(status="success")
    result = await agent.run("test")
    assert result.status == "success"
```

### Mocking Context Managers

```python
from unittest import mock

def test_sandbox_executes_within_scope():
    with mock.patch(
        "openjiuwen.core.sys_operation.sandbox.SandboxedRunner.run"
    ) as mock_run:
        mock_run.return_value = CommandResult(stdout="ok", stderr="", code=0)

        result = sandbox.run("echo hello")
        assert result.stdout == "ok"
        mock_run.assert_called_once_with("echo hello")
```

## Async Testing

### pytest-asyncio Configuration

In `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```

### Async Test Functions

```python
@pytest.mark.asyncio
async def test_ability_manager_registers_tool():
    manager = AbilityManager()
    card = ToolCard(name="test", description="test tool", parameters={})

    await manager.register_tool(card, handler)

    tools = manager.list_tools()
    assert "test" in tools
```

### IsolatedAsyncioTestCase

Use `unittest.IsolatedAsyncioTestCase` for test classes that need strict
per-test isolation:

```python
from unittest import IsolatedAsyncioTestCase

class TestRunner(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.runner = await create_runner()
        self.runner.resource_mgr.clear()

    async def asyncTearDown(self) -> None:
        await self.runner.shutdown()

    async def test_it_executes_task(self) -> None:
        result = await self.runner.execute("test task")
        self.assertEqual(result.status, "success")
```

### tmp_path for File Operations

```python
@pytest.mark.asyncio
async def test_saves_session(tmp_path):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()

    manager = SessionManager(base_dir=session_dir)
    await manager.save(SessionData(id="s1", turns=[]))

    assert (session_dir / "s1.json").exists()
```

## Test Organization

Mirror the source path in test paths:

| Source | Test |
|--------|------|
| `openjiuwen/harness/deep_agent.py` | `tests/unit_tests/harness/test_deep_agent.py` |
| `openjiuwen/core/single_agent/react_agent.py` | `tests/unit_tests/core/single_agent/test_react_agent.py` |
| `openjiuwen/core/workflow/engine.py` | `tests/unit_tests/core/workflow/test_engine.py` |

## pytest.ini + pyproject.toml Configuration

```ini
# pytest.ini
[pytest]
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
testpaths = tests/unit_tests tests/system_tests
markers =
    unit: fast deterministic tests, no I/O
    integration: tests requiring external services
    slow: long-running tests, skipped by default
    e2e: end-to-end workflow tests
```

```toml
# pyproject.toml additions
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
filterwarnings = [
    "ignore::DeprecationWarning",
]

[tool.coverage.run]
source = ["openjiuwen/"]
omit = [
    "*/tests/*",
    "*/__pycache__/*",
    "*/migrations/*",
]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "raise NotImplementedError",
]
fail_under = 80
```

