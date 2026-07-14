# Agent Teams Observability Stack

Local docker-compose deployment for the OpenTelemetry observability subsystem
shipped under `openjiuwen/agent_teams/observability/`.

## What's in here

- `docker-compose.yml` — OTel Collector + Langfuse (Postgres + ClickHouse).
- `otel-collector-config.yaml` — Collector pipeline definition.

## Quick start

```bash
cd deploy/observability
docker-compose up -d
```

After the stack is healthy:

- Langfuse UI: http://localhost:3000 (sign up on first visit, then create a project).
- OTLP gRPC endpoint for the application: `http://localhost:4317`.
- OTLP HTTP endpoint: `http://localhost:4318`.

Point the application at the collector via `ObservabilityConfig`:

```python
from openjiuwen.agent_teams.observability import (
    ObservabilityConfig,
    init_observability,
)

init_observability(
    ObservabilityConfig(
        service_name="my-service",
        exporter="otlp_grpc",
        endpoint="http://localhost:4317",
    ),
)
```

## Backend choice

Default: **Langfuse**. Reasons documented in
`/Users/alan/.claude/plans/opentelemetry-replicated-quilt.md` (section D2).
Replace `langfuse-server` with Phoenix / Jaeger / Tempo by editing
`otel-collector-config.yaml`'s `exporters` block and pointing the pipeline
at the new exporter.

## Production notes

- Replace `NEXTAUTH_SECRET`, `SALT`, and the database password before
  exposing the stack outside localhost.
- Set `ObservabilityConfig.sample_rate` < 1.0 in production
  (e.g. 0.1) to bound trace volume.
- Migrate Postgres / ClickHouse to managed services for any non-toy
  deployment — running stateful databases in docker-compose is fine for
  evaluation only.
