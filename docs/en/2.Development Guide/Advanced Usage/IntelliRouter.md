# IntelliRouter

IntelliRouter is the **smart routing layer** in agent-core. It acts as a unified routing dispatcher between agent-core and multiple **OpenAI-compatible** LLM endpoints. Through IntelliRouter, you can manage many LLM deployments (different models, instances, and regions) under one roof, and let the router automatically pick the best deployment and fail over when one becomes unavailable.

**Core capabilities:**

- **Endpoint pooling** — manage multiple deployments transparently behind a single entry point
- **Smart routing strategies** — random, lowest-latency, tag-based, and adaptive multi-factor strategies
- **Automatic failover** — on failure, automatically retry other deployments
- **Health checks** — periodically probe deployment availability and proactively evict unhealthy nodes
- **Runtime state queries** — inspect each deployment's health status and routing statistics in real time

**Architecture overview:**

```
Agent / Workflow
       │
       ▼
ModelClientConfig(client_provider="intelli_router")
       │
       ▼
IntelliRouterModelClient
       │
       ▼
ReliableRouter (intelli_router package)
       │
       ├── Deployment A (qwen-plus @ DashScope compatible-mode)
       ├── Deployment B (deepseek-v3 @ DeepSeek)
       └── Deployment C (gpt-4o @ OpenAI)
```

> **Note**
> The current version of IntelliRouter reaches each deployment through a unified OpenAI-compatible endpoint (`{api_base}/v1/chat/completions`). Make sure the configured `api_base` exposes an OpenAI-compatible endpoint (for example DeepSeek, OpenAI, or DashScope's compatible-mode).

## Installation & Environment

IntelliRouter is an **optional dependency** of agent-core and must be installed separately:

```bash
pip install intelli-router
```

Verify the installation:

```python
from intelli_router import ReliableRouter, Deployment
print("intelli-router installed successfully")
```

> **Note**
> If the `intelli-router` package is not installed, agent-core still loads normally, but creating an IntelliRouter client will raise a `MODEL_SERVICE_CONFIG_ERROR` with a prompt to install it.

**Environment variables (set as needed):**

```bash
export OPENAI_API_KEY="sk-..."          # OpenAI
export DEEPSEEK_API_KEY="sk-..."        # DeepSeek
export DASHSCOPE_API_KEY="sk-..."       # DashScope (Tongyi Qianwen, via the compatible-mode endpoint)
```

## Configuration

### Basic Configuration

Use `ModelClientConfig` to create an IntelliRouter client configuration:

```python
from openjiuwen.core.foundation.llm import ModelClientConfig

model_client_config = ModelClientConfig(
    client_provider="intelli_router",    # Use IntelliRouter
    api_key="placeholder",               # Required by schema; not used by IntelliRouter
    api_base="http://placeholder",       # Required by schema; not used by IntelliRouter
    verify_ssl=False,                    # Default SSL setting for deployments
    # ... IntelliRouter-specific configuration below
)
```

> **Note**
> `api_key` and `api_base` are required fields of the `ModelClientConfig` schema, but IntelliRouter does not use them (each deployment carries its own key/base). Just fill them with `"placeholder"`.

### Deployment List

`intelli_router_deployments` is the most important IntelliRouter setting. It defines all available LLM deployments:

```python
intelli_router_deployments = [
    {
        "id": "deepseek-v3",                     # Unique deployment identifier
        "model_name": "deepseek-v3",             # Model name served by this deployment
        "api_key": "sk-xxx",                     # API key for this deployment
        "api_base": "https://api.deepseek.com",  # OpenAI-compatible API endpoint for this deployment
        "tpm": 100000,                           # Tokens-per-minute quota
        "rpm": 60,                               # Requests-per-minute quota
        "tags": ["primary", "fast"],             # Tags (used by tag-based routing)
        "timeout": 30.0,                         # Per-deployment timeout (seconds)
    },
    {
        "id": "openai-gpt-4o",
        "model_name": "gpt-4o",
        "api_key": "sk-yyy",
        "api_base": "https://api.openai.com",
        "tpm": 100000,
        "rpm": 60,
        "tags": ["powerful"],
        "timeout": 30.0,
    },
]
```

**Deployment field reference:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | No | Unique deployment identifier (auto-generated if omitted) |
| `model_name` | string | Yes | Model name served by this deployment |
| `api_key` | string | Yes | Deployment-specific API key |
| `api_base` | string | Yes | OpenAI-compatible API endpoint for this deployment |
| `tpm` | int | No | Tokens-per-minute quota |
| `rpm` | int | No | Requests-per-minute quota |
| `tags` | list[str] | No | Tags, used by tag-based routing |
| `timeout` | float | No | Per-deployment timeout override (seconds) |
| `verify_ssl` | bool | No | Per-deployment SSL verification override |

### Routing Strategy

Specify the routing strategy via `intelli_router_strategy`:

| Strategy | Description | Use case |
|----------|-------------|----------|
| `simple-shuffle` | Randomly pick a deployment (default) | Development/testing, equally weighted deployments |
| `lowest-latency` | Pick the deployment with the lowest recent latency | Latency-sensitive production |
| `tag-based` | Filter deployments by tag, then route | Deployments grouped by capability/purpose |
| `token-aware` | Prefer deployments with ample token headroom | Scenarios constrained by TPM quota |
| `rate-limit-aware` | Prefer deployments with ample RPM headroom | Scenarios constrained by RPM quota |
| `adaptive` | Adaptive multi-factor scoring | Production with heterogeneous deployments |

**`adaptive` strategy parameters** (`intelli_router_strategy_kwargs`):

```python
intelli_router_strategy_kwargs={
    "token_threshold": 1000,      # Token usage threshold
    "rpm_threshold": 10,          # RPM usage threshold
    "exploration_ratio": 0.1,     # Exploration ratio (10% of requests routed randomly to gather data)
    "w_health": 1.0,              # Health weight
    "w_token": 0.5,               # Token headroom weight
    "w_rpm": 0.3,                 # RPM headroom weight
    "w_latency": 0.2,             # Latency weight
}
```

### Retries, Timeout, and Health Checks

```python
model_client_config = ModelClientConfig(
    client_provider="intelli_router",
    api_key="placeholder",
    api_base="http://placeholder",
    intelli_router_deployments=intelli_router_deployments,
    intelli_router_strategy="adaptive",
    intelli_router_num_retries=3,                    # Max retries (default 3)
    intelli_router_timeout=30.0,                     # Global request timeout (default 30s)
    intelli_router_enable_health_check=True,         # Enable periodic health checks
    intelli_router_health_check_interval=300.0,      # Health check interval (default 300s)
    intelli_router_strategy_kwargs={...},
)
```

### Full Configuration Example

```python
import os
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig

DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

# Define the deployment list
deployments = [
    {
        "id": "deepseek-v3",
        "model_name": "deepseek-v3",
        "api_key": DEEPSEEK_KEY,
        "api_base": "https://api.deepseek.com",
        "tpm": 100000,
        "rpm": 60,
        "tags": ["primary", "fast"],
        "timeout": 30.0,
    },
    {
        "id": "deepseek-v3-backup",
        "model_name": "deepseek-v3",
        "api_key": DEEPSEEK_KEY,
        "api_base": "https://api.deepseek.com",
        "tpm": 100000,
        "rpm": 60,
        "tags": ["backup"],
        "timeout": 30.0,
    },
    {
        "id": "openai-gpt-4o",
        "model_name": "gpt-4o",
        "api_key": OPENAI_KEY,
        "api_base": "https://api.openai.com",
        "tpm": 100000,
        "rpm": 60,
        "tags": ["powerful"],
        "timeout": 30.0,
    },
]

# Create the IntelliRouter client configuration
model_client_config = ModelClientConfig(
    client_provider="intelli_router",
    api_key="placeholder",
    api_base="http://placeholder",
    verify_ssl=False,
    intelli_router_deployments=deployments,
    intelli_router_strategy="adaptive",
    intelli_router_num_retries=3,
    intelli_router_timeout=30.0,
    intelli_router_enable_health_check=True,
    intelli_router_health_check_interval=300.0,
    intelli_router_strategy_kwargs={
        "token_threshold": 1000,
        "rpm_threshold": 10,
        "exploration_ratio": 0.1,
        "w_health": 1.0,
        "w_token": 0.5,
        "w_rpm": 0.3,
        "w_latency": 0.2,
    },
)

# Create the request configuration
model_config = ModelRequestConfig(temperature=0.7, top_p=0.9)
```

## Usage Patterns

### Unified Dispatch (Cross-Deployment Failover)

When `ModelRequestConfig` does not specify a `model` (or `model` is an empty string), IntelliRouter sets the model to `"*"` and selects the best deployment from all of them.

```python
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig

# Empty model → unified dispatch mode
model_config = ModelRequestConfig(temperature=0.7)

model = Model(
    model_client_config=model_client_config,
    model_config=model_config,
)

# The router picks the best deployment across all of them.
# If the selected deployment fails, it automatically switches to another available one.
result = await model.invoke(messages=[
    {"role": "user", "content": "Hello"}
])
```

**Behavior:**
- The router selects the best deployment from all of them according to the strategy
- If the selected deployment fails (timeout, network error, API error), it automatically retries other deployments
- Failover can cross models (deepseek-v3 → gpt-4o) and instances

### Routing to a Specific Model

When `ModelRequestConfig` specifies a concrete `model` name, the router only routes within that model's deployment pool:

```python
# Route only within deepseek-v3 deployments
model_config = ModelRequestConfig(model="deepseek-v3", temperature=0.7)

model = Model(
    model_client_config=model_client_config,
    model_config=model_config,
)

result = await model.invoke(messages=[
    {"role": "user", "content": "Hello"}
])
```

> **Note**
> Routing to a specific model is compatible with the traditional single-provider usage and suits scenarios that need deterministic model selection.

### Sharing a Router Across Multiple Agents

IntelliRouter uses a **config-hash caching mechanism**: multiple agents that use the same `ModelClientConfig` automatically share the same `ReliableRouter` instance.

```python
from openjiuwen.core.single_agent.agents import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent import AgentCard

# Agent A and Agent B use the same model_client_config
agent_a_config = ReActAgentConfig(
    model_client_config=model_client_config,
    model_config_obj=ModelRequestConfig(temperature=0.7),
)

agent_b_config = ReActAgentConfig(
    model_client_config=model_client_config,  # the same config object
    model_config_obj=ModelRequestConfig(temperature=0.5),
)

agent_a = ReActAgent(card=AgentCard(id="agent_a", name="Agent A")).configure(agent_a_config)
agent_b = ReActAgent(card=AgentCard(id="agent_b", name="Agent B")).configure(agent_b_config)

# agent_a and agent_b share the same underlying ReliableRouter instance.
# Benefits:
#   1. Once Agent A finds a deployment unavailable, Agent B avoids it too
#   2. Health state is shared across all agents
#   3. Reduced memory and connection overhead
```

### Using It in a Workflow

IntelliRouter can be used inside a Workflow's `LLMComponent`:

```python
from openjiuwen.core.workflow import (
    Workflow, WorkflowCard, Start, End,
    LLMComponent, LLMCompConfig, generate_workflow_key,
)
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.legacy import WorkflowAgentConfig
from openjiuwen.core.application.workflow_agent import WorkflowAgent

# Define the workflow
workflow_card = WorkflowCard(
    id="my_workflow", name="my_workflow", version="1.0",
    description="A workflow using IntelliRouter",
    input_params={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
)

flow = Workflow(card=workflow_card)
start = Start()
end = End({"responseTemplate": "{{output}}"})

llm_config = LLMCompConfig(
    model_client_config=model_client_config,
    model_config=ModelRequestConfig(),  # unified dispatch
    template_content=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "{{query}}"},
    ],
    response_format={"type": "text"},
    output_config={"output": {"type": "string", "description": "LLM output"}},
)
llm = LLMComponent(llm_config)

flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
flow.add_workflow_comp("llm", llm, inputs_schema={"query": "${start.query}"})
flow.set_end_comp("end", end, inputs_schema={"output": "${llm.output}"})
flow.add_connection("start", "llm")
flow.add_connection("llm", "end")

# Register and run
Runner.resource_mgr.add_workflow(
    WorkflowCard(id=generate_workflow_key(flow.card.id, flow.card.version)),
    lambda: flow,
)
agent = WorkflowAgent(WorkflowAgentConfig(
    id="my_agent", version="0.1.0", description="IntelliRouter workflow agent",
))
agent.add_workflows([flow])

result = await Runner.run_agent(agent, {"query": "Hello, tell me about yourself"})
```

### Streaming

IntelliRouter transparently supports streaming; usage is the same as a normal Model:

```python
model = Model(
    model_client_config=model_client_config,
    model_config=ModelRequestConfig(),
)

async for chunk in model.stream(messages=[{"role": "user", "content": "Tell me a story"}]):
    print(chunk.content, end="", flush=True)
```

## Inspecting Runtime State

`ReliableRouter` provides a `get_stats()` method to inspect each deployment's real-time health status and routing statistics:

```python
from openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client import _router_cache

# Get a router instance
router = next(iter(_router_cache.values()))

router_stats = router.get_stats()

# Inspect the health status of each deployment
for dep_id, status in router_stats.get("deployment_status", {}).items():
    print(f"{dep_id}: {status}")
```

Example output:

```
deepseek-v3: healthy
deepseek-v3-backup: unhealthy
openai-gpt-4o: healthy
```

## Best Practices

### Deployment Configuration

- **At least 2 deployments** for redundancy; 3+ recommended for better availability
- **Assign tags by capability**: `fast` (fast response), `powerful` (high quality), `backup` (standby)
- **Set reasonable timeouts**: 10–30s for fast models, 60–120s for reasoning models
- **Set accurate tpm/rpm**: match the provider's real quota to help the adaptive strategy make correct decisions

### Strategy Selection

| Scenario | Recommended strategy | Rationale |
|----------|---------------------|-----------|
| Development/testing | `simple-shuffle` | Simple, covers all deployments |
| Latency-sensitive (multiple instances of one model) | `lowest-latency` | Fastest response |
| Quota-constrained | `token-aware` / `rate-limit-aware` | Prefer deployments with ample headroom |
| Production (heterogeneous deployments) | `adaptive` | Balances health, load, and latency |

### Production Recommendations

```python
model_client_config = ModelClientConfig(
    client_provider="intelli_router",
    api_key="placeholder",
    api_base="http://placeholder",
    verify_ssl=True,                              # enable SSL in production
    intelli_router_deployments=deployments,
    intelli_router_strategy="adaptive",
    intelli_router_num_retries=3,
    intelli_router_timeout=30.0,
    intelli_router_enable_health_check=True,       # must be enabled
    intelli_router_health_check_interval=60.0,     # shorten the check interval
    intelli_router_strategy_kwargs={
        "token_threshold": 1000,
        "rpm_threshold": 10,
        "exploration_ratio": 0.05,                 # lower exploration ratio in production
    },
)
```

### Security Notes

- Use an independent `api_key` per deployment for independent rotation and access control
- Set `verify_ssl=True` in production
- Never hardcode API keys in code; use environment variables or a secret management service

## FAQ

**Q: What happens if the `intelli-router` package is not installed?**

A: The agent-core module loads normally (imports do not fail), but creating an IntelliRouter client raises a `MODEL_SERVICE_CONFIG_ERROR` with a prompt to `pip install intelli-router`.

**Q: Why fill `api_key` and `api_base` with placeholders?**

A: The Pydantic schema of `ModelClientConfig` requires both fields. The IntelliRouter client overrides `_validate_config()` to a no-op, so these values are never actually used. Each deployment connects independently via its own `api_key` and `api_base`.

**Q: What are the requirements for each deployment's `api_base`?**

A: The current version reaches deployments through a unified OpenAI-compatible endpoint (`{api_base}/v1/chat/completions`), so `api_base` must point to an OpenAI-compatible endpoint (such as OpenAI, DeepSeek, or DashScope's compatible-mode).

**Q: What is the difference between unified dispatch and a specific model?**

A: Unified dispatch (empty model → `"*"`) selects from all deployments and can fail over across models; a specific model (e.g., model="deepseek-v3") routes only within the deployment pool for that model name, so failover is limited to different instances of the same model.

**Q: When is a router instance released?**

A: `ReliableRouter` is cached in a module-level `_router_cache` dict (indexed by config hash) and is not released automatically during the process lifetime. This guarantees multi-agent sharing and state persistence.

**Q: How do I inspect the current router's runtime state?**

A: Use `router.get_stats()` to get each deployment's health status and routing statistics (see "Inspecting Runtime State" above).

## References

- Basic usage example: `examples/intelli_router/intelliRouter_demo.py`
- Implementation source: `openjiuwen/core/foundation/llm/model_clients/intelli_router_model_client.py`
- Config schema: `openjiuwen/core/foundation/llm/schema/config.py`
- Architecture design doc: `docs/dev/model_clients_design.md`
