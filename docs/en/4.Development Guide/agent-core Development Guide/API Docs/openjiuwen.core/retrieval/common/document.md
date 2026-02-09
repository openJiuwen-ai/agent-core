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

