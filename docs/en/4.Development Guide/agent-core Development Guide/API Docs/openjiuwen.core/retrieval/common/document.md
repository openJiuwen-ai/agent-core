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

Get the content field, returns a formatted content list for embedding.

**Returns**:

**list[dict[str, Any]]**, returns a formatted content list, each element contains type and corresponding data.

### add_field

```python
add_field(kind: Literal["text", "image", "audio", "video"], data: str = NOT_SET, file_path: Path = NOT_SET, data_id: str = "") -> Self
```

Add a data field to current multimodal document, supports method chaining.

**Parameters**:

* **kind**(Literal["text", "image", "audio", "video"]): Type of the new field.
* **data**(str, optional): Base64-encoded data string.
* **file_path**(Path, optional): Valid file path to a multimodal file.
* **data_id**(str, optional): UUID for multimodal caching, leave blank if unsure. Default: "".

**Returns**:

**Self**, returns the current MultimodalDocument instance.

**Description**:

Supported modality types:
* **text**: Plain text content
* **image**: Image files (supports common formats like jpg, png, etc.)
* **audio**: Audio files (supports various audio formats)
* **video**: Video files (supports various video formats)

**Example**:

```python
>>> from pathlib import Path
>>> from openjiuwen.core.retrieval.common.document import MultimodalDocument
>>> 
>>> # Create multimodal document with text and image
>>> doc = MultimodalDocument()
>>> doc.add_field("text", "This is a description")
>>> doc.add_field("image", file_path=Path("image.jpg"))
>>> 
>>> # Or using method chaining
>>> doc = (MultimodalDocument()
...        .add_field("text", "Hello world")
...        .add_field("image", file_path=Path("photo.png")))
>>> 
>>> # Add base64-encoded data directly
>>> doc.add_field("audio", data="data:audio/wav;base64,...")
>>> 
>>> # Access structured content for embedding
>>> content = doc.content  # Returns formatted content list for embedding
```
