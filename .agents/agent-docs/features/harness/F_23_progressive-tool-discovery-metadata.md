---
title: 渐进式工具发现元数据
author: MengHan18
id: F_23_progressive-tool-discovery-metadata
type: F
modules:
- openjiuwen/harness
files:
- openjiuwen/harness/tools/tool_discovery/bm25.py
- openjiuwen/harness/rails/progressive_tool_rail.py
designs: []
design_note: 复用现有渐进式工具发现架构，仅增加向后兼容的卡片元数据扩展点
author_email: hanmeng22@huawei.com
---

# 渐进式工具发现元数据

## 元信息

| 项 | 值 |
|---|---|
| 类型 | feature |
| 日期 | 2026-10-27 |
| 范围 | `openjiuwen/harness/tools/tool_discovery/bm25.py`、`openjiuwen/harness/rails/progressive_tool_rail.py` |
| 关联 spec | `S_05_tools-contract.md` |
| Refs | #5615 |

## 背景

`ProgressiveToolRail` 的 BM25 索引只包含工具名称、原始描述和参数。运行提示词与用户 query
语言不一致时，两个文本没有共同词，即使工具已注册也可能搜不到。把翻译词直接追加到工具
描述会污染模型最终拿到的 schema，并让仅用于搜索的文案变化影响调用授权版本。

## 契约

工具提供方可以使用现有 `ToolCard.properties` 声明可选发现元数据：

```python
card.properties["progressive_discovery"] = {
    "display": {"en": "Create reminders and scheduled jobs"},
    "aliases": ["提醒我", "定时提醒", "闹钟"],
}
```

- `display` 是语言代码到短目录摘要的映射。键匹配前会执行 `strip().lower()`；`cn` 与 `zh`
  互为别名并按“当前语言、别名”的顺序选取首个非空字符串。`en` 等其他语言只匹配自身，
  不跨语言回退。目录摘要与目录标题共用同一个语言来源：优先使用当前 prompt builder，
  缺失时回退 rail 配置，并归一为现有渲染器支持的 `cn` / `en`。没有匹配项时，初始目录与
  后续目录附件显示原始描述。
- `aliases` 与 `display` 的所有有效字符串只追加到 BM25 搜索文本。
- 搜索结果、`tool_call` 授权指纹和执行 schema 始终使用原始 `ToolInfo`。
- 未声明、空值或错误类型一律按旧行为处理。
- 索引 `source_fingerprint` 同时覆盖原始 `ToolInfo` 和额外搜索文本，使卡片原地更新别名后
  能在下一次搜索前重建索引。

## 实现边界

core 不提供具体工具翻译，也不判断工具来自哪个产品或 MCP。产品层负责给自己的内置工具
附加元数据；外部工具没有元数据时保留提供方原文。`BM25ToolIndex` 仍保存并返回原始
`ToolInfo`，额外文本只参与分词和索引版本计算。

## 验证

- 中文别名可在英文 rail 中命中英文工具；
- 命中结果的描述与参数保持原值；
- 修改别名但不推进 registry revision 时仍重建索引；
- 无元数据的既有工具检索、授权和调用契约保持兼容。
