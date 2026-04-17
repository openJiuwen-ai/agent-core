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

    @classmethod
    def test_call_long_sentence_with_tokenizer_dec_produces_multiple_windows(cls, mock_tokenizer):
        """Long segments are sub-split into overlapping token windows when tokenizer_dec is set."""
        long_sentence = " ".join([f"w{i}" for i in range(25)])  # 25 whitespace tokens
        with patch("openjiuwen.core.retrieval.indexing.processor.splitter.splitter.Segmenter") as mock_segmenter_class:
            mock_segmenter = MagicMock()
            mock_segmenter.segment.return_value = [long_sentence]
            mock_segmenter_class.return_value = mock_segmenter

            splitter = SentenceSplitter(
                tokenizer=mock_tokenizer,
                chunk_size=10,
                chunk_overlap=0,
                lan="en",
                tokenizer_dec=_mock_decode,
            )
            chunks = splitter(long_sentence)
            assert len(chunks) == 3
            covered = set()
            for text, start, end in chunks:
                assert start == 0 and end == len(long_sentence)
                covered.update(text.split())
            assert covered == set(long_sentence.split())

    @classmethod
    def test_call_long_sentence_without_tokenizer_dec_stays_single_chunk(cls, mock_tokenizer):
        """Without tokenizer_dec, an over-limit sentence remains one chunk (no sub-split)."""
        long_sentence = " ".join([f"w{i}" for i in range(25)])
        with patch("openjiuwen.core.retrieval.indexing.processor.splitter.splitter.Segmenter") as mock_segmenter_class:
            mock_segmenter = MagicMock()
            mock_segmenter.segment.return_value = [long_sentence]
            mock_segmenter_class.return_value = mock_segmenter

            splitter = SentenceSplitter(
                tokenizer=mock_tokenizer,
                chunk_size=10,
                chunk_overlap=0,
                lan="en",
                tokenizer_dec=None,
            )
            chunks = splitter(long_sentence)
            assert len(chunks) == 1
            assert chunks[0][0] == long_sentence

    @classmethod
    def test_split_long_segment_respects_overlap_step(cls, mock_tokenizer):
        """Sub-windows use step chunk_size - chunk_overlap."""
        long_sentence = " ".join([f"t{i}" for i in range(20)])
        with patch("openjiuwen.core.retrieval.indexing.processor.splitter.splitter.Segmenter") as mock_segmenter_class:
            mock_segmenter = MagicMock()
            mock_segmenter.segment.return_value = [long_sentence]
            mock_segmenter_class.return_value = mock_segmenter

            splitter = SentenceSplitter(
                tokenizer=mock_tokenizer,
                chunk_size=6,
                chunk_overlap=2,
                lan="en",
                tokenizer_dec=_mock_decode,
            )
            chunks = splitter(long_sentence)
            assert len(chunks) >= 2
            for text, _, _ in chunks:
                assert len(text.split()) <= 6


class TestDetectChinese:
    """
    Tests for SentenceSplitter._detect_chinese (numpy UTF-32 CJK count + punctuation heuristic).

    Fullwidth punctuation (e.g. U+FF1F/U+FF01) is not counted in the 0x4E00-0x9FFF range;
    the fallback uses counts of ？ vs ? and ！ vs ! when the CJK ratio is below threshold.
    """

    @staticmethod
    def test_empty_text_is_en():
        assert SentenceSplitter._detect_chinese("") == "en"

    @staticmethod
    def test_ascii_is_en():
        assert SentenceSplitter._detect_chinese("Hello world. How are you?") == "en"

    @staticmethod
    def test_mostly_chinese_is_zh():
        text = "这是中文句子。它应该被检测为中文。"
        assert SentenceSplitter._detect_chinese(text) == "zh"

    @staticmethod
    def test_mixed_below_threshold_is_en():
        # 2 CJK ideographs in long ASCII; int(0.1 * n) threshold not met without punctuation boost
        text = "Hello world " * 5 + "中文"
        assert SentenceSplitter._detect_chinese(text) == "en"

    @staticmethod
    def test_integer_threshold_requires_floor_of_ratio():
        """Uses threshold_val = int(threshold * len(text)), then chinese_count >= threshold_val."""
        # 20 chars, default threshold 0.1 -> threshold_val=2; one CJK ideograph -> stay en
        text = "abcdefghijklmnopqrs中"
        assert len(text) == 20
        assert SentenceSplitter._detect_chinese(text) == "en"
        # Same length with two CJK in range -> zh
        text_two = "abcdefghijklmnopqr中文"
        assert len(text_two) == 20
        assert SentenceSplitter._detect_chinese(text_two) == "zh"

    @staticmethod
    def test_punctuation_heuristic_zh_when_cjk_count_low():
        """More fullwidth ？ than ASCII ? AND more ！ than ASCII ! -> zh (ideograph count can be 0)."""
        text = "Latin only here？？！！"
        assert SentenceSplitter._detect_chinese(text) == "zh"

    @staticmethod
    def test_punctuation_heuristic_requires_both_conditions():
        """Only ？>? but not ！>! -> still en when ideograph count below threshold."""
        text = "Hello world？？？"
        assert SentenceSplitter._detect_chinese(text) == "en"

    @staticmethod
    def test_custom_threshold_parameter():
        assert SentenceSplitter._detect_chinese("ab中", threshold=0.5) == "zh"
        assert SentenceSplitter._detect_chinese("ab中", threshold=0.99) == "en"
