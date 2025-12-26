from typing import List, Tuple, Callable
from pysbd import Segmenter

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.common.document import Document, TextChunk
from openjiuwen.core.retrieval.indexing.processor.spliter.base import Splitter


class SentenceSplitter(Splitter):
    def __init__(
        self,
        tokenizer: Callable,
        chunk_size: int,
        chunk_overlap: int,
        lan: str = "zh",
    ):
        """
        初始化句子分割器
        
        Args:
            tokenizer: 分词器，需要具有 encode 和 decode 方法
            chunk_size: 分块大小（token 数）
            chunk_overlap: 分块重叠大小（token 数）
            lan: 语言代码，默认为 "zh"（中文）
        """
        super().__init__(
            tokenizer=tokenizer,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        
        self.seg = Segmenter(language=lan, clean=False)

    def __call__(self, doc: str) -> List[Tuple[str, int, int]]:
        """
        分割文档为句子级别的块
        
        Args:
            doc: 待分割的文档文本
            
        Returns:
            分割后的块列表，每个元素为 (文本, 起始字符位置, 结束字符位置)
        """
        if not doc or not doc.strip():
            return []
        sentences_with_spans = self._sentences_with_spans(doc)
        chunks: List[Tuple[str, int, int]] = []
        cur_sents: List[Tuple[str, int, int]] = []

        for sent_text, sent_start, sent_end in sentences_with_spans:
            if not sent_text.strip():
                continue

            sent_tokens = self.tokenizer_enc(sent_text)
            sent_len = len(sent_tokens)

            if sent_len > self.chunk_size:
                chunks, cur_sents = self._flush(chunks, cur_sents)
                chunks.append((sent_text, sent_start, sent_end))
                continue

            cur_chunk_text = " ".join(s[0] for s in cur_sents)
            cur_chunk_tokens = self.tokenizer_enc(cur_chunk_text) if cur_sents else []

            if len(cur_chunk_tokens) + sent_len <= self.chunk_size:
                cur_sents.append((sent_text, sent_start, sent_end))
            else:
                chunks, cur_sents = self._flush(chunks, cur_sents)
                cur_sents = [(sent_text, sent_start, sent_end)]

        chunks, _ = self._flush(chunks, cur_sents)
        logger.info(f"Computed the following sentence-level chunks: {len(chunks)} chunks")
        return chunks

    def _sentences_with_spans(self, text: str) -> List[Tuple[str, int, int]]:
        sentences = self.seg.segment(text)
        spans = []
        start = 0

        for sent in sentences:
            if not sent.strip():
                continue
            # Find next occurrence starting from `start` to avoid duplicates
            idx = text.find(sent, start)
            if idx == -1:
                # Fallback: skip (unlikely with clean=False)
                logger.warning(f"Span recovery failed for: {repr(sent[:30])}...")
                continue
            end = idx + len(sent)
            spans.append((sent, idx, end))
            start = end

        return spans

    def _flush(
        self, chunks: List[Tuple[str, int, int]], cur_sents: List[Tuple[str, int, int]]
    ) -> Tuple[List[Tuple[str, int, int]], List[Tuple[str, int, int]]]:
        if not cur_sents:
            return chunks, []

        chunk_text = " ".join(s[0] for s in cur_sents)
        start_char = cur_sents[0][1]
        end_char = cur_sents[-1][2]
        chunks.append((chunk_text, start_char, end_char))

        # Handle overlap
        next_cur_sents = []
        if self.chunk_overlap > 0 and len(cur_sents) > 1:
            overlap_tokens = 0
            overlap_sents = []
            for sent_text, s_start, s_end in reversed(cur_sents):
                sent_toks = len(self.tokenizer_enc(sent_text))
                if overlap_tokens + sent_toks <= self.chunk_overlap:
                    overlap_sents.append((sent_text, s_start, s_end))
                    overlap_tokens += sent_toks
                else:
                    break
            next_cur_sents = list(reversed(overlap_sents))

        return chunks, next_cur_sents

