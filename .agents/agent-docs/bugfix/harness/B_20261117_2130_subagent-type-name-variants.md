---
title: subagent_type 名称机械变体导致子代理委派失败
author: mulderlover
author_email: huyun8@huawei.com
id: B_20261117_2130_subagent-type-name-variants
type: B
modules:
- openjiuwen/harness
files:
- openjiuwen/harness/subagent_type.py
- openjiuwen/harness/deep_agent.py
- openjiuwen/harness/tools/subagent/task_tool.py
- openjiuwen/harness/tools/subagent/subagent_tools.py
- openjiuwen/harness/tools/subagent/session_tools.py
- openjiuwen/harness/subagent_runtime/control.py
- tests/unit_tests/harness/test_subagent_type.py
designs: []
design_note: 确定性名称归一化缺陷修复，无独立设计；只映射到已注册/已允许名称，不做模糊匹配
---

# subagent_type 名称机械变体导致子代理委派失败

## 现象

模型在 tool call 中把注册名 `general-purpose` 输出为 `general_purpose`、
`GENERAL-PURPOSE`、U+2011 非换行连字符或带首尾空格的名称时，子代理在启动前失败：

```text
Subagent spec not found: general_purpose
```

或：

```text
Subagent type 'general_purpose' is not available through task_tool
```

## 根因

`subagent_type` 是模型生成的自由字符串，但以下入口按精确字符串处理：

- `TaskTool._parse_invocation_inputs` 对 `allowed_subagent_types` 做精确集合校验；
- `SubagentSpawnTool.invoke` 同样做精确集合校验；
- `DeepAgent._find_subagent_spec` 用 `==` 比较注册名；
- `SessionsSpawnTool` 用原始字符串判断 `browser_agent` 并写入任务 metadata；
- `SubagentControl.spawn` 用原始字符串生成会话 ID、展示名并查找配置。

机械等价的名称变体因此无法命中已注册子代理。

## 修复

新增 `openjiuwen/harness/subagent_type.py`，提供确定性的名称处理：

- `normalize_subagent_type`：统一 Unicode 破折号、NBSP、软连字符和首尾空白；
- `canonical_subagent_key`：去掉空白、`_`、`-` 后 `casefold()`；
- `resolve_subagent_type_name`：先通过父 agent 的注册表解析，再在 allowed 列表中做唯一 canonical 匹配。

接入点：

- `DeepAgent._find_subagent_spec`：精确匹配 → Unicode 归一后精确匹配 → canonical key 唯一匹配；canonical 多命中返回 `None`，保留原“not found”错误；
- `TaskTool` / `SubagentSpawnTool` 在 allowed 校验前解析注册名；
- `SessionsSpawnTool` 在 browser 能力判断和写入 metadata 前解析；
- `SubagentControl.spawn` 在生成 ID、展示名和查找配置前解析。

只映射到已注册或已允许的名称，不扩大可委派范围；不做 Levenshtein、同义词或前缀匹配。

## 验证

- 新增 `tests/unit_tests/harness/test_subagent_type.py`，覆盖下划线、Unicode 连字符、大小写、首尾空白、canonical 歧义、拼写错误、allowed 校验、sessions_spawn browser 能力和 control spawn；
- 相关目标测试与 `agent/react_agent` 回归通过；CI 曾失败的
  `test_turn_step_event_integration.py::test_model_rail_retry_remains_one_turn_step`
  在最新 develop 上连续通过。
