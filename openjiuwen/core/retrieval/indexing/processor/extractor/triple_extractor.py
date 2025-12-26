# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
三元组提取器实现

使用 LLM 进行三元组提取。
"""
from typing import List, Any, Optional
import json
import asyncio

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.indexing.processor.extractor.base import Extractor
from openjiuwen.core.retrieval.common.document import TextChunk
from openjiuwen.core.retrieval.common.triple import Triple


class TripleExtractor(Extractor):
    """三元组提取器实现，使用 LLM 进行 OpenIE 三元组提取"""

    def __init__(
        self,
        llm_client: Any,
        model_name: str,
        temperature: float = 0.0,
        max_concurrent: int = 50,
        **kwargs: Any,
    ):
        """
        初始化三元组提取器
        
        Args:
            llm_client: LLM 客户端实例
            model_name: 模型名称
            temperature: 温度参数
            max_concurrent: 最大并发数，默认 50
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
        提取三元组
        
        Args:
            chunks: 文本块列表
            **kwargs: 额外参数
            
        Returns:
            三元组列表
        """
        async def _extract_chunk(chunk: TextChunk) -> List[Triple]:
            """处理单个 chunk 的三元组提取"""
            async with self.limiter:
                try:
                    # 构建提示词
                    prompt = self._build_prompt(chunk.text, chunk.metadata.get("title", ""))
                    messages = [{"role": "user", "content": prompt}]
                    
                    # 调用 LLM
                    completion = await self.llm_client.ainvoke(
                        model_name=self.model_name,
                        messages=messages,
                        temperature=self.temperature,
                    )
                    
                    # 解析结果
                    triples = self._parse_triples(completion.content, chunk.doc_id)
                    return triples
                    
                except Exception as e:
                    logger.error(f"Failed to extract triples from chunk {chunk.id_}: {e}")
                    return []
        
        # 使用 create_task 创建并行任务
        tasks = [asyncio.create_task(_extract_chunk(chunk)) for chunk in chunks]
        
        # 等待所有任务完成并收集结果
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 合并所有结果
        all_triples = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Task failed with exception: {result}")
            elif isinstance(result, list):
                all_triples.extend(result)
        
        return all_triples

    def _build_prompt(self, passage: str, title: str = "") -> str:
        """构建提取三元组的提示词"""
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
        """解析 LLM 返回的三元组"""
        triples = []
        
        try:
            # 尝试解析 JSON
            # 移除可能的 markdown 代码块标记
            content = content.strip()
            if content.startswith("```"):
                # 移除代码块标记
                lines = content.split("\n")
                content = "\n".join(lines[1:-1]) if len(lines) > 2 else content
            
            # 尝试直接解析 JSON
            try:
                triple_list = json.loads(content)
            except json.JSONDecodeError:
                # 如果不是有效的 JSON，尝试提取 JSON 部分
                import re
                json_match = re.search(r'\[\[.*?\]\]', content, re.DOTALL)
                if json_match:
                    triple_list = json.loads(json_match.group())
                else:
                    logger.error(f"Failed to parse triples from content: {content[:100]}")
                    return []
            
            # 转换为 Triple 对象
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
