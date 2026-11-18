---
id: B_20261118_1133_tool-enum-schema
title: 工具枚举参数的 JSON 类型推断修复
type: B
author: liaojunzhuo-sefonsoft
author_email: liaojunzhuo@sefonsoft.com
modules:
  - openjiuwen/core/foundation/tool
files:
  - openjiuwen/core/foundation/tool/utils/callable_schema_extractor.py
  - tests/unit_tests/core/foundation/tool/test_enum_schema.py
designs: []
design_note: 单模块修复既有 Enum 类型注解的 Schema 生成错误，不新增公开接口或改变枚举合法值契约。
---

# 工具枚举参数的 JSON 类型推断修复

## 现象与影响

Issue #2326。基线 `bdbb52a9ecbe2d285b5949e692abbbad0bc22dd1` 中，公开 `@tool` 入口把 `IntEnum(1, 2)` 生成的参数声明为 `type: string` 与 `enum: [1, 2]`，导致声明的合法值也无法通过 JSON Schema 校验。布尔、浮点、空值及混合类型枚举也受影响。

## 关联设计与当前契约

未找到本模块针对枚举值类型的独立 spec 或既有故障记录。本次依据 `@tool` 的参数类型生成职责、现有枚举提取器及 JSON Schema enum 规则修复，不新增设计文档。规范依据：https://json-schema.org/understanding-json-schema/reference/enum。

## 根因与修复决策

`EnumSchemaExtractor` 已匹配全部 Enum 类，但 `get_enum_schema()` 固定返回字符串类型。修复按枚举实际值推断 JSON 类型，整数与浮点混合时使用 number；其他混合类型不添加统一 type，继续由 enum 严格限定合法值。

保留字符串枚举的完整 Schema、枚举顺序和描述。使用精确 Python 类型区分 bool 与 int，避免布尔值因继承关系被误判为整数。不修改 Literal、Optional、Union 或工具运行时参数转换，不引入新依赖。

## 回归证据与适用范围

- 环境：Windows、Python 3.11.15，通过 `uv sync --group test` 安装仓库锁定依赖。
- 新增 14 项测试；基线 11 项失败、3 项兼容场景通过，修复后全部通过。
- 相关工具测试共 55 项通过，范围为新增枚举测试、工具装饰器、工具卡片暴露、模型工具渲染及参数校验。
- 对实际修改的 callable_schema_extractor 模块执行覆盖率验证，结果为 87%；同轮 55 项测试通过。
- 测试用 Draft202012Validator 验证最终公开工具卡片接受已声明值并拒绝其他值，同时比较原有字符串 Schema 的兼容性。
- 14 条告警来自既有 Pydantic 弃用提示；本次未修改这些无关路径。
- 按仓库根规则未运行格式化或静态检查。未调用真实模型或外部服务，也未声称完成全仓验证。

## 防错模式与资料同步

类型注解到 Schema 的转换需要对已声明值做正向校验，不能仅比较生成字典的表面结构。本次在对应方法注释和工具 API 文档中说明类型推断行为，并用公开装饰器入口保留回归覆盖；不新增全仓规则。

## 遗留与后续

等待维护者评审与远端门禁。后续若枚举类型推断、工具 Schema 组装或 JSON Schema 依赖改变，需要重跑相关测试。此处证据不涵盖真实模型供应商对工具参数声明的兼容性。
