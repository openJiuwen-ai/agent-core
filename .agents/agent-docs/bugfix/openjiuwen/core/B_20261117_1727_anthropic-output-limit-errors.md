---
id: B_20261117_1727_anthropic-output-limit-errors
title: Anthropic 输出上限耗尽可见错误修复
type: B
author: lizhiyuan
author_email: lizhiyuan65@huawei.com
modules:
  - core/foundation/llm/model_clients
  - harness/moa
files:
  - openjiuwen/core/foundation/llm/model_clients/anthropic_model_client.py
  - openjiuwen/harness/moa/proposal.py
  - tests/unit_tests/core/foundation/llm/test_anthropic_model_client.py
  - tests/unit_tests/core/foundation/llm/test_reasoning_resolver.py
  - tests/unit_tests/harness/moa/test_moa_proposal.py
designs: []
design_note: 用户在本轮会话确认的 Anthropic 输出上限错误处理范围，未形成独立设计文档。
---

# Anthropic 输出上限耗尽可见错误修复

## 现象与影响

Anthropic 返回 `stop_reason=max_tokens` 时，客户端将其归一化为普通 `stop`。ReAct 因而无法将只包含思考内容、且没有正文或工具调用的响应转为既有模型错误，前端只能收到空结果。

## 根因与修复决策

- Anthropic 客户端保留 `max_tokens` 作为统一完成原因，而不是把它降级为 `stop`；流式和非流式响应使用同一语义。
- ReAct 保持原有的统一截断处理：没有正文或工具调用时，`length` 与 `max_tokens` 都转为模型调用失败；已有部分内容时保持既有返回行为。
- Anthropic `max_tokens` 小于等于思考预算时一律返回配置错误，不能生成超过模型安全上限的请求，也不能静默突破用户设置的输出上限。
- MoA 的输出预算镜像不再凭空把不兼容的上限抬高；它将该配置标为不可解析、不注入 proposal，底层调用仍向用户返回配置错误。

## 回归证据与适用范围

- `uv run pytest tests/unit_tests/agent/react_agent/test_react_agent_streaming.py tests/unit_tests/agent/react_agent/test_react_agent_provider_error.py tests/unit_tests/core/foundation/llm/test_anthropic_model_client.py tests/unit_tests/core/foundation/llm/test_reasoning_resolver.py tests/unit_tests/harness/moa/test_moa_proposal.py tests/unit_tests/harness/tools/browser_move/test_browser_contract_compatibility.py -q`：271/271 通过。
- 覆盖非流式和流式 `max_tokens` 映射、工具调用优先级、输出上限与思考预算冲突，以及 MoA 对无效输出预算的镜像行为。

## 遗留与后续

无。其他模型客户端若暴露同义的输出上限完成原因，应保持 `max_tokens` 语义而非映射为普通停止。
