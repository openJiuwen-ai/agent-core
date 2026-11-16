---
id: B_20261116_1708_symphony-temperature-fallback
title: Symphony 构图在模型拒绝温度参数后恢复
type: B
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
modules:
  - openjiuwen/core/foundation/llm
files:
  - openjiuwen/core/foundation/llm/request_context.py
  - openjiuwen/core/foundation/llm/model_clients/anthropic_model_client.py
  - openjiuwen/core/foundation/llm/model_clients/openai_model_client.py
  - openjiuwen/core/foundation/llm/model_clients/openai_account_model_client.py
  - openjiuwen/core/foundation/llm/utils/request_parameter_fallback.py
  - openjiuwen/core/foundation/llm/utils/responses_transport.py
  - openjiuwen/core/foundation/llm/utils/responses_utils.py
  - openjiuwen/symphony/README.md
  - tests/unit_tests/symphony/test_model_temperature_fallback.py
designs: []
design_note: 按已确认的发布线补丁方案扩展已有请求参数回退。
---

# Symphony 构图温度兼容

Core `release/v0.1.21`（`26daa6e49`）的指纹提取显式发送 `temperature=0.0`，覆盖宿主模型配置。
只接受默认采样设置的网关返回 HTTP 400，Core 以 `181001` 终止构图。Responses 默认省略温度，
但额外参数也能将其带入请求并触发同类错误。

沿用[关闭思考修复的请求作用域](B_20261110_2112_anthropic-disabled-thinking-fallback.md)。
当 400 明确拒绝温度设置时，Core 从最终请求移除 `temperature` 后重发，由模型使用服务端默认采样设置。
温度和关闭思考分别处理，每个参数最多移除一次。温度回退成功后，在当前客户端内按接口、模型、发送值和有效思考设置缓存，
供后续同类请求使用。Responses 的 API Key 和账号认证路径共用 HTTP 传输，回退发生在开始读取流之前。

在 macOS arm64、Python 3.12、OpenAI SDK 2.32.0 和 Anthropic SDK 0.120.2 上离线回放。
旧版在 Chat Completions、Anthropic Messages 和两种 Responses 认证路径收到同类 400 后中止；
候选版通过 `OrchestrationService.build()` 发布了包含测试技能的 `graph.json`。
回归覆盖两种参数拒绝顺序、缓存隔离、参数校验错误以及流式请求开始后的失败。
Core 的 LLM 和 Symphony 回归共通过 1538 项，跳过 3 项。本机回环端口用例单独运行。
Swarm `397fc69a1` 的主模型探测通过三种接口的模拟 HTTP 回放，模型与指纹适配器现有用例通过 38 项。
真实网关和桌面安装包尚未验证。
