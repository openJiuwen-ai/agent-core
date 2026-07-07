# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Cognition memory summarization and update operations."""

import json
import hashlib
from typing import List
from datetime import datetime, timezone
from openjiuwen.core.common.logging import context_engine_logger as logger

from ....core.op import BaseOp
from ....core.context import RuntimeContext
from ....schema import CognitionMemory
from ....core.persistence import MemoryPersistenceHelper

from .prompt import CognitionSummaryPrompts
from .utils import safe_json_loads


class SolutionClassifyOp(BaseOp):
    """Reclassify attributes based on the complete trajectory."""
    
    async def async_execute(self, context: RuntimeContext) -> None:
        trajectories = context.get("trajectories", [])
        if not trajectories:
            logger.warning("No trajectories found. Skipping.")
            return

        is_correct_list = context.get("is_correct", [])
        scores = context.get("score", context.get("label", []))

        best_idx = 0  # 默认选第一份
        
        # 如果有成功的答卷，找到第一份成功答卷的索引
        if isinstance(is_correct_list, list) and True in is_correct_list:
            best_idx = is_correct_list.index(True)
        # 如果没有明确的 True/False，但有具体的分数，选分数最高的
        elif isinstance(scores, list) and scores and max(scores) > 0:
            best_idx = scores.index(max(scores))
        
        # 将选出的“最佳草稿”和它的“是否正确”单独存入 context，供所有后续节点使用
        best_traj_raw = trajectories[best_idx]
        if isinstance(best_traj_raw, list) and best_traj_raw and isinstance(best_traj_raw[0], list):
            best_traj_raw = best_traj_raw[0]
        context.best_trajectory = "\n".join(best_traj_raw) if isinstance(best_traj_raw, list) else str(best_traj_raw)

        if is_correct_list:
            context.best_is_correct = is_correct_list[best_idx]
        elif scores:
            context.best_is_correct = bool(scores[best_idx])
        else:
            context.best_is_correct = None

        query = context.query
        initial_attributes = context.get("query_attributes", {})
        schema = context.get("attribute_schema", {"domain": [], "intent": [], "other": []})

        cs_dict = {
            "query": query,
            "initial_attributes": json.dumps(initial_attributes, ensure_ascii=False),
            "trajectory": context.best_trajectory,
            "current_schema": json.dumps(schema, ensure_ascii=False, indent=2)
        }
        prompt = CognitionSummaryPrompts.classify_solution_prompt.lstrip().format(**cs_dict)
        
        if not self.llm:
            logger.warning("LLM not configured. Using initial attributes.")
            context.final_attributes = initial_attributes
            return

        max_retries = 5
        for attempt in range(max_retries):
            try:
                response = await self.llm.async_generate(prompt=prompt, temperature=0.3)
                result = safe_json_loads(response)
                if not result:
                    raise ValueError("Parsed JSON is empty.")
                context.final_attributes = {k: result.get(k) for k in schema.keys()}
                logger.debug("Successfully classified solution.")
                break
            except Exception as e:
                logger.warning("Solution Classify attempt %s failed: %s", attempt + 1, e)
                if attempt == max_retries - 1:
                    logger.error("All solution classify attempts failed. Using initial attributes.")
                    context.final_attributes = initial_attributes


class GenerateExperienceOp(BaseOp):
    """Extract Description and Experience insights from trajectory."""
    
    async def async_execute(self, context: RuntimeContext) -> None:
        best_trajectory = context.get("best_trajectory", "")
        best_is_correct = context.get("best_is_correct")

        wc_dict = {
            "query": context.query, 
            "trajectory": best_trajectory, 
            "is_correct": best_is_correct
        }
        prompt = CognitionSummaryPrompts.write_cognition_prompt.lstrip().format(**wc_dict)
        
        if not self.llm:
            logger.warning("LLM not configured. Setting empty experience.")
            context.generated_description = "LLM not available."
            context.generated_experience = ["No insights extracted."]
            return

        max_retries = 5
        for attempt in range(max_retries):
            try:
                response = await self.llm.async_generate(prompt=prompt, temperature=0.3)
                result = safe_json_loads(response)
                
                description = result.get("description")
                experience = result.get("experience")
                
                if not description or not experience or not isinstance(experience, list):
                    raise ValueError("Missing or invalid 'description'/'experience' keys in JSON")
                    
                context.generated_description = description
                context.generated_experience = experience
                logger.debug("Successfully generated experience with %s items.", len(experience))
                break
            except Exception as e:
                logger.warning("Write Cognition attempt %s failed: %s", attempt + 1, e)
                if attempt == max_retries - 1:
                    logger.error("All write cognition attempts failed. Using fallback empty experience.")
                context.generated_description = "The description of this task is not generated properly."
                context.generated_experience = [""]


class UpdateVectorStoreOp(BaseOp):
    """Convert generated info to CognitionMemory Node and upsert to Vector Store."""
    
    async def async_execute(self, context: RuntimeContext) -> None:
        experience = context.get("generated_experience")
        description = context.get("generated_description")
        attributes = context.get("final_attributes")
        is_correct = context.get("best_is_correct")
        user_id = context.get("user_id", "default")
        query = context.get("query", "Unknown Query")

        if not experience or not attributes:
            logger.warning("Missing experience or attributes. Skipping vector store update.")
            context.memories = []
            context.memory_ids = []
            return

        try:
            content_str = json.dumps(experience, ensure_ascii=False) + query
            mem_id = f"cog_{hashlib.md5(content_str.encode()).hexdigest()[:8]}"

            new_memory = CognitionMemory(
                id=mem_id,
                workspace_id=user_id,
                query=query,
                description=description,
                experience=experience,
                is_correct=is_correct,
                attributes=attributes,
                created_at=datetime.now(timezone.utc).isoformat()
            )

            vector_node = new_memory.to_vector_node()

            if not self.vector_store or not self.embedding_model:
                logger.error("Vector store or Embedding model not configured. Memory lost.")
                context.memories = []
                context.memory_ids = []
                return

            vector_node.embedding = await self.embedding_model.async_embed(vector_node.content)
            await self.vector_store.async_upsert(vector_node)
            logger.info("Successfully upserted Cognition memory: %s", vector_node.id)

            context.memories = [new_memory]
            context.memory_ids = [vector_node.id]

        except Exception as e:
            logger.error("Failed to upsert Cognition memory to vector store: %s", e)
            context.memories = []
            context.memory_ids = []


class PersistMemoryOp(BaseOp):
    """Persist Cognition memories from the in-memory vector store to disk/Milvus."""
    
    _ALGO_NAME = "cognition"

    def __init__(
            self, persist_type: str = "auto", 
            persist_path: str = "./memories/{algo_name}/{user_id}.json", 
            **kwargs):
        super().__init__(persist_type=persist_type, persist_path=persist_path, **kwargs)
        self._helper = MemoryPersistenceHelper(
            persist_type=persist_type, 
            persist_path=persist_path, 
            milvus_host=kwargs.get("milvus_host", "localhost"),
            milvus_port=kwargs.get("milvus_port", 19530),
            milvus_collection=kwargs.get("milvus_collection", "vector_nodes")
        )

    async def async_execute(self, context: RuntimeContext) -> None:
        user_id = context.get("user_id", "default")
        
        if not self.vector_store:
            context.persist_count = 0
            return
        
        all_nodes = self.vector_store.get_all(
            metadata_filter={"workspace_id": user_id, "type": "exp_memory"}
        )
        
        if not all_nodes:
            logger.info("PersistMemoryOp (Cognition): no memories to persist for user=%s", user_id)
            context.persist_count = 0
            return

        try:
            nodes_dict = {node.id: node.to_dict() for node in all_nodes}
            self._helper.save(user_id, self._ALGO_NAME, nodes_dict)
            context.persist_count = len(nodes_dict)
            logger.info("PersistMemoryOp (Cognition): persisted %d memories for user=%s via %s", 
                        len(nodes_dict), user_id, self._helper.persist_type)
        except Exception as e:
            logger.error("Failed to persist Cognition memories: %s", e)
            context.persist_count = 0