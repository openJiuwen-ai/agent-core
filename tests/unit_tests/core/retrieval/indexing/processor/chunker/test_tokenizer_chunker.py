# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Tokenizer chunker test cases
"""

from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.retrieval import TokenizerChunker
from openjiuwen.core.retrieval import TextChunk


@pytest.fixture
def mock_tokenizer():
    """Create mock tokenizer"""
    tokenizer = MagicMock()
    return tokenizer


class TestTokenizerChunker:
    """Tokenizer chunker tests"""

    @classmethod
    def test_init(cls, mock_tokenizer):
        """Test initialization"""
        chunker = TokenizerChunker(
            chunk_size=512,
            chunk_overlap=50,
            tokenizer=mock_tokenizer,
        )
        assert chunker.chunk_size == 512
        assert chunker.chunk_overlap == 50
        assert chunker.tokenizer == mock_tokenizer

    @patch("openjiuwen.core.retrieval.indexing.processor.chunker.tokenizer_chunker.IndexSentenceSplitter")
    def test_chunk_text_success(self, mock_splitter_class, mock_tokenizer):
        """Test chunking text successfully"""
        # Mock IndexSentenceSplitter
        mock_splitter = MagicMock()
        mock_node1 = TextChunk(id_="1", text="chunk 1", doc_id="doc_1")
        mock_node2 = TextChunk(id_="2", text="chunk 2", doc_id="doc_1")
        mock_splitter.split.return_value = [mock_node1, mock_node2]
        mock_splitter_class.return_value = mock_splitter

        chunker = TokenizerChunker(
            chunk_size=512,
            chunk_overlap=50,
            tokenizer=mock_tokenizer,
        )
        chunks = chunker.chunk_text("This is a test text for chunking")
        assert len(chunks) == 2
        assert chunks[0] == "chunk 1"
        assert chunks[1] == "chunk 2"
        mock_splitter_class.assert_called_once_with(
            tokenizer=mock_tokenizer,
            chunk_size=512,
            chunk_overlap=50,
        )

    @classmethod
    def test_chunk_text_empty(cls, mock_tokenizer):
        """Test chunking empty text"""
        chunker = TokenizerChunker(
            chunk_size=512,
            chunk_overlap=50,
            tokenizer=mock_tokenizer,
        )
        chunks = chunker.chunk_text("")
        assert chunks == []

    @classmethod
    def test_chunk_text_none(cls, mock_tokenizer):
        """Test chunking None"""
        chunker = TokenizerChunker(
            chunk_size=512,
            chunk_overlap=50,
            tokenizer=mock_tokenizer,
        )
        chunks = chunker.chunk_text(None)
        assert chunks == []
