---
id: B_20261110_2112_anthropic-disabled-thinking-fallback
title: Anthropic Messages 支持图谱关闭思考失败后的回退
type: B
author: lin-xiaoyu
author_email: xiaoyu.lin.work@outlook.com
modules:
  - openjiuwen/core/foundation/llm
files:
  - openjiuwen/core/foundation/llm/model_clients/_disabled_thinking.py
  - openjiuwen/core/foundation/llm/model_clients/anthropic_model_client.py
  - openjiuwen/core/foundation/llm/model_clients/openai_model_client.py
  - tests/unit_tests/core/foundation/llm/test_anthropic_model_client.py
designs: []
design_note: 补齐已有请求级关闭思考回退在 Anthropic 非流式调用中的接入。
---

# Anthropic Messages 图谱构建回退

## 现象与根因

WorkSwarm 0.2.9 图谱在主模型探测时向 K3.1 Pro 发出关闭思考的参数。Anthropic Messages 网关
返回 HTTP 400，Core 将其包装为 `181001`，构建在技能指纹提取前终止。
Swarm 的探测和 Core 的指纹提取都已进入 `disabled_thinking_fallback_scope()`，当时只有
OpenAI Chat Completions 客户端在调用接口时消费这一标记。[Swarm Issue #6197](https://gitcode.com/openJiuwen/jiuwenswarm/issues/6197)

## 修复

将原有拒绝识别、参数清理、单次重试和客户端内按模型缓存的逻辑移入同一 Core 模块，
由 Anthropic `invoke()` 和原 OpenAI 入口共用。在允许回退的请求里，识别到模型拒绝关闭思考时，
保留其余请求参数并移除关闭思考的字段，再按服务端默认模式请求一次。普通聊天仍保留其显式思考设置和原有错误处理。
阿里云兼容接口中 Kimi K3.1 Pro 的默认模式开启思考。[Anthropic 兼容接口文档](https://docs.modelstudio.console.alibabacloud.com/en/model-studio/anthropic-api-messages)

## 回归证据与适用范围

- Core 基线为 Swarm 0.2.9 锁定的 `2f4bedb9c`。新增 Anthropic SDK 模拟传输用例在原版收到同类 400 后失败，
  候选版确认重试去掉关闭字段、保留自定义参数，并验证缓存只在回退作用域内使用。
- macOS arm64，Python 3.12，Anthropic SDK 0.120.2，OpenAI SDK 2.32.0。
  Core 的 Anthropic、OpenAI、推理参数和 Symphony 定向用例 348 项通过。
  Swarm `397fc69a1` 使用候选 Core 源码运行图谱模型与指纹适配器用例，38 项通过。
- 通过 Swarm 的 `SwarmSymphonyService.refresh_graph()` 离线回放该 400。Anthropic 和 OpenAI 两条路径
  都在探测和指纹提取时按默认模式重试，终态为 `update.done`，发布的 `graph.json` 包含测试技能。
  Anthropic 探测重试只返回思考内容时也通过连接测试。

Swarm 0.2.9 的依赖锁需引用包含修复的 Core 提交，才能在安装包中生效。真实 K3.1 Pro 网关验证待依赖更新后执行。
