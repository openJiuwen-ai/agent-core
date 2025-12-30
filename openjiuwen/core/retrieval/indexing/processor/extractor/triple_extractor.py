# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Triple Extractor Implementation

Uses LLM for triple extraction.
"""
from typing import List, Any, Optional
import json
import asyncio

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.indexing.processor.extractor.base import Extractor
from openjiuwen.core.retrieval.common.document import TextChunk
from openjiuwen.core.retrieval.common.triple import Triple


class TripleExtractor(Extractor):
    """Triple extractor implementation using LLM for OpenIE triple extraction"""

    def __init__(
        self,
        llm_client: Any,
        model_name: str,
        temperature: float = 0.0,
        max_concurrent: int = 50,
        **kwargs: Any,
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
        **kwargs: Any,
    ) -> List[Triple]:
        """
        Extract triples
        
        Args:
            chunks: List of text chunks
            **kwargs: Additional parameters
            
        Returns:
            List of triples
        """
        async def _extract_chunk(chunk: TextChunk) -> List[Triple]:
            """Process triple extraction for a single chunk"""
            async with self.limiter:
                try:
                    # Build prompt
                    prompt = self._build_prompt(chunk.text, chunk.metadata.get("title", ""))
                    messages = [{"role": "user", "content": prompt}]
                    
                    # Call LLM
                    completion = await self.llm_client.ainvoke(
                        model_name=self.model_name,
                        messages=messages,
                        temperature=self.temperature,
                    )
                    
                    # Parse result
                    triples = self._parse_triples(completion.content, chunk.doc_id)
                    return triples
                    
                except Exception as e:
                    logger.error(f"Failed to extract triples from chunk {chunk.id_}: {e}")
                    return []
        
        # Create parallel tasks using create_task
        tasks = [asyncio.create_task(_extract_chunk(chunk)) for chunk in chunks]
        
        # Wait for all tasks to complete and collect results
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Merge all results
        all_triples = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Task failed with exception: {result}")
            elif isinstance(result, list):
                all_triples.extend(result)
        
        return all_triples

    def _build_prompt(self, passage: str, title: str = "") -> str:
        """Build prompt for triple extraction"""
        prompt_template = """Extract entities and relationships from the following passage. 
Return the results in JSON format with a list of triples, where each triple is represented as [subject, predicate, object].

Passage:
{passage}

Title: {title}

Please extract all meaningful triples from the passage. Return only the JSON array, no additional text.
Format: [["subject1", "predicate1", "object1"], ["subject2", "predicate2", "object2"], ...]
"""
        return prompt_template.format(passage=passage, title=title or "Untitled")

    def _parse_triples(self, content: str, doc_id: str) -> List[Triple]:
        """Parse triples returned by LLM"""
        triples = []
        
        try:
            # Try to parse JSON
            # Remove possible markdown code block markers
            content = content.strip()
            if content.startswith("```"):
                # Remove code block markers
                lines = content.split("\n")
                content = "\n".join(lines[1:-1]) if len(lines) > 2 else content
            
            # Try to parse JSON directly
            try:
                triple_list = json.loads(content)
            except json.JSONDecodeError:
                # If not valid JSON, try to extract JSON portion
                import re
                json_match = re.search(r'\[\[.*?\]\]', content, re.DOTALL)
                if json_match:
                    triple_list = json.loads(json_match.group())
                else:
                    logger.error(f"Failed to parse triples from content: {content[:100]}")
                    return []
            
            # Convert to Triple objects
            for triple_data in triple_list:
                if isinstance(triple_data, list) and len(triple_data) >= 3:
                    triple = Triple(
                        subject=str(triple_data[0]),
                        predicate=str(triple_data[1]),
                        object=str(triple_data[2]),
                        confidence=float(triple_data[3]) if len(triple_data) > 3 else None,
                        metadata={"doc_id": doc_id},
                    )
                    triples.append(triple)
                    
        except Exception as e:
            logger.error(f"Failed to parse triples: {e}")
        
        return triples
