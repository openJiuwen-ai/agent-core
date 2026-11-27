---
id: B_20261116_1636_glm53-flash-reasoning-levels
title: GLM-5.3-Flash 推理强度与禁用编码修复
type: B
author: lizhiyuan
author_email: lizhiyuan65@huawei.com
modules:
  - core/foundation/llm
files:
  - openjiuwen/core/foundation/llm/reasoning_profiles.py
  - openjiuwen/core/foundation/llm/reasoning.py
  - tests/unit_tests/core/foundation/llm/test_reasoning_profiles.py
  - tests/unit_tests/core/foundation/llm/test_reasoning_resolver.py
designs: []
design_note: 用户已确认继续采用端点/模型族静态能力表；本次仅补齐既有 Zhipu 配置的一个模型族，无独立设计。
---

# GLM-5.3-Flash 推理强度与禁用编码修复

## 现象与根因

Zhipu 官方端点上的 `GLM-5.3-Flash` 被宽泛的 `glm-5*` 开关规则命中，向用户暴露了不被该模型支持的关闭推理选项，并在关闭时发出 `thinking.type=disabled`。

## 修复决策

- 在 Zhipu 官方端点、宽泛 `glm-5*` 规则之前，新增 `glm-5.3-flash*` 专用静态规则。
- 该规则只暴露 `low`、`high`、`max`，推荐 `max`；不包含关闭档位。
- OpenAI 兼容请求把选定档位编码为 `thinking.type=enabled` 与精确的 `reasoning_effort`；禁用请求不下发思考控制，避免向模型发送已知无效的关闭参数。
- 未把该条目加入跨端点模型 fallback，确保能力只在 Zhipu 官方端点生效；自定义端点由 Swarm 不展示推理配置的策略处理。

## 回归证据

`uv run pytest tests/unit_tests/core/foundation/llm/test_reasoning_profiles.py tests/unit_tests/core/foundation/llm/test_reasoning_resolver.py`：78/78 通过。覆盖 Zhipu 官方端点能力返回、`low` 参数编码，以及禁用请求不下发思考控制。
