# openjiuwen.core.retrieval.indexing.processor.splitter.splitter

## class openjiuwen.core.retrieval.indexing.processor.splitter.splitter.SentenceSplitter

句子级别的文本分割器，使用 pysbd 进行句子分割，然后根据 token 数量进行分块。


```python
SentenceSplitter(tokenizer: Callable, chunk_size: int, chunk_overlap: int, lan: str = "zh")
```

初始化句子分割器。

**参数**：

* **tokenizer**(Callable)：分词器（比如具有 encode 和 decode 方法的对象），必须具有 encode 和 decode 方法。
* **chunk_size**(int)：分块大小（token 数量）。
* **chunk_overlap**(int)：分块重叠大小（token 数量）。
* **lan**(str, 可选)：语言代码，默认为 "zh"（中文）。默认值："zh"。

### __call__

```python
__call__(doc: str) -> List[Tuple[str, int, int]]
```

将文档分割为句子级别的分块。

**参数**：

* **doc**(str)：待分割的文档文本。

**返回**：

**List[Tuple[str, int, int]]**，返回分块列表（比如 `[("文本1", 0, 10), ("文本2", 10, 20)]`），每个元素为 (文本, 起始字符位置, 结束字符位置)。

**说明**：

* 使用 pysbd 进行句子分割
* 根据 token 数量进行分块，确保每个分块不超过 chunk_size
* 支持分块重叠，重叠部分不超过 chunk_overlap
* 如果单个句子超过 chunk_size，该句子将作为一个独立分块
