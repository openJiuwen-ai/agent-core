# openJiuwen HTTP 请求组件

HTTP 请求组件是一个功能强大的工作流组件，提供类似于 n8n HTTP 请求节点的扩展 HTTP 客户端功能。它允许您在智能体工作流中向外部服务和 API 发出 HTTP 请求。

## 实现摘要

截至 2026 年 2 月 11 日，HTTP 请求组件已成功实现以下关键功能：

### 核心架构
- 按 openJiuwen 框架要求继承自 `ComponentComposable` 和 `ComponentExecutable`
- 与工作流执行引擎完全兼容
- 遵循框架中其他组件的相同模式

### 已实现功能
- **HTTP 方法**：完整支持 GET、POST、PUT、PATCH、DELETE 等
- **身份验证**：基本认证、Bearer Token、API Key 认证
- **请求体**：支持 JSON、表单数据、多部分表单、文本和二进制数据
- **响应处理**：自动检测、JSON、文本和二进制响应处理
- **高级选项**：SSL 控制、代理支持、超时配置
- **重试机制**：可配置的重试和指数退避
- **安全性**：URL 验证、SSL 验证、安全凭据处理

### 创建的文件
- `openjiuwen/core/workflow/components/http/http_request_component.py` - 主要实现
- `openjiuwen/core/workflow/components/http/__init__.py` - 模块导出
- 已更新 `openjiuwen/core/workflow/__init__.py` - 添加 HTTP 组件导出

### 测试
- 通过导入和实例化测试验证基本功能
- 通过全面的配置测试
- 通过多种场景演示示例用法

## 功能特性

### HTTP 方法
- 支持所有标准 HTTP 方法（GET、POST、PUT、PATCH、DELETE 等）
- 每个组件实例可配置请求方法

### 身份验证
- **无**：无需身份验证
- **基本认证**：用户名和密码认证
- **Bearer Token**：基于 Token 的身份验证
- **API Key**：自定义 API Key 身份验证，支持 header、query 或 body
- 可以添加更多身份验证方法（Digest、OAuth2、AWS Signature）

### 请求体选项
- **JSON**：发送 JSON 负载并自动序列化
- **表单数据**：发送 URL 编码的表单数据
- **多部分表单**：发送多部分表单数据（适用于文件上传）
- **文本**：发送纯文本数据
- **二进制**：发送二进制数据

### 响应处理
- **自动检测**：根据 Content-Type 自动检测响应格式
- **JSON**：将响应解析为 JSON
- **文本**：将响应视为纯文本
- **二进制**：将响应作为二进制数据处理
- 可配置的成功/错误状态码
- 响应过滤和属性提取

### 高级选项
- SSL 证书验证控制
- 代理支持
- 请求超时配置
- 重定向跟随控制
- 压缩设置
- 最大体长限制

### 重试机制
- 可配置的重试次数
- 可选择性地重试特定状态码（例如 429、500、502、503、504）
- 多种退避策略（固定、线性、指数）
- 可自定义的重试延迟

### 速率限制
- 可配置的请求速率限制
- 基于时间的速率限制（每秒、每分钟、每小时）

## 使用示例

### 基本 GET 请求
```python
from openjiuwen.core.workflow import (
    HTTPRequestComponent,
    HttpComponentConfig,
    HttpRequestParamConfig
)

# 为简单 GET 请求创建配置
config = HttpComponentConfig(
    request_params=HttpRequestParamConfig(
        url="https://api.example.com/data",
        method="GET",
        headers={
            "User-Agent": "openJiuwen HTTP Component",
            "Accept": "application/json"
        },
        timeout=30.0
    )
)

# 创建 HTTP 组件
http_component = HTTPRequestComponent(config=config)
```

### 带 JSON 体的 POST 请求
```python
from openjiuwen.core.workflow import HttpRequestBodyConfig, HttpContentType

config = HttpComponentConfig(
    request_params=HttpRequestParamConfig(
        url="https://api.example.com/users",
        method="POST",
        headers={"Content-Type": "application/json"},
        body=HttpRequestBodyConfig(
            content_type=HttpContentType.JSON,
            json_data={
                "name": "John Doe",
                "email": "john@example.com",
                "age": 30
            }
        ),
        response_handling=HttpResponseHandlingConfig(
            response_format="json",
            response_mode="full"
        )
    )
)

http_component = HTTPRequestComponent(config=config)
```

### 认证请求
```python
from openjiuwen.core.workflow import HttpAuthConfig, HttpAuthType

config = HttpComponentConfig(
    request_params=HttpRequestParamConfig(
        url="https://api.example.com/protected-endpoint",
        method="GET",
        authentication=HttpAuthConfig(
            type=HttpAuthType.BEARER,
            token="your-jwt-token-here"
        )
    )
)

http_component = HTTPRequestComponent(config=config)
```

### 带重试逻辑的请求
```python
from openjiuwen.core.workflow import HttpRetryConfig

config = HttpComponentConfig(
    request_params=HttpRequestParamConfig(
        url="https://api.example.com/reliable-endpoint",
        method="GET",
        retry_config=HttpRetryConfig(
            enabled=True,
            max_retries=3,
            retry_on_status_codes=[500, 502, 503, 504],
            retry_delay=2000,  # 2 秒，以毫秒为单位
            backoff_type="exponential"
        )
    )
)

http_component = HTTPRequestComponent(config=config)
```

## 与工作流集成

HTTP 请求组件遵循 openJiuwen 组件架构，可以无缝集成到工作流中：

```python
config = HttpComponentConfig(
    request_params=HttpRequestParamConfig(
        url="{{url}}",  # URL 将从名为'url'的输入中动态设置
        method="GET"
    ),
    advanced_options=HttpAdvancedOptionsConfig(ignore_ssl_issues=True)  # 测试时禁用 SSL 验证
)
http_component = HTTPRequestComponent(config=config)
# 设置工作流连接
flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
flow.set_end_comp("e", end_component, inputs_schema={"output": "${http.output}"})
flow.add_workflow_comp("http", http_component, inputs_schema={"url": "${s.query}"})

# 添加连接：start -> http -> end
flow.add_connection("s", "http")
flow.add_connection("http", "e")

# 创建会话上下文
context = create_workflow_session()

# 使用测试 URL 调用工作流
result = await flow.invoke(inputs={"query": "https://httpbin.org/get?test=value"}, session=context)
```

## 错误处理

该组件提供全面的错误处理：
- 网络超时
- 连接错误
- HTTP 错误状态码
- 无效响应格式
- 身份验证失败

错误通过 openJiuwen 错误处理系统传播，并带有适当的状态码。

## 安全考虑

- 默认启用 SSL/TLS 验证
- 支持自定义证书
- 安全凭据处理
- 通过 URL 验证防止 SSRF 攻击

## 性能考虑

- 异步请求处理
- 连接池
- 可配置超时以防止请求挂起
- 二进制响应大小限制以防止内存耗尽
