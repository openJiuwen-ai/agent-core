# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Example script demonstrating reranker usage with standard reranker
"""

import asyncio

from configs import RERANKER_CONFIG
from utils.output import write_output

from openjiuwen.core.retrieval import StandardReranker

# Query and documents for reranking (feel free to edit)
QUERY = "Hello"
DOCUMENTS = ["Hi", "Aloha", "bonjour"]
INSTRUCTION = "greeting in french"


async def main():
    """Main example demonstrating reranker usage"""
    standard_reranker = StandardReranker(RERANKER_CONFIG, verify=False)
    rerank_req = []
    for instruction in [False, INSTRUCTION]:
        rerank_req.append(standard_reranker.rerank(query=QUERY, doc=DOCUMENTS, instruct=instruction))
    write_output("Query: %s", QUERY)
    write_output("Documents: %s", DOCUMENTS)
    no_instruct, instruct = await asyncio.gather(*rerank_req)
    write_output("Reranked result without instruction:")
    for doc, prob in no_instruct.items():
        write_output("  %7s: %.4f", doc, prob)
    write_output("Reranked result with instruction=%s:", INSTRUCTION)
    for doc, prob in instruct.items():
        write_output("  %7s: %.4f", doc, prob)


if __name__ == "__main__":
    asyncio.run(main())
