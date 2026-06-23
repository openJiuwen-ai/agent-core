# Agent Teams Observability Stack

Local docker-compose deployment for OpenTelemetry observability with Langfuse backend.

## Architecture

```
Application --(OTLP gRPC)--> OTel Collector --(OTLP HTTP)--> Langfuse
           localhost:4317                        localhost:3000
```

- **Application → Collector**: gRPC (port 4317) or HTTP (port 4318), no auth required
- **Collector → Langfuse**: HTTP with Basic Auth (configured in `otel-collector-config.yaml`)

## Quick Start

```bash
cd deploy/observability

# Start all services
docker-compose up -d

# Check status
docker-compose ps

# View collector logs
docker-compose logs -f otel-collector
```

Wait for all services to become healthy (~30-60s on first start).

### Access Langfuse UI

- URL: http://localhost:3000
- Default user: `jiuwenswarm` / `jiuwenswarm`
- Project keys: `pk-lf-jiuwen` / `sk-lf-jiuwen`

### Stop and Clean Up

```bash
# Stop services (keep data)
docker-compose down

# Stop and remove all data
docker-compose down -v
```

## Application Configuration

### Method 1: OTLP gRPC → Collector → Langfuse (Recommended)

Application sends traces to local Collector. Auth is handled by Collector.

```python
from openjiuwen.agent_teams.observability import (
    ObservabilityConfig,
    init_observability,
    shutdown_observability,
)

obs_config = ObservabilityConfig(
    enabled=True,
    service_name="openjiuwen-agent-teams",
    exporter="otlp_grpc",
    endpoint="http://localhost:4317",
    sample_rate=1.0,
)
init_observability(obs_config)

# Your application code here...

# Shutdown when done
shutdown_observability()
```

### Method 2: OTLP HTTP → Langfuse Direct

Application sends traces directly to Langfuse Cloud (bypasses local Collector).

```python
obs_config = ObservabilityConfig(
    enabled=True,
    service_name="my-service",
    exporter="otlp_http",
    endpoint="https://cloud.langfuse.com/api/public/otel/v1/traces",
    langfuse_public_key="pk-lf-xxx",
    langfuse_secret_key="sk-lf-xxx",
)
init_observability(obs_config)
```

### Method 3: Console Output (Debug)

```python
obs_config = ObservabilityConfig(
    enabled=True,
    exporter="console",
)
init_observability(obs_config)
# Traces printed as JSON to console
```

## Configuration Reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | True | Enable/disable observability |
| `service_name` | str | "openjiuwen-agent-teams" | OTel resource service name |
| `exporter` | str | "otlp_grpc" | Exporter type: `otlp_grpc`, `otlp_http`, `console` |
| `endpoint` | str | "http://localhost:4317" | OTLP endpoint URL |
| `sample_rate` | float | 1.0 | Sampling rate (0.0-1.0) |
| `langfuse_public_key` | str | "" | Required for direct Langfuse connection |
| `langfuse_secret_key` | str | "" | Required for direct Langfuse connection |

## Customizing Langfuse Keys

The default keys (`pk-lf-jiuwen` / `sk-lf-jiuwen`) are configured in:

1. **docker-compose.yml** - Langfuse initialization:
   ```yaml
   LANGFUSE_INIT_PROJECT_PUBLIC_KEY: pk-lf-jiuwen
   LANGFUSE_INIT_PROJECT_SECRET_KEY: sk-lf-jiuwen
   ```

2. **otel-collector-config.yaml** - Collector authentication:
   ```yaml
   exporters:
     otlphttp/langfuse:
       headers:
         Authorization: "Basic <base64(pk:sk)>"
   ```

To generate a new Base64 auth header:
```bash
echo -n "pk-lf-xxx:sk-lf-xxx" | base64
```

Or in PowerShell:
```powershell
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("pk-lf-xxx:sk-lf-xxx"))
```

## Production Notes

1. **Security**: Change `NEXTAUTH_SECRET`, `SALT`, `ENCRYPTION_KEY`, and database passwords in `docker-compose.yml` before exposing outside localhost.

2. **Sampling**: Set `sample_rate < 1.0` in production (e.g., 0.1) to reduce trace volume.

3. **Databases**: For production, use managed Postgres and ClickHouse instead of docker-compose volumes.

4. **Remove debug exporter**: In `otel-collector-config.yaml`, remove `debug` from exporters once stable:
   ```yaml
   exporters: [otlphttp/langfuse]
   ```

## Files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Service orchestration: OTel Collector, Langfuse, Postgres, ClickHouse, Redis, MinIO |
| `otel-collector-config.yaml` | Collector pipeline: receivers, processors, exporters |
