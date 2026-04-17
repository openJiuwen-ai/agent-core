# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import uuid
from abc import ABCMeta, abstractmethod
from typing import TYPE_CHECKING, Any, Callable, Tuple, Union

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.common.document import Document, TextChunk
from openjiuwen.core.retrieval.indexing.processor.splitter.splitter import SentenceSplitter

if TYPE_CHECKING:
    from transformers import PreTrainedTokenizerBase

# Defaults for sentence / token chunking and for character-based CharSplitter.
DEFAULT_CHUNK_SIZE = 200
DEFAULT_CHAR_CHUNK_SIZE = 200
DEFAULT_CHAR_CHUNK_OVERLAP = 40

# Fallback for Hugging Face encode(..., truncation=True, max_length=...) when the tokenizer
# does not report model_max_length (or reports inf). 131072 tokens is a common "128K context"
# headline figure; we use half so one encode() stays within practical ingestion limits—room
# for prompts, special tokens, and pipelines where chunks must still fit a downstream LLM.
DEFAULT_SAFE_ENCODE_MAX_LENGTH = 65536


class TextSplitter(metaclass=ABCMeta):
    """Abstract base class for text splitters"""

    @abstractmethod
    def split(self, doc: TextChunk | Document) -> list[TextChunk]:
        """Split document into text chunks"""


class CharSplitter(TextSplitter):
    """Simple text splitter based on character length, no dependency on tokenizer."""

    def __init__(self, chunk_size: int | None = None, chunk_overlap: int | None = None) -> None:
        super().__init__()
        # None -> use module defaults above
        size = chunk_size or DEFAULT_CHAR_CHUNK_SIZE
        overlap = chunk_overlap if chunk_overlap is not None else DEFAULT_CHAR_CHUNK_OVERLAP
        # Limit range to avoid step becoming 0 or negative
        overlap = max(0, min(overlap, size - 1))
        self.chunk_size = max(1, size)
        self.chunk_overlap = overlap

    def split(self, doc: TextChunk | Document) -> list[TextChunk]:
        text = doc.text or ""
        # Keep metadata and exclusion fields for subsequent indexing/deletion
        doc_id = doc.id_
        meta = doc.metadata or {}

        res: list[TextChunk] = []
        step = self.chunk_size - self.chunk_overlap if self.chunk_size > self.chunk_overlap else self.chunk_size
        start = 0
        while start < len(text):
            end = min(len(text), start + self.chunk_size)
            res.append(
                TextChunk(
                    id_=str(uuid.uuid4()),
                    text=text[start:end],
                    doc_id=doc_id,
                    metadata=meta,
                )
            )
            start += step
        return res


class IndexSentenceSplitter(TextSplitter):
    """
    SentenceSplitter wrapper with sentence splitting capabilities.
    """

    def __init__(
        self,
        tokenizer: Union["PreTrainedTokenizerBase", Any] = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        splitter_config: dict | None = None,
        language: str = "auto",
    ) -> None:
        """Wrapper with sentence splitting capabilities.

        Args:
            tokenizer (PreTrainedTokenizerBase): Tokenizer.
            chunk_size (int | None, optional): Chunk size to split documents into passages. Defaults to None.
                Note: this is based on tokens produced by the tokenizer of embedding model.
                If None, set to the maximum sequence length of the embedding model.
            chunk_overlap (int | None, optional): Window size for passage overlap. Defaults to None.
                If None, set to `chunk_size // 5`.
            splitter_config (dict, optional): Reserved for future splitter options. Defaults to None.
            language: pysbd language: "auto" infers zh vs en from text; other values pass through to Segmenter.
        """
        super().__init__()
        self._tokenizer = tokenizer

        # Reserved for future splitter options; normalize non-dict (e.g. None) to a default dict.
        if not isinstance(splitter_config, dict):
            splitter_config = {
                "paragraph_separator": "\n",
            }

        # encode + optional decode; decode is passed to SentenceSplitter for long-sentence sub-splitting.
        tokenizer_fn, tokenizer_dec, max_token_length = self._resolve_tokenizer(self._tokenizer)
        chunk_size = self._resolve_chunk_size(chunk_size, max_token_length)

        self._splitter = SentenceSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap or chunk_size // 5,
            tokenizer=tokenizer_fn,
            tokenizer_dec=tokenizer_dec,
            lan=language,
        )

    @staticmethod
    def _resolve_tokenizer(
        tokenizer: Union["PreTrainedTokenizerBase", Any],
    ) -> Tuple[Callable[[str], list], Callable[[list], str] | None, int | None]:
        """
        Return (encode_callable, decode_callable, max_token_length).
        Decode is used to sub-split long segments so no content is lost (no truncation).
        """
        max_token_length = IndexSentenceSplitter._max_length(tokenizer)
        # Cap for HF encode(max_length=...) on very long strings; avoids unbounded allocation.
        fallback_cap = DEFAULT_SAFE_ENCODE_MAX_LENGTH
        safe_max = max_token_length if max_token_length and max_token_length != float("inf") else fallback_cap

        if tokenizer is not None:
            # Prefer full tokenizer with encode+decode so windows can be decoded after slicing ids.
            if hasattr(tokenizer, "encode") and hasattr(tokenizer, "decode"):

                def safe_encode(text: str) -> list:
                    if not (text and text.strip()):
                        return []
                    try:
                        return tokenizer.encode(
                            text,
                            truncation=True,
                            max_length=safe_max,
                            add_special_tokens=False,
                        )
                    except Exception:
                        return tokenizer.encode(text)

                def safe_decode(ids: list) -> str:
                    if not ids:
                        return ""
                    try:
                        return tokenizer.decode(ids, skip_special_tokens=False)
                    except Exception:
                        return ""

                return safe_encode, safe_decode, max_token_length
            # tokenize() only — no round-trip decode for sub-splitting long sentences.
            if hasattr(tokenizer, "tokenize"):
                return tokenizer.tokenize, None, max_token_length
            # Custom encode callable without decode wrapper.
            if callable(tokenizer):
                return tokenizer, None, max_token_length

        # No tokenizer: lazy-import tiktoken (avoids import cost when a real tokenizer is always passed).
        try:
            import tiktoken

            encoding = tiktoken.get_encoding("cl100k_base")
            dec = getattr(encoding, "decode", None)
            return (
                encoding.encode,
                dec,
                getattr(encoding, "max_token_value", None),
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to load tiktoken fallback tokenizer: %s", exc)
            # Last resort: word-split proxy; no decode for long-sentence sub-splitting.
            return lambda text: text.split(), None, None

    @staticmethod
    def _max_length(tokenizer: Any) -> int | None:
        """Try to infer a reasonable maximum token length from a tokenizer."""
        # Common Hugging Face and similar tokenizer attributes, in typical precedence order.
        for attr in (
            "model_max_length",
            "max_len_single_sentence",
            "max_position_embeddings",
            "max_seq_length",
        ):
            if hasattr(tokenizer, attr):
                try:
                    val = int(getattr(tokenizer, attr))
                    if val and val != float("inf"):
                        return val
                except Exception:
                    logger.warning("Failed to get max length", exc_info=True)
                    continue
        return None

    @staticmethod
    def _resolve_chunk_size(
        chunk_size: int | None,
        max_token_length: int | None,
    ) -> int:
        """
        Decide chunk_size based on caller input and tokenizer limits.
        """
        # Caller omitted chunk_size — align with embedding/model sequence cap when known.
        if chunk_size is None and max_token_length:
            return max_token_length
        # Both set — do not exceed tokenizer-reported maximum.
        if chunk_size is not None and max_token_length:
            return min(chunk_size, max_token_length)
        return chunk_size or DEFAULT_CHUNK_SIZE

    def split(self, doc: TextChunk | Document) -> list[TextChunk]:
        # Note: we don't want to consider the length of metadata for chunking
        if isinstance(doc, Document):
            node = doc
        else:
            node = Document(
                id_=doc.doc_id,
                text=doc.text,
                metadata=doc.metadata,
            )

        return self._splitter.get_nodes_from_documents([node])
