---
id: B_20261117_1727_anthropic-output-limit-errors
title: 模型输出上限截断可见错误修复
type: B
author: lizhiyuan
author_email: lizhiyuan65@huawei.com
modules:
  - core/foundation/llm/model_clients
  - core/single_agent
  - harness/moa
files:
  - openjiuwen/core/foundation/llm/model_clients/anthropic_model_client.py
  - openjiuwen/core/single_agent/agents/react_agent.py
  - openjiuwen/harness/moa/proposal.py
  - tests/unit_tests/core/foundation/llm/test_anthropic_model_client.py
  - tests/unit_tests/agent/react_agent/test_react_agent_provider_error.py
  - tests/unit_tests/agent/react_agent/test_react_agent_streaming.py
  - tests/unit_tests/core/foundation/llm/test_reasoning_resolver.py
  - tests/unit_tests/harness/moa/test_moa_proposal.py
designs: []
design_note: 用户在本轮会话确认的 Anthropic 输出上限错误处理范围，未形成独立设计文档。
---

# 模型输出上限截断可见错误修复

## 现象与影响

Anthropic 返回 `stop_reason=max_tokens` 时，客户端此前会将其归一化为普通 `stop`。完成归一化后，ReAct 只会将没有正文或工具调用的截断响应转为模型错误；OpenAI 兼容端点返回 `finish_reason=length` 且已有部分正文时，在用户显式配置单次最大输出后仍会被当作成功结果，前端不显示错误。

## 根因与修复决策

- Anthropic 客户端保留 `max_tokens` 作为统一完成原因，而不是把它降级为 `stop`；流式和非流式响应使用同一语义。
- ReAct 将 `length` 与 `max_tokens` 统一识别为输出截断：没有正文或工具调用时始终转为模型调用失败；用户显式配置 `ModelRequestConfig.max_tokens` 时，部分正文或工具调用同样转为失败。这样能让用户设置的单次最大输出可见地失败，同时保留未配置时既有的部分输出兼容语义。OpenAI 兼容端点使用 `length`，Anthropic 使用 `max_tokens`；在显式上限场景下两者的会话语义一致。
- Anthropic `max_tokens` 小于等于思考预算时一律返回配置错误，不能生成超过模型安全上限的请求，也不能静默突破用户设置的输出上限。
- MoA 的输出预算镜像不再凭空把不兼容的上限抬高；它将该配置标为不可解析、不注入 proposal，底层调用仍向用户返回配置错误。

## 回归证据与适用范围

- `D:\lzy\new\agent-core\.venv\Scripts\python.exe -m pytest tests/unit_tests/harness/tools/browser_move/test_browser_contract_compatibility.py tests/unit_tests/agent/react_agent/test_react_agent_provider_error.py tests/unit_tests/agent/react_agent/test_react_agent_streaming.py -q --disable-warnings`：59/59 通过。
- 覆盖无正文截断始终失败、显式单次最大输出下非流式和流式的部分文本截断失败、`length` / `max_tokens` 的一致错误语义，以及未配置输出上限时的部分输出兼容路径。

## 遗留与后续

模型客户端应继续保留原始完成原因；ReAct 负责在无输出或显式单次最大输出场景中把 `length` / `max_tokens` 转成面向会话的失败。直接调用 Model 的低成本连通性探测和未配置上限的既有调用仍可按其自身语义使用部分输出来验证端点可用性。
