# OTEL 团队级追踪

AgentTeams 的 OpenTelemetry（OTEL）可观测性把一次团队运行转换为层级化 Trace，可导出到 Langfuse、Jaeger、Grafana、SigNoz 或其他兼容 OTLP 的后端，用于分析团队协作、模型耗时、工具调用和任务状态变化。

OTEL 追踪与 `TeamMonitor` 是两条独立通路：Monitor 面向运行中的事件消费，OTEL 面向标准化遥测后端和事后分析。

## Span 结构

典型团队 Trace 包含：

```text
team.<team_name>
├── agent.<member_name>
│   ├── llm.call
│   │   └── llm.reasoning
│   └── tool.<tool_name>
├── task.<task_id>
└── team event / message event
```

| Span | 说明 |
|------|------|
| `team.<team_name>` | 一次团队运行的根 Span，承载团队上下文和成员、消息事件 |
| `agent.<member_name>` | Leader 或 Teammate 的执行区间 |
| `llm.call` | 模型请求、响应、Token 用量和首 Token 延迟 |
| `llm.reasoning` | 模型返回的 reasoning 内容与耗时 |
| `tool.<tool_name>` | 工具调用参数、结果、错误和耗时 |
| `task.<task_id>` | 团队任务从创建到状态迁移的区间 |

## 安装依赖

团队观测依赖 OpenTelemetry SDK 和所选 exporter。使用仓库可选依赖安装：

```bash
uv sync --extra observability
```

本地调试可以使用 `console` 或 `file` exporter，不需要启动 Collector。使用 `otlp_grpc` 或 `otlp_http` 时，需要准备可访问的 OTLP 后端。

## 快速开始

必须在团队运行前调用 `init_observability()`，并在 Runner 停止后调用 `shutdown_observability()`，确保残留 Span 被关闭和刷新。

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
            inputs={"query": "分析项目风险并形成评审报告"},
            session="otel_demo_session",
        ):
            print(chunk, end="", flush=True)
    finally:
        await Runner.stop()
        shutdown_observability()

asyncio.run(main())
```

初始化后，团队装配过程会自动添加观测 Rail，并把 Runner 的 LLM、工具和 Agent 回调连接到同一个 Trace，不需要为每个成员手工注册 handler。

## Exporter 配置

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

### 控制台

```python
config = ObservabilityConfig(exporter="console")
```

适合确认 Span 是否产生，不适合长期运行。

### 本地文件

```python
config = ObservabilityConfig(
    exporter="file",
    traces_dir="./traces",
    file_retention_days=7,
)
```

文件 exporter 每天写入一个 `traces-YYYY-MM-DD.jsonl` 文件。每行是单个 Span 的 OTLP JSON 请求；同一文件可以包含多个 Trace，导入端按 `traceId` 区分。

## Langfuse 配置

通过 Langfuse OTLP 端点导出时，可以直接配置访问凭证：

```python
config = ObservabilityConfig(
    exporter="otlp_http",
    endpoint="https://your-langfuse.example.com/api/public/otel/v1/traces",
    backend="langfuse",
    langfuse_public_key="pk-lf-...",
    langfuse_secret_key="sk-lf-...",
)
```

`backend="langfuse"` 会避免同时写入语义相同的标准 `gen_ai.*` 和 `langfuse.*` 内容属性。导出到通用 OTLP 后端时可设置 `backend="otlp"`。

不要把密钥提交到配置文件；生产环境应从环境变量或密钥管理服务读取。

## 配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `enabled` | `True` | 总开关；为 `False` 时初始化为空操作 |
| `service_name` | `openjiuwen-agent-teams` | OTel Resource 的 `service.name` |
| `exporter` | `otlp_grpc` | `otlp_grpc`、`otlp_http`、`console` 或 `file` |
| `endpoint` | `http://localhost:4317` | OTLP 地址；文件 exporter 忽略该项 |
| `sample_rate` | `1.0` | 父级继承的比例采样率，范围 `0.0` 到 `1.0` |
| `redact_prompts` | `False` | 对 prompt 内容做哈希/截断处理 |
| `redact_completions` | `False` | 对 completion 内容做哈希/截断处理 |
| `attribute_value_max_length` | `40960` | 单个字符串属性的最大长度 |
| `max_attributes` | `200` | 每个 Span 的最大属性数 |
| `backend` | `langfuse` | 属性兼容模式：`langfuse` 或 `otlp` |
| `export_timeout_ms` | `5000` | exporter 关闭和刷新超时 |
| `traces_dir` | `./traces` | 文件 exporter 输出目录 |
| `file_retention_days` | `7` | 文件 exporter 的保留天数 |

## 从环境变量构建配置

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

## Trace 收尾

正常使用 Runner 时，团队生命周期会自动收尾。直接操作底层团队运行时或实现自定义 Host 时，可以显式结束指定团队的 Trace：

```python
from openjiuwen.agent_teams.observability import finalize_team_trace

finalize_team_trace("review_team")
```

进程退出前仍需调用 `shutdown_observability()`。它会注销回调、关闭未完成 Span、刷新 exporter 并关闭 Provider。不要在每轮任务后重复初始化和关闭全局 Provider；在应用启动和退出阶段各调用一次。

## 数据安全

Prompt、Completion、工具参数和工具结果可能包含用户数据或密钥。生产环境建议：

1. 默认开启 `redact_prompts` 和 `redact_completions`。
2. 在工具层对参数和结果做业务脱敏，不能只依赖字符串长度限制。
3. 将 Trace 后端视为生产数据系统，配置访问控制、传输加密和保留策略。
4. 根据业务需要降低 `sample_rate`，但安全事件审计不要只依赖采样 Trace。
5. 文件 exporter 的 `traces_dir` 不要放在公开静态目录或提交到版本库。

## 排查问题

| 现象 | 检查项 |
|------|--------|
| 没有任何 Span | 是否在构建/运行团队前调用了 `init_observability()`；`enabled` 和 `sample_rate` 是否有效 |
| 只有 Team Span，没有 LLM/Tool 子 Span | Runner 回调框架是否已启动；相关调用是否真的发生 |
| OTLP 导出失败 | exporter 类型、端口和协议是否匹配；gRPC 通常为 4317，HTTP 通常为 4318 |
| 进程结束后缺少尾部 Span | `Runner.stop()` 后是否调用了 `shutdown_observability()` |
| 属性被截断或缺失 | 检查 `attribute_value_max_length`、`max_attributes` 和脱敏设置 |
| Langfuse 内容属性重复 | 使用 `backend="langfuse"` |

可运行的完整示例位于 `tests/system_tests/agent_swarm/agent_team_observability_e2e.py`。
