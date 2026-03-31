# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Sentence splitter test cases
"""

from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.retrieval import SentenceSplitter


def _mock_encode(x):
    return x.split()


def _mock_decode(x):
    return " ".join(x)


@pytest.fixture
def mock_tokenizer():
    """Create mock tokenizer"""
    tokenizer = MagicMock()
    tokenizer.encode = _mock_encode
    tokenizer.decode = _mock_decode
    return tokenizer


@pytest.fixture
def mock_segmenter():
    """Create mock sentence segmenter"""
    segmenter = MagicMock()
    return segmenter


class TestSentenceSplitter:
    """Sentence splitter tests"""

    @classmethod
    def test_init_with_defaults(cls, mock_tokenizer):
        """Test initialization with default values"""
        splitter = SentenceSplitter(
            tokenizer=mock_tokenizer,
            chunk_size=512,
            chunk_overlap=50,
            lan="auto",
        )
        assert splitter.chunk_size == 512
        assert splitter.chunk_overlap == 50
        assert splitter.tokenizer == mock_tokenizer
        assert splitter.default_lan == ""
        assert splitter.seg is None  # Segmenter is initialized lazily in __call__

    @classmethod
    def test_init_with_custom_language(cls, mock_tokenizer):
        """Test initialization with custom language"""
        splitter = SentenceSplitter(
            tokenizer=mock_tokenizer,
            chunk_size=512,
            chunk_overlap=50,
            lan="en",
        )
        assert splitter.default_lan == "en"
        assert splitter.seg is None  # Segmenter is initialized lazily in __call__

    @classmethod
    def test_call_empty_text(cls, mock_tokenizer):
        """Test splitting empty text"""
        with patch("openjiuwen.core.retrieval.indexing.processor.splitter.splitter.Segmenter") as mock_segmenter_class:
            mock_segmenter = MagicMock()
            mock_segmenter_class.return_value = mock_segmenter

            splitter = SentenceSplitter(
                tokenizer=mock_tokenizer,
                chunk_size=512,
                chunk_overlap=50,
            )
            chunks = splitter("")
            assert chunks == []

    @classmethod
    def test_call_whitespace_only(cls, mock_tokenizer):
        """Test splitting text containing only whitespace"""
        with patch("openjiuwen.core.retrieval.indexing.processor.splitter.splitter.Segmenter") as mock_segmenter_class:
            mock_segmenter = MagicMock()
            mock_segmenter_class.return_value = mock_segmenter

            splitter = SentenceSplitter(
                tokenizer=mock_tokenizer,
                chunk_size=512,
                chunk_overlap=50,
            )
            chunks = splitter("   \n\t   ")
            assert chunks == []

    @classmethod
    def test_call_single_sentence(cls, mock_tokenizer):
        """Test splitting single sentence"""
        with patch("openjiuwen.core.retrieval.indexing.processor.splitter.splitter.Segmenter") as mock_segmenter_class:
            mock_segmenter = MagicMock()
            mock_segmenter.segment.return_value = ["This is a test sentence."]
            mock_segmenter_class.return_value = mock_segmenter

            splitter = SentenceSplitter(
                tokenizer=mock_tokenizer,
                chunk_size=512,
                chunk_overlap=50,
            )
            chunks = splitter("This is a test sentence.")
            assert len(chunks) == 1
            assert chunks[0][0] == "This is a test sentence."
            assert chunks[0][1] == 0
            assert chunks[0][2] == len("This is a test sentence.")

    @classmethod
    def test_call_multiple_sentences(cls, mock_tokenizer):
        """Test splitting multiple sentences"""
        with patch("openjiuwen.core.retrieval.indexing.processor.splitter.splitter.Segmenter") as mock_segmenter_class:
            mock_segmenter = MagicMock()
            mock_segmenter.segment.return_value = [
                "First sentence.",
                "Second sentence.",
                "Third sentence.",
            ]
            mock_segmenter_class.return_value = mock_segmenter

            splitter = SentenceSplitter(
                tokenizer=mock_tokenizer,
                chunk_size=512,
                chunk_overlap=50,
            )
            text = "First sentence. Second sentence. Third sentence."
            chunks = splitter(text)
            assert len(chunks) >= 1
            # Verify all sentences are processed
            all_text = " ".join(chunk[0] for chunk in chunks)
            assert "First sentence" in all_text
            assert "Second sentence" in all_text
            assert "Third sentence" in all_text

    @classmethod
    def test_call_long_sentence(cls, mock_tokenizer):
        """Test splitting very long sentence"""
        with patch("openjiuwen.core.retrieval.indexing.processor.splitter.splitter.Segmenter") as mock_segmenter_class:
            mock_segmenter = MagicMock()
            long_sentence = " ".join(["word"] * 1000)  # Very long sentence
            mock_segmenter.segment.return_value = [long_sentence]
            mock_segmenter_class.return_value = mock_segmenter

            splitter = SentenceSplitter(
                tokenizer=mock_tokenizer,
                chunk_size=100,  # Small chunk size
                chunk_overlap=10,
            )
            chunks = splitter(long_sentence)
            # Very long sentence should be treated as a single chunk
            assert len(chunks) >= 1

    @classmethod
    def test_call_combines_sentences(cls, mock_tokenizer):
        """Test combining sentences into chunks"""
        with patch("openjiuwen.core.retrieval.indexing.processor.splitter.splitter.Segmenter") as mock_segmenter_class:
            mock_segmenter = MagicMock()
            mock_segmenter.segment.return_value = [
                "Short sentence 1.",
                "Short sentence 2.",
                "Short sentence 3.",
            ]
            mock_segmenter_class.return_value = mock_segmenter

            splitter = SentenceSplitter(
                tokenizer=mock_tokenizer,
                chunk_size=100,  # Large enough chunk size
                chunk_overlap=10,
            )
            text = "Short sentence 1. Short sentence 2. Short sentence 3."
            chunks = splitter(text)
            # If chunk size is sufficient, should combine multiple sentences
            assert len(chunks) >= 1
            # Verify sentences are combined
            if len(chunks) == 1:
                assert "Short sentence 1" in chunks[0][0]
                assert "Short sentence 2" in chunks[0][0]
                assert "Short sentence 3" in chunks[0][0]

    @classmethod
    def test_call_splits_by_token_count_not_char_count(cls, mock_tokenizer):
        """Packing splits two sentences when tokenizer tokens (cur_sents 4-tuple) exceed chunk_size."""
        # Mock encode = split on whitespace => token count = word count.
        # First sentence 5 tokens, second 2 tokens; chunk_size=5 => two chunks (not one by character length).
        with patch("openjiuwen.core.retrieval.indexing.processor.splitter.splitter.Segmenter") as mock_segmenter_class:
            mock_segmenter = MagicMock()
            mock_segmenter.segment.return_value = [
                "one two three four five",
                "six seven",
            ]
            mock_segmenter_class.return_value = mock_segmenter

            doc = "one two three four five six seven"
            splitter = SentenceSplitter(
                tokenizer=mock_tokenizer,
                chunk_size=5,
                chunk_overlap=0,
                lan="en",
            )
            chunks = splitter(doc)
            assert len(chunks) == 2
            assert "one" in chunks[0][0] and "five" in chunks[0][0]
            assert "six" in chunks[1][0] and "seven" in chunks[1][0]
            assert chunks[0][1] == 0
            assert chunks[1][1] > chunks[0][1]

    @classmethod
    def test_call_chunk_overlap_respects_token_lengths(cls, mock_tokenizer):
        """_flush overlap keeps trailing sentences while token sum ≤ chunk_overlap (not char-based)."""
        # Three sentences, 2 tokens each. chunk_size=4 fits first two; third triggers flush.
        # chunk_overlap=3: carry last sentence of flushed group (2 tokens) into next chunk.
        with patch("openjiuwen.core.retrieval.indexing.processor.splitter.splitter.Segmenter") as mock_segmenter_class:
            mock_segmenter = MagicMock()
            s1, s2, s3 = "w1 w2", "w3 w4", "w5 w6"
            mock_segmenter.segment.return_value = [s1, s2, s3]
            mock_segmenter_class.return_value = mock_segmenter

            doc = "w1 w2 w3 w4 w5 w6"
            splitter = SentenceSplitter(
                tokenizer=mock_tokenizer,
                chunk_size=4,
                chunk_overlap=3,
                lan="en",
            )
            chunks = splitter(doc)
            assert len(chunks) == 2
            # First chunk is first two sentences (joined without extra separator by implementation).
            assert "w1" in chunks[0][0] and "w4" in chunks[0][0]
            # Second chunk must include overlapped middle sentence plus the third.
            assert "w3" in chunks[1][0] and "w4" in chunks[1][0]
            assert "w5" in chunks[1][0] and "w6" in chunks[1][0]
