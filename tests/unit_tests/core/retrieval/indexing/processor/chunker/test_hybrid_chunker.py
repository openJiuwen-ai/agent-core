# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
HybridChunker test cases
"""

import pytest

from openjiuwen.core.retrieval.common.document import Document, TextChunk
from openjiuwen.core.retrieval.indexing.processor.chunker.char_chunker import CharChunker
from openjiuwen.core.retrieval.indexing.processor.chunker.hybrid_chunker import (
    HybridChunker,
    _default_no_split,
)


class TestDefaultNoSplit:
    """Tests for the default no-split predicate"""

    @staticmethod
    def test_row_source_type():
        doc = Document(id_="1", text="a]", metadata={"source_type": "row"})
        assert _default_no_split(doc) is True

    @staticmethod
    def test_column_source_type():
        doc = Document(id_="1", text="a", metadata={"source_type": "column"})
        assert _default_no_split(doc) is True

    @staticmethod
    def test_other_source_type():
        doc = Document(id_="1", text="a", metadata={"source_type": "paragraph"})
        assert _default_no_split(doc) is False

    @staticmethod
    def test_no_source_type():
        doc = Document(id_="1", text="a", metadata={"title": "hello"})
        assert _default_no_split(doc) is False

    @staticmethod
    def test_empty_metadata():
        doc = Document(id_="1", text="a", metadata={})
        assert _default_no_split(doc) is False


class TestHybridChunker:
    """HybridChunker tests"""

    @staticmethod
    def _make_inner(chunk_size=512, chunk_overlap=50):
        return CharChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    def test_init_inherits_inner_params(self):
        inner = self._make_inner(chunk_size=256, chunk_overlap=30)
        chunker = HybridChunker(inner_chunker=inner)
        assert chunker.chunk_size == 256
        assert chunker.chunk_overlap == 30

    def test_chunk_text_delegates_to_inner(self):
        inner = self._make_inner(chunk_size=10, chunk_overlap=2)
        chunker = HybridChunker(inner_chunker=inner)
        result = chunker.chunk_text("hello world, this is a test")
        inner_result = inner.chunk_text("hello world, this is a test")
        assert result == inner_result

    def test_row_doc_single_chunk(self):
        """Row doc should be emitted as one chunk without splitting"""
        inner = self._make_inner(chunk_size=10, chunk_overlap=2)
        chunker = HybridChunker(inner_chunker=inner)
        doc = Document(
            id_="row1",
            text="姓名: 张三, 部门: 研发, 工号: 1001",
            metadata={"source_type": "row", "sheet_name": "Sheet1", "row_index": 2},
        )
        chunks = chunker.chunk_documents([doc])
        assert len(chunks) == 1
        assert chunks[0].doc_id == "row1"
        assert chunks[0].text == doc.text.strip()
        assert chunks[0].metadata["source_type"] == "row"
        assert chunks[0].metadata["chunk_index"] == 0
        assert chunks[0].metadata["total_chunks"] == 1

    def test_column_doc_single_chunk(self):
        """Column doc should be emitted as one chunk without splitting"""
        inner = self._make_inner(chunk_size=10, chunk_overlap=2)
        chunker = HybridChunker(inner_chunker=inner)
        doc = Document(
            id_="col1",
            text="列名: 姓名。取值: 张三, 李四, 王五",
            metadata={"source_type": "column", "sheet_name": "Sheet1", "column_name": "姓名"},
        )
        chunks = chunker.chunk_documents([doc])
        assert len(chunks) == 1
        assert chunks[0].doc_id == "col1"
        assert chunks[0].metadata["source_type"] == "column"

    def test_normal_doc_delegates_to_inner(self):
        """Normal doc (no source_type) should be split by inner chunker"""
        inner = self._make_inner(chunk_size=10, chunk_overlap=2)
        chunker = HybridChunker(inner_chunker=inner)
        doc = Document(
            id_="doc1",
            text="This is a long document that should be split into multiple chunks by the inner chunker",
            metadata={"title": "test"},
        )
        chunks = chunker.chunk_documents([doc])
        assert len(chunks) > 1
        assert all(c.doc_id == "doc1" for c in chunks)

    def test_mixed_documents(self):
        """Mixed row + normal docs should be handled correctly"""
        inner = self._make_inner(chunk_size=10, chunk_overlap=2)
        chunker = HybridChunker(inner_chunker=inner)
        docs = [
            Document(id_="row1", text="姓名: 张三", metadata={"source_type": "row"}),
            Document(
                id_="doc1",
                text="This is a long document that should be split into multiple chunks",
                metadata={},
            ),
            Document(id_="col1", text="列名: 部门", metadata={"source_type": "column"}),
        ]
        chunks = chunker.chunk_documents(docs)
        row_chunks = [c for c in chunks if c.doc_id == "row1"]
        col_chunks = [c for c in chunks if c.doc_id == "col1"]
        doc_chunks = [c for c in chunks if c.doc_id == "doc1"]
        assert len(row_chunks) == 1
        assert len(col_chunks) == 1
        assert len(doc_chunks) > 1

    def test_empty_text_row_doc_delegates_to_inner(self):
        """Row doc with blank text: no_split_when matches but text is empty, so delegates to inner"""
        inner = self._make_inner()
        chunker = HybridChunker(inner_chunker=inner)
        doc = Document(id_="row1", text="   ", metadata={"source_type": "row"})
        hybrid_chunks = chunker.chunk_documents([doc])
        inner_chunks = inner.chunk_documents([doc])
        assert len(hybrid_chunks) == len(inner_chunks)

    def test_empty_string_text_row_doc(self):
        """Row doc with empty string text delegates to inner"""
        inner = self._make_inner()
        chunker = HybridChunker(inner_chunker=inner)
        doc = Document(id_="row1", text="", metadata={"source_type": "row"})
        hybrid_chunks = chunker.chunk_documents([doc])
        inner_chunks = inner.chunk_documents([doc])
        assert len(hybrid_chunks) == len(inner_chunks)

    def test_custom_no_split_when(self):
        """Custom predicate should override default behavior"""
        inner = self._make_inner(chunk_size=10, chunk_overlap=2)
        chunker = HybridChunker(
            inner_chunker=inner,
            no_split_when=lambda doc: (doc.metadata or {}).get("keep_whole") is True,
        )
        doc_keep = Document(id_="a", text="short text", metadata={"keep_whole": True})
        doc_split = Document(
            id_="b",
            text="This is a long document that should be split by the inner chunker",
            metadata={"source_type": "row"},  # would match default, but custom predicate ignores it
        )
        chunks = chunker.chunk_documents([doc_keep, doc_split])
        keep_chunks = [c for c in chunks if c.doc_id == "a"]
        split_chunks = [c for c in chunks if c.doc_id == "b"]
        assert len(keep_chunks) == 1
        assert len(split_chunks) > 1

    def test_metadata_preserved_in_single_chunk(self):
        """Original metadata should be preserved and chunk metadata added"""
        inner = self._make_inner()
        chunker = HybridChunker(inner_chunker=inner)
        doc = Document(
            id_="row1",
            text="content",
            metadata={"source_type": "row", "sheet_name": "S1", "custom_key": "custom_val"},
        )
        chunks = chunker.chunk_documents([doc])
        assert len(chunks) == 1
        meta = chunks[0].metadata
        assert meta["source_type"] == "row"
        assert meta["sheet_name"] == "S1"
        assert meta["custom_key"] == "custom_val"
        assert meta["chunk_index"] == 0
        assert meta["total_chunks"] == 1
        assert "chunk_id" in meta

    def test_empty_documents_list(self):
        """Empty input should return empty output"""
        inner = self._make_inner()
        chunker = HybridChunker(inner_chunker=inner)
        assert chunker.chunk_documents([]) == []
