# openjiuwen.core.retrieval.common.document

## class openjiuwen.core.retrieval.common.document.Document

Document data model, representing a document object.

**Parameters**:

* **id_**(str): Document ID, will be automatically generated as UUID if not provided. Default: Auto-generated.
* **text**(str): Document text content.
* **metadata**(Dict[str, Any]): Document metadata. Default: {}.

**Example**:

```python
>>> from openjiuwen.core.retrieval.common.document import Document
>>> 
>>> # Create document
>>> doc = Document(text="This is a test document", id_="doc1", metadata={"author": "test"})
>>> print(f"Document ID: {doc.id_}, Text: {doc.text}")
Document ID: doc1, Text: This is a test document
```

## class openjiuwen.core.retrieval.common.document.TextChunk

Text chunk data model, representing a text chunk of a document.

**Parameters**:

* **id_**(str): Text chunk ID.
* **text**(str): Text chunk text content.
* **doc_id**(str): Parent document ID.
* **metadata**(Dict[str, Any]): Text chunk metadata. Default: {}.
* **embedding**(list[float] | None): Text chunk embedding vector. Default: None.

### classmethod from_document

```python
from_document(doc: Document, chunk_text: str, id_: str = "") -> TextChunk
```

Create TextChunk from Document.

**Parameters**:

* **doc**(Document): Document object.
* **chunk_text**(str): Text chunk text content.
* **id_**(str): Text chunk ID, will be automatically generated as UUID if not provided. Default: "".

**Returns**:

**TextChunk**, returns the created text chunk object.

**Example**:

```python
>>> from openjiuwen.core.retrieval.common.document import Document, TextChunk
>>> 
>>> # Create document
>>> doc = Document(text="This is a test document", id_="doc1")
>>> # Create text chunk from document
>>> chunk = TextChunk.from_document(doc, chunk_text="This is a test", id_="chunk1")
>>> print(f"Chunk ID: {chunk.id_}, Doc ID: {chunk.doc_id}, Text: {chunk.text}")
Chunk ID: chunk1, Doc ID: doc1, Text: This is a test
```

## class openjiuwen.core.retrieval.common.document.MultimodalDocument

Multimodal document data model, inherits from Document, supports handling documents with multiple content types (text, image, audio, video).

> **Reference Examples**: For more usage examples, please refer to the example code in the [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) repository under the `examples/retrieval/` directory, including:
> - `showcase_multimodal_embedding.py` - Multimodal embedding examples
> - `showcase_dashscope_multimodal_embedding.py` - Alibaba Cloud DashScope multimodal embedding examples

```python
MultimodalDocument(id_: str = "", text: str = "", metadata: Dict[str, Any] = {})
```

Initialize multimodal document.

**Parameters**:

* **id_**(str): Document ID, will be automatically generated as UUID if not provided. Default: Auto-generated.
* **text**(str): Document text content (serves as a fallback for text-only services, use add_field method for multimodal content). Default: "".
* **metadata**(Dict[str, Any]): Document metadata. Default: {}.

### property content

```python
content -> list[dict[str, Any]]
```

Build a generic embedding-ready content list in field order (cached internally; invalidated when `add_field` runs).

**Returns**:

**list[dict[str, Any]]**, each item is roughly:
* **text**: `{"type": "text", "text": ...}`;
* **image** / **video**: `{"type": "image_url" | "video_url", "image_url" | "video_url": {"url": ...}}` (typical when `data` is a public URL);
* **audio**: `{"type": "input_audio", "input_audio": {"data": ..., "format": ...}}` parsed from `data:audio/...;base64,...`.

If a non-empty `data_id` was set on the field, the item includes a `"uuid"` key.

### property dashscope_input

```python
dashscope_input -> dict[str, Any]
```

Build a **single** input dict for Alibaba Cloud DashScope multimodal embedding APIs (cached internally; invalidated when `add_field` runs).

**Returns**:

**dict[str, Any]**, commonly including: `text` (string), `image` (single image string), `multi_images` (list when multiple images), `video` (video URL string). One image uses `image`; several images use `multi_images`.

**Notes**:

* Each modality usually appears at most once in the DashScope payload; **multiple images** are merged into `image` or `multi_images`. Duplicate entries for other modalities fail validation.
* **audio** is not supported in this DashScope-shaped payload and will error when building.
* **video** must be a URL; `data:video/...;base64,...` inline payloads are rejected for `dashscope_input`.

### add_field

```python
add_field(kind: Literal["text", "image", "audio", "video"], data: str = NOT_SET, file_path: Path = NOT_SET, data_id: str = "") -> Self
```

Add a multimodal field; chainable. For `kind="text"`, pass the text as `data`. Other modalities may use `file_path` for local files, or `data` as an `http`/`https` URL (except as restricted for audio) or a `data:{kind}/...;base64,...` data URL.

**Parameters**:

* **kind**(Literal["text", "image", "audio", "video"]): Modality of the new field.
* **data**(str, optional): Plain text, resource URL, or a `data:{kind}/...;base64,...` string. Mutually exclusive with `file_path`. Default: module sentinel meaning “not provided”.
* **file_path**(Path, optional): Local file path; MIME is inferred, non-text fields are read and encoded as a prefixed Base64 string. Mutually exclusive with `data`. Default: module sentinel meaning “not provided”.
* **data_id**(str, optional): Optional id for non-text fields; if provided, must be a string of length at most 32. If omitted, non-`text` fields get an auto-generated UUID (32 hex chars). `text` fields keep an empty id. Default: "".

**Returns**:

**Self**, the current MultimodalDocument instance.

**Description**:

Supported modality types:
* **text**: Plain text; may also be loaded from a UTF-8 file via `file_path`.
* **image**: Common image formats; local path, URL, or Base64 data URL.
* **audio**: Audio file or `data:audio/...;base64,...`.
* **video**: Video file or URL; for `dashscope_input` it must be a URL, not `data:video/` Base64.

`file_path` must be an existing `Path`; exactly one of `data` and `file_path` must be supplied (enforced by validation).

### strip

```python
strip() -> Self | None
```

Str-like compatibility helper: returns `None` if no fields were added via `add_field` (internal `_data` empty), otherwise returns `self`.

**Returns**:

**Self | None**, `None` when there is no multimodal field data, otherwise this document instance.

**Example**:

```python
>>> from pathlib import Path
>>> from openjiuwen.core.retrieval.common.document import MultimodalDocument
>>> 
>>> # Text + local file + URL (two images -> multi_images in dashscope_input)
>>> doc = MultimodalDocument()
>>> doc.add_field("text", "This is a description")
>>> doc.add_field("image", file_path=Path("image.jpg"))
>>> doc.add_field("image", data="https://example.com/image.png")
>>> content = doc.content
>>> dash_payload = doc.dashscope_input  # single DashScope `input`; do not mix with audio on the same doc
>>> 
>>> # Chained calls
>>> doc2 = (
...     MultimodalDocument()
...     .add_field("text", "Hello world")
...     .add_field("image", file_path=Path("photo.png"))
... )
>>> 
>>> # Audio only for generic content embedding (do not read dashscope_input after adding audio)
>>> doc2.add_field("audio", data="data:audio/wav;base64,...")
>>> _ = doc2.content
```
