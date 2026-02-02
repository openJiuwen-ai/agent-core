# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Example script demonstrating text embedding usage
"""

import asyncio

from configs import EMBEDDING_CONFIG
from utils.output import write_output
from utils.vector_similarities import cosine_similarity, euclidean_distance

from openjiuwen.core.retrieval import VLLMEmbedding

# Longer text documents for embedding (feel free to edit)
DOCUMENT_1 = """
Modern ray tracing technology has revolutionized computer graphics rendering. 
Physically-based rendering uses bidirectional reflectance distribution functions 
(BRDF) to accurately simulate how light interacts with surfaces, accounting for 
diffuse and specular reflections. Advanced aperture simulation techniques model 
camera depth of field and bokeh effects by tracing multiple rays through virtual 
lens apertures. These techniques produce photorealistic images by simulating 
the physical behavior of light. Bounding Volume Hierarchy (BVH) trees organize 
geometry in axis-aligned bounding boxes, enabling efficient ray-scene intersection 
tests and real-time rendering of complex scenes with accurate shadows, reflections, 
and global illumination.
"""

DOCUMENT_2 = """
现代光线追踪技术已经彻底改变了计算机图形渲染。基于物理的渲染使用双向
反射分布函数（BRDF）来精确模拟光线与表面的交互，考虑漫反射和镜面反射。
先进的光圈模拟技术通过追踪穿过虚拟镜头光圈的多条光线来模拟相机景深和
散景效果。这些技术通过模拟光的物理行为来生成逼真的图像。包围盒层次结构
（BVH）树将几何体组织在轴对齐的包围盒中，实现高效的射线-场景相交测试，
能够实时渲染具有精确阴影、反射和全局光照的复杂场景。
"""

DOCUMENT_3 = """
Doom's revolutionary graphics engine, released in 1993, used ray casting to 
create pseudo-3D environments from 2D maps. The engine employed binary space 
partitioning (BSP) trees to efficiently determine which walls and surfaces 
were visible from the player's viewpoint. This technique enabled smooth gameplay 
on hardware of the era by avoiding true 3D polygon rendering, instead using 
pre-calculated visibility data stored in the BSP tree structure for efficient 
ray-scene intersection tests, similar to Bounding Volume Hierarchy (BVH).
"""

EMBEDDING_DIM = 128  # Set to None to use default dimension


async def main():
    """Main example demonstrating text embedding usage"""
    # Initialize embedding model
    model = VLLMEmbedding(EMBEDDING_CONFIG, dimension=EMBEDDING_DIM, timeout=10)

    # Generate embeddings for documents
    write_output("Generating embeddings for documents...")
    embeddings = await model.embed_documents([DOCUMENT_1, DOCUMENT_2, DOCUMENT_3])
    emb1, emb2, emb3 = embeddings[0], embeddings[1], embeddings[2]

    write_output("Embedding dimensions: %d", len(emb1))

    # Compare embeddings
    # doc1 vs doc2: Same content (modern ray tracing) in different languages - should be very similar
    sim_1_2 = cosine_similarity(emb1, emb2)
    dist_1_2 = euclidean_distance(emb1, emb2)
    write_output("\nDocument 1 (Modern ray tracing - English) vs Document 2 (Modern ray tracing - Chinese):")
    write_output("  Cosine similarity: %.4f", sim_1_2)
    write_output("  Euclidean distance: %.4f", dist_1_2)

    # doc1 vs doc3: Related graphics rendering topics (modern vs classic techniques) - should be similar
    sim_1_3 = cosine_similarity(emb1, emb3)
    dist_1_3 = euclidean_distance(emb1, emb3)
    write_output("\nDocument 1 (Modern ray tracing - English) vs Document 3 (Classic ray casting - Doom):")
    write_output("  Cosine similarity: %.4f", sim_1_3)
    write_output("  Euclidean distance: %.4f", dist_1_3)

    # doc2 vs doc3: Related graphics rendering topics (modern vs classic techniques) - should be similar
    sim_2_3 = cosine_similarity(emb2, emb3)
    dist_2_3 = euclidean_distance(emb2, emb3)
    write_output("\nDocument 2 (Modern ray tracing - Chinese) vs Document 3 (Classic ray casting - Doom):")
    write_output("  Cosine similarity: %.4f", sim_2_3)
    write_output("  Euclidean distance: %.4f", dist_2_3)

    # Analysis
    write_output("=" * 60)
    write_output("Analysis:")
    write_output("=" * 60)

    # Same topic across languages should have highest similarity
    if sim_1_2 > sim_1_3 and sim_1_2 > sim_2_3:
        write_output("✓ PASS: Same topic (cross-language) produces highest similarity")
        write_output("  Similarity between same topic (English vs Chinese): %.4f", sim_1_2)
        write_output("  Similarity between related topics (modern vs classic rendering): %.4f", max(sim_1_3, sim_2_3))
    else:
        write_output("✗ FAIL: Cross-language similarity is not highest")

    # Related graphics rendering topics should have high similarity (both are rendering techniques)
    if sim_1_3 > 0.7 or sim_2_3 > 0.7:
        write_output(
            "✓ PASS: Related graphics rendering topics (modern ray tracing vs classic ray casting) are similar"
        )
        write_output("  Similarity between related rendering techniques: %.4f", sim_1_3)
        write_output("  Similarity between related rendering techniques (cross-language): %.4f", sim_2_3)
        write_output("  This demonstrates embeddings capture semantic similarity in technical domains")
    else:
        write_output("✗ WARNING: Related rendering techniques may not be sufficiently similar")
        write_output("  Similarity between related rendering techniques: %.4f", sim_1_3)
        write_output("  Similarity between related rendering techniques (cross-language): %.4f", sim_2_3)

    # Euclidean distance: similar documents should have smaller distance
    if dist_1_2 < dist_1_3 and dist_1_2 < dist_2_3:
        write_output("✓ PASS: Similar documents have smaller euclidean distance")
        write_output("  Distance between similar documents: %.4f", dist_1_2)
        write_output("  Distance between different documents: %.4f", min(dist_1_3, dist_2_3))
    else:
        write_output("✗ FAIL: Similar documents do not have smaller euclidean distance")

    # Summary
    write_output("=" * 60)
    write_output("Summary:")
    write_output("=" * 60)
    write_output("Cross-language similarity (ray tracing English vs Chinese): %.4f", sim_1_2)
    write_output("Related rendering techniques similarity (ray tracing vs ray casting): %.4f", max(sim_1_3, sim_2_3))
    if sim_1_2 > max(sim_1_3, sim_2_3):
        write_output("This embedding model captures semantic similarity across different languages!")
        write_output(
            "Even though both modern ray tracing and classic ray casting are related, cross-language semantic "
            "similarity (same topic) is higher than cross-technique similarity."
        )


if __name__ == "__main__":
    asyncio.run(main())
