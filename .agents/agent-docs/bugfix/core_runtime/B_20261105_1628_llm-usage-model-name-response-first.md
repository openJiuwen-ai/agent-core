---
id: B_20261105_1628_llm-usage-model-name-response-first
title: LLM usage.model_name 改为响应体 model 优先
type: B
author: yidaaichixia
author_email: 1046386000@qq.com
modules:
  - openjiuwen/core/foundation/llm
files:
  - openjiuwen/core/foundation/llm/model_clients/openai_model_client.py
  - openjiuwen/core/foundation/llm/model_clients/anthropic_model_client.py
  - tests/unit_tests/core/foundation/llm/test_openai_model_client.py
  - tests/unit_tests/core/foundation/llm/test_anthropic_model_client.py
designs: []
design_note: 语义纠错类修复；依据 GenAI 语义约定对 gen_ai.response.model 的定义（生成响应的模型名）与同文件 response_model 既有响应体优先取法，无独立方案取舍需要设计文档。
---

# LLM usage.model_name 改为响应体 model 优先

## 现象与影响

OTel llm span 的 `gen_ai.response.model`（tracer_otel handler 从
`usage.model_name` 载荷读取）显示的是请求侧配置名：在 ModelArts 部署 ID
生态下表现为部署 UUID 而非 `qwen3-max` 一类模型名；直连场景下也与
OpenAI 响应体可能返回的带快照日期精确版本不一致。

根因位置在 openai / anthropic 两个 model client：构造
`UsageMetadata` 时 `model_name` 硬编码 `self.model_config.model_name`
（请求侧解析终值），从未消费响应体。同文件 `response_model`
（AssistantMessage 字段）早已是 `str(getattr(response, "model", "") or "")`
的响应体优先取法，usage 侧未跟上；intelli_router 客户端也已从路由
payload 读取 `model_name`。本次将两个客户端拉齐到既有约定。

## 关联设计与当前契约

- 消费方契约：tracer_otel `GEN_AI_RESPONSE_MODEL = usage.get("model_name")`
  （handler.py），写出的属性对应 GenAI 语义约定 `gen_ai.response.model`
  ——"生成响应的模型"。填请求配置名偏离该语义。
- `UsageMetadata.model_name`（schema/message.py）无字段级描述，语义由
  消费方决定，schema 零变更。
- 与 agent-studio 侧问题的边界：IR `modelName` 字段本身可能存部署 UUID
  （名字在 IR 生成时即丢失），导致 config 兜底值也是 UUID——该名字来源
  问题在 studio 侧独立跟踪（!2598 display 通道 / IR 生成端），core 侧
  职责限于"服务侧说了什么记什么"。

## 根因与修复决策

修复：`model_name` 取 `响应体 model（非空）→ self.model_config.model_name`，
单层回退即原行为。

- openai 批式 `_parse_response` 读 `response.model`；流式
  `_parse_stream_chunk` 读 `chunk.model`。
- anthropic `_usage_from_anthropic` 新增 keyword 兼容参数
  `served_model: str = ""`，非流式与 message_start 调用点传入
  `response.model` / `msg.model`；message_delta 协议不携带 model，
  走默认空串回退 config。

备选与拒绝理由：

- 多层回退（settings、注册链）：名字多级解析已在客户端构造前完成
  （studio IRModelConfigProvider `modelName → model_name → settings` 链），
  客户端再叠回退链属职责重复，排查更难。
- 不改（维持请求配置名）：直连用户两值通常相同看似无害，但网关 /
  路由 / 部署 ID 生态下 `gen_ai.response.model` 永远无法反映实际服务
  模型，语义约定形同虚设。

## 回归证据与适用范围

develop 修正分支 feature/otel-drop-usage-cost-attrs（!3584）：
`python3 -m pytest tests/unit_tests/core/foundation/llm/test_openai_model_client.py
tests/unit_tests/core/foundation/llm/test_anthropic_model_client.py
tests/unit_tests/extensions/tracer_otel/ -q` → 340 passed（含消费方
tracer_otel 套件）。新增 8 用例：批式/流式 served-model 优先与
config 回退各 2（openai）、helper 传入/缺省/空串回退与 message_start
接线（anthropic）。

未验证范围：真实网关响应体 `model` 字段内容（决定部署 ID 生态下的
实际收益：回显真实名则 response.model 变真名，回显请求 UUID 则与现状
一致，无回归）；0.1.17_hotfix 侧（!3585）待同改动落地后重跑同一套件。

## 防错模式与资料同步

"遥测语义键按语义约定取数，不照抄请求配置"——与 F_ tpot 键收敛
（F_20261104_2034）同族：语义键的取值来源错了，键名本身没错。暂未
提炼新模块规则；若后续再出现同类错位再写回 AGENTS/spec。

## 遗留与后续

- hotfix 分支 feature/otel-drop-usage-cost-attrs-0.1.17 同改动 + 回归。
- !3584 / !3585 PR 描述补第四项纠错说明。
- 集群实测确认网关响应体 model 字段内容（外部依赖，非代码问题）。
