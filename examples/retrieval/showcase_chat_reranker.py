# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Example script demonstrating chat reranker usage with ChatReranker
"""

import asyncio

from configs import CHAT_RERANKER_CONFIG
from utils.output import write_output

from openjiuwen.core.retrieval.reranker.chat_reranker import ChatReranker

# Query and documents for reranking (feel free to edit)
QUERY = "Hello"
DOCUMENTS = ["Hi", "Aloha", "bonjour"]
INSTRUCTION = "greeting in french"


async def main():
    """Main example demonstrating chat reranker usage"""
    chat_reranker = ChatReranker(CHAT_RERANKER_CONFIG, verify=False)

    write_output("Query: %s", QUERY)
    write_output("Documents: %s", DOCUMENTS)
    write_output("Model: %s", CHAT_RERANKER_CONFIG.model_name)
    write_output("")

    # Test compatibility first
    write_output("Testing compatibility...")
    is_compatible = chat_reranker.test_compatibility()
    write_output("Compatible: %r", is_compatible)
    write_output("")

    # Rerank each document with default instruction
    write_output("Reranking with default instruction:")
    write_output("-" * 60)
    rerank_req_default = []
    for doc in DOCUMENTS:
        rerank_req_default.append(chat_reranker.rerank(query=QUERY, doc=[doc], instruct=True))

    results_default = await asyncio.gather(*rerank_req_default)
    for doc, result in zip(DOCUMENTS, results_default):
        score = result.get(doc, 0.0)
        write_output("  %7s: %.4f", doc, score)

    write_output("")

    # Rerank each document with custom instruction
    write_output("Reranking with custom instruction='%s':", INSTRUCTION)
    write_output("-" * 60)
    rerank_req_custom = []
    for doc in DOCUMENTS:
        rerank_req_custom.append(chat_reranker.rerank(query=QUERY, doc=[doc], instruct=INSTRUCTION))

    results_custom = await asyncio.gather(*rerank_req_custom)
    for doc, result in zip(DOCUMENTS, results_custom):
        score = result.get(doc, 0.0)
        write_output("  %7s: %.4f", doc, score)

    write_output("")

    # Compare results
    write_output("=" * 60)
    write_output("Comparison:")
    write_output("=" * 60)
    for i, doc in enumerate(DOCUMENTS):
        default_score = results_default[i].get(doc, 0.0)
        custom_score = results_custom[i].get(doc, 0.0)
        write_output(
            "%7s: default=%.4f, custom=%.4f, diff=%.4f", doc, default_score, custom_score, custom_score - default_score
        )


if __name__ == "__main__":
    asyncio.run(main())
