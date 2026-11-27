---
id: B_20261127_1537_tool-call-object-arguments
title: 兼容对象形式的 tool_calls.arguments
type: B
author: Huazijiayou
author_email: yaohua@huawei.com
modules:
  - openjiuwen/core/foundation/llm
files:
  - openjiuwen/core/foundation/llm/schema/tool_call.py
  - tests/unit_tests/core/foundation/llm/test_tool_call_arguments.py
designs: []
design_note: 单字段输入兼容修复，无独立设计文档。
---

# 兼容对象形式的 tool_calls.arguments

## 现象

部分 OpenAI 兼容网关（例如火山方舟 deepseek 系列在默认思考模式下）会在
`tool_calls[].function.arguments` 返回 JSON 对象或数组，而不是规范要求的 JSON 字符串。
`ToolCall.arguments` 声明为 `str`，pydantic 校验报 `string_type`；错误文本写入会话历史后，
后续轮次因历史无法解析而不可用。

最小复现：

```python
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall

ToolCall(id="1", type="function", name="bash", arguments={"command": "pwd"})
# 修复前：pydantic ValidationError(string_type)
# 修复后：arguments == '{"command": "pwd"}'
```

## 根因与修复

根因是协议实现差异：OpenAI 规范要求 `arguments` 为 JSON 字符串，兼容网关可直接返回对象。

修复在 `ToolCall` 上增加 `field_validator("arguments", mode="before")`：仅当输入为 `dict`/`list`
时调用 `json.dumps(..., ensure_ascii=False)`；字符串原样返回；其他类型仍按原校验失败。
对外字段类型保持 `str`，不改变下游消费方与序列化结构。

## 验证

- `tests/unit_tests/core/foundation/llm/test_tool_call_arguments.py`：dict、list、str、
  非法类型四组用例，4 passed；
- 环境：Python 3.12.10、pydantic 2.14.3；
- 坏 history 会话加载：使用同一补丁在调用方本地分支验证 history 接口返回 200。

## 兼容性

字符串输入行为不变；不新增依赖；不改变对外字段类型。
