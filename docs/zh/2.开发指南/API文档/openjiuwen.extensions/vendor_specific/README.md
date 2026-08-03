# openjiuwen.extensions.vendor_specific

Vendor-Specific Model Services —— 厂商特定的模型服务扩展。

本子包为开发者提供便利与参考实现（reference implementation）。当前仅包含一个已弃用的别名再导出，正式能力请使用 `openjiuwen.core` 对应实现。

## aliyun_reranker

定义于 `aliyun_reranker.py`。

| 类 | 说明 |
|---|---|
| `AliyunReranker` | **已弃用** —— 请改用 `openjiuwen.core.retrieval.DashscopeReranker`。本模块在导入时即通过 logger 打印弃用警告；`AliyunReranker` 实为 `DashscopeReranker` 的别名再导出，不提供额外能力。 |

## 备注

AS-14 同时标记的 `extensions/harness/` 与 `extensions/common/` 子目录无活跃 `.py` 源文件（前者 `__init__.py` 为 0 字节空命名空间，后者仅含 `__pycache/` 下的 `.pyc` 字节码），故不生成 API 文档。这与附录 A.2 中 `deepagents/`（仅 `.pyc`）属同类遗留，非真实缺失。
