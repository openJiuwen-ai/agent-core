# tracer_otel maintenance guide

Legacy workflow/agent tracer extension: an alternative OTel integration built
on the `core.session.tracer` extension-handler SPI (`TraceExtAgentHandler` /
`TraceExtWorkflowHandler`), not on the GenAI-semantic observability stack. It
is installed via the `tracer-otel` extra and registered explicitly by the
embedding application (`init_otel_tracer` + handler registration on the tracer
registry); nothing inside `openjiuwen` imports this package.

## Scope

- `handler.py`: `OtelAgentHandler` / `OtelWorkflowHandler`. Every callback is
  try/except-guarded — OTel failures must never propagate into the business
  flow.
- `span_manager.py`: agent-span lifecycle bookkeeping for the handlers.
- `setup.py`: builds a private `TracerProvider` from `OtelTracerConfig`. It
  deliberately never calls `trace.set_tracer_provider()`, so it cannot clash
  with the global provider owned by `agent_teams.observability`.
- `config.py`: immutable `OtelTracerConfig`.
- `redaction.py`: prompt/completion redaction local to this extension.
- `semconv.py`: this package's own attribute keys (below).

## Attribute keys are defined here, independently

`semconv.py` in this package is **not** synchronized with
`extensions/observability/semconv.py`. The two stacks describe overlapping
facts with different wire formats, and that is intentional:

- GenAI keys (`gen_ai.input.messages`, `gen_ai.operation.name`, ...) are
  re-exported from `extensions.observability.semconv` so there is exactly one
  authoritative definition per standard key. These six are the only shared
  symbols; do not grow this list casually.
- Project keys (`openjiuwen.workflow.*`, `openjiuwen.agent.*`,
  `openjiuwen.invoke_id`, `openjiuwen.session_id` — note the underscore — and
  the base-span block `OJ_INVOKE_ID` … `OJ_META_DATA`) are this package's own
  vocabulary, emitted by the workflow/agent tracer spans. In particular
  `openjiuwen.session_id` here is a different key from the removed
  `openjiuwen.session.id` mirror in the observability stack; consumers (e.g.
  the swarm frontend projector) read it as a foreign convention, not as the
  canonical session carrier.

Do not rename, remove, or "converge" these keys to match
`extensions/observability`. That stack went through a hard cutover to GenAI
standard keys; this one keeps its historical wire format because its spans are
consumed as an external convention. If a key here must change, treat it as a
wire-format break for downstream readers and update them in the same change.

## Invariants

1. Handlers must stay side-effect-free for the business flow: swallow and log
   all exceptions in callbacks.
2. Never touch the global OTel state (tracer provider, propagators). All
   providers/tracers are created and bound locally.
3. Keep the dependency on `extensions.observability` limited to re-exporting
   standard GenAI key constants — no runtime imports from its handlers,
   processors, or exporters.
4. Redaction applies before any prompt/completion value reaches a span
   attribute, in both streaming and non-streaming paths.

## Change checklist

- Mirrored unit tests live in `tests/unit_tests/extensions/tracer_otel/`.
- Adding an attribute key: define it in `semconv.py` here, never import a
  project key from the observability facade.
- Changing a written attribute key is a consumer-facing break — update the
  swarm-side readers (trajectory projector `COMPATIBILITY` table) in lockstep.
