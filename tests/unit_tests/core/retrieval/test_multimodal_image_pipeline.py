# Copyright (c) Huawei Technologies Co., Ltd. 2025–2026. All rights reserved.
"""
Unit tests for the multimodal image embedding pipeline (mocked, no real API).

Covers: ImageParser output (metadata["image_path"]), compute_chunk_embeddings
(embed_multimodal for image chunks, use_caption_for_images), MultimodalDocument
construction, and a showcase-style flow (multimodal embed + similarity). All use
mocks only.

For E2E with real API and Milvus, see test_image_embedding_vllm_e2e.py.
Mirrors the flow in examples/retrieval/showcase_multimodal_embedding.py.

Run from agent-core root:
  pytest tests/unit_tests/core/retrieval/test_multimodal_image_pipeline.py -v
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.retrieval.common.document import MultimodalDocument, TextChunk
from openjiuwen.core.retrieval.indexing.indexer.embed_chunks import compute_chunk_embeddings
from openjiuwen.core.retrieval.indexing.processor.parser.image_parser import ImageParser


def _cosine_similarity(a, b):
    """Minimal cosine similarity for tests (no numpy required)."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---- ImageParser: metadata["image_path"] ----
class TestImageParserPipeline:
    """ImageParser produces documents with metadata['image_path'] for the pipeline."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_parse_returns_document_with_image_path_in_metadata():
        parser = ImageParser()
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff")
            temp_path = f.name
        try:
            saved_path = "/kb/images/photo.jpg"
            with patch(
                "openjiuwen.core.retrieval.indexing.processor.parser.image_parser.ImageCaptioner"
            ) as mock_captioner_cls:
                mock_inst = MagicMock()
                mock_inst.cp_image.return_value = saved_path
                mock_inst.caption_images = AsyncMock(return_value=["A photograph"])
                mock_captioner_cls.return_value = mock_inst

                documents = await parser.parse(temp_path, doc_id="img_doc_1")
                assert len(documents) == 1
                assert documents[0].metadata.get("image_path") == saved_path
                assert "photograph" in documents[0].text
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


# ---- compute_chunk_embeddings (embed_chunks module) ----
class TestComputeChunkEmbeddingsPipeline:
    """compute_chunk_embeddings: embed_multimodal for image chunks, use_caption_for_images, text-only."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_image_chunk_uses_embed_multimodal():
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff")
            temp_path = f.name
        try:
            chunk = TextChunk(
                id_="c1",
                text="A photo",
                doc_id="d1",
                metadata={"image_path": temp_path},
            )
            mock_embed = AsyncMock()
            mock_embed.embed_documents = AsyncMock(return_value=[[0.1] * 8])
            mock_embed.embed_multimodal = AsyncMock(return_value=[0.2] * 8)

            await compute_chunk_embeddings(
                [chunk],
                mock_embed,
                use_caption_for_images=False,
            )
            assert chunk.embedding == [0.2] * 8
            mock_embed.embed_multimodal.assert_called_once()
            call_arg = mock_embed.embed_multimodal.call_args[0][0]
            assert isinstance(call_arg, MultimodalDocument)
            mock_embed.embed_documents.assert_not_called()
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    @pytest.mark.asyncio
    @staticmethod
    async def test_use_caption_for_images_uses_embed_documents_only():
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff")
            temp_path = f.name
        try:
            chunk = TextChunk(
                id_="c1",
                text="A photo caption",
                doc_id="d1",
                metadata={"image_path": temp_path},
            )
            mock_embed = AsyncMock()
            mock_embed.embed_documents = AsyncMock(return_value=[[0.3] * 8])
            mock_embed.embed_multimodal = AsyncMock(return_value=[0.4] * 8)

            await compute_chunk_embeddings(
                [chunk],
                mock_embed,
                use_caption_for_images=True,
            )
            assert chunk.embedding == [0.3] * 8
            mock_embed.embed_documents.assert_called_once()
            mock_embed.embed_multimodal.assert_not_called()
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    @pytest.mark.asyncio
    @staticmethod
    async def test_text_only_chunk_uses_embed_documents():
        chunk = TextChunk(
            id_="c1",
            text="Plain text chunk",
            doc_id="d1",
            metadata={},
        )
        mock_embed = AsyncMock()
        mock_embed.embed_documents = AsyncMock(return_value=[[0.5] * 8])

        await compute_chunk_embeddings([chunk], mock_embed, use_caption_for_images=False)
        assert chunk.embedding == [0.5] * 8
        mock_embed.embed_documents.assert_called_once()
        assert mock_embed.embed_documents.call_args[0][0] == ["Plain text chunk"]

    @pytest.mark.asyncio
    @staticmethod
    async def test_model_without_embed_multimodal_uses_embed_documents_for_all():
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff")
            temp_path = f.name
        try:
            chunk = TextChunk(
                id_="c1",
                text="Caption",
                doc_id="d1",
                metadata={"image_path": temp_path},
            )
            mock_embed = AsyncMock()
            mock_embed.embed_documents = AsyncMock(return_value=[[0.6] * 8])
            del mock_embed.embed_multimodal

            await compute_chunk_embeddings([chunk], mock_embed, use_caption_for_images=False)
            assert chunk.embedding == [0.6] * 8
            mock_embed.embed_documents.assert_called_once_with(["Caption"])
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    @pytest.mark.asyncio
    @staticmethod
    async def test_image_path_nonexistent_file_treated_as_text_only():
        chunk = TextChunk(
            id_="c1",
            text="Caption",
            doc_id="d1",
            metadata={"image_path": "/nonexistent/image.jpg"},
        )
        mock_embed = AsyncMock()
        mock_embed.embed_documents = AsyncMock(return_value=[[0.7] * 8])
        mock_embed.embed_multimodal = AsyncMock(return_value=[0.8] * 8)

        await compute_chunk_embeddings([chunk], mock_embed, use_caption_for_images=False)
        assert chunk.embedding == [0.7] * 8
        mock_embed.embed_documents.assert_called_once()
        mock_embed.embed_multimodal.assert_not_called()

    @pytest.mark.asyncio
    @staticmethod
    async def test_mixed_image_and_text_chunks():
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff")
            temp_path = f.name
        try:
            chunks = [
                TextChunk(id_="c1", text="Caption", doc_id="d1", metadata={"image_path": temp_path}),
                TextChunk(id_="c2", text="Text only", doc_id="d1", metadata={}),
            ]
            mock_embed = AsyncMock()
            mock_embed.embed_multimodal = AsyncMock(return_value=[0.1] * 8)
            mock_embed.embed_documents = AsyncMock(return_value=[[0.2] * 8])

            await compute_chunk_embeddings(chunks, mock_embed, use_caption_for_images=False)
            assert chunks[0].embedding == [0.1] * 8
            assert chunks[1].embedding == [0.2] * 8
            mock_embed.embed_multimodal.assert_called_once()
            mock_embed.embed_documents.assert_called_once_with(["Text only"])
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


# ---- MultimodalDocument in pipeline (content format) ----
class TestMultimodalDocumentPipeline:
    """MultimodalDocument built from chunk (text + image path) has embedding-ready content."""

    @staticmethod
    def test_multimodal_document_content_has_text_and_image_url():
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff")
            temp_path = f.name
        try:
            doc = (
                MultimodalDocument()
                .add_field("text", "A photograph of a person")
                .add_field("image", file_path=Path(temp_path))
            )
            content = doc.content
            assert len(content) >= 1
            text_items = [c for c in content if c.get("type") == "text"]
            image_items = [c for c in content if c.get("type") == "image_url"]
            assert any("photograph" in (c.get("text") or "") for c in text_items)
            assert len(image_items) == 1
            assert "url" in (image_items[0].get("image_url") or {})
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


# ---- Full pipeline: ImageParser -> Document -> Chunk -> compute_chunk_embeddings ----
class TestFullPipeline:
    """Pipeline from ImageParser output to compute_chunk_embeddings."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_image_parser_to_compute_chunk_embeddings_flow():
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff")
            temp_path = f.name
        try:
            saved_path = temp_path
            with patch(
                "openjiuwen.core.retrieval.indexing.processor.parser.image_parser.ImageCaptioner"
            ) as mock_captioner_cls:
                mock_inst = MagicMock()
                mock_inst.cp_image.return_value = saved_path
                mock_inst.caption_images = AsyncMock(return_value=["A photograph of a person"])
                mock_captioner_cls.return_value = mock_inst

                parser = ImageParser()
                documents = await parser.parse(temp_path, doc_id="img_1")
                assert len(documents) == 1
                doc = documents[0]
                assert doc.metadata.get("image_path") == saved_path

                chunk = TextChunk.from_document(doc, doc.text, id_="chunk_1")
                chunk.metadata = doc.metadata

                mock_embed = AsyncMock()
                mock_embed.embed_multimodal = AsyncMock(return_value=[0.0] * 8)
                mock_embed.embed_documents = AsyncMock(return_value=[])

                await compute_chunk_embeddings([chunk], mock_embed, use_caption_for_images=False)
                assert chunk.embedding == [0.0] * 8
                mock_embed.embed_multimodal.assert_called_once()
                call_doc = mock_embed.embed_multimodal.call_args[0][0]
                assert isinstance(call_doc, MultimodalDocument)
                text_content = [c.get("text") for c in call_doc.content if c.get("type") == "text"]
                assert "A photograph of a person" in (text_content or [""])
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


# ---- Showcase flow: MultimodalDocument + embed_multimodal + similarity ----
class TestShowcaseFlow:
    """Mirrors showcase_multimodal_embedding: MultimodalDocument, embed_multimodal, similarity."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_multimodal_embedding_and_similarity():
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff")
            ref_image = f.name
        try:
            doc1 = (
                MultimodalDocument()
                .add_field("text", "A photograph of a person")
                .add_field("image", file_path=Path(ref_image))
            )
            doc2 = (
                MultimodalDocument()
                .add_field("text", "Picture of an octopus in ocean")
                .add_field("image", file_path=Path(ref_image))
            )
            vec1 = [1.0] * 4 + [0.0] * 4
            vec2 = [0.9] * 4 + [0.1] * 4
            vec3 = [0.0] * 4 + [1.0] * 4
            mock_embed = AsyncMock()
            mock_embed.embed_multimodal = AsyncMock(side_effect=[vec1, vec2, vec3])

            emb1 = await mock_embed.embed_multimodal(doc1)
            emb2 = await mock_embed.embed_multimodal(doc2)
            emb3 = await mock_embed.embed_multimodal(
                MultimodalDocument().add_field("text", "Different").add_field("image", file_path=Path(ref_image))
            )

            sim_same_image = _cosine_similarity(emb1, emb2)
            sim_different = _cosine_similarity(emb1, emb3)
            assert sim_same_image > sim_different
            assert mock_embed.embed_multimodal.call_count == 3
        finally:
            if os.path.exists(ref_image):
                os.unlink(ref_image)
