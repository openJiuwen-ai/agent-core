# IntelliRouter 智能路由

IntelliRouter 是 agent-core 中的**智能路由层**，它在 agent-core 和多个 LLM 端点之间充当统一的路由调度器。通过 IntelliRouter，用户可以将多个 LLM 部署（不同模型、不同厂商、不同实例、不同区域）统一管理，由路由器自动选择最优部署并在故障时自动切换。每个部署通过 **provider 适配器**对接对应厂商的原生协议，因此不再要求所有端点都是 OpenAI 兼容接口。

**核心能力：**

- **多端点池化** — 将多个部署（Deployment）统一纳管，对上层透明
- **多 provider 适配** — 通过 provider 适配器对接 OpenAI、DeepSeek、智谱、Anthropic、DashScope 等厂商的原生协议
- **智能路由策略** — 支持随机、最低延迟、标签、自适应多因子等策略
- **自动 Failover** — 某部署失败后自动重试其他部署
- **健康检查** — 定期探测部署可用性，主动剔除不健康节点
- **可观测性** — 路由事件日志、指标采集、Web/Prometheus 看板
- **多模态生成** — 通过 DashScope provider 支持文生图、语音合成、视频生成
- **运行状态查询** — 实时查看各部署的健康状态与路由统计

**架构示意：**

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
ReliableRouter (intelli_router 包)
       │
       ├── Deployment A (qwen-plus    @ DashScope, provider="dashscope")
       ├── Deployment B (deepseek-chat @ DeepSeek,  provider="deepseek")
       └── Deployment C (gpt-4o        @ OpenAI,    provider="openai")
```

> **说明**
> 每个部署通过 `provider` 字段指定厂商适配器，由适配器负责拼接正确的 API 路径并完成请求/响应协议转换（例如 OpenAI 用 `{api_base}/v1/chat/completions`、DeepSeek 用 `{api_base}/chat/completions`、智谱用 `{api_base}/api/paas/v4/chat/completions`、Anthropic 用 `{api_base}/v1/messages`）。`api_base` 只需填写厂商的根地址，无需自行拼接路径。当前内置 provider：`openai`、`deepseek`、`zhipu`、`anthropic`、`dashscope`、`siliconflow`、`google-gemini`、`aws-bedrock`（缺省为 `openai`）。

## 安装与环境准备

IntelliRouter 是 agent-core 的**可选依赖**，需要单独安装：

```bash
pip install intelli-router
```

验证安装是否成功：

```python
from intelli_router import ReliableRouter, Deployment
print("intelli-router 安装成功")
```

> **说明**
> 如果未安装 `intelli-router` 包，agent-core 仍可正常加载，但创建 IntelliRouter 客户端时会抛出 `MODEL_SERVICE_CONFIG_ERROR` 错误并提示安装。

**环境变量准备（按需设置）：**

```bash
export OPENAI_API_KEY="sk-..."          # OpenAI
export DEEPSEEK_API_KEY="sk-..."        # DeepSeek
export DASHSCOPE_API_KEY="sk-..."       # DashScope（通义千问）
export ZHIPU_API_KEY="..."              # 智谱 AI
export ANTHROPIC_API_KEY="sk-ant-..."   # Anthropic
```

> **说明**
> 各厂商的 API 路径差异由 provider 适配器内部处理（例如 DashScope 自动拼接 compatible-mode 路径），`api_base` 只需填厂商根地址。多模态生成另需 `pip install dashscope`，Prometheus 导出另需 `pip install prometheus-client`。

## 配置方式

### 基础配置

使用 `ModelClientConfig` 创建 IntelliRouter 客户端配置：

```python
from openjiuwen.core.foundation.llm import ModelClientConfig

model_client_config = ModelClientConfig(
    client_provider="intelli_router",    # 指定使用 IntelliRouter
    api_key="placeholder",               # schema 要求，IntelliRouter 不实际使用
    api_base="http://placeholder",       # schema 要求，IntelliRouter 不实际使用
    verify_ssl=False,                    # 各部署的默认 SSL 设置
    # ... IntelliRouter 专属配置见下文
)
```

> **说明**
> `api_key` 和 `api_base` 是 `ModelClientConfig` schema 的必填字段，但 IntelliRouter 不使用它们（每个部署有独立的 key/base）。填写 `"placeholder"` 即可。

### Deployment 列表

`intelli_router_deployments` 是 IntelliRouter 最核心的配置，定义了所有可用的 LLM 部署：

```python
intelli_router_deployments = [
    {
        "id": "deepseek-v3",                     # 唯一部署标识
        "model_name": "deepseek-v3",             # 该部署提供的模型名
        "api_key": "sk-xxx",                     # 该部署的 API Key
        "api_base": "https://api.deepseek.com",  # 该部署的厂商根地址
        "provider": "deepseek",                  # provider 适配器（缺省 "openai"）
        "tpm": 100000,                           # 每分钟 Token 限额
        "rpm": 60,                               # 每分钟请求限额
        "tags": ["primary", "fast"],             # 标签（用于 tag 路由）
        "timeout": 30.0,                         # 该部署的超时时间（秒）
    },
    {
        "id": "openai-gpt-4o",
        "model_name": "gpt-4o",
        "api_key": "sk-yyy",
        "api_base": "https://api.openai.com",
        "provider": "openai",
        "tpm": 100000,
        "rpm": 60,
        "tags": ["powerful"],
        "timeout": 30.0,
    },
]
```

**Deployment 字段说明：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | 否 | 唯一部署标识符（缺省自动生成） |
| `model_name` | string | 是 | 该部署提供的模型名称 |
| `api_key` | string | 是 | 部署专用 API Key |
| `api_base` | string | 是 | 厂商 API 根地址（路径由 provider 适配器拼接） |
| `provider` | string | 否 | provider 适配器名（缺省 `"openai"`，见下方列表） |
| `tpm` | int | 否 | 每分钟 Token 限额（缺省 `None`，不限制） |
| `rpm` | int | 否 | 每分钟请求限额（缺省 `None`，不限制） |
| `tags` | list[str] | 否 | 标签，用于 tag 路由 |
| `timeout` | float | 否 | 部署级超时覆盖（秒，缺省 `None`，回退到全局超时） |
| `verify_ssl` | bool | 否 | 部署级 SSL 校验覆盖 |

**内置 provider：** `openai`、`deepseek`、`zhipu`、`anthropic`、`dashscope`、`siliconflow`、`google-gemini`、`aws-bedrock`。适配器负责按厂商协议拼接 API 路径、转换请求/响应格式，因此异构厂商可以混入同一个部署池。

### 路由策略

通过 `intelli_router_strategy` 指定路由策略：

| 策略 | 说明 | 适用场景 |
|------|------|----------|
| `simple-shuffle` | 随机选择部署（默认） | 开发测试、等权部署 |
| `lowest-latency` | 选择最近延迟最低的部署 | 延迟敏感的生产场景 |
| `tag-based` | 按标签筛选部署后路由 | 按能力/用途分组的部署 |
| `token-aware` | 优先选择 Token 余量充足的部署 | 受 TPM 配额约束的场景 |
| `rate-limit-aware` | 优先选择 RPM 余量充足的部署 | 受 RPM 配额约束的场景 |
| `adaptive` | 自适应多因子评分 | 异构部署的生产环境 |

**adaptive 策略参数** (`intelli_router_strategy_kwargs`)：

```python
intelli_router_strategy_kwargs={
    "token_threshold": 1000,      # Token 使用量阈值
    "rpm_threshold": 10,          # RPM 使用量阈值
    "exploration_ratio": 0.1,     # 探索率（10% 请求随机路由以收集数据）
    "w_health": 1.0,              # 健康权重
    "w_token": 0.5,               # Token 余量权重
    "w_rpm": 0.3,                 # RPM 余量权重
    "w_latency": 0.2,             # 延迟权重
}
```

### 重试、超时与健康检查

```python
model_client_config = ModelClientConfig(
    client_provider="intelli_router",
    api_key="placeholder",
    api_base="http://placeholder",
    intelli_router_deployments=intelli_router_deployments,
    intelli_router_strategy="adaptive",
    intelli_router_num_retries=3,                    # 最大重试次数（默认 3）
    intelli_router_timeout=30.0,                     # 全局请求超时（默认 30s）
    intelli_router_enable_health_check=True,         # 开启定期健康检查
    intelli_router_health_check_interval=300.0,      # 健康检查间隔（默认 300s）
    intelli_router_strategy_kwargs={...},
)
```

### 可观测性配置

```python
model_client_config = ModelClientConfig(
    client_provider="intelli_router",
    api_key="placeholder",
    api_base="http://placeholder",
    intelli_router_deployments=intelli_router_deployments,
    intelli_router_enable_observability=True,        # 开启可观测性（默认 False）
    intelli_router_web_dashboard_port=9090,          # Web 看板端口（默认 0，关闭）
)
```

| 配置项 | 类型 | 默认 | 说明 |
|--------|------|------|------|
| `intelli_router_enable_observability` | bool | `False` | 开启后挂载 `EventBus`，注册 `LoggingHook`（路由事件日志）与 `MetricsCollector`（指标采集） |
| `intelli_router_web_dashboard_port` | int | `0` | 大于 0 时启动 `MetricsWebServer` 实时 Web 看板；**需同时开启 `enable_observability`**，否则忽略并告警 |

> **说明**
> Web 看板由模块级 `_web_servers` 跟踪，并通过 `atexit` 在进程退出时统一关闭。若 `intelli_router` 缺少 observability 子模块，开启该选项不会报错，仅打印告警并跳过。详细用法见下文「可观测性」。

### 完整配置示例

```python
import os
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig

DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

# 定义部署列表
deployments = [
    {
        "id": "deepseek-v3",
        "model_name": "deepseek-v3",
        "api_key": DEEPSEEK_KEY,
        "api_base": "https://api.deepseek.com",
        "provider": "deepseek",
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
        "provider": "deepseek",
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
        "provider": "openai",
        "tpm": 100000,
        "rpm": 60,
        "tags": ["powerful"],
        "timeout": 30.0,
    },
]

# 创建 IntelliRouter 客户端配置
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
    intelli_router_enable_observability=True,
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

# 创建请求配置
model_config = ModelRequestConfig(temperature=0.7, top_p=0.9)
```

## 使用模式

### 统一调度（跨部署 Failover）

当 `ModelRequestConfig` 不指定 `model`（或 model 为空字符串）时，IntelliRouter 会将 model 设为 `"*"`，从所有部署中选择最优的。

```python
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig

# model 为空 → 统一调度模式
model_config = ModelRequestConfig(temperature=0.7)

model = Model(
    model_client_config=model_client_config,
    model_config=model_config,
)

# Router 会自动从所有 deployment 中选择最佳的
# 如果选中的 deployment 失败，自动切换到其他可用 deployment
result = await model.invoke(messages=[
    {"role": "user", "content": "你好"}
])
```

**行为说明：**
- Router 从所有 deployment 中按策略选择最佳的
- 如果选中的部署失败（超时、网络错误、API 错误），自动重试其他部署
- Failover 可跨模型（从 deepseek-v3 切到 gpt-4o）、跨实例

### 指定模型路由

当 `ModelRequestConfig` 指定了具体的 `model` 名称时，Router 仅在该模型的部署池内路由：

```python
# 仅在 deepseek-v3 的部署中路由
model_config = ModelRequestConfig(model="deepseek-v3", temperature=0.7)

model = Model(
    model_client_config=model_client_config,
    model_config=model_config,
)

result = await model.invoke(messages=[
    {"role": "user", "content": "你好"}
])
```

> **说明**
> 指定模型路由与传统的单 Provider 使用方式兼容，适合需要确定性模型选择的场景。

### 多 Agent 共享 Router

IntelliRouter 采用 **config hash 缓存机制**：使用相同 `ModelClientConfig` 的多个 Agent 会自动共享同一个 `ReliableRouter` 实例。

```python
from openjiuwen.core.single_agent.agents import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent import AgentCard

# Agent A 和 Agent B 使用相同的 model_client_config
agent_a_config = ReActAgentConfig(
    model_client_config=model_client_config,
    model_config_obj=ModelRequestConfig(temperature=0.7),
)

agent_b_config = ReActAgentConfig(
    model_client_config=model_client_config,  # 同一个配置对象
    model_config_obj=ModelRequestConfig(temperature=0.5),
)

agent_a = ReActAgent(card=AgentCard(id="agent_a", name="Agent A")).configure(agent_a_config)
agent_b = ReActAgent(card=AgentCard(id="agent_b", name="Agent B")).configure(agent_b_config)

# agent_a 和 agent_b 底层共享同一个 ReliableRouter 实例
# 好处：
#   1. Agent A 发现某部署不可用后，Agent B 也会避开该部署
#   2. 健康状态在所有 Agent 间共享
#   3. 减少内存和连接开销
```

### 在 Workflow 中使用

IntelliRouter 可以在 Workflow 的 `LLMComponent` 中使用：

```python
from openjiuwen.core.workflow import (
    Workflow, WorkflowCard, Start, End,
    LLMComponent, LLMCompConfig, generate_workflow_key,
)
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.legacy import WorkflowAgentConfig
from openjiuwen.core.application.workflow_agent import WorkflowAgent

# 定义 Workflow
workflow_card = WorkflowCard(
    id="my_workflow", name="my_workflow", version="1.0",
    description="使用 IntelliRouter 的工作流",
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
    model_config=ModelRequestConfig(),  # 统一调度
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

# 注册并运行
Runner.resource_mgr.add_workflow(
    WorkflowCard(id=generate_workflow_key(flow.card.id, flow.card.version)),
    lambda: flow,
)
agent = WorkflowAgent(WorkflowAgentConfig(
    id="my_agent", version="0.1.0", description="IntelliRouter workflow agent",
))
agent.add_workflows([flow])

result = await Runner.run_agent(agent, {"query": "你好，介绍一下自己"})
```

### 流式调用

IntelliRouter 透明支持流式调用，用法与普通 Model 一致：

```python
model = Model(
    model_client_config=model_client_config,
    model_config=ModelRequestConfig(),
)

async for chunk in model.stream(messages=[{"role": "user", "content": "讲个故事"}]):
    print(chunk.content, end="", flush=True)
```

### 多模态生成（DashScope）

IntelliRouter 支持文生图、语音合成、视频生成，目前**仅 `provider="dashscope"` 的部署可用**（底层走 DashScope 专有 SDK，而非聊天的统一路由路径）。需要额外安装 `dashscope` 包：

```bash
pip install dashscope
```

```python
# 部署需指定 provider="dashscope"，并配置 DashScope api_key / api_base
model = Model(
    model_client_config=model_client_config,
    model_config=ModelRequestConfig(),
)

# 文生图
image_resp = await model.generate_image(
    messages=[{"role": "user", "content": "一只在草地上奔跑的柴犬"}],
    model="qwen-image-max",
    size="1664*928",
)
print(image_resp.images)        # 图片 URL 列表

# 语音合成
audio_resp = await model.generate_speech(
    messages=[{"role": "user", "content": "你好，欢迎使用智能路由"}],
    model="cosyvoice-v1",
    voice="Cherry",
)
print(audio_resp.audio_url)

# 视频生成
video_resp = await model.generate_video(
    messages=[{"role": "user", "content": "海浪拍打礁石的慢镜头"}],
    model="wan2.6-t2v",
)
print(video_resp.video_url)
```

> **说明**
> 这些生成 API 通过部署的 `provider` 解析适配厂商；当部署的 provider 不在支持列表（当前仅 `dashscope` 支持 image/speech/video）时，会抛出 `NotImplementedError`。每个方法要求 `messages` 恰好包含一条消息。

## 运行状态查看

`ReliableRouter` 提供 `get_stats()` 方法，可查看各部署的实时健康状态与路由统计：

```python
from openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client import _router_cache

# 获取 router 实例
router = next(iter(_router_cache.values()))

router_stats = router.get_stats()

# 查看各部署的健康状态
for dep_id, status in router_stats.get("deployment_status", {}).items():
    print(f"{dep_id}: {status}")
```

输出示例（状态取值为 `healthy` 或 `cooldown`，冷却中的部署会被暂时跳过）：

```
deepseek-v3: healthy
deepseek-v3-backup: cooldown
openai-gpt-4o: healthy
```

`get_stats()` 还返回 `total_deployments`、`model_list`、`consecutive_failures` 及各部署的 `latency_stats`（平均延迟、累计 token、请求数）。

## 可观测性

开启 `intelli_router_enable_observability=True` 后，Router 会挂载一个 `EventBus`，并注册两个内置处理器：

- **`LoggingHook`** — 将路由事件（选择、成功、重试、failover、耗尽等）输出为结构化日志
- **`MetricsCollector`** — 在内存中聚合请求数、成功/失败/重试次数、延迟分布、token 消耗，并支持按模型/部署/错误类型分组

### 读取指标

```python
from openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client import _router_cache

router = next(iter(_router_cache.values()))

# 从 event_bus 中取出 MetricsCollector
collector = next(
    (h for h in router.event_bus.handlers if h.__class__.__name__ == "MetricsCollector"),
    None,
)
if collector:
    stats = collector.get_stats()
    print(stats["total_requests"], stats["successful"], stats["failed"], stats["retries"])
    print(stats["latency"])        # avg / min / max / count
    print(stats["tokens"])         # prompt / completion / total
    print(stats["by_model"])       # 按模型统计
    print(stats["by_deployment"])  # 按部署统计
    print(stats["errors_by_type"]) # 按错误类型统计
```

### Web 看板

设置 `intelli_router_web_dashboard_port`（需同时开启 observability）即可启动实时 Web 看板：

```python
model_client_config = ModelClientConfig(
    client_provider="intelli_router",
    api_key="placeholder",
    api_base="http://placeholder",
    intelli_router_deployments=deployments,
    intelli_router_enable_observability=True,
    intelli_router_web_dashboard_port=9090,   # 浏览器打开 http://localhost:9090
)
```

看板基于 Python 标准库 `http.server`，零额外依赖，进程退出时由 `atexit` 自动关闭。

### Prometheus 导出

`MetricsCollector` 可暴露 Prometheus 指标端点（需安装 `prometheus-client`）：

```python
collector.expose_prometheus(port=9092)   # http://localhost:9092/metrics
```

> **说明**
> 可观测性子模块（`EventBus`/`LoggingHook`/`MetricsCollector`/`MetricsWebServer`）随 `intelli_router` 包提供。如包版本不含这些模块，开启相关选项时会打印告警并跳过，不影响路由本身。完整示例见 `examples/intelli_router/observability_demo.py`。

## 最佳实践

### 部署配置

- **至少 2 个以上部署**确保冗余，建议 3+ 以获得更好的可用性
- **按能力分配 tag**：`fast`（快速响应）、`powerful`（高质量）、`backup`（备用）
- **设置合理 timeout**：快速模型 10-30s，推理模型 60-120s
- **设置准确的 tpm/rpm**：匹配厂商实际配额，帮助 adaptive 策略做出正确决策

### 策略选择

| 场景 | 推荐策略 | 理由 |
|------|----------|------|
| 开发/测试 | `simple-shuffle` | 简单，覆盖所有部署 |
| 延迟敏感（同模型多实例） | `lowest-latency` | 最快响应 |
| 受配额约束 | `token-aware` / `rate-limit-aware` | 优先选择余量充足的部署 |
| 生产环境（异构部署） | `adaptive` | 综合考量健康、负载、延迟 |

### 生产环境建议

```python
model_client_config = ModelClientConfig(
    client_provider="intelli_router",
    api_key="placeholder",
    api_base="http://placeholder",
    verify_ssl=True,                              # 生产环境开启 SSL
    intelli_router_deployments=deployments,
    intelli_router_strategy="adaptive",
    intelli_router_num_retries=3,
    intelli_router_timeout=30.0,
    intelli_router_enable_health_check=True,       # 必须开启
    intelli_router_health_check_interval=60.0,     # 缩短检查间隔
    intelli_router_strategy_kwargs={
        "token_threshold": 1000,
        "rpm_threshold": 10,
        "exploration_ratio": 0.05,                 # 生产环境降低探索率
    },
)
```

### 安全注意事项

- 每个部署使用独立的 `api_key`，便于独立轮换和权限控制
- 生产环境设置 `verify_ssl=True`
- 不要在代码中硬编码 API Key，使用环境变量或密钥管理服务

## 常见问题

**Q: `intelli-router` 包未安装会怎样？**

A: agent-core 模块可正常加载（import 不会失败），但在实际创建 IntelliRouter 客户端时会抛出 `MODEL_SERVICE_CONFIG_ERROR` 错误，并提示 `pip install intelli-router`。

**Q: `api_key` 和 `api_base` 为什么要填 placeholder？**

A: `ModelClientConfig` 的 Pydantic schema 要求这两个字段为必填。IntelliRouter 客户端重写了 `_validate_config()` 为空操作，因此这两个值不会被实际使用。每个部署通过自己的 `api_key` 和 `api_base` 独立连接。

**Q: 各部署的 `api_base` 有什么要求？**

A: `api_base` 只需填写厂商的根地址，由部署的 `provider` 适配器负责拼接正确的 API 路径并完成协议转换（OpenAI → `/v1/chat/completions`、DeepSeek → `/chat/completions`、智谱 → `/api/paas/v4/chat/completions`、Anthropic → `/v1/messages` 等）。因此不再要求所有端点都是 OpenAI 兼容接口，异构厂商可混入同一部署池。

**Q: 支持哪些 provider？怎么指定？**

A: 在每个 deployment 中通过 `provider` 字段指定（缺省 `"openai"`）。当前内置 `openai`、`deepseek`、`zhipu`、`anthropic`、`dashscope`、`siliconflow`、`google-gemini`、`aws-bedrock`。指定未知 provider 会在创建 Router 时报错并列出受支持的取值。

**Q: IntelliRouter 支持文生图/语音/视频吗？**

A: 支持，但目前仅 `provider="dashscope"` 的部署可用（底层走 DashScope 专有 SDK），需额外 `pip install dashscope`。其他 provider 调用生成类 API 会抛出 `NotImplementedError`。详见上文「多模态生成（DashScope）」。

**Q: 怎么开启可观测性 / 看路由指标？**

A: 设置 `intelli_router_enable_observability=True`，Router 会注册 `LoggingHook`（事件日志）与 `MetricsCollector`（指标）。再设置 `intelli_router_web_dashboard_port` 可启动 Web 看板，`MetricsCollector.expose_prometheus()` 可暴露 Prometheus 端点。详见「可观测性」章节。

**Q: 统一调度和指定模型有什么区别？**

A: 统一调度（model 为空 → `"*"`）从所有部署中选择，可跨模型 failover；指定模型（如 model="deepseek-v3"）仅在该模型名对应的部署池内路由，failover 范围受限于同模型的不同实例。

**Q: Router 实例何时释放？**

A: `ReliableRouter` 缓存在模块级 `_router_cache` 字典中（按配置 hash 索引），进程生命周期内不会自动释放。这保证了多 Agent 共享和状态持久。

**Q: 如何查看当前 Router 的运行状态？**

A: 通过 `router.get_stats()` 获取各部署的健康状态与路由统计（参见上文「运行状态查看」）。

## 参考

- 基础使用示例：`examples/intelli_router/intelliRouter_demo.py`
- 可观测性示例：`examples/intelli_router/observability_demo.py`
- 实现源码：`openjiuwen/core/foundation/llm/model_clients/intelli_router_model_client.py`
- 配置 Schema：`openjiuwen/core/foundation/llm/schema/config.py`
- 架构设计文档：`docs/dev/model_clients_design.md`
