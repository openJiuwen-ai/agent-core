# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from dataclasses import dataclass

CLASSIFY_QUERY_TEMPLATES = """You are a strict, highly accurate JSON classification engine. 
Your task is to classify the provided "User Query" based on a dynamic schema.

### REQUIRED OUTPUT FORMAT
You MUST output ONLY a valid JSON object. Do NOT wrap the response in markdown blocks (e.g., no ```json). 
The JSON object MUST contain exactly these keys and no others:
{all_keys}

### STRUCTURED SCHEMA
For the predefined categories, you may ONLY choose from the exact strings listed below:
{key_value}

### EXISTING "OTHER" TAGS
{existing_others}

### STRICT RULES
1. EXACT MATCH ONLY FOR STRUCTURED KEYS: For the structured keys defined in the STRUCTURED SCHEMA above, your assigned value MUST perfectly match one of the provided strings. If no existing value appropriately fits the query, you MUST assign `null`. Do NOT invent new values for these keys.
2. THE "other" KEY (Fallback & Semantic Tag):
   - You MUST first attempt to select the most appropriate tag from the "EXISTING 'OTHER' TAGS" list.
   - If AND ONLY IF none of the existing tags accurately describe the query, you may generate a concise (1-2 words) new semantic tag. 
   - CRITICAL: You MUST replace any spaces in the generated value with underscores (`_`, e.g., output "Time_Management" instead of "Time Management").
   - IF all structured keys are assigned `null`, the "other" key MUST NOT be `null`. 
   - IF one or more structured keys are successfully matched, the "other" key may be `null`, or you may provide a supplementary tag from the list or generate a new one.

### USER QUERY
{query}"""

RERANK_COGNITION_TEMPLATES = """You are a strict, highly accurate semantic ranking engine. 
Your task is to evaluate a list of candidate past experiences and select the top {top_k} most relevant ones for the given user query.

### REQUIRED OUTPUT FORMAT
You MUST output ONLY a valid JSON list of IDs. Do NOT wrap the response in markdown blocks (e.g., no ```json).
Depending on the type of IDs in the candidate list, the output should look like this:
Example (Integer IDs): [0, 5, 12]

### STRICT RULES
1. LIMIT: You MUST select strictly up to {top_k} IDs. If fewer than {top_k} are genuinely relevant, you may return fewer, but NEVER more.
2. EXACT MATCH: The IDs in your output MUST be exact matches from the "Candidate Experiences" list provided below. Do NOT invent or hallucinate IDs.
3. RELEVANCE CRITERIA: Base your judgment on the semantic similarity between the "CURRENT USER QUERY" and the candidate's "query" and "description".

### CURRENT USER QUERY
"{current_query}"

### CANDIDATE EXPERIENCES
{candidates_json}"""


@dataclass
class CognitionPrompt:
    classify_query_prompt: str = CLASSIFY_QUERY_TEMPLATES
    rerank_prompt: str = RERANK_COGNITION_TEMPLATES

CognitionPrompts = CognitionPrompt()