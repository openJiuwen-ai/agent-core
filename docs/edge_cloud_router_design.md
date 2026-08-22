# Inference-Only Edge–Cloud Router Design

> One agent-core model provider routes requests by complexity across two local and three cloud deployments.
> Optional privacy enforcement adds S2 redaction and S3 forced-local routing. Router inference is included;
> router training and RL are not.

---

## 1. Design decision

The router is exposed as a built-in agent-core provider:

```python
ProviderType.EdgeCloudRouter = "EdgeCloudRouter"
```

`EdgeCloudRouterModelClient` implements the normal `BaseModelClient` interface, so agents, workflows,
JiuwenSwarm, trajectories, and evaluators use it like any other model provider.

The implementation is split across two repositories:

| Owner | Responsibilities |
|---|---|
| `agent-xrouter` / `agent_xrouter` | Privacy detection and redaction, classifier prompt and output parsing, complexity policy, and `RoutePlan`. |
| agent-core | Built-in provider wiring, configuration, agent-core/request conversion, classifier/local/cloud child clients, transport, streaming, fallback, response conversion, and metadata. |

`agent-xrouter` is an in-process Python library, not a proxy or service. It imports neither agent-core nor EdgeTRL
and implements no model transport. It receives complexity inference through a small async callback supplied by
the agent-core adapter.

### Why the adapter remains built-in

Agent-core has no automatic discovery path for third-party model-client providers. A registry-only provider is
unknown until its module is imported and can fail `ModelClientConfig` validation before that happens.
`ProviderType.EdgeCloudRouter` plus a lazy branch in `_builtin_model_client()` gives deterministic validation,
construction, and JiuwenSwarm provider discovery.

This does not require the routing engine to live in agent-core. IntelliRouter uses the same useful boundary: a
built-in `BaseModelClient` adapter delegates to the optional external `intelli_router` package maintained in the
`agent-protocol` repository. IntelliRouter is a health/quota/load-balancing router and owns its provider
transport. `agent-xrouter` is different only in what it does: it owns privacy and semantic routing policy, returns
a plan, and leaves all transport to agent-core.

---

## 2. Scope

### Current scope

- A sole-purpose, separately installable router distribution exposed as the `agent_xrouter` package.
- Configurable S1/S2/S3 privacy enforcement and request-scoped S2 redaction, disabled by default.
- Five complexity levels: `SIMPLE`, `MEDIUM`, `COMPLEX`, `RESEARCH`, and `REASONING`.
- Explicit `llm` classifier and model-free `heuristic` modes; the classifier is local and may be served on CPU or GPU.
- Five fixed deployments with a different model/provider configuration for every complexity level.
- `SIMPLE` and `MEDIUM` use local deployments; `COMPLEX`, `RESEARCH`, and `REASONING` use cloud deployments.
- Non-streaming and streaming chat, tools, output parsers, provider usage fields, token IDs, and logprobs where
  supported.
- Cloud-to-local failure fallback and sanitized route metadata.
- JiuwenSwarm activation through YAML using its normal constructed-model-client validation, without a
  router-specific runtime path.

### Out of scope

- Router RL, rewards, trajectories for router training, trainers, Ray/verl changes, or model updates.
- EdgeTRL proxy/process management, routing memory, cache, dual-track storage, or speculative local execution.
- A router service, sidecar, dashboard, or dedicated router JSONL sink.
- Image, speech, or video routing.
- JiuwenSwarm UI authoring for the nested router configuration.
- KV-cache affinity through the wrapper.
- Restoring private values into cloud responses or streamed tool-call arguments.

Online bandit memory belongs in a separate `agent_xrouter.evolution` package when added. RL-based evolution may
follow later, while the fixed inference router remains usable without training dependencies.

---

## 3. Runtime flow

```text
Agent / workflow / JiuwenSwarm
              |
              v
EdgeCloudRouterModelClient                         agent-core
  1. retain the untouched request
  2. convert messages and tools to RouterRequest
              |
              v
EdgeRouterEngine                                   agent-xrouter
  3. when privacy is enabled, inspect privacy
       S3 / unknown / error -> LOCAL; skip classifier
       S2                   -> redact canonical copy
       S1                   -> safe canonical copy
     otherwise              -> treat as S1 and classify the original copy
  4. build classifier input
  5. call injected ComplexityBackend
       timeout / error / bad output -> LOCAL
  6. return RoutePlan
              |
              v
EdgeCloudRouterModelClient                         agent-core
  7. LOCAL -> local_fast/local_medium with untouched original
     CLOUD -> cloud_complex/cloud_research/cloud_reasoning with safe/redacted payload
              |
              v
AssistantMessage / AssistantMessageChunk
```

A representative package interface is:

```python
class ComplexityBackend(Protocol):
    async def classify(self, request: ClassifierRequest) -> str: ...

plan = await EdgeRouterEngine(policy).route(request, classifier=backend)
```

The classifier child owns its configured timeout and transport errors. The important package contract is that
`agent-xrouter` owns prompt construction/parsing while agent-core performs the model call.
In explicit heuristic mode the callback is omitted and classification runs in-process. An LLM failure never
silently switches to the heuristic; it still forces local.

`RoutePlan` is asymmetric:

- A local plan contains the decision but no outbound payload or matched sensitive values. The adapter uses the
  original request it retained.
- A cloud plan contains only the canonical safe/redacted payload.
- No public result contains the redaction reverse map.

Here, “local” means the operator-configured cheap/local model. When privacy is enabled, the router guarantees
that S3 and indeterminate requests never use the configured classifier or cloud answer client. The deployment
must point the local child at its intended local endpoint; v1 does not try to prove where that endpoint is hosted.

`invoke()` and `stream()` must share the same `_prepare_route()` path so their privacy behavior cannot diverge.

---

## 4. Routing, privacy, and complexity

When `privacy.enabled: true`, routing follows this table:

| Privacy | Complexity | Destination | Answer payload |
|---|---|---|---|
| S1 | SIMPLE | `local_fast` | original |
| S1 | MEDIUM | `local_medium` | original |
| S1 | COMPLEX / RESEARCH / REASONING | matching cloud deployment | safe copy |
| S2 | SIMPLE | `local_fast` | original |
| S2 | MEDIUM | `local_medium` | original |
| S2 | COMPLEX / RESEARCH / REASONING | matching cloud deployment | redacted messages and tools |
| S3 | not evaluated | `local_medium` | original; classifier skipped |
| detector/classifier failure | unknown | `local_medium` | original |

When enabled, privacy runs before complexity:

- S3 never reaches the classifier or cloud answer client.
- S2 is redacted before the classifier sees it.
- Only the local model receives original S2/S3 content.

This fixes an EdgeTRL issue: its router builds the S2 redacted copy but passes the original messages to
`ComplexityJudge.classify()`.

### Privacy rules

Privacy detection is deterministic and rule-based in v1. It does not use a privacy model or another inference
client. `privacy.enabled` defaults to `false` for complexity-only experiments. In that mode the router skips
privacy inspection, treats the request as S1, and lets complexity select a local or cloud deployment using the
original canonical request.

Port EdgeTRL's PII, credential, high-risk keyword, path, tool-name, and typed-placeholder rules as a baseline,
then harden the behavior:

1. Any detector error or unknown content forces local; it must never become S1.
2. Inspect all message roles, text parts, historical tool calls, and current tool definitions.
3. Unknown/multimodal content forces local in v1.
4. Never mutate the agent's request. Redact an independent canonical copy and keep the reverse map private and
   request-scoped.
5. Never log raw prompts, matched values, credentials, classifier prompts, reverse maps, or exception text.
6. Keep privacy behavior explicit: enabled means redaction and forced-local rules are active; disabled means
   neither rule is applied.

Tool redaction must preserve structure. Parse tool-call argument JSON, redact scalar values recursively, and
serialize valid JSON again. Preserve tool names and schema/property identifiers. Tool descriptions and
default/example values may be redacted only when their structure remains valid. If arguments cannot be parsed
or safe redaction would break the tool protocol/schema, force local.

Cloud responses retain placeholders such as `[REDACTED:PHONE_0]` in v1. Stateful restoration, particularly for
streamed text and tool arguments, is deferred.

### Complexity rules

- Use the five EdgeTRL labels and a bounded recent-conversation prompt. With privacy enabled the prompt uses
  privacy-approved content; otherwise it uses the original canonical content.
- Exclude runtime system scaffolding from complexity scoring; include only safe structural counts where useful.
- Use deterministic sampling with a small completion limit.
- Accept exactly one known label using a case-insensitive full match.
- Timeout, child failure, or invalid output forces local. Do not use EdgeTRL's heuristic escalation fallback.
- Heuristic mode is an explicit configuration, not an automatic fallback from failed LLM inference.

The adapter validates all five fixed deployments and their `local` or `cloud` privacy scope.

---

## 5. Agent-core adapter contract

Create the classifier and five answer children once when constructing the router client:

```python
classifier_client = create_model_client(classifier.client_config, classifier.request_config)
local_fast = create_model_client(...)
local_medium = create_model_client(...)
cloud_complex = create_model_client(...)
cloud_research = create_model_client(...)
cloud_reasoning = create_model_client(...)
```

### Request arguments

Keep argument behavior explicit and small:

- `model`: ignore the caller's router model name for answer dispatch. Use the model configured for the selected
  fixed deployment.
- `temperature`, `top_p`, `max_tokens`, `stop`: explicit call value, then a router-level value explicitly set by
  the user, then the selected child's default. Do not treat Pydantic's automatically populated defaults as
  explicitly configured values; use `model_fields_set` or equivalent parsed-config state.
- `tools` and `output_parser`: forward the call values to the selected answer child only.
- `timeout`: an explicit call value overrides the selected answer child's configured timeout. The classifier
  keeps its own configured timeout.
- Provider-specific request options stay with their child. Forward only a small allowlist of understood call kwargs
  to cloud; local calls may keep their original kwargs.

Request-level custom headers are not forwarded to cloud by default. Each child uses its own configured headers.
This prevents local gateway, tenant, authorization, session, and cache-affinity values from leaking.

The top-level `stream_first_chunk_timeout` is an end-to-end budget covering privacy, classification, selected
child startup, and any cloud-to-local fallback before the first emitted chunk.

### Failure behavior

- When privacy is enabled, privacy failure routes locally with the original request. Canonical-conversion,
  classifier, or unexpected `agent-xrouter` failure also routes locally.
- Cloud `invoke()` failure after the cloud child's retries: make one local attempt.
- Cloud stream failure before the first emitted chunk: make one local streaming attempt.
- A cloud stream that ends without yielding a chunk is also a pre-first-chunk failure.
- Cloud stream failure after a chunk was emitted: propagate the error; never splice two answers.
- Local failure: propagate the normal model error. Cloud is never a fallback for a local/S3 decision.

### Response behavior

Preserve the selected child's content, reasoning, tool calls, finish reason, usage fields, parser content, token
IDs, and logprobs. Add one sanitized namespace:

```json
{
  "edge_cloud_router": {
    "target": "local",
    "privacy_enabled": true,
    "privacy_tier": "S2",
    "complexity_level": "MEDIUM",
    "selected_deployment": "local_medium",
    "selected_provider": "InferenceAffinity",
    "selected_model": "Qwen3-4B",
    "route_reason": "local_complexity",
    "fallback_reason": null,
    "policy_origin": "local",
    "classifier_model": "Qwen3-0.6B"
  }
}
```

Metadata describes the endpoint that produced the answer. A successful cloud-to-local fallback therefore reports
the local provider/model plus a sanitized reason code. The router preserves the selected answer child's normal
`UsageMetadata`, but does not calculate cost, collect classifier usage, or use cost in routing decisions.

For streaming, add route metadata to the first emitted chunk and preserve it through aggregation. This requires:

- merging metadata in `AssistantMessageChunk.__add__`;
- copying accumulated metadata when the current ReAct agent builds its final `AssistantMessage`;
- copying metadata when that message is stored in context; and
- preserving it in the supported `llm_controller` aggregation path.

Add a ReAct test that checks metadata on the aggregated message and saved context.

Image, speech, and video methods must be concrete so the provider can be instantiated, but v1 raises
`MODEL_CALL_FAILED` for those operations. This is a router-specific v1 choice; existing clients are inconsistent.

### Trajectory and training behavior

The router does not invoke a judge or training loop. Standard trajectories/evaluators continue to see the answer.
Cloud answers are off-policy for the local model and usually lack the token IDs/logprobs required for online PPO.
The provider records `policy_origin` and never invents logprobs; any existing on-policy training flow must select
local-origin samples itself.

---

## 6. Configuration

Use one nested `edge_cloud_router` object in `ModelClientConfig`:

```yaml
models:
  defaults:
    - model_client_config:
        model_name: edge-cloud-router
        client_provider: EdgeCloudRouter
        edge_cloud_router:
          privacy:
            enabled: false

          complexity:
            mode: llm
            privacy_scope: local
            model_client_config:
              client_provider: OpenAI
              api_base: ${ROUTER_JUDGE_API_BASE}
              api_key: ${ROUTER_JUDGE_API_KEY:-EMPTY}
              timeout: 5
            model_request_config:
              model: Qwen3-0.6B
              temperature: 0
              max_tokens: 16

          deployments:
            local_fast:
              privacy_scope: local
              model_client_config:
                client_provider: OpenAI
                api_base: ${LOCAL_FAST_API_BASE}
                api_key: ${LOCAL_FAST_API_KEY:-EMPTY}
              model_request_config:
                model: gemma-4b

            local_medium:
              privacy_scope: local
              model_client_config:
                client_provider: OpenAI
                api_base: ${LOCAL_MEDIUM_API_BASE}
                api_key: ${LOCAL_MEDIUM_API_KEY:-EMPTY}
              model_request_config:
                model: Qwen3-8B

            cloud_complex:
              privacy_scope: cloud
              model_client_config:
                client_provider: OpenRouter
                api_base: https://openrouter.ai/api/v1
                api_key: ${CLOUD_API_KEY}
              model_request_config:
                model: deepseek/deepseek-v3

            cloud_research:
              privacy_scope: cloud
              model_client_config:
                client_provider: OpenRouter
                api_base: https://openrouter.ai/api/v1
                api_key: ${CLOUD_API_KEY}
              model_request_config:
                model: deepseek/deepseek-v4

            cloud_reasoning:
              privacy_scope: cloud
              model_client_config:
                client_provider: OpenRouter
                api_base: https://openrouter.ai/api/v1
                api_key: ${CLOUD_API_KEY}
              model_request_config:
                model: moonshotai/kimi-k2

      model_config_obj:
        temperature: 0.7
      is_default: true
```

Agent-core parses this once into private adapter configuration and passes only portable privacy/policy settings
to `agent-xrouter`. Credentials, provider headers, and agent-core config objects never cross the package boundary.
For model-free routing, replace the classifier block with `complexity: {mode: heuristic}`. In `llm` mode, the
classifier child can point to a local CPU- or GPU-backed OpenAI-compatible endpoint; the router policy is
hardware-neutral. Agent-core can call a CPU service such as a llama.cpp-compatible server, but it does not
load or manage model weights or inference processes. An optional `agent-xrouter` inference launcher/service is
deferred.
Runnable baseline and router model sections, including Ollama and llama.cpp launch examples, live in
`examples/edge_cloud_router/`.

The examples define three directly comparable experiments:

1. Local baseline: no router; every request uses `local_medium`.
2. Cloud baseline: no router; every request uses the most capable configured cloud model.
3. Router: all five fixed deployments plus the local complexity classifier.

Run the same tasks and sampling settings through all three configurations. Compare answer quality, latency, route
distribution, and fallback rate. Capture only billed cloud dollar cost from the external provider's usage or
billing system; treat the local answer models and local classifier as zero-cost for these reports.

Construction rejects:

- a missing/incompatible `agent-xrouter` package, with `MODEL_SERVICE_CONFIG_ERROR` and install guidance;
- a router child configured as `EdgeCloudRouter`;
- missing classifier configuration in `llm` mode;
- a classifier with a privacy scope other than `local`;
- a missing fixed deployment or deployment model name;
- a `local_*` deployment without `privacy_scope: local` or a `cloud_*` deployment without
  `privacy_scope: cloud`; and
- unknown complexity modes or labels.

JiuwenSwarm already passes nested model configuration and resolves nested environment variables. Its generic
model-usability guard accepts any model whose agent-core client was constructed successfully while still rejecting
documentation-placeholder endpoints. `EdgeCloudRouter` does not require provider-specific runtime handling. Its
current model settings UI cannot author this nested structure, so UI configuration is deferred.

---

## 7. Code structure

```text
agent-xrouter/                                  separate repository/distribution
├── pyproject.toml                           distribution: agent-xrouter
├── src/agent_xrouter/
│   ├── __init__.py                          public API
│   ├── engine.py                            privacy -> callback -> RoutePlan
│   ├── models.py                            portable requests/config/results/enums
│   ├── request.py                           validation and immutable copies
│   ├── privacy.py                           detection and structured redaction
│   ├── complexity.py                        prompt, parser, ComplexityBackend
│   ├── policy.py                            pure routing decision
│   └── evolution/                           optional bandit memory (added separately)
└── tests/
    ├── test_engine.py
    ├── test_request.py
    ├── test_privacy.py
    ├── test_complexity.py
    └── test_policy.py

agent-core/
├── openjiuwen/core/foundation/llm/
│   ├── model_clients/
│   │   ├── __init__.py                      lazy built-in factory branch
│   │   └── edge_cloud_router_model_client.py adapter, config, children, and dispatch
│   └── schema/
│       ├── config.py                        ProviderType.EdgeCloudRouter
│       └── message_chunk.py                 metadata aggregation
├── openjiuwen/core/single_agent/agents/
│   └── react_agent.py                       preserve metadata after aggregation/context save
├── openjiuwen/core/application/llm_agent/
│   └── llm_controller.py                    preserve metadata after aggregation
└── tests/unit_tests/core/foundation/llm/
    ├── test_edge_cloud_router_model_client.py provider/config/conversion/dispatch tests
    ├── test_message_chunk.py                existing; add metadata regression
    └── test_model_client_config.py          existing; add enum/config cases

jiuwenswarm/
├── jiuwenswarm/server/runtime/agent_adapter/interface_deep.py
│                                               use generic constructed-client validation
└── tests/unit_tests/agentserver/               focused compatibility test
```

`agent-xrouter` exports only `EdgeRouterEngine`, `ComplexityBackend`, canonical request/config types, and typed
results. Internal patterns and helpers remain private. Agent-core requires a compatible installed package and
must not contain a second policy implementation. The agent-core adapter intentionally stays in one provider file,
matching the existing `*_model_client.py` layout; the reusable policy remains modular in `agent-xrouter`.

---

## 8. EdgeTRL reuse boundary

EdgeTRL is a source and test reference, not a runtime dependency.

| Disposition | Land in | What |
|---|---|---|
| Direct port | `agent-xrouter` | `ComplexityLevel`; five labels; PII/credential/high-risk patterns; tool/path lists; placeholder format; useful fixtures and decision cases. |
| Adapt | `agent-xrouter` | `PrivacyDetector.check()`, traversal, tool inspection, rule engine, redaction, classifier prompt/parser, bounded preview, and `Router._make_decision()`. |
| Reimplement | `agent-xrouter` | Portable request/result/config types, `EdgeRouterEngine`, `ComplexityBackend`, conservative normalization, and safe error/logging behavior. |
| Reimplement | agent-core | `EdgeCloudRouterModelClient`, nested config, conversions, classifier backend, child construction, invoke/stream fallback, argument/header isolation, response metadata, and aggregation fixes. |
| Do not port | neither | `CloudClient`, proxy/OPD, routing memory, dual-track context, side turns, heuristic failure escalation, cache/training fields, trainers, rewards, and dashboards. |

Important adaptations:

- Detector failure becomes indeterminate/local instead of S1.
- Scan system messages and current tool definitions, not only conversational text/history.
- Redact S2 before invoking the classifier callback.
- Parse classifier output with a strict full match instead of searching arbitrary prose.
- Replace `BLOCK` with a local S3 plan.
- Parse/redact/reserialize tool argument JSON; never copy EdgeTRL's raw string replacement.

---

## 9. Validation contracts

### `agent-xrouter`

- Imports and runs without agent-core, EdgeTRL, or answer-model transport.
- Covers every S1/S2/S3 and complexity decision.
- Covers privacy disabled by default and explicit privacy enablement.
- Covers explicit `llm` and `heuristic` modes without automatic LLM-to-heuristic fallback.
- S2 classifier/cloud payload contains placeholders and no matched raw values.
- S3 and privacy failures never call the classifier.
- Unknown content, invalid tool JSON, callback errors, timeouts, and bad labels force local.
- Concurrent requests do not share redaction state.
- Local plans contain no payload; cloud plans contain only the safe/redacted payload.
- Logs/results contain no raw PII, credentials, reverse map, prompts, or exception text.

### Agent-core and integration

- Provider enum, lazy construction, optional-package error, nested config, and recursion validation.
- Correct argument precedence, selected deployment/model, headers, and cloud-kwargs allowlisting for each child.
- Original requests are unchanged; classifier/cloud receive only allowed canonical payloads.
- Child response fields and truthful fallback metadata are preserved for `invoke()` and `stream()`.
- Empty/pre-first-chunk cloud streams fall back; post-chunk failures propagate.
- Metadata survives chunk addition, ReAct conversion, and context save.
- ReAct aggregated and saved messages preserve sanitized route metadata; normal token usage metadata remains
  provider-agnostic.
- Each complexity level reaches its fixed deployment. With privacy enabled, S2 reaches cloud only redacted and
  S3 reaches `local_medium` only.
- No router trainer, reward judge, proxy, sidecar, Ray actor, or EdgeTRL process starts.

---

## 10. Deferred

- Stateful restoration of S2 values in cloud responses/tool calls.
- Session-aware cloud context after an earlier S3 message.
- KV-cache release/affinity through nested providers.
- Routing caches, budgets, and cost policies.
- JiuwenSwarm UI authoring.
- Online bandit and outcome-memory evolution (`agent_xrouter.evolution`).
- RL-based router evolution and training.
- An optional small local privacy model; v1 privacy detection remains rule-based.
- An optional `agent-xrouter` inference launcher/service for local models. V1 instead consumes operator-managed
  OpenAI-compatible endpoints such as vLLM, Ollama, or llama.cpp.
