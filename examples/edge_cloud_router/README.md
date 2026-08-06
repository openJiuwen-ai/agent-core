# Edge-cloud router experiments

This directory contains the JiuwenSwarm model configuration for three controlled
experiments:

1. `jiuwenswarm-local-baseline.yaml` — no router; every request uses the local
   medium model.
2. `jiuwenswarm-baseline.yaml` — no router; every request uses the most capable
   configured cloud model.
3. `jiuwenswarm-router.yaml` — the local complexity classifier and all five
   answer deployments.

The goal is to compare answer quality, end-to-end latency, routing behavior, and
externally billed cloud cost. Local answer models and the local classifier count
as zero-cost for these experiments. Cost is not part of the routing policy.

## Architecture and repository changes

The implementation is split across three repositories. Clone the repositories
side-by-side and use revisions that contain the router implementation:

```text
workspace/
├── agent-core/
├── agent-xrouter/
└── jiuwenswarm/
```

EdgeTRL is not needed on the testing machine.

### `agent-xrouter`

`agent-xrouter` contains the framework-neutral routing policy under
`src/agent_xrouter/`:

- canonical request, policy, and result types;
- deterministic S1/S2/S3 privacy detection and structured redaction;
- the five-level heuristic and LLM classifier prompt/parser;
- the fixed complexity-to-deployment decision; and
- the privacy → classification → `RoutePlan` engine.

It has no agent-core, EdgeTRL, HTTP-client, GPU, or inference-engine dependency.
There is no RL, bandit evolution, reward judge, routing memory, or training.

### `agent-core`

Agent-core adds the built-in `ProviderType.EdgeCloudRouter` model client. The
adapter creates one classifier client in `llm` mode and five answer clients:

| Complexity | Deployment | Privacy scope |
|---|---|---|
| `SIMPLE` | `local_fast` | `local` |
| `MEDIUM` | `local_medium` | `local` |
| `COMPLEX` | `cloud_complex` | `cloud` |
| `RESEARCH` | `cloud_research` | `cloud` |
| `REASONING` | `cloud_reasoning` | `cloud` |

Privacy enforcement is configurable:

- It is disabled by default in the supplied router experiment, so all requests
  route only by complexity using their original content.
- Set `privacy.enabled: true` to evaluate privacy behavior. In that mode S1
  follows complexity, S2 is redacted before classification/cloud dispatch, and
  S3 or privacy failure uses the original request with `local_medium`.
- Classifier failure uses the original request with `local_medium` in either mode.
- Cloud failure may fall back once to `local_medium` before any answer output.
- Local failure is never sent to cloud.

The client supports normal and streaming chat. It preserves the selected answer
provider's response and usage metadata and adds sanitized route metadata. It
does not calculate cost, collect classifier usage, or use cost when routing.

### `jiuwenswarm`

JiuwenSwarm has one provider-generic compatibility change: a model is usable
when agent-core successfully constructed its client and its top-level endpoint
is not a documentation placeholder. There is no router-specific runtime path.
The UI cannot author the nested router configuration yet, so edit YAML directly.

## Python environment

Use Python `>=3.11,<3.14`. `agent-xrouter` is not published yet, so install the
local checkouts into JiuwenSwarm's environment. JiuwenSwarm otherwise resolves
agent-core from its configured upstream dependency.

```bash
cd workspace/jiuwenswarm
uv sync --extra test

uv pip install --python .venv/bin/python \
  -e ../agent-core \
  -e ../agent-xrouter
```

Verify that imports resolve to the local source trees:

```bash
uv run --no-sync python -c \
  "import openjiuwen, agent_xrouter; print(openjiuwen.__file__); print(agent_xrouter.__file__)"
```

Both printed paths must point into `workspace/agent-core` and
`workspace/agent-xrouter`. Use `uv run --no-sync` for later JiuwenSwarm commands.
Running a normal `uv sync` again may restore the upstream agent-core package; if
that happens, reinstall the two editable packages.

### Focused validation

Run unit tests before starting the live experiment:

```bash
cd workspace/agent-xrouter
uv sync --extra test
uv run pytest -q

cd ../agent-core
uv sync
uv pip install --python .venv/bin/python -e ../agent-xrouter
PYTHONPATH=../agent-xrouter/src uv run --no-sync pytest -q \
  tests/unit_tests/core/foundation/llm/test_edge_cloud_router_model_client.py \
  tests/unit_tests/core/foundation/llm/test_edge_cloud_router_metadata.py \
  tests/unit_tests/core/foundation/llm/test_message_chunk.py \
  tests/unit_tests/core/foundation/llm/test_model_client_config.py

cd ../jiuwenswarm
uv run --no-sync pytest -q \
  tests/unit_tests/agentserver/test_deep_adapter_model_config.py
```

## Local inference endpoint requirement

Neither agent-core, agent-xrouter, nor JiuwenSwarm loads model weights or starts an
inference process. Before running the local baseline or router experiment, start
OpenAI-compatible local endpoints yourself using **Ollama or llama.cpp**. vLLM
is not required or used by this experiment guide.

The router needs these three logical local models:

| Purpose | Example model | Default configuration endpoint |
|---|---|---|
| Complexity classifier | Qwen3 0.6B | `http://127.0.0.1:8081/v1` |
| `local_fast` answer | Gemma 3 4B | `http://127.0.0.1:8082/v1` |
| `local_medium` answer | Qwen3 8B | `http://127.0.0.1:8083/v1` |

The classifier is a sixth model, separate from the five answer deployments. It
is always configured with `privacy_scope: local`. CPU and GPU execution are
both acceptable; the router only sees an HTTP endpoint.

### Option A: Ollama (quickest setup)

Install Ollama using its platform instructions, then download suitable model
tags. These tags are examples; use tags available for the installed Ollama
version and record the exact choices:

```bash
ollama pull qwen3:0.6b
ollama pull gemma3:4b
ollama pull qwen3:8b
```

Start the server if the desktop/service installation has not already started
it:

```bash
OLLAMA_CONTEXT_LENGTH=8192 ollama serve
```

Ollama serves all installed models from one OpenAI-compatible base URL. Override
the three model configurations before starting JiuwenSwarm:

```bash
export CLASSIFIER_API_BASE="http://127.0.0.1:11434/v1"
export CLASSIFIER_API_KEY="EMPTY"
export CLASSIFIER_MODEL="qwen3:0.6b"

export LOCAL_FAST_API_BASE="http://127.0.0.1:11434/v1"
export LOCAL_FAST_API_KEY="EMPTY"
export LOCAL_FAST_MODEL="gemma3:4b"

export LOCAL_MEDIUM_API_BASE="http://127.0.0.1:11434/v1"
export LOCAL_MEDIUM_API_KEY="EMPTY"
export LOCAL_MEDIUM_MODEL="qwen3:8b"
```

Ollama's OpenAI-compatible chat endpoint supports reasoning control. The
classifier configuration in `jiuwenswarm-router.yaml` therefore includes:

```yaml
reasoning_effort: none
```

The classifier must return only one label. If the server still includes thinking
or explanatory prose, fix the local model/template configuration before the
experiment; invalid classifier output deliberately routes to `local_medium`.

Use `ollama ps` to see whether each loaded model is running on CPU, GPU, or a
mixture. Model swapping on a memory-constrained machine can add latency, so keep
the serving setup unchanged across comparable runs.

### Option B: llama.cpp

Use GGUF files appropriate for the testing machine. Start one `llama-server`
process per model and set each API alias to the model identifier expected by the
YAML:

```bash
llama-server \
  --model /models/Qwen3-0.6B-Q4_K_M.gguf \
  --alias Qwen/Qwen3-0.6B \
  --host 127.0.0.1 \
  --port 8081 \
  --ctx-size 8192

llama-server \
  --model /models/gemma-3-4b-it-Q4_K_M.gguf \
  --alias google/gemma-3-4b-it \
  --host 127.0.0.1 \
  --port 8082 \
  --ctx-size 8192

llama-server \
  --model /models/Qwen3-8B-Q4_K_M.gguf \
  --alias Qwen/Qwen3-8B \
  --host 127.0.0.1 \
  --port 8083 \
  --ctx-size 8192
```

The file names are placeholders; set them to the downloaded GGUF files. CPU/GPU
offload flags depend on the llama.cpp build and machine, so check
`llama-server --help`. Keep the API aliases aligned with the YAML model names,
or override the corresponding environment variables. If the selected
llama.cpp build/model rejects `reasoning_effort`, remove that setting and disable
thinking through its model template or server configuration instead.

Recent llama.cpp versions also support a multi-model router server, but three
explicit processes are easier to inspect for the first experiment.

### Endpoint smoke test

Before starting JiuwenSwarm, check every configured base URL:

```bash
curl -s http://127.0.0.1:8081/v1/models
curl -s http://127.0.0.1:8082/v1/models
curl -s http://127.0.0.1:8083/v1/models
```

For Ollama, use its shared endpoint instead:

```bash
curl -s http://127.0.0.1:11434/v1/models
```

Also send a small `/v1/chat/completions` request to each configured model. Check
that the returned model identifier matches the configuration and that the
classifier can produce exactly one of:

```text
SIMPLE
MEDIUM
COMPLEX
RESEARCH
REASONING
```

Do not proceed with the router run if a local endpoint is unreachable. Client
construction does not start or attest the physical location of these services;
`privacy_scope: local` is enforced by pointing it only at the local endpoints.

## Cloud configuration

Set the external provider credentials and verify each model identifier with that
provider. The YAML defaults use OpenRouter-style names, but all values are
overridable:

```bash
export CLOUD_API_BASE="https://openrouter.ai/api/v1"
export CLOUD_API_KEY="..."

export BASELINE_API_BASE="$CLOUD_API_BASE"
export BASELINE_API_KEY="$CLOUD_API_KEY"
export BASELINE_MODEL="moonshotai/kimi-k2"

export CLOUD_COMPLEX_MODEL="deepseek/deepseek-v3"
export CLOUD_RESEARCH_MODEL="deepseek/deepseek-v4"
export CLOUD_REASONING_MODEL="moonshotai/kimi-k2"
```

Use the actual model identifiers supported by the provider account. Do not put
real API keys directly in the checked-in YAML files.

## JiuwenSwarm configuration

Initialize JiuwenSwarm once:

```bash
cd workspace/jiuwenswarm
uv run --no-sync jiuwenswarm-init
```

The active configuration is normally:

```text
~/.jiuwenswarm/config/config.yaml
```

Keep the rest of that file and replace only its `models` section with one of the
three YAML examples in this directory. Restart the runtime between controlled
runs so no model clients or conversation state are reused:

```bash
uv run --no-sync jiuwenswarm-start
```

## Experiment 1: all-local baseline

Use `jiuwenswarm-local-baseline.yaml`.

- No router or classifier is involved.
- Every request goes directly to the same `local_medium` model used by the
  router, normally Qwen3 8B.
- The local endpoint must already be running.
- Experimental dollar cost is zero.

## Experiment 2: all-cloud baseline

Use `jiuwenswarm-baseline.yaml`.

- No router or classifier is involved.
- Every request goes to the most capable configured cloud model, normally the
  same model used by `cloud_reasoning`.
- Capture billed dollar cost from the external provider's usage or billing
  system.

## Experiment 3: five-level router

Use `jiuwenswarm-router.yaml`.

- The local classifier selects one of the five complexity levels.
- `SIMPLE` and `MEDIUM` use the two local answer models.
- `COMPLEX`, `RESEARCH`, and `REASONING` use their corresponding cloud models.
- Privacy is disabled by default so this experiment measures complexity routing.
- To test privacy separately, set `privacy.enabled: true`; S2 is then redacted
  before classification/cloud dispatch, and S3 or detector failure uses the
  original request with `local_medium`.
- Classifier failure uses the original request with `local_medium`.
- Cloud failure can fall back once to `local_medium` before output starts.

Router responses retain sanitized `edge_cloud_router` metadata containing the
privacy-enabled flag, privacy tier, complexity result, selected deployment/model,
and fallback reason.

## Controlled comparison

Use one fixed workload for all three experiments. Include representative simple,
medium, complex, research, and reasoning tasks. For a separate privacy-enabled
run, add synthetic S2/S3 cases. Never use real secrets as test fixtures.

For every task:

- use a fresh conversation/session;
- keep the prompt, tools, timeouts, and sampling settings identical;
- keep `temperature: 0` as configured;
- record the final answer and end-to-end latency; and
- for router runs, record the route and fallback metadata when available.

Run the experiments in this order:

1. all-local baseline;
2. all-cloud baseline; and
3. five-level router.

Compare:

- answer quality;
- end-to-end latency;
- route distribution and fallback rate; and
- externally billed cloud dollar cost.

Only the dollar amount reported by the cloud provider counts as experiment cost.
Treat local model and classifier inference as zero-cost. The router preserves
normal answer-provider usage metadata but does not calculate or aggregate cost,
and cost never affects routing decisions.

## Troubleshooting checklist

- Confirm `openjiuwen.__file__` and `agent_xrouter.__file__` point to the local
  editable checkouts.
- Confirm every local model appears in `/v1/models` under the configured name.
- Confirm the classifier emits one exact label without thinking text.
- Confirm environment variables are present in the JiuwenSwarm process.
- Restart JiuwenSwarm after replacing the `models` section.
- Use fresh sessions to avoid context leaking between experiments.
- A missing `agent-xrouter` package should produce `MODEL_SERVICE_CONFIG_ERROR`;
  reinstall the local editable package rather than copying router policy into
  agent-core.

## Serving references

- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- [Ollama CLI reference](https://docs.ollama.com/cli)
- [Ollama FAQ for context size and CPU/GPU inspection](https://docs.ollama.com/faq)
- [llama.cpp HTTP server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
