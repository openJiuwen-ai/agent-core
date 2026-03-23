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
> - `showcase_dashscope_multimodal_embedding.py` - 阿里云 DashScope 多模态嵌入示例

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

按字段顺序生成通用的“多模态嵌入用”内容列表（内部带缓存；调用 `add_field` 后会失效并重建）。

**返回**：

**list[dict[str, Any]]**，每项大致为：
* **text**：`{"type": "text", "text": ...}`；
* **image** / **video**：`{"type": "image_url" | "video_url", "image_url" | "video_url": {"url": ...}}`（`data` 为可公开访问的 URL 时常用）；
* **audio**：`{"type": "input_audio", "input_audio": {"data": ..., "format": ...}}`（由 `data:audio/...;base64,...` 解析格式）。

若字段提供了 `data_id`（非空），对应项会附带 `"uuid"` 字段。

### property dashscope_input

```python
dashscope_input -> dict[str, Any]
```

生成适用于阿里云 DashScope 多模态向量接口的**单个**输入字典（内部带缓存；调用 `add_field` 后会失效并重建）。

**返回**：

**dict[str, Any]**，常见键包括：`text`（字符串）、`image`（单图 URL 或可被接口接受的字符串）、`multi_images`（多图列表）、`video`（视频 URL 字符串）。多图时若仅一张则使用 `image`，多张则使用 `multi_images`。

**说明**：

* 每种模态在 DashScope 载荷中通常至多出现一次；**多张图片**会合并为 `image` 或 `multi_images`，其它模态重复添加会校验失败。
* **audio** 当前不在 DashScope 该格式中支持，构建时会失败。
* **video** 仅支持 URL 形式，不支持 `data:video/...;base64,...` 这类内联 Base64。

### add_field

```python
add_field(kind: Literal["text", "image", "audio", "video"], data: str = NOT_SET, file_path: Path = NOT_SET, data_id: str = "") -> Self
```

向当前多模态文档添加数据字段，支持链式调用。`kind` 为 `text` 时一般传入文本字符串作为 `data`；其它模态可通过 `file_path` 读本地文件，或通过 `data` 传入 URL（非 audio 时可为 `http`/`https`）或 `data:{kind}/...;base64,...` 形式的数据。

**参数**：

* **kind**(Literal["text", "image", "audio", "video"])：新字段的模态类型。
* **data**(str, 可选)：文本内容、资源 URL，或符合 `data:{kind}/...;base64,...` 的编码串。与 `file_path` 二选一，不可同时提供。默认值：模块内未设置哨兵（未传则表示未提供）。
* **file_path**(Path, 可选)：本地多模态文件路径；将按 MIME 推断类型，非文本会读入并转为带前缀的 Base64 字符串。与 `data` 二选一。默认值：模块内未设置哨兵。
* **data_id**(str, 可选)：非文本字段的可选标识；若显式传入则须为长度不超过 32 的字符串。留空时，非 `text` 字段会自动生成 UUID（32 位十六进制）。`text` 字段不使用 `data_id`（保持为空字符串）。默认值：""。

**返回**：

**Self**，返回当前 MultimodalDocument 实例。

**说明**：

支持的模态类型：
* **text**：纯文本；也可通过 `file_path` 从 UTF-8 文本文件载入。
* **image**：常见图片格式（如 jpg、png）；本地路径或 URL / Base64 数据 URL。
* **audio**：音频文件或 `data:audio/...;base64,...`。
* **video**：视频文件或 URL；若需写入 `dashscope_input`，须为 URL，不能为 `data:video/` Base64。

`file_path` 须为存在的 `Path`；`data` 与 `file_path` 必须且只能提供其一（由实现校验）。

### strip

```python
strip() -> Self | None
```

与字符串类 API 类似的兼容方法：若尚未通过 `add_field` 添加任何多模态字段（内部 `_data` 为空），返回 `None`；否则返回当前实例。

**返回**：

**Self | None**，无多模态字段时为 `None`，否则为当前文档实例。

**样例**：

```python
>>> from pathlib import Path
>>> from openjiuwen.core.retrieval.common.document import MultimodalDocument
>>> 
>>> # 文本 + 本地图 + URL 图（两张图时 dashscope_input 使用 multi_images）
>>> doc = MultimodalDocument()
>>> doc.add_field("text", "这是一个描述")
>>> doc.add_field("image", file_path=Path("image.jpg"))
>>> doc.add_field("image", data="https://example.com/image.png")
>>> content = doc.content
>>> dash_payload = doc.dashscope_input  # DashScope 请求里单条 input，勿与 audio 字段混在同一文档上访问
>>> 
>>> # 链式调用
>>> doc2 = (
...     MultimodalDocument()
...     .add_field("text", "Hello world")
...     .add_field("image", file_path=Path("photo.png"))
... )
>>> 
>>> # 仅通用嵌入格式需要音频时（含 audio 时不要对该文档取 dashscope_input）
>>> doc2.add_field("audio", data="data:audio/wav;base64,...")
>>> _ = doc2.content
```
