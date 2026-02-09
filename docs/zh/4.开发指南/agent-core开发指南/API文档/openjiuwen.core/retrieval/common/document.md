# openjiuwen.core.retrieval.common.document

## class openjiuwen.core.retrieval.common.document.Document

文档数据模型，表示一个文档对象。

**参数**：

* **id_**(str)：文档ID，如果未提供将自动生成UUID。默认值：自动生成。
* **text**(str)：文档文本内容。
* **metadata**(Dict[str, Any])：文档元数据。默认值：{}。

**样例**：

```python
>>> from openjiuwen.core.retrieval.common.document import Document
>>> 
>>> # 创建文档
>>> doc = Document(text="这是一个测试文档", id_="doc1", metadata={"author": "test"})
>>> print(f"Document ID: {doc.id_}, Text: {doc.text}")
Document ID: doc1, Text: 这是一个测试文档
```

## class openjiuwen.core.retrieval.common.document.TextChunk

文本块数据模型，表示文档的一个文本块。

**参数**：

* **id_**(str)：文本块ID。
* **text**(str)：文本块文本内容。
* **doc_id**(str)：父文档ID。
* **metadata**(Dict[str, Any])：文本块元数据。默认值：{}。
* **embedding**(list[float] | None)：文本块嵌入向量。默认值：None。

### classmethod from_document

```python
from_document(doc: Document, chunk_text: str, id_: str = "") -> TextChunk
```

从Document创建TextChunk。

**参数**：

* **doc**(Document)：文档对象。
* **chunk_text**(str)：文本块文本内容。
* **id_**(str)：文本块ID，如果未提供将自动生成UUID。默认值：""。

**返回**：

**TextChunk**，返回创建的文本块对象。

**样例**：

```python
>>> from openjiuwen.core.retrieval.common.document import Document, TextChunk
>>> 
>>> # 创建文档
>>> doc = Document(text="这是一个测试文档", id_="doc1")
>>> # 从文档创建文本块
>>> chunk = TextChunk.from_document(doc, chunk_text="这是一个测试", id_="chunk1")
>>> print(f"Chunk ID: {chunk.id_}, Doc ID: {chunk.doc_id}, Text: {chunk.text}")
Chunk ID: chunk1, Doc ID: doc1, Text: 这是一个测试
```

