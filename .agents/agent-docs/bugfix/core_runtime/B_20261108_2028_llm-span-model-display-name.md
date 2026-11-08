---
id: B_20261108_2028_llm-span-model-display-name
title: llm span 的 gen_ai.request.model 与 span 名显示网关服务 ID 而非模型名缺陷
type: B
author: yidaaichixia
author_email: 1046386000@qq.com
modules:
  - openjiuwen/extensions/tracer_otel
  - openjiuwen/core/session/tracer
files:
  - openjiuwen/extensions/tracer_otel/otel_rail.py
  - openjiuwen/core/session/tracer/decorator.py
  - tests/unit_tests/extensions/tracer_otel/test_otel_rail.py
  - tests/unit_tests/core/session/tracer/test_decorator.py
designs: []
design_note: 常规缺陷修复（遥测展示层纯增量可选字段），无方案取舍与公开契约变更，未归档独立设计。
---

# llm span 模型显示名缺陷修复记录

## 现象与影响

网关形态（如 studio OBS 模型网关）下 `ModelRequestConfig.model_name` 存的是
modelServiceId（UUID）：路由由网关客户端在调用期覆盖，配置侧只保留服务 ID。
`OtelRail.before_model_call` 与 `decorate_model_with_trace` 探针都把该值填进
`instance_info["class_name"]`，于是 llm span 名（`llm.<class_name>`）与
`gen_ai.request.model` 展示为一串 UUID——人读不可辨，前端/洞察按模型名聚合、
对账时无法对齐真实模型。

## 根因

两个 llm span 生产者都只有 `class_name` 单一身份通道，取值固定等于
`model_config.model_name`；core 侧无从自行反查网关元数据拿到人读名。名字只能
由构造模型的一方（知道网关元数据的调用方）带出，而带出通道此前不存在。

## 修复决策

新增**可选** `model_display_name` 载体，仅生产者侧消费，handler 零改动：

- `OtelRail._resolve_model_display_name` 依次探测：解析出的 request config 的
  extra 字段（`extra="allow"`，与 `top_k` 在 `_extract_request_params` 的搭车
  机制相同）→ agent config 的 `model_config_obj` 同名字段 →
  `agent._llm.model_display_name`（foundation `Model.__getattr__` 把未知属性
  透传给 `model_client_config`，client config 上设置的显示名由此浮出）；
- `decorate_model_with_trace` 探针新增 `_model_display_name` 帮手：client config
  载体（顶层 / `config` 包装）优先，request config 载体兜底；
- 命中即覆盖 `class_name`（span 名与 `gen_ai.request.model` 同步显示真名）；
  未命中时 span 载荷与原先字节级一致（含服务 ID 场景）——既有采集不受影响。

**why-not**：不在 handler 侧加 `model_name` 键优先级——所有直接
`on_llm_start` 调用方的现值语义会随之漂移；不引入第二个 instance_info 键——
`class_name` 在两生产者处本就是"模型展示身份"，再开通道徒增合并语义。

## 验证

- 单测：rail 新增 4 条（config extra 命中 / llm 对象命中 / 双载体 config 优先 /
  无显示名保持 UUID 红线）、decorator 新增 2 条（命中覆盖 / 缺省保持探针结果）；
- 回归：`tests/unit_tests/extensions/tracer_otel/` + `tests/unit_tests/core/session/`
  全量 587 passed（Python 3.11，仓内源码 PYTHONPATH 直引执行）。

## 遗留与配套

- 显示名由调用方供给：studio OBS provider 已在 `ModelClientConfig` 上带出
  `model_display_name`（随其遥测 PR 合入后，react rail 路径无需额外 studio 改动
  即显示真名）；非网关模型不设该字段，行为不变。
- 响应侧 `gen_ai.response.model`（服务端实际模型）语义不受本修复影响。
- develop 侧同构孪生：feature/otel-llm-display-name-develop。
