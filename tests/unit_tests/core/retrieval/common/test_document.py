# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Document data model test cases
"""
import pytest
from pydantic import ValidationError

from openjiuwen.core.retrieval.common.document import Document, TextChunk


class TestDocument:
    """Document data model tests"""

    @staticmethod
    def test_create_document():
        """Test creating document"""
        doc = Document(text="Test document")
        assert doc.text == "Test document"
        assert doc.id_ is not None
        assert doc.metadata == {}

    @staticmethod
    def test_create_document_with_metadata():
        """Test creating document with metadata"""
        metadata = {"source": "test", "author": "test_author"}
        doc = Document(text="Test document", metadata=metadata)
        assert doc.text == "Test document"
        assert doc.metadata == metadata

    @staticmethod
    def test_create_document_with_id():
        """Test creating document with ID"""
        doc = Document(id_="test_id", text="Test document")
        assert doc.id_ == "test_id"
        assert doc.text == "Test document"

    @staticmethod
    def test_missing_text():
        """Test missing required text"""
        with pytest.raises(ValidationError):
            Document()


class TestTextChunk:
    """Text chunk data model tests"""

    @staticmethod
    def test_create_text_chunk():
        """Test creating text chunk"""
        chunk = TextChunk(
            id_="chunk_1",
            text="Test chunk",
            doc_id="doc_1",
        )
        assert chunk.id_ == "chunk_1"
        assert chunk.text == "Test chunk"
        assert chunk.doc_id == "doc_1"
        assert chunk.metadata == {}
        assert chunk.embedding is None

    @staticmethod
    def test_create_text_chunk_with_metadata():
        """Test creating text chunk with metadata"""
        metadata = {"chunk_index": 0, "source": "test"}
        chunk = TextChunk(
            id_="chunk_1",
            text="Test chunk",
            doc_id="doc_1",
            metadata=metadata,
        )
        assert chunk.metadata == metadata

    @staticmethod
    def test_create_text_chunk_with_embedding():
        """Test creating text chunk with embedding"""
        embedding = [0.1, 0.2, 0.3]
        chunk = TextChunk(
            id_="chunk_1",
            text="Test chunk",
            doc_id="doc_1",
            embedding=embedding,
        )
        assert chunk.embedding == embedding

    @staticmethod
    def test_from_document():
        """Test creating text chunk from document"""
        doc = Document(id_="doc_1", text="Test document", metadata={"source": "test"})
        chunk = TextChunk.from_document(doc, "Test chunk", "chunk_1")
        assert chunk.id_ == "chunk_1"
        assert chunk.text == "Test chunk"
        assert chunk.doc_id == "doc_1"
        assert chunk.metadata == {"source": "test"}

    @staticmethod
    def test_from_document_without_id():
        """Test creating text chunk from document (auto-generate ID)"""
        doc = Document(id_="doc_1", text="Test document")
        chunk = TextChunk.from_document(doc, "Test chunk")
        assert chunk.id_ is not None
        assert chunk.text == "Test chunk"
        assert chunk.doc_id == "doc_1"

    @staticmethod
    def test_missing_required_fields():
        """Test missing required fields"""
        with pytest.raises(ValidationError):
            TextChunk()
        
        with pytest.raises(ValidationError):
            TextChunk(id_="chunk_1")
        
        with pytest.raises(ValidationError):
            TextChunk(id_="chunk_1", text="Test chunk")

