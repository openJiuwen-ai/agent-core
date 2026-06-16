# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Triple Extractor Implementation

Uses LLM for triple extraction and optional triple validation.
"""

import asyncio
from collections import defaultdict
from typing import Any, List

import json
from json_repair import repair_json

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.common.document import TextChunk
from openjiuwen.core.retrieval.common.triple import Triple
from openjiuwen.core.retrieval.indexing.processor.extractor.base import Extractor


class TripleExtractor(Extractor):
    """Triple extractor with triple validation using LLM for OpenIE triple extraction"""

    def __init__(
        self,
        llm_client: Any,
        model_name: str,
        temperature: float = 0.0,
        max_concurrent: int = 50,
        validate: bool = False,
        **kwargs,
    ):
        """
        Initialize triple extractor

        Args:
            llm_client: LLM client instance
            model_name: Model name
            temperature: Temperature parameter
            max_concurrent: Maximum concurrency, defaults to 50
            validate: Whether to validate extracted triples via LLM
        """
        self.llm_client = llm_client
        self.model_name = model_name
        self.temperature = temperature
        self.limiter = asyncio.Semaphore(max_concurrent)
        self.validate = validate

    async def extract(
        self,
        chunks: List[TextChunk],
        **kwargs,
    ) -> List[Triple]:
        """
        Extract and validate triples from chunks via parallel LLM calls.

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

        all_triples = await self._extract_internal(chunks)

        if not self.validate:
            return all_triples
        
        return await self._validate_internal(all_triples, chunks)
    

    async def _invoke_and_parse(self, prompt: str, chunk: TextChunk) -> List[Triple]:
        """
        Call the LLM and parse output

        Args:
            prompt: Prompt to be given to the LLM.
            chunk: Text chunk given as context

        Returns:
            Parsed response of the LLM

        Raises:
            BaseError: Specifically `RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR`, 
                raised if the LLM's response cannot be parsed into valid JSON.
        """

        messages = [{"role": "user", "content": prompt}]
        
        completion = await self.llm_client.invoke(
            messages=messages,
            temperature=self.temperature,
        )

        triples, parse_success = self._parse_triples(
            completion.content, chunk.doc_id, chunk.id_
        )
        
        if not parse_success:
            raise build_error(
                StatusCode.RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR,
                error_msg=(
                    f"{chunk.id_}: LLM response could not be parsed as valid triple JSON"
                ),
            )
        return triples

    async def _gather_results(
        self, tasks: list[asyncio.Task], chunks: List[TextChunk]
    ) -> List[Triple]:
        """
        Await tasks, handle exceptions, and gather the resulting triples.

        Args:
            tasks: List of asyncio tasks executing the LLM requests.
            chunks: List of text chunks corresponding to the tasks.

        Returns:
            A flattened list of all triples gathered from the successful tasks.

        Raises:
            BaseError: Re-raises the first encountered BaseError or wraps a standard 
                Exception into a RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR.
        """
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



    async def _extract_internal(self, chunks: List[TextChunk]) -> List[Triple]:
        """
        Execute the internal extraction of triples across all provided chunks.

        Args:
            chunks: List of text chunks to extract triples from.

        Returns:
            All triples merged from successful chunk results.
        
        Raises:
            BaseError: Propagates any `BaseError` from parsing, or wraps unexpected 
                system/network exceptions into a `BaseError`.
        """
        async def _extract_chunk(chunk: TextChunk) -> List[Triple]:
            """
            Process a single text chunk to extract triples.

            Args:
                chunk: The specific text chunk being processed.

            Returns:
                A list of extracted Triple objects.

            Raises:
                BaseError: Propagates any `BaseError` from parsing, or wraps unexpected 
                    exceptions into a `BaseError`.
            """
            async with self.limiter:
                try:
                    prompt = self._build_prompt(chunk.text, chunk.metadata.get("title", ""))
                    return await self._invoke_and_parse(prompt, chunk)
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
        return await self._gather_results(tasks, chunks)

    async def _validate_internal(
        self, triples: List[Triple], chunks: List[TextChunk]
    ) -> List[Triple]:
        """
        Group candidate triples by their source chunk and validate them via the LLM.

        Args:
            triples: List of previously extracted candidate triples.
            chunks: List of text chunks that serve as the ground truth context.

        Returns:
            A filtered list of valid triples that are directly supported by the text.
        """
        if not triples:
            return []

        triples_by_chunk: dict[str, List[Triple]] = defaultdict(list)
        for t in triples:
            chunk_id = t.metadata.get("chunk_id")
            if chunk_id:
                triples_by_chunk[chunk_id].append(t)

        async def _validate_chunk(chunk: TextChunk, chunk_triples: List[Triple]) -> List[Triple]:
            """
            Validate candidate triples via the LLM.

            Args:
                triples: List of previously extracted candidate triples.
                chunks: List of text chunks that serve as the ground truth context.

            Returns:
                A filtered list of valid triples that are directly supported by the text.
            """
            async with self.limiter:
                try:
                    prompt = self._build_validation_prompt(chunk.text, chunk_triples)
                    return await self._invoke_and_parse(prompt, chunk)
                except BaseError:
                    raise
                except Exception as e:
                    logger.error(f"Failed to validate triples for chunk {chunk.id_}: {e}")
                    raise build_error(
                        StatusCode.RETRIEVAL_KB_TRIPLE_EXTRACTION_PROCESS_ERROR,
                        error_msg=f"{chunk.id_}: {e}",
                        cause=e,
                    ) from e

        tasks = []
        valid_chunks = []
        for chunk in chunks:
            chunk_triples = triples_by_chunk.get(chunk.id_)
            if chunk_triples:
                tasks.append(asyncio.create_task(_validate_chunk(chunk, chunk_triples)))
                valid_chunks.append(chunk)

        if not tasks:
            return []

        return await self._gather_results(tasks, valid_chunks)

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

    def _build_validation_prompt(self, passage: str, triples: List[Triple]) -> str:
        """
        Build the triple validation prompt for the LLM.

        Args:
            passage: Chunk body text.
            triples: Candidate triples to validate.

        Returns:
            Rendered prompt string.
        """
        prompt_template = """# Instruction

You are an OpenIE triple validator.
Given the Text and Candidate Triples, output only the triples that are directly supported by the text.
Modify predicates where needed for correctness or clarity
Drop triples that rely on outside knowledge, mismatch dates/numbers/places
or are not necessarily true based on the text.

Validate the following list of triples based on the rules above.

Return only the valid triples in one valid JSON object in this format:
{{
  "triples": [
    ["subject1", "predicate1", "object1"],
    ["subject2", "predicate2", "object2"]
  ]
}}

Requirements:
- Output valid JSON only. Do not use markdown, comments, or extra text.
- Return exactly one top-level JSON object.
- The top-level object must contain exactly one key: "triples".
- "triples" must be a JSON array.
- Each item in "triples" must be a JSON array of exactly three strings.
- Do not output tuples, objects, or arrays with more than three elements inside "triples".
- Use double quotes for all JSON strings.
- If no valid triples are found, return {{"triples": []}}.
- Prefer triples that use at least one, and preferably two, named entities from the title or passage.
- Keep entity and predicate wording consistent with the source language.
- Do not include duplicate triples.

You are given the following Text:
{passage}

You are given the following extracted Triples:
{triples}
"""
        triples_text = json.dumps([[t.subject, t.predicate, t.object] for t in triples], ensure_ascii=False, indent=2)
        return prompt_template.format(passage=passage, triples=triples_text)

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