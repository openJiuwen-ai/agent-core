# OTEL Team-Level Tracing

AgentTeams OpenTelemetry (OTEL) observability converts a team run into a hierarchical trace. Traces can be exported to Langfuse, Jaeger, Grafana, SigNoz, or another OTLP-compatible backend to analyze collaboration, model latency, tool calls, and task-state transitions.

OTEL tracing and `TeamMonitor` are independent data paths. Monitor provides live runtime events, while OTEL targets standard telemetry backends and post-run analysis.

## Span Structure

A typical team trace contains:

```text
team.<team_name>
├── agent.<member_name>
│   ├── llm.call
│   │   └── llm.reasoning
│   └── tool.<tool_name>
├── task.<task_id>
└── team event / message event
```

| Span | Description |
|------|-------------|
| `team.<team_name>` | Root span for a team run, including team context and member/message events |
| `agent.<member_name>` | Execution window for a Leader or Teammate |
| `llm.call` | Model request, response, token usage, and time to first token |
| `llm.reasoning` | Reasoning content and duration returned by the model |
| `tool.<tool_name>` | Tool arguments, result, error, and duration |
| `task.<task_id>` | Team task lifetime and status transitions |

## Install Dependencies

Team observability requires the OpenTelemetry SDK and the selected exporter. Install the repository's optional dependencies:

```bash
uv sync --extra observability
```

The `console` and `file` exporters work without a Collector. The `otlp_grpc` and `otlp_http` exporters require a reachable OTLP backend.

## Quick Start

Call `init_observability()` before running a team and call `shutdown_observability()` after Runner stops so all remaining spans are closed and flushed.

```python
import asyncio

from openjiuwen.agent_teams import TeamAgentSpec
from openjiuwen.agent_teams.observability import (
    ObservabilityConfig,
    init_observability,
    shutdown_observability,
)
from openjiuwen.core.runner import Runner

async def main():
    config = ObservabilityConfig(
        service_name="openjiuwen-agent-teams",
        exporter="console",
        sample_rate=1.0,
        redact_prompts=True,
        redact_completions=True,
    )
    init_observability(config)
    await Runner.start()

    try:
        spec = TeamAgentSpec.model_validate(team_config)
        async for chunk in Runner.run_agent_team_streaming(
            agent_team=spec,
            inputs={"query": "Analyze project risks and prepare a review report"},
            session="otel_demo_session",
        ):
            print(chunk, end="", flush=True)
    finally:
        await Runner.stop()
        shutdown_observability()

asyncio.run(main())
```

After initialization, team assembly automatically adds the observability Rail and connects Runner's LLM, tool, and Agent callbacks to the same trace. Per-member handler registration is not required.

## Exporter Configuration

### OTLP gRPC

```python
config = ObservabilityConfig(
    exporter="otlp_grpc",
    endpoint="http://localhost:4317",
)
```

### OTLP HTTP

```python
config = ObservabilityConfig(
    exporter="otlp_http",
    endpoint="http://localhost:4318/v1/traces",
)
```

### Console

```python
config = ObservabilityConfig(exporter="console")
```

Use this to confirm that spans are being produced, not for long-running deployments.

### Local Files

```python
config = ObservabilityConfig(
    exporter="file",
    traces_dir="./traces",
    file_retention_days=7,
)
```

The file exporter writes one `traces-YYYY-MM-DD.jsonl` file per day. Each line is a single-span OTLP JSON request. One file may contain multiple traces, which an ingestion backend separates by `traceId`.

## Langfuse Configuration

Configure credentials when exporting to a Langfuse OTLP endpoint:

```python
config = ObservabilityConfig(
    exporter="otlp_http",
    endpoint="https://your-langfuse.example.com/api/public/otel/v1/traces",
    backend="langfuse",
    langfuse_public_key="pk-lf-...",
    langfuse_secret_key="sk-lf-...",
)
```

`backend="langfuse"` avoids writing duplicate standard `gen_ai.*` and `langfuse.*` content attributes. Use `backend="otlp"` for a generic OTLP backend.

Do not commit credentials to configuration files. Load them from environment variables or a secret-management service in production.

## Configuration Reference

| Option | Default | Description |
|--------|---------|-------------|
| `enabled` | `True` | Master switch; initialization is a no-op when disabled |
| `service_name` | `openjiuwen-agent-teams` | OTel Resource `service.name` |
| `exporter` | `otlp_grpc` | `otlp_grpc`, `otlp_http`, `console`, or `file` |
| `endpoint` | `http://localhost:4317` | OTLP endpoint; ignored by the file exporter |
| `sample_rate` | `1.0` | Parent-based ratio sampling from `0.0` to `1.0` |
| `redact_prompts` | `False` | Hash and truncate prompt content |
| `redact_completions` | `False` | Hash and truncate completion content |
| `attribute_value_max_length` | `40960` | Maximum string attribute length |
| `max_attributes` | `200` | Maximum attributes per span |
| `backend` | `langfuse` | Attribute compatibility mode: `langfuse` or `otlp` |
| `export_timeout_ms` | `5000` | Exporter flush and shutdown timeout |
| `traces_dir` | `./traces` | File exporter output directory |
| `file_retention_days` | `7` | File exporter retention period |

## Build Configuration from Environment Variables

```python
import os

config = ObservabilityConfig(
    enabled=os.getenv("OTEL_ENABLED", "1").lower() not in {"0", "false"},
    service_name=os.getenv("OTEL_SERVICE_NAME", "openjiuwen-agent-teams"),
    exporter=os.getenv("OTEL_EXPORTER", "otlp_grpc"),
    endpoint=os.getenv("OTEL_ENDPOINT", "http://localhost:4317"),
    sample_rate=float(os.getenv("OTEL_SAMPLE_RATE", "1.0")),
    redact_prompts=os.getenv("OTEL_REDACT_PROMPTS", "1").lower() in {"1", "true"},
    redact_completions=os.getenv("OTEL_REDACT_COMPLETIONS", "1").lower() in {"1", "true"},
)
```

## Finalize Traces

Runner normally finalizes the team lifecycle automatically. A custom Host that operates the low-level team runtime can explicitly finalize one team trace:

```python
from openjiuwen.agent_teams.observability import finalize_team_trace

finalize_team_trace("review_team")
```

The process must still call `shutdown_observability()` before exiting. It unregisters callbacks, closes unfinished spans, flushes the exporter, and shuts down the Provider. Initialize and shut down the global Provider once per application lifecycle, not once per task round.

## Data Security

Prompts, completions, tool arguments, and tool results may contain user data or credentials. In production:

1. Enable `redact_prompts` and `redact_completions` by default.
2. Apply business-specific redaction to tool arguments and results; string-length limits are not sufficient protection.
3. Treat the trace backend as a production data system with access control, encrypted transport, and a retention policy.
4. Reduce `sample_rate` when appropriate, but do not rely solely on sampled traces for security auditing.
5. Do not place `traces_dir` under a public static directory or commit it to version control.

## Troubleshooting

| Symptom | Check |
|---------|-------|
| No spans | Confirm `init_observability()` runs before team construction; check `enabled` and `sample_rate` |
| Team span only, without LLM/tool children | Confirm Runner's callback framework is active and those calls actually occurred |
| OTLP export fails | Confirm exporter protocol and port match; gRPC commonly uses 4317 and HTTP commonly uses 4318 |
| Final spans are missing on exit | Call `shutdown_observability()` after `Runner.stop()` |
| Attributes are truncated or missing | Check `attribute_value_max_length`, `max_attributes`, and redaction settings |
| Duplicate content attributes in Langfuse | Use `backend="langfuse"` |

See `tests/system_tests/agent_swarm/agent_team_observability_e2e.py` for a complete runnable example.
