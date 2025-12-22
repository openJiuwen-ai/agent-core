"""
O3 Model Integration Handler
Handles O3 model calls for hints extraction and final answer extraction with type-specific formatting
"""

import re
from typing import Optional, Tuple

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from examples.super_agent.agent.prompt_templates import (
    get_o3_hints_prompt,
    get_o3_answer_type_prompt,
    get_o3_final_answer_prompt
)
from openjiuwen.core.common.logging import logger


class O3Handler:
    """
    Handler for O3 model integration

    Features:
    - Extract task hints to identify potential challenges
    - Determine answer type (number, date, time, string)
    - Extract final answer with type-specific formatting
    - Confidence scoring
    - Message ID support
    """

    def __init__(self, api_key: str, enable_message_ids: bool = True):
        """
        Initialize O3 Handler

        Args:
            api_key: OpenAI API key for O3 model
            enable_message_ids: Whether to add message IDs to requests
        """
        self.client = AsyncOpenAI(api_key=api_key, timeout=600)
        self.enable_message_ids = enable_message_ids

    def _generate_message_id(self) -> str:
        """Generate random message ID using common LLM format"""
        import uuid
        return f"msg_{uuid.uuid4().hex[:8]}"

    @retry(wait=wait_exponential(multiplier=15), stop=stop_after_attempt(1))
    async def extract_hints(self, question: str) -> str:
        """
        Use O3 model to extract task hints

        Args:
            question: Task description/question

        Returns:
            Extracted hints highlighting potential challenges

        Raises:
            ValueError: If O3 returns empty result
        """
        content = get_o3_hints_prompt(question)
        if self.enable_message_ids:
            message_id = self._generate_message_id()
            content = f"[{message_id}] {content}"

        response = await self.client.chat.completions.create(
            model="o3",
            messages=[{"role": "user", "content": content}],
            reasoning_effort="high"
        )

        logger.debug(f"O3 hints extraction response: {response}")

        result = response.choices[0].message.content

        # Check if result is empty, raise exception to trigger retry if empty
        if not result or not result.strip():
            raise ValueError("O3 hints extraction returned empty result")

        return result

    @retry(wait=wait_exponential(multiplier=15), stop=stop_after_attempt(5))
    async def get_answer_type(self, task_description: str) -> str:
        """
        Determine the expected answer type for the task

        Args:
            task_description: Task description/question

        Returns:
            Answer type: "number", "date", "time", or "string"

        Raises:
            ValueError: If answer type detection returns empty result
        """
        content = get_o3_answer_type_prompt(task_description)
        logger.debug(f"Answer type instruction: {content}")
        if self.enable_message_ids:
            message_id = self._generate_message_id()
            content = f"[{message_id}] {content}"

        response = await self.client.chat.completions.create(
            model="gpt-5",
            messages=[{"role": "user", "content": content}],
            reasoning_effort="medium"
        )

        answer_type = response.choices[0].message.content

        # Check if result is empty, raise exception to trigger retry if empty
        if not answer_type or not answer_type.strip():
            raise ValueError("Answer type detection returned empty result")

        logger.debug(f"Answer type: {answer_type}")

        return answer_type.strip()

    @retry(wait=wait_exponential(multiplier=15), stop=stop_after_attempt(5))
    async def extract_final_answer(
        self,
        answer_type: str,
        task_description: str,
        summary: str
    ) -> Tuple[str, Optional[int]]:
        """
        Use O3 model to extract final answer from summary with type-specific formatting

        Args:
            answer_type: Expected answer type (number, date, time, string)
            task_description: Original task description
            summary: Agent's summary of the task execution

        Returns:
            Tuple of (full_response, confidence_score)
            - full_response: Complete O3 response with analysis and boxed answer
            - confidence_score: Confidence score (0-100), None if not found

        Raises:
            ValueError: If O3 returns empty result or no boxed answer
        """
        # Get type-specific prompt from template
        content = get_o3_final_answer_prompt(answer_type, task_description, summary)
        logger.debug("O3 Extract Final Answer Prompt:")
        logger.debug(content)
        if self.enable_message_ids:
            message_id = self._generate_message_id()
            content = f"[{message_id}] {content}"

        response = await self.client.chat.completions.create(
            model="o3",
            messages=[{"role": "user", "content": content}],
            reasoning_effort="medium",
        )

        result = response.choices[0].message.content

        # Check if result is empty, raise exception to trigger retry if empty
        if not result or not result.strip():
            raise ValueError("O3 final answer extraction returned empty result")

        # Verify boxed answer exists
        match = re.search(r"\\boxed{([^}]*)}", result)
        if not match:
            raise ValueError("O3 final answer extraction returned empty answer")

        # Extract confidence score if present
        confidence = None
        conf_match = re.search(r"\*\*Confidence:\*\*\s*(\d+)", result)
        if conf_match:
            confidence = int(conf_match.group(1))

        return result, confidence

    def extract_boxed_answer(self, o3_response: str) -> Optional[str]:
        """
        Extract the boxed answer from O3 response

        Args:
            o3_response: Full O3 response text

        Returns:
            Boxed answer content, or None if not found
        """
        match = re.search(r"\\boxed{([^}]*)}", o3_response)
        if match:
            return match.group(1)
        return None
