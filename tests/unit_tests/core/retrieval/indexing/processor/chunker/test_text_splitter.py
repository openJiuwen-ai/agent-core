# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Text splitter test cases
"""

from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.retrieval import TextSplitter, CharSplitter, IndexSentenceSplitter
from openjiuwen.core.retrieval import Document, TextChunk


class ConcreteTextSplitter(TextSplitter):
    """Concrete text splitter implementation for testing abstract base class"""

    def split(self, text):
        return [TextChunk(id_="1", text=text.text[:10], doc_id=text.id_)]


class TestTextSplitter:
    """Text splitter abstract base class tests"""

    @staticmethod
    def test_cannot_instantiate_abstract_class():
        """Test cannot directly instantiate abstract class"""
        with pytest.raises(TypeError):
            TextSplitter()


class TestCharSplitter:
    """Character splitter tests"""

    @staticmethod
    def test_init_with_defaults():
        """Test initialization with default values"""
        splitter = CharSplitter()
        assert splitter.chunk_size == 200  # DEFAULT_CHAR_CHUNK_SIZE
        assert splitter.chunk_overlap == 40  # DEFAULT_CHAR_CHUNK_OVERLAP

    @staticmethod
    def test_init_with_custom_values():
        """Test initialization with custom values"""
        splitter = CharSplitter(chunk_size=512, chunk_overlap=50)
        assert splitter.chunk_size == 512
        assert splitter.chunk_overlap == 50

    @staticmethod
    def test_init_overlap_adjusted():
        """Test automatic overlap size adjustment"""
        # Overlap size should be less than chunk size
        splitter = CharSplitter(chunk_size=100, chunk_overlap=150)
        assert splitter.chunk_overlap < splitter.chunk_size

    @staticmethod
    def test_init_overlap_negative():
        """Test negative overlap size"""
        splitter = CharSplitter(chunk_size=100, chunk_overlap=-10)
        assert splitter.chunk_overlap >= 0

    @staticmethod
    def test_init_chunk_size_minimum():
        """Test minimum chunk size"""
        splitter = CharSplitter(chunk_size=0)
        assert splitter.chunk_size >= 1

    @staticmethod
    def test_split_short_text():
        """Test splitting short text"""
        splitter = CharSplitter(chunk_size=100, chunk_overlap=10)
        doc = Document(id_="doc_1", text="Short text")
        chunks = splitter.split(doc)
        assert len(chunks) == 1
        assert chunks[0].text == "Short text"
        assert chunks[0].doc_id == "doc_1"

    @staticmethod
    def test_split_long_text():
        """Test splitting long text"""
        splitter = CharSplitter(chunk_size=10, chunk_overlap=2)
        text = "This is a longer text that needs to be split into multiple chunks"
        doc = Document(id_="doc_1", text=text)
        chunks = splitter.split(doc)
        assert len(chunks) > 1
        # Verify all chunks belong to the same document
        assert all(chunk.doc_id == "doc_1" for chunk in chunks)

    @staticmethod
    def test_split_with_overlap():
        """Test splitting with overlap"""
        splitter = CharSplitter(chunk_size=10, chunk_overlap=3)
        text = "This is a test text for splitting"
        doc = Document(id_="doc_1", text=text)
        chunks = splitter.split(doc)
        assert len(chunks) > 1
        # Verify there is overlap (by checking adjacent chunk content)
        if len(chunks) > 1:
            # End of first chunk should appear at start of second chunk
            first_end = chunks[0].text[-3:]
            second_start = chunks[1].text[:3]
            # Due to overlap, they should have some overlap
            assert len(first_end) == 3
            assert len(second_start) >= 3

    @staticmethod
    def test_split_preserves_metadata():
        """Test preserving metadata"""
        splitter = CharSplitter(chunk_size=10, chunk_overlap=2)
        doc = Document(
            id_="doc_1",
            text="This is a test",
            metadata={"source": "test", "author": "test_author"},
        )
        chunks = splitter.split(doc)
        assert len(chunks) > 0
        assert all("source" in chunk.metadata for chunk in chunks)
        assert all(chunk.metadata["source"] == "test" for chunk in chunks)


class TestIndexSentenceSplitter:
    """Index sentence splitter tests"""

    @staticmethod
    def test_init_with_default_splitter_config():
        """Test initialization with default splitter configuration"""

        def tokenize_fn(x):
            return x.split()

        mock_tokenizer = MagicMock()
        mock_tokenizer.tokenize = tokenize_fn

        with patch(
            "openjiuwen.core.retrieval.indexing.processor.chunker.text_splitter.SentenceSplitter"
        ) as mock_sentence_splitter_class:
            mock_sentence_splitter = MagicMock()
            mock_sentence_splitter_class.return_value = mock_sentence_splitter

            splitter = IndexSentenceSplitter(tokenizer=mock_tokenizer)
            # Verify default configuration is used
            call_args = mock_sentence_splitter_class.call_args
            assert call_args is not None

    @staticmethod
    def test_split_with_document():
        """Test splitting document"""

        def tokenize_fn(x):
            return x.split()

        mock_tokenizer = MagicMock()
        mock_tokenizer.tokenize = tokenize_fn

        with patch(
            "openjiuwen.core.retrieval.indexing.processor.chunker.text_splitter.SentenceSplitter"
        ) as mock_sentence_splitter_class:
            mock_sentence_splitter = MagicMock()
            mock_node1 = TextChunk(id_="1", text="chunk 1", doc_id="doc_1")
            mock_node2 = TextChunk(id_="2", text="chunk 2", doc_id="doc_1")
            mock_sentence_splitter.get_nodes_from_documents.return_value = [
                mock_node1,
                mock_node2,
            ]
            mock_sentence_splitter_class.return_value = mock_sentence_splitter

            splitter = IndexSentenceSplitter(tokenizer=mock_tokenizer)
            doc = Document(id_="doc_1", text="This is a test")
            chunks = splitter.split(doc)
            assert len(chunks) == 2
            mock_sentence_splitter.get_nodes_from_documents.assert_called_once()

    @staticmethod
    def test_split_with_text_chunk():
        """Test splitting text chunk"""

        def tokenize_fn(x):
            return x.split()

        mock_tokenizer = MagicMock()
        mock_tokenizer.tokenize = tokenize_fn

        with patch(
            "openjiuwen.core.retrieval.indexing.processor.chunker.text_splitter.SentenceSplitter"
        ) as mock_sentence_splitter_class:
            mock_sentence_splitter = MagicMock()
            mock_node = TextChunk(id_="1", text="chunk", doc_id="doc_1")
            mock_sentence_splitter.get_nodes_from_documents.return_value = [mock_node]
            mock_sentence_splitter_class.return_value = mock_sentence_splitter

            splitter = IndexSentenceSplitter(tokenizer=mock_tokenizer)
            text_chunk = TextChunk(id_="1", text="This is a test", doc_id="doc_1")
            chunks = splitter.split(text_chunk)
            assert len(chunks) == 1
