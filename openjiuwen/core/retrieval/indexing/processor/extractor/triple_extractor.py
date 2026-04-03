# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Triple Extractor Implementation

Uses LLM for triple extraction.
"""

import asyncio
from typing import Any, List

from json_repair import repair_json

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.common.document import TextChunk
from openjiuwen.core.retrieval.common.triple import Triple
from openjiuwen.core.retrieval.indexing.processor.extractor.base import Extractor


class TripleExtractor(Extractor):
    """Triple extractor implementation using LLM for OpenIE triple extraction"""

    def __init__(
        self,
        llm_client: Any,
        model_name: str,
        temperature: float = 0.0,
        max_concurrent: int = 50,
        **kwargs,
    ):
        """
        Initialize triple extractor

        Args:
            llm_client: LLM client instance
            model_name: Model name
            temperature: Temperature parameter
            max_concurrent: Maximum concurrency, defaults to 50
        """
        self.llm_client = llm_client
        self.model_name = model_name
        self.temperature = temperature
        self.limiter = asyncio.Semaphore(max_concurrent)

    async def extract(
        self,
        chunks: List[TextChunk],
        **kwargs,
    ) -> List[Triple]:
        """
        Extract triples from chunks via parallel LLM calls.

        On any failure, raises the first error in chunk order (``BaseError`` is re-raised
        unchanged; other exceptions are wrapped).

        Args:
            chunks: Text chunks to process.
            **kwargs: Reserved for extractor API compatibility.

        Returns:
            All triples merged from successful chunk results.

        Raises:
            BaseError: Including ``RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR`` when
                extraction or parsing fails.
        """

        async def _extract_chunk(chunk: TextChunk) -> List[Triple]:
            """
            Invoke LLM and parse triples for a single chunk.

            Args:
                chunk: Text chunk to process.

            Returns:
                Extracted triples for this chunk.

            Raises:
                BaseError: On invalid LLM output or failed invoke (after wrapping).
            """

            async with self.limiter:
                try:
                    # Build prompt
                    prompt = self._build_prompt(chunk.text, chunk.metadata.get("title", ""))
                    messages = [{"role": "user", "content": prompt}]

                    # Call LLM
                    completion = await self.llm_client.invoke(
                        messages=messages,
                        temperature=self.temperature,
                    )

                    # Parse result
                    triples, parse_success = self._parse_triples(completion.content, chunk.doc_id, chunk.id_)
                    if not parse_success:
                        raise build_error(
                            StatusCode.RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR,
                            error_msg=(
                                f"{chunk.id_}: LLM response could not be parsed as valid triple JSON"
                            ),
                        )
                    return triples

                except BaseError:
                    raise
                except Exception as e:
                    logger.error(f"Failed to extract triples from chunk {chunk.id_}: {e}")
                    raise build_error(
                        StatusCode.RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR,
                        error_msg=f"{chunk.id_}: {e}",
                        cause=e,
                    ) from e

        tasks = [asyncio.create_task(_extract_chunk(chunk)) for chunk in chunks]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_triples: List[Triple] = []
        first_error: BaseError | Exception | None = None
        first_error_chunk_id: str | None = None

        for idx, result in enumerate(results):
            chunk_id = chunks[idx].id_
            if isinstance(result, BaseException) and not isinstance(result, Exception):
                raise result
            if isinstance(result, Exception):
                logger.error(f"Task failed for chunk {chunk_id}: {result}")
                if first_error is None:
                    first_error = result
                    first_error_chunk_id = chunk_id
                continue
            all_triples.extend(result)

        if first_error is not None:
            if isinstance(first_error, BaseError):
                raise first_error
            raise build_error(
                StatusCode.RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR,
                error_msg=f"{first_error_chunk_id}: {first_error}",
                cause=first_error,
            ) from first_error

        return all_triples

    def _build_prompt(self, passage: str, title: str = "") -> str:
        """
        Build the OpenIE triple-extraction prompt for the LLM.

        Args:
            passage: Chunk body text.
            title: Optional document or section title from chunk metadata.

        Returns:
            Rendered prompt string.
        """
        prompt_template = """# Instruction

Your task is to construct an RDF-style graph from the given title and passage.
Extract named entities and relationships, then return the result as exactly one valid JSON object.

Each triple should represent a meaningful relationship in the graph.
Each triple should contain at least one, and preferably two, named entities from the title or passage.
Clearly resolve pronouns to specific names whenever possible.

Return only one valid JSON object in this format:
{{
  "named_entities": ["entity1", "entity2"],
  "triples": [
    ["subject1", "predicate1", "object1"],
    ["subject2", "predicate2", "object2"]
  ]
}}

Requirements:
- Output valid JSON only. Do not use markdown, comments, or extra text.
- Return exactly one top-level JSON object.
- The top-level object must contain exactly two keys: "named_entities" and "triples".
- "named_entities" must be a JSON array of strings.
- "triples" must be a JSON array.
- Each item in "triples" must be a JSON array of exactly three strings.
- Do not output tuples, objects, or arrays with more than three elements inside "triples".
- Use double quotes for all JSON strings.
- If no triples are found, return {{"named_entities": [...], "triples": []}}.
- Resolve pronouns to specific names when possible.
- Prefer triples that use at least one, and preferably two, named entities from the title or passage.
- Keep entity and predicate wording consistent with the source language.
- Do not include duplicate triples.

# Demonstration 1

Title:
Magic Johnson

Passage:
After winning a national championship with Michigan State in 1979, Johnson was selected first overall in the 1979 NBA draft by the Lakers, leading the team to five NBA championships during their "Showtime" era.

Output:
{{
  "named_entities": [
    "Michigan State",
    "national championship",
    "1979",
    "Magic Johnson",
    "National Basketball Association",
    "Los Angeles Lakers",
    "NBA Championship"
  ],
  "triples": [
    ["Magic Johnson", "member of sports team", "Michigan State"],
    ["Michigan State", "award", "national championship"],
    ["Michigan State", "award date", "1979"],
    ["Magic Johnson", "draft pick number", "1"],
    ["Magic Johnson", "drafted in", "1979"],
    ["Magic Johnson", "drafted by", "Los Angeles Lakers"],
    ["Magic Johnson", "member of sports team", "Los Angeles Lakers"],
    ["Magic Johnson", "league", "National Basketball Association"],
    ["Los Angeles Lakers", "league", "National Basketball Association"],
    ["Los Angeles Lakers", "award received", "NBA Championship"]
  ]
}}

# Demonstration 2

Title:
Elden Ring

Passage:
Elden Ring is a 2022 action role-playing game developed by FromSoftware. It was directed by Hidetaka Miyazaki with worldbuilding provided by American fantasy writer George R. R. Martin.

Output:
{{
  "named_entities": [
    "Elden Ring",
    "2022",
    "action role-playing game",
    "FromSoftware",
    "Hidetaka Miyazaki",
    "United States of America",
    "fantasy",
    "George R. R. Martin"
  ],
  "triples": [
    ["Elden Ring", "publication", "2022"],
    ["Elden Ring", "genre", "action role-playing game"],
    ["Elden Ring", "publisher", "FromSoftware"],
    ["Elden Ring", "director", "Hidetaka Miyazaki"],
    ["Elden Ring", "screenwriter", "George R. R. Martin"],
    ["George R. R. Martin", "country of citizenship", "United States of America"],
    ["George R. R. Martin", "genre", "fantasy"]
  ]
}}

# Input

Title:
{title}

Passage:
{passage}
"""
        return prompt_template.format(passage=passage, title=title or "Untitled")

    def _parse_triples(self, content: str, doc_id: str, chunk_id: str) -> tuple[List[Triple], bool]:
        """
        Parse LLM output into ``Triple`` objects.

        Args:
            content: Raw model output (JSON or markdown-fenced JSON).
            doc_id: Document id for triple metadata.
            chunk_id: Chunk id for triple metadata.

        Returns:
            ``(triples, parse_success)``: ``parse_success`` is True when JSON was valid
            and structure was acceptable (empty triple list allowed); False on hard parse errors.
        """
        triples = []

        try:
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1]) if len(lines) > 2 else content

            try:
                parsed = repair_json(content, return_objects=True)
            except Exception as e:
                logger.error("Failed to parse triples from content: %s. Content: %s", e, content[:200])
                return [], False

            if isinstance(parsed, dict):
                if "triples" not in parsed or not isinstance(parsed.get("triples"), list):
                    return [], False
                triple_list = parsed["triples"]
            elif isinstance(parsed, list):
                triple_list = parsed
            else:
                return [], False

            if not triple_list:
                return [], True

            invalid_count = 0
            for triple_data in triple_list:
                if not isinstance(triple_data, (list, tuple)):
                    invalid_count += 1
                    continue
                if len(triple_data) < 3:
                    invalid_count += 1
                    continue

                head = triple_data[:3]
                if any(isinstance(x, (list, tuple, dict)) or x is None for x in head):
                    invalid_count += 1
                    continue

                triples.append(
                    Triple(
                        subject=str(head[0]).strip(),
                        predicate=str(head[1]).strip(),
                        object=str(head[2]).strip(),
                        metadata={"doc_id": doc_id, "chunk_id": chunk_id},
                    )
                )

            if invalid_count:
                logger.warning(
                    "Ignored %d invalid triples for chunk %s during parsing",
                    invalid_count,
                    chunk_id,
                )

            return triples, bool(triples)

        except Exception as e:
            logger.error("Failed to parse triples: %s", e)
            return [], False
