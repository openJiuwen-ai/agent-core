# IntelliRouter 智能路由

IntelliRouter 是 agent-core 中的**智能路由层**，它在 agent-core 和多个**兼容 OpenAI 接口**的 LLM 端点之间充当统一的路由调度器。通过 IntelliRouter，用户可以将多个 LLM 部署（不同模型、不同实例、不同区域）统一管理，由路由器自动选择最优部署并在故障时自动切换。

**核心能力：**

- **多端点池化** — 将多个部署（Deployment）统一纳管，对上层透明
- **智能路由策略** — 支持随机、最低延迟、标签、自适应多因子等策略
- **自动 Failover** — 某部署失败后自动重试其他部署
- **健康检查** — 定期探测部署可用性，主动剔除不健康节点
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
       ├── Deployment A (qwen-plus @ DashScope 兼容模式)
       ├── Deployment B (deepseek-v3 @ DeepSeek)
       └── Deployment C (gpt-4o @ OpenAI)
```

> **说明**
> 当前版本的 IntelliRouter 通过统一的 OpenAI 兼容接口（`{api_base}/v1/chat/completions`）访问各部署。请确保所配置的 `api_base` 暴露的是 OpenAI 兼容端点（例如 DeepSeek、OpenAI，以及 DashScope 的 compatible-mode）。

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
export DASHSCOPE_API_KEY="sk-..."       # DashScope（通义千问，使用 compatible-mode 端点）
```

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
        "api_base": "https://api.deepseek.com",  # 该部署的 OpenAI 兼容 API 地址
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
| `api_base` | string | 是 | 部署 OpenAI 兼容 API 端点地址 |
| `tpm` | int | 否 | 每分钟 Token 限额 |
| `rpm` | int | 否 | 每分钟请求限额 |
| `tags` | list[str] | 否 | 标签，用于 tag 路由 |
| `timeout` | float | 否 | 部署级超时覆盖（秒） |
| `verify_ssl` | bool | 否 | 部署级 SSL 校验覆盖 |

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

输出示例：

```
deepseek-v3: healthy
deepseek-v3-backup: unhealthy
openai-gpt-4o: healthy
```

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

A: 当前版本通过统一的 OpenAI 兼容接口（`{api_base}/v1/chat/completions`）访问部署，因此 `api_base` 必须指向 OpenAI 兼容端点（如 OpenAI、DeepSeek，或 DashScope 的 compatible-mode）。

**Q: 统一调度和指定模型有什么区别？**

A: 统一调度（model 为空 → `"*"`）从所有部署中选择，可跨模型 failover；指定模型（如 model="deepseek-v3"）仅在该模型名对应的部署池内路由，failover 范围受限于同模型的不同实例。

**Q: Router 实例何时释放？**

A: `ReliableRouter` 缓存在模块级 `_router_cache` 字典中（按配置 hash 索引），进程生命周期内不会自动释放。这保证了多 Agent 共享和状态持久。

**Q: 如何查看当前 Router 的运行状态？**

A: 通过 `router.get_stats()` 获取各部署的健康状态与路由统计（参见上文「运行状态查看」）。

## 参考

- 基础使用示例：`examples/intelli_router/intelliRouter_demo.py`
- 实现源码：`openjiuwen/core/foundation/llm/model_clients/intelli_router_model_client.py`
- 配置 Schema：`openjiuwen/core/foundation/llm/schema/config.py`
- 架构设计文档：`docs/dev/model_clients_design.md`
