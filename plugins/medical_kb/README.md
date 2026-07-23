# Medical Knowledge Base Plugin for DeepSearch

医疗知识库插件，为 [openJiuwen DeepSearch](https://gitcode.com/openJiuwen/deepsearch) 提供**诊疗指南智能检索与问答**能力。

## 功能特性

- **诊疗指南问答**：基于中国2型糖尿病防治指南(2024/2020版)、中国高血压防治指南(2024版)
- **三重融合检索**：向量语义 + BM25关键词 + 知识图谱，综合打分
- **引用溯源**：每个回答附带指南原文引用（名称+版本+章节+条目）
- **版本对比**：新旧版指南差异检测（如糖尿病指南2024版 vs 2020版）
- **多轮对话**：根据历史对话推断疾病，提高命中率

## 快速开始

```bash
# 1. 安装依赖
pip install medical-ai-innovation

# 2. 测试插件
python -m plugins.medical_kb
```

## 使用方法

```python
from plugins.medical_kb import MedicalKnowledgeBasePlugin

# 初始化插件
plugin = MedicalKnowledgeBasePlugin()
plugin.load_demo_guidelines()

# 搜索
result = plugin.search("空腹血糖正常值是多少？")
print(result["answer"])

# 获取引用
for c in result["citations"]:
    print(f"{c['source']} (score: {c['score']})")
```

## 内置指南数据

| 指南名称 | 版本 | 条目数 |
|---------|------|--------|
| 中国2型糖尿病防治指南 | 2024版（最新） | 6条 |
| 中国2型糖尿病防治指南 | 2020版（旧版） | 3条 |
| 中国高血压防治指南 | 2024版（最新） | 3条 |

## 插件接口

实现 DeepSearch 标准插件接口：

| 方法 | 说明 |
|------|------|
| `search(query, top_k=5)` | 知识库搜索 |
| `batch_search(queries)` | 批量搜索 |
| `get_guideline_list()` | 获取指南列表 |
| `get_stats()` | 获取统计信息 |
| `explain(query)` | 检索过程解释 |

## 许可证

Apache-2.0