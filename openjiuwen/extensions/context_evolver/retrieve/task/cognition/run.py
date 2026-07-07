# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Cognition algorithm retrieve operations."""

import json
from typing import List
import numpy as np
from openjiuwen.core.common.logging import context_engine_logger as logger

from ....core.op import BaseOp
from ....core.context import RuntimeContext
from ....schema import CognitionMemory, CognitionRetrievedMemory

from .prompt import CognitionPrompts


def _strip_json(response: str) -> str:
    """Safely strip markdown blocks from LLM output."""
    response_text = response.strip()
    if response_text.startswith("```json"): 
        response_text = response_text[7:]
    elif response_text.startswith("```"): 
        response_text = response_text[3:]
    if response_text.endswith("```"): 
        response_text = response_text[:-3]
    return response_text.strip()


class LoadSchemaOp(BaseOp):
    """Load dynamic attribute schema from vector store."""
    
    async def async_execute(self, context: RuntimeContext) -> None:
        user_id = context.get("user_id", "default")
        schema = {"domain": [], "intent": [], "other": []}
        all_cognitions: List[CognitionMemory] = []
        
        if not self.vector_store:
            logger.warning("Vector store not configured, using empty schema.")
            context.attribute_schema = schema
            context.all_cognitions = all_cognitions
            return

        try:
            # Query all cognition memories for this user to rebuild the dynamic schema
            nodes = await self.vector_store.async_search(
                embedding=[0.0] * 2560, 
                top_k=10000, 
                metadata_filter={"workspace_id": user_id, "type": "exp_memory"}
            )
            for node in nodes:
                try:
                    cog = CognitionMemory.from_vector_node(node)
                    all_cognitions.append(cog)
                    for k, v in cog.attributes.items():
                        if k not in schema: 
                            schema[k] = []
                        if v is not None and v not in schema[k]: 
                            schema[k].append(v)
                except Exception as parse_err:
                    logger.warning("Failed to parse cognition node %s: %s", node.id, parse_err)
        except Exception as e:
            logger.error("Error retrieving nodes from vector store: %s", e)
                    
        context.attribute_schema = schema
        context.all_cognitions = all_cognitions
        logger.info("Loaded %s cognitions, rebuilt dynamic schema.", len(all_cognitions))


class ClassifyQueryOp(BaseOp):
    """Classify user query into attributes using LLM."""
    
    async def async_execute(self, context: RuntimeContext) -> None:
        query = context.query
        schema = context.get("attribute_schema", {"domain": [], "intent": [], "other": []})
        
        if not self.llm:
            logger.warning("LLM not configured. Skipping classification.")
            context.query_attributes = {k: None for k in schema.keys()}
            return

        structured_schema = {k: v for k, v in schema.items() if k != "other"}
        all_keys = list(schema.keys())
        
        cq_dict = {
            "all_keys": all_keys, 
            "key_value": json.dumps(structured_schema, ensure_ascii=False), 
            "existing_others": json.dumps(schema.get("other", []), ensure_ascii=False), 
            "query": query
        }
        
        prompt = CognitionPrompts.classify_query_prompt.lstrip().format(**cq_dict)
        
        max_retries = 5
        for attempt in range(max_retries):
            try:
                response = await self.llm.async_generate(prompt=prompt, temperature=0.3)
                result = json.loads(_strip_json(response))
                
                # Validation
                all_structured_null = all(result.get(k) is None for k in structured_schema.keys())
                if all_structured_null and result.get("other") is None:
                    raise ValueError("Logical conflict: 'other' cannot be null if all structured keys are null.")
                    
                context.query_attributes = {k: result.get(k) for k in all_keys}
                break  # Success, exit retry loop
                
            except Exception as e:
                logger.warning("Classify Query attempt %s failed: %s", attempt + 1, e)
                if attempt == max_retries - 1:
                    fallback = {k: None for k in all_keys}
                    fallback["other"] = "Unclassified"
                    context.query_attributes = fallback


class RecallCognitionOp(BaseOp):
    """Recall relevant memories using attributes and semantics."""
    
    def __init__(self, top_k: int = 5):
        super().__init__(topk_retrieval=top_k)
        self.top_k = top_k
        
    async def async_execute(self, context: RuntimeContext) -> None:
        query_attrs = context.get("query_attributes", {})
        all_cognitions: List[CognitionMemory] = context.get("all_cognitions", [])
        schema = context.get("attribute_schema", {})
        
        relevant: List[CognitionMemory] = []
        matched_ids = set()
        
        # 1. Structured Match
        assigned_structured = {k: v for k, v in query_attrs.items() if k != "other" and v is not None}
        if assigned_structured:
            # Strict match
            for cog in all_cognitions:
                if all(cog.attributes.get(k) == v for k, v in assigned_structured.items()):
                    relevant.append(cog)
                    matched_ids.add(cog.id)
            # Relaxed match
            if not relevant:
                for cog in all_cognitions:
                    if any(cog.attributes.get(k) == v for k, v in assigned_structured.items()):
                        if cog.id not in matched_ids:
                            relevant.append(cog)
                            matched_ids.add(cog.id)
                            
        # 2. Semantic Match (Other)
        q_other = query_attrs.get("other")
        existing_others = schema.get("other", [])
        top_others = existing_others
        
        if q_other and existing_others:
            if len(existing_others) > self.top_k and self.embedding_model:
                try:
                    q_emb = await self.embedding_model.async_embed(q_other)
                    t_embs = await self.embedding_model.async_embed_batch(existing_others)

                    q_vec = np.array(q_emb)
                    t_vecs = np.array(t_embs)
                    dot_products = np.dot(t_vecs, q_vec)
                    q_norm = np.linalg.norm(q_vec)
                    t_norms = np.linalg.norm(t_vecs, axis=1)
                    with np.errstate(divide='ignore', invalid='ignore'):
                        similarities = dot_products / (q_norm * t_norms)
                        similarities = np.nan_to_num(similarities) # set NaN as 0
                        
                    top_indices = np.argsort(similarities)[-self.top_k:][::-1]

                    top_others = [existing_others[i] for i in top_indices]
                except Exception as e:
                    logger.warning("Semantic 'other' match failed, using all existing others: %s", e)
                    top_others = existing_others
                
            for cog in all_cognitions:
                if cog.id not in matched_ids and cog.attributes.get("other") in top_others:
                    relevant.append(cog)
                    matched_ids.add(cog.id)
                    
        # 3. Fallback: if neither pass matched anything, treat all memories as candidates
        # so the reranker can still select the most relevant ones.
        if not relevant:
            logger.info("No attribute/semantic match found. Using all %s cognitions as fallback candidates.",
                        len(all_cognitions))
            relevant = all_cognitions

        context.recalled_cognitions = relevant
        logger.info("Recalled %s candidate cognitions.", len(relevant))


class RerankCognitionOp(BaseOp):
    """Rerank recalled memories using LLM."""
    
    def __init__(self, top_k: int = 5):
        super().__init__(topk_rerank=top_k)
        self.top_k = top_k
        
    async def async_execute(self, context: RuntimeContext) -> None:
        relevant: List[CognitionMemory] = context.get("recalled_cognitions", [])
        
        if len(relevant) <= self.top_k:
            context.selected_cognitions = relevant
            return
            
        if not self.llm:
            logger.warning("LLM not configured. Skipping reranking.")
            context.selected_cognitions = relevant[:self.top_k]
            return
            
        candidates_data = [{"id": r.id, "query": r.query, "description": r.description} for r in relevant]
        valid_ids = {str(r.id) for r in relevant} # Ensure IDs are string for reliable check
        
        rc_dict = {
            "current_query": context.query, 
            "top_k": self.top_k,
            "candidates_json": json.dumps(candidates_data, ensure_ascii=False, indent=2)
        }
        
        prompt = CognitionPrompts.rerank_prompt.lstrip().format(**rc_dict)
        
        max_retries = 5
        for attempt in range(max_retries):
            try:
                response = await self.llm.async_generate(prompt=prompt, temperature=0.3)
                selected_ids_raw = json.loads(_strip_json(response))
                
                if not isinstance(selected_ids_raw, list):
                    raise ValueError(f"Expected a list of IDs, got {type(selected_ids_raw)}")
                    
                # Filter and convert to strings
                selected_ids = [str(sid) for sid in selected_ids_raw if str(sid) in valid_ids][:self.top_k]
                
                context.selected_cognitions = [r for r in relevant if str(r.id) in selected_ids]
                logger.debug("Successfully reranked cognitions.")
                break  # Success, exit retry loop
            except Exception as e:
                logger.warning("Rerank attempt %s failed: %s", attempt + 1, e)
                if attempt == max_retries - 1:
                    logger.error("All rerank attempts failed. Using top_k recalled items.")
                    context.selected_cognitions = relevant[:self.top_k]


class RewriteMemoryOp(BaseOp):
    """Format the final memory string and construct RetrievedMemory objects."""
    
    async def async_execute(self, context: RuntimeContext) -> None:
        selected: List[CognitionMemory] = context.get("selected_cognitions", [])
        retrieved_memories: List[CognitionRetrievedMemory] = []
        
        if not selected:
            logger.info("No cognitions selected, setting empty memory string.")
            context.memory_string = ""
            context.retrieved_memories = []
            return
            
        catalog = ""
        i = 1
        for cog in selected:
            for exp_str in cog.experience:
                catalog += f"- [{i}]: {exp_str}\n"
                i += 1
                
            # Create standard RetrievedMemory object defined in schema
            retrieved_memories.append(
                CognitionRetrievedMemory(
                    id=cog.id,
                    query=cog.query,
                    description=cog.description,
                    experience=cog.experience
                )
            )
                
        context.memory_string = catalog
        context.retrieved_memories = retrieved_memories
        logger.info("Generated memory string with %s insights from %s cognitions.", i - 1, len(selected))