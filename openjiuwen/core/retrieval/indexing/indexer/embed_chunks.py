# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Shared logic for computing chunk embeddings in indexers.

Used by ChromaIndexer and MilvusIndexer to apply embed_documents. When the embed
model supports embed_multimodal, chunks with metadata["image_path"] are embedded
via embed_multimodal (image + text), unless use_caption_for_images=True, in which
case all chunks (including image chunks) use embed_documents with text only.
"""

import os
from pathlib import Path
from typing import List, Optional, Type

from openjiuwen.core.retrieval.common.callbacks import BaseCallback
from openjiuwen.core.retrieval.common.document import MultimodalDocument, TextChunk
from openjiuwen.core.retrieval.embedding.base import Embedding


async def compute_chunk_embeddings(
    chunks: List[TextChunk],
    embed_model: Embedding,
    *,
    doc_index_callback: Optional[Type[BaseCallback]] = None,
    use_caption_for_images: bool = False,
) -> None:
    """
    Compute embeddings for chunks in place (sets chunk.embedding).

    Args:
        chunks: Chunks to embed (mutated in place).
        embed_model: Embedding model.
        doc_index_callback: Optional callback for embed_documents progress.
        use_caption_for_images:
            If True, image chunks are embedded using their text/caption instead
            of using multimodal embeddings.
    """

    embed_multimodal = getattr(embed_model, "embed_multimodal", None)

    # If multimodal not supported OR captions explicitly requested -> text only
    if not callable(embed_multimodal) or use_caption_for_images:
        texts = [chunk.text for chunk in chunks]
        kwargs = {} if doc_index_callback is None else {"callback_cls": doc_index_callback}
        embeddings = await embed_model.embed_documents(texts, **kwargs)

        for chunk, embedding in zip(chunks, embeddings):
            chunk.embedding = embedding
        return

    image_indices: List[int] = []
    text_only: List[tuple[int, TextChunk]] = []

    for i, chunk in enumerate(chunks):
        img_path = (chunk.metadata or {}).get("image_path")
        if img_path and os.path.isfile(img_path):
            image_indices.append(i)
        else:
            text_only.append((i, chunk))

    # multimodal embeddings for image chunks
    for idx in image_indices:
        chunk = chunks[idx]
        path = Path(chunk.metadata["image_path"])

        multimodal_doc = (
            MultimodalDocument()
            .add_field("text", chunk.text or "")
            .add_field("image", file_path=path)
        )

        chunk.embedding = await embed_model.embed_multimodal(multimodal_doc)

    # text embeddings for non-image chunks
    if text_only:
        texts = [c.text for _, c in text_only]
        kwargs = {} if doc_index_callback is None else {"callback_cls": doc_index_callback}
        embeddings = await embed_model.embed_documents(texts, **kwargs)

        for (idx, chunk), emb in zip(text_only, embeddings):
            chunks[idx].embedding = emb