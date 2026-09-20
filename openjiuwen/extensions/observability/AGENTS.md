# observability maintenance guide

The GenAI-semantic OpenTelemetry stack: span collection, semantic
conventions, trajectory records, redaction and exporters. It is
dependency-neutral — it knows about OTel and the core callback events, never
about a specific runtime — and it is the **single** collection framework for
every runtime in this repo.

## One stack for harness and Team

`openjiuwen.harness.observability` (single agent / DeepAgent / sub-agent) and
`openjiuwen.agent_teams.observability` (Team) are thin layers on top of this
package. They own their own rails, run-root spans and lifecycle, and they
share everything below that: the provider in `runtime.py`, the demand
bookkeeping in `demand.py`, the attribute vocabulary in `semconv.py`, the
callback handler, the processors and the exporters.

So a change to collection behaviour, an attribute key or a trajectory record
lands **here**, once, and both tiers get it. Do not fork a handler, a key or a
projection into `harness/` or `agent_teams/` because one tier needs a variant —
if the two tiers genuinely differ, express it as a parameter of the shared
primitive, not as a second copy.

## `extensions/tracer_otel` is a different scenario, maintained separately

`extensions/tracer_otel` is **not** part of this stack and is not being merged
into it. It is the legacy workflow/agent tracer extension: it hangs off the
`core.session.tracer` extension-handler SPI, keeps its own historical wire
format, builds a private `TracerProvider`, and is installed via the
`tracer-otel` extra and registered explicitly by the embedding application.
Nothing inside `openjiuwen` imports it.

The relationship between the two packages is fixed:

- The dependency is one-way and narrow. `tracer_otel/semconv.py` re-exports
  exactly six standard GenAI keys from `semconv.py` here so there is one
  authoritative definition per standard key. This package imports nothing from
  `tracer_otel`, at any time.
- The two vocabularies overlap and stay separate on purpose. `tracer_otel`
  owns `openjiuwen.workflow.*` / `openjiuwen.agent.*` and its own span shape;
  this stack owns the GenAI standard keys plus the `openjiuwen.*` /
  `agentteam.*` / `deepagent.*` extensions. Neither side "converges" onto the
  other — see `tracer_otel/AGENTS.md` for why its keys are frozen.
- New harness/Team observability work belongs here, never in `tracer_otel`.
  Conversely, do not reshape anything here to accommodate `tracer_otel`'s
  consumers; its spans are read as a foreign convention.

## Scope

- `runtime.py` / `setup.py`: process-wide `TracerProvider` lifecycle and the
  facade (`init_observability`). This stack owns the **global** provider.
- `demand.py`: acquire/release bookkeeping so one runtime's shutdown never
  tears down a provider another runtime still depends on. OTel allows exactly
  one global provider per process; first initializer wins.
- `config.py`: `ObservabilityConfig` (exporter, endpoint, sampling, redaction).
- `semconv.py`: the project's attribute facade — standard GenAI keys
  re-exported from `gen_ai_semconv.py`, plus `openjiuwen.*` / `agentteam.*` /
  `deepagent.*` extensions. A project key exists only where the standard
  carries no attribute for the same fact, so readers never need a fallback
  chain.
- `gen_ai_semconv.py`: generated upstream definitions, pinned by revision.
  Replace it as one unit on upgrade; never hand-edit a key into it.
- `callback_handler.py`: LLM/tool span lifecycle over `AsyncCallbackFramework`
  events. Agent spans come from the host integration's rail.
- `span_context.py`: shared span state, context propagation and forced-close
  cleanup.
- `span_record_processor.py` / `otlp_codec.py` / `content_addressing.py`:
  non-blocking fan-out of ended spans as canonical OTLP records, with
  content-addressed sequences for the storage path.
- `trajectory_events.py` / `context_compression_handler.py`: trajectory v2
  record emission.
- `tool_outcome.py` / `error_reporting.py`: one reading of how a call ended and
  one normalization of error causes, shared by the rail and the handler so the
  two paths cannot drift.
- `redaction.py`: prompt/completion/error redaction, applied before any value
  reaches a span attribute.
- `exporters/`, `file_exporter.py`: transport and backend projection.

## Invariants

1. Backend-specific projection (the `langfuse.*` namespace included) lives
   **only** under `exporters/`, derived at export time from the canonical span.
   The collection layer stays backend-neutral.
2. Redaction runs before a prompt/completion/error value becomes a span
   attribute, on both streaming and non-streaming paths.
3. Observability is side-effect-free for the business flow: callbacks swallow
   and log their exceptions; span export never blocks the business thread
   (`BatchSpanProcessor` + the non-blocking record processor).
4. Provider lifecycle goes through `demand.py`. Never call
   `trace.set_tracer_provider()` or shut a provider down from a caller tier.
5. One carrier per fact. Where a standard GenAI key exists it is the carrier
   and no project mirror is written.

## Change checklist

- Mirrored unit tests live in `tests/unit_tests/extensions/observability/`.
- Adding an attribute: define it in `semconv.py`; extend `gen_ai_semconv.py`
  only by regenerating it against a new pinned revision.
- Changing a written attribute key is a consumer-facing break — update the
  Langfuse projection, the trajectory readers under
  `openjiuwen/agent_evolving/trajectory/`, and the swarm-side projector in the
  same change.
- Touching provider or demand logic: exercise both tiers, since a single
  process can run harness and Team observability at once.
