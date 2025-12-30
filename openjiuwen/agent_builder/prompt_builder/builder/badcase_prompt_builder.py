# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import re
from typing import Optional, List, Generator

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.utils.llm.messages import AIMessage
from openjiuwen.core.utils.prompt.template.template import Template
from openjiuwen.core.component.common.configs.model_config import ModelConfig

from openjiuwen.agent_builder.prompt_builder.base import BasePromptBuilder
from openjiuwen.agent_builder.tune.base import EvaluatedCase
import openjiuwen.agent_builder.prompt_builder.builder.utils as TEMPLATE

MAX_CASES_LIMIT = 10


class BadCasePromptBuilder(BasePromptBuilder):
    def __init__(self, model_config: ModelConfig):
        super().__init__(model_config)

    def build(self,
              prompt: str | Template,
              cases: List[EvaluatedCase],
              ) -> Optional[str]:
        prompt = TEMPLATE.get_string_prompt(prompt)
        messages = self._format_bad_case_template(prompt, cases)
        response = self._model.invoke(self._model_name, messages)
        return response.content

    def stream_build(self,
                     prompt: str | Template,
                     cases: List[EvaluatedCase],
                     ) -> Generator:
        prompt = TEMPLATE.get_string_prompt(prompt)
        messages = self._format_bad_case_template(prompt, cases)
        chunks = self._model.stream(self._model_name, messages)
        for chunk in chunks:
            yield chunk.content

    def _format_bad_case_template(self,
                                 prompt: str,
                                 cases: List[EvaluatedCase],
                                 ) -> str:
        feedback = self._get_feedback_from_bad_case(prompt, cases)
        bad_case_optimize_template = TEMPLATE.PROMPT_BAD_CASE_OPTIMIZE_TEMPLATE
        messages = bad_case_optimize_template.format(
            dict(original_prompt=prompt,
                 feedback=feedback
                 )
        ).to_messages()
        return messages

    def _get_feedback_from_bad_case(self, prompt: str, cases: List[EvaluatedCase]) -> Optional[str]:
        self._validate_input(prompt, cases)
        bad_case_string = self._build_bad_case_string(cases)
        analyze_template = TEMPLATE.PROMPT_BAD_CASE_ANALYZE_TEMPLATE
        messages = analyze_template.format(
            dict(original_prompt=prompt,
                 bad_cases=bad_case_string
                 )
        ).to_messages()
        response = self._model.invoke(self._model_name, messages)
        feedback_summary = self._parse_feedback_summary(response)
        return feedback_summary

    def _parse_feedback_summary(self, response: AIMessage) -> Optional[str]:
        intent = re.findall(r"<intent>((?:(?!<intent>).)*?)</intent>", response.content, re.DOTALL)
        intent = [intent_text.strip() for intent_text in intent]
        if "false" in intent:
            logger.warning("Failed to get intent")
        text_match = re.findall(r"<summary>((?:(?!</summary>).)*?)</summary>", response.content, re.DOTALL)
        parse_summary = text_match[-1].strip() if len(text_match) >= 1 else response.content
        return parse_summary

    def _build_bad_case_string(self, cases: List[EvaluatedCase]) -> Optional[str]:
        bad_case_template = TEMPLATE.FORMAT_BAD_CASE_TEMPLATE
        bad_case_string = "\n".join(
            bad_case_template.format(
                dict(question=str(case.case.inputs),
                     label=str(case.case.label),
                     answer=str(case.answer),
                     reason=case.reason)
            ).content
            for case in cases
        )
        return bad_case_string

    def _validate_input(self, prompt: str, cases: List[EvaluatedCase]):
        if prompt is None:
            raise JiuWenBaseException(
                StatusCode.AGENT_BUILDER_FEEDBACK_TEMPLATE_ERROR.code,
                StatusCode.AGENT_BUILDER_FEEDBACK_TEMPLATE_ERROR.errmsg.format(
                    error_msg=f"prompt cannot be None"
                )
            )
        if not prompt.strip():
            raise JiuWenBaseException(
                StatusCode.AGENT_BUILDER_BAD_CASE_TEMPLATE_ERROR.code,
                StatusCode.AGENT_BUILDER_BAD_CASE_TEMPLATE_ERROR.errmsg.format(
                    error_msg=f"prompt cannot be empty"
                )
            )
        if not cases:
            raise JiuWenBaseException(
                StatusCode.AGENT_BUILDER_BAD_CASE_TEMPLATE_ERROR.code,
                StatusCode.AGENT_BUILDER_BAD_CASE_TEMPLATE_ERROR.errmsg.format(
                    error_msg=f"The cases cannot be empty"
                )
            )
        if len(cases) > MAX_CASES_LIMIT:
            raise JiuWenBaseException(
                StatusCode.AGENT_BUILDER_BAD_CASE_TEMPLATE_ERROR.code,
                StatusCode.AGENT_BUILDER_BAD_CASE_TEMPLATE_ERROR.errmsg.format(
                    error_msg=f"The number of cases cannot exceed {MAX_CASES_LIMIT}"
                )
            )
