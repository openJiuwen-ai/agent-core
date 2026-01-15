# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Text splitter abstract base class test cases
"""

from unittest.mock import MagicMock

import pytest

from openjiuwen.core.retrieval import Splitter
from openjiuwen.core.retrieval import Document, TextChunk


class ConcreteSplitter(Splitter):
    """Concrete splitter implementation for testing abstract base class"""

    def __call__(self, doc: str):
        # Simple split implementation: one chunk per 10 characters
        chunks = []
        for i in range(0, len(doc), 10):
            start = i
            end = min(i + 10, len(doc))
            chunks.append((doc[start:end], start, end))
        return chunks


class TestSplitter:
    """Text splitter abstract base class tests"""

    @staticmethod
    def test_init_with_defaults():
        """Test initialization with default values"""
        splitter = ConcreteSplitter()
        assert splitter.chunk_size == 512
        assert splitter.chunk_overlap == 50
        assert splitter.tokenizer is None
        assert splitter.tokenizer_enc is None
        assert splitter.tokenizer_dec is None

    @staticmethod
    def test_init_with_custom_values():
        """Test initialization with custom values"""
        splitter = ConcreteSplitter(chunk_size=1024, chunk_overlap=100)
        assert splitter.chunk_size == 1024
        assert splitter.chunk_overlap == 100

    @staticmethod
    def test_init_with_tokenizer():
        """Test initialization with tokenizer"""

        def encode_fn(x):
            return x.split()

        def decode_fn(x):
            return " ".join(x)

        mock_tokenizer = MagicMock()
        mock_tokenizer.encode = encode_fn
        mock_tokenizer.decode = decode_fn

        splitter = ConcreteSplitter(tokenizer=mock_tokenizer)
        assert splitter.tokenizer == mock_tokenizer
        assert splitter.tokenizer_enc is not None
        assert splitter.tokenizer_dec is not None

    @staticmethod
    def test_init_with_callable_tokenizer():
        """Test initialization with callable tokenizer"""

        def tokenizer_func(text):
            return text.split()

        splitter = ConcreteSplitter(tokenizer=tokenizer_func)
        assert splitter.tokenizer == tokenizer_func
        assert splitter.tokenizer_enc == tokenizer_func
        assert splitter.tokenizer_dec is None

    @staticmethod
    def test_call():
        """Test calling split method"""
        splitter = ConcreteSplitter()
        text = "This is a test text for splitting"
        chunks = splitter(text)
        assert len(chunks) > 0
        assert all(isinstance(chunk, tuple) and len(chunk) == 3 for chunk in chunks)
        assert all(isinstance(chunk[0], str) for chunk in chunks)
        assert all(isinstance(chunk[1], int) for chunk in chunks)
        assert all(isinstance(chunk[2], int) for chunk in chunks)

    @staticmethod
    def test_get_nodes_from_documents():
        """Test getting nodes from document list"""
        splitter = ConcreteSplitter()
        documents = [
            Document(id_="doc_1", text="This is document 1"),
            Document(id_="doc_2", text="This is document 2"),
        ]
        nodes = splitter.get_nodes_from_documents(documents)
        assert len(nodes) > 0
        assert all(isinstance(node, TextChunk) for node in nodes)
        assert all(node.doc_id in ["doc_1", "doc_2"] for node in nodes)

    @staticmethod
    def test_get_nodes_from_documents_empty_doc():
        """Test getting nodes from empty document"""
        splitter = ConcreteSplitter()
        documents = [
            Document(id_="doc_1", text=""),
            Document(id_="doc_2", text="This is document 2"),
        ]
        nodes = splitter.get_nodes_from_documents(documents)
        # Empty documents should be skipped
        assert len(nodes) > 0
        assert all(node.doc_id == "doc_2" for node in nodes)

    @staticmethod
    def test_get_nodes_from_documents_none_doc():
        """Test getting nodes from None document"""
        splitter = ConcreteSplitter()
        documents = [None, Document(id_="doc_2", text="This is document 2")]
        nodes = splitter.get_nodes_from_documents(documents)
        # None documents should be skipped
        assert len(nodes) > 0
        assert all(node.doc_id == "doc_2" for node in nodes)

    @staticmethod
    def test_split_text():
        """Test splitting text (returns only text list)"""
        splitter = ConcreteSplitter()
        text = "This is a test text"
        chunks = splitter.split_text(text)
        assert isinstance(chunks, list)
        assert all(isinstance(chunk, str) for chunk in chunks)
        assert len(chunks) > 0

    @staticmethod
    def test_cannot_instantiate_abstract_class():
        """Test cannot directly instantiate abstract class"""
        with pytest.raises(TypeError):
            Splitter()
