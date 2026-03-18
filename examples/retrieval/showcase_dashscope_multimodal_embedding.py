# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Example script demonstrating that images significantly affect embedded vectors
"""

import asyncio
from pathlib import Path

from configs import DASHSCOPE_API_KEY
from utils.output import write_output
from utils.vector_similarities import cosine_similarity, euclidean_distance

from openjiuwen.core.retrieval import DashscopeEmbedding, EmbeddingConfig, MultimodalDocument

# Text section of documents (feel free to edit)
REFERENCE_TEXT = "A photograph of a person"
DIFFERENT_TEXT = "Picture of an octopus in ocean"

# Image file paths (supply your own images)
LOCAL_REF_IMAGE = Path("reference.jpg")
DIFFERENT_IMAGE = "https://openjiuwen.com/img/jiuwen_logo.png"

EMBEDDING_DIM = 256  # Set to None to use default dimension
MULTIMODAL_EMBEDDING_CONFIG = EmbeddingConfig(
    api_key=DASHSCOPE_API_KEY,
    base_url="https://dashscope.aliyuncs.com/api/v1/",
    model_name="qwen3-vl-embedding",
)


async def main():
    """Main test"""
    # Create documents with different images
    # doc1 and doc2 have the same text but different images
    # doc3 has a different image and alternative text
    docs = [MultimodalDocument() for _ in range(3)]
    docs[0].add_field("text", REFERENCE_TEXT).add_field("image", file_path=LOCAL_REF_IMAGE)
    docs[1].add_field("text", REFERENCE_TEXT).add_field("image", data=DIFFERENT_IMAGE)
    docs[2].add_field("text", DIFFERENT_TEXT).add_field("image", data=DIFFERENT_IMAGE)

    # Initialize embedding model
    model = DashscopeEmbedding(MULTIMODAL_EMBEDDING_CONFIG, dimension=EMBEDDING_DIM, timeout=30)

    # Generate embeddings
    write_output("Generating embeddings...")
    emb1, emb2, emb3 = await model.embed_documents(docs)

    write_output("Embedding dimensions: %d", len(emb1))

    # Compare embeddings
    # doc1 vs doc2: Different images - should be different
    sim_1_2 = cosine_similarity(emb1, emb2)
    dist_1_2 = euclidean_distance(emb1, emb2)
    write_output("\ndoc1 (REF_IMAGE) vs doc2 (DIFFERENT_IMAGE):")
    write_output("  Cosine similarity: %.4f", sim_1_2)
    write_output("  Euclidean distance: %.4f", dist_1_2)

    # doc2 vs doc3: Different texts - should be different
    sim_2_3 = cosine_similarity(emb2, emb3)
    dist_2_3 = euclidean_distance(emb2, emb3)
    write_output("\ndoc2 (DIFFERENT_IMAGE + REF_TEXT) vs doc3 (DIFFERENT_IMAGE + DIFFERENT_TEXT):")
    write_output("  Cosine similarity: %.4f", sim_2_3)
    write_output("  Euclidean distance: %.4f", dist_2_3)

    # doc1 vs doc3: Different image, different text
    sim_1_3 = cosine_similarity(emb1, emb3)
    dist_1_3 = euclidean_distance(emb1, emb3)
    write_output("\ndoc1 (REF_IMAGE + REF_TEXT) vs doc3 (DIFFERENT_IMAGE + DIFFERENT_TEXT):")
    write_output("  Cosine similarity: %.4f", sim_1_3)
    write_output("  Euclidean distance: %.4f", dist_1_3)

    # Analysis
    write_output("=" * 60)
    write_output("Analysis:")
    write_output("=" * 60)

    # Different images should have low similarity (typically < 0.9 for different images)
    if max(sim_1_3, sim_1_2) < 0.9:
        write_output("✓ PASS: Different images produce significantly different embeddings")
        write_output("  Similarity between different images: %.4f < 0.9", max(sim_1_3, sim_1_2))
    else:
        write_output("✗ WARNING: Different images may not be sufficiently different")
        write_output("  Similarity between different images: %.4f", max(sim_1_3, sim_1_2))

    # Euclidean distance: same image should have smaller distance
    if dist_2_3 < dist_1_3:
        write_output("✓ PASS: Same image (different text) has smaller euclidean distance")
        write_output("  Distance between same image: %.4f", dist_2_3)
        write_output("  Distance between different images: %.4f", dist_1_3)
    else:
        write_output("✗ FAIL: Same image does not have smaller euclidean distance")

    # Text influence analysis: same image with different text
    write_output("-" * 60)
    write_output("Text Influence Analysis (same image, different text):")
    write_output("-" * 60)
    if sim_2_3 > sim_1_2:
        write_output("✓ PASS: Same image with different text is more similar than different image & text")
        write_output("  Similarity (same image, different text): %.4f", sim_2_3)
        write_output("  Similarity (different image & text): %.4f", sim_1_2)
    else:
        write_output("✗ WARNING: Text may have strong influence, or images are not selected correctly")
        write_output("  Similarity (same image, different text): %.4f", sim_2_3)
        write_output("  Similarity (different image & text): %.4f", sim_1_2)

    # Summary
    write_output("=" * 60)
    write_output("Summary:")
    write_output("=" * 60)
    write_output("Images significantly affect embeddings: %r", sim_2_3 > sim_1_2)
    write_output("Same image, different text similarity: %.4f", sim_2_3)
    write_output("Different image & text similarity: %.4f", sim_1_3)


if __name__ == "__main__":
    asyncio.run(main())
