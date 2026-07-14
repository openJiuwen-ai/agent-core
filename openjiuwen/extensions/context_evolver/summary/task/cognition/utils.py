# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Utility functions for Cognition summary operations."""

import json
from typing import Any, Dict
from openjiuwen.core.common.logging import context_engine_logger as logger


def safe_json_loads(response: str) -> Dict[str, Any]:
    """Safely extract and load JSON from LLM response strings."""
    response_text = response.strip()
    
    # Strip common markdown formatting
    if response_text.startswith("```json"): 
        response_text = response_text[7:]
    elif response_text.startswith("```"): 
        response_text = response_text[3:]
    if response_text.endswith("```"): 
        response_text = response_text[:-3]
        
    response_text = response_text.strip()
    
    try:
        return json.loads(response_text)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse JSON response. Attempting regex fallback. Error: %s", e)
        # Regex fallback for dirty json blocks
        import re
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass
        
        logger.error("Could not parse valid JSON from response.")
        return {}