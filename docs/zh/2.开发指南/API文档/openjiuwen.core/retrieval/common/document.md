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

## class openjiuwen.core.retrieval.common.document.MultimodalDocument

多模态文档数据模型，继承自Document，支持处理包含多种内容类型的文档（文本、图像、音频、视频）。

> **参考示例**：更多使用示例请参考 [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) 仓库中 `examples/retrieval/` 目录下的示例代码，包括：
> - `showcase_multimodal_embedding.py` - 多模态嵌入示例

```python
MultimodalDocument(id_: str = "", text: str = "", metadata: Dict[str, Any] = {})
```

初始化多模态文档。

**参数**：

* **id_**(str)：文档ID，如果未提供将自动生成UUID。默认值：自动生成。
* **text**(str)：文档文本内容（作为仅文本服务的后备字段，对于多模态内容应使用 add_field 方法）。默认值：""。
* **metadata**(Dict[str, Any])：文档元数据。默认值：{}。

### property content

```python
content -> list[dict[str, Any]]
```

获取内容字段，返回用于嵌入的格式化内容列表。

**返回**：

**list[dict[str, Any]]**，返回格式化的内容列表，每个元素包含类型和对应的数据。

### add_field

```python
add_field(kind: Literal["text", "image", "audio", "video"], data: str = NOT_SET, file_path: Path = NOT_SET, data_id: str = "") -> Self
```

向当前多模态文档添加数据字段，支持链式调用。

**参数**：

* **kind**(Literal["text", "image", "audio", "video"])：新字段的类型。
* **data**(str, 可选)：Base64编码的数据字符串。
* **file_path**(Path, 可选)：多模态文件的有效文件路径。
* **data_id**(str, 可选)：用于多模态缓存的UUID，如果不确定请留空。默认值：""。

**返回**：

**Self**，返回当前 MultimodalDocument 实例。

**说明**：

支持的模态类型：
* **text**：纯文本内容
* **image**：图像文件（支持常见格式如 jpg、png 等）
* **audio**：音频文件（支持各种音频格式）
* **video**：视频文件（支持各种视频格式）

**样例**：

```python
>>> from pathlib import Path
>>> from openjiuwen.core.retrieval.common.document import MultimodalDocument
>>> 
>>> # 创建包含文本和图像的多模态文档
>>> doc = MultimodalDocument()
>>> doc.add_field("text", "这是一个描述")
>>> doc.add_field("image", file_path=Path("image.jpg"))
>>> 
>>> # 或使用链式调用
>>> doc = (MultimodalDocument()
...        .add_field("text", "Hello world")
...        .add_field("image", file_path=Path("photo.png")))
>>> 
>>> # 直接添加Base64编码的数据
>>> doc.add_field("audio", data="data:audio/wav;base64,...")
>>> 
>>> # 访问用于嵌入的结构化内容
>>> content = doc.content  # 返回用于嵌入的格式化内容列表
```
