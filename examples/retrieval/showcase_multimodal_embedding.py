# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Example script demonstrating that images significantly affect embedded vectors
"""

import asyncio
from pathlib import Path

from configs import MULTIMODAL_EMBEDDING_CONFIG
from utils.output import write_output
from utils.vector_similarities import cosine_similarity, euclidean_distance

from openjiuwen.core.retrieval import MultimodalDocument, VLLMEmbedding

# Text section of documents (feel free to edit)
REFERENCE_TEXT = "A photograph of a person"
DIFFERENT_TEXT = "Picture of an octopus in ocean"

# Image file paths (supply your own images)
REF_IMAGE = Path("reference.jpg")
SAME_CONTENT_DIFFERENT_IMAGE = Path("reference.ppm")
DIFFERENT_IMAGE = Path("different.ppm")

EMBEDDING_DIM = 128  # Set to None to use default dimension


async def main():
    """Main test"""
    # Create documents with different images
    # doc1 and doc2 have the same image but different formats
    # doc3 has a different image
    # doc4 has alternative text
    docs = [MultimodalDocument() for _ in range(4)]
    docs[0].add_field("text", REFERENCE_TEXT).add_field("image", file_path=REF_IMAGE)
    docs[1].add_field("text", REFERENCE_TEXT).add_field("image", file_path=SAME_CONTENT_DIFFERENT_IMAGE)
    docs[2].add_field("text", REFERENCE_TEXT).add_field("image", file_path=DIFFERENT_IMAGE)
    docs[3].add_field("text", DIFFERENT_TEXT).add_field("image", file_path=REF_IMAGE)

    # Initialize embedding model
    model = VLLMEmbedding(MULTIMODAL_EMBEDDING_CONFIG, dimension=EMBEDDING_DIM, timeout=10)

    # Generate embeddings
    write_output("Generating embeddings...")
    emb1, emb2, emb3, emb4 = await asyncio.gather(*(model.embed_multimodal(doc) for doc in docs))

    write_output("Embedding dimensions: %d", len(emb1))

    # Compare embeddings
    # doc1 vs doc2: Same image, different format - should be similar
    sim_1_2 = cosine_similarity(emb1, emb2)
    dist_1_2 = euclidean_distance(emb1, emb2)
    write_output("\ndoc1 (REF_IMAGE) vs doc2 (SAME_CONTENT_DIFFERENT_IMAGE):")
    write_output("  Cosine similarity: %.4f", sim_1_2)
    write_output("  Euclidean distance: %.4f", dist_1_2)

    # doc1 vs doc3: Different images - should be different
    sim_1_3 = cosine_similarity(emb1, emb3)
    dist_1_3 = euclidean_distance(emb1, emb3)
    write_output("\ndoc1 (REF_IMAGE) vs doc3 (DIFFERENT_IMAGE):")
    write_output("  Cosine similarity: %.4f", sim_1_3)
    write_output("  Euclidean distance: %.4f", dist_1_3)

    # doc2 vs doc3: Different images - should be different
    sim_2_3 = cosine_similarity(emb2, emb3)
    dist_2_3 = euclidean_distance(emb2, emb3)
    write_output("\ndoc2 (SAME_CONTENT_DIFFERENT_IMAGE) vs doc3 (DIFFERENT_IMAGE):")
    write_output("  Cosine similarity: %.4f", sim_2_3)
    write_output("  Euclidean distance: %.4f", dist_2_3)

    # doc1 vs doc4: Same image, different text - tests text influence
    sim_1_4 = cosine_similarity(emb1, emb4)
    dist_1_4 = euclidean_distance(emb1, emb4)
    write_output("\ndoc1 (REF_IMAGE + REFERENCE_TEXT) vs doc4 (REF_IMAGE + DIFFERENT_TEXT):")
    write_output("  Cosine similarity: %.4f", sim_1_4)
    write_output("  Euclidean distance: %.4f", dist_1_4)

    # doc4 vs doc2: Same image (different format), different text
    sim_4_2 = cosine_similarity(emb4, emb2)
    dist_4_2 = euclidean_distance(emb4, emb2)
    write_output("\ndoc4 (REF_IMAGE + DIFFERENT_TEXT) vs doc2 (SAME_CONTENT_DIFFERENT_IMAGE + REFERENCE_TEXT):")
    write_output("  Cosine similarity: %.4f", sim_4_2)
    write_output("  Euclidean distance: %.4f", dist_4_2)

    # doc4 vs doc3: Different image, different text
    sim_4_3 = cosine_similarity(emb4, emb3)
    dist_4_3 = euclidean_distance(emb4, emb3)
    write_output("\ndoc4 (REF_IMAGE + DIFFERENT_TEXT) vs doc3 (DIFFERENT_IMAGE + REFERENCE_TEXT):")
    write_output("  Cosine similarity: %.4f", sim_4_3)
    write_output("  Euclidean distance: %.4f", dist_4_3)

    # Analysis
    write_output("=" * 60)
    write_output("Analysis:")
    write_output("=" * 60)

    # Same image (different format) should be more similar than different images
    if sim_1_2 > sim_1_3 and sim_1_2 > sim_2_3:
        write_output("✓ PASS: Same image (different format) produces more similar embeddings")
        write_output("  Similarity between same image: %.4f", sim_1_2)
        write_output("  Similarity between different images: %.4f", max(sim_1_3, sim_2_3))
    else:
        write_output("✗ FAIL: Same image embeddings are not more similar than different images")
        write_output("  Similarity between same image: %.4f", sim_1_2)
        write_output("  Similarity between different images: %.4f", max(sim_1_3, sim_2_3))

    # Different images should have low similarity (typically < 0.9 for different images)
    if sim_1_3 < 0.9 and sim_2_3 < 0.9:
        write_output("✓ PASS: Different images produce significantly different embeddings")
        write_output("  Similarity between different images: %.4f < 0.9", max(sim_1_3, sim_2_3))
    else:
        write_output("✗ WARNING: Different images may not be sufficiently different")
        write_output("  Similarity between different images: %.4f", max(sim_1_3, sim_2_3))

    # Euclidean distance: same image should have smaller distance
    if dist_1_2 < dist_1_3 and dist_1_2 < dist_2_3:
        write_output("✓ PASS: Same image (different format) has smaller euclidean distance")
        write_output("  Distance between same image: %.4f", dist_1_2)
        write_output("  Distance between different images: %.4f", min(dist_1_3, dist_2_3))
    else:
        write_output("✗ FAIL: Same image does not have smaller euclidean distance")

    # Text influence analysis: same image with different text
    write_output("-" * 60)
    write_output("Text Influence Analysis (same image, different text):")
    write_output("-" * 60)
    if sim_1_4 > sim_1_3 and sim_1_4 > sim_2_3:
        write_output("✓ PASS: Same image with different text is more similar than different images")
        write_output("  Similarity (same image, different text): %.4f", sim_1_4)
        write_output("  Similarity (different images): %.4f", max(sim_1_3, sim_2_3))
    else:
        write_output("✗ WARNING: Text may have strong influence, or images are not selected correctly")
        write_output("  Similarity (same image, different text): %.4f", sim_1_4)
        write_output("  Similarity (different images): %.4f", max(sim_1_3, sim_2_3))

    # Compare same image with same text vs different text
    if sim_1_2 > sim_1_4:
        write_output("✓ PASS: Same image with same text is more similar than same image with different text")
        write_output("  Similarity (same image, same text): %.4f", sim_1_2)
        write_output("  Similarity (same image, different text): %.4f", sim_1_4)
        write_output("  Text influence ratio: %.4f", sim_1_4 / sim_1_2)
    else:
        write_output("✗ WARNING: Text may have minimal influence on embeddings")
        write_output("  Similarity (same image, same text): %.4f", sim_1_2)
        write_output("  Similarity (same image, different text): %.4f", sim_1_4)

    # Summary
    write_output("=" * 60)
    write_output("Summary:")
    write_output("=" * 60)
    write_output("Images significantly affect embeddings: %r", sim_1_2 > sim_1_3 and sim_1_2 > sim_2_3)
    write_output("Same image, same text similarity: %.4f", sim_1_2)
    write_output("Same image, different text similarity: %.4f", sim_1_4)
    write_output("Different images similarity: %.4f", max(sim_1_3, sim_2_3))
    write_output("Image difference ratio: %.4f", max(sim_1_3, sim_2_3) / sim_1_2)


if __name__ == "__main__":
    asyncio.run(main())
