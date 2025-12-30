# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import re
from typing import Optional, Literal, List, Generator

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.component.common.configs.model_config import ModelConfig
from openjiuwen.core.utils.llm.messages import BaseMessage
from openjiuwen.core.utils.prompt.template.template import Template

from openjiuwen.agent_builder.prompt_builder.base import BasePromptBuilder
import openjiuwen.agent_builder.prompt_builder.builder.utils as TEMPLATE


INSERT_STR: str = "[用户要插入的位置]"

JSON_STRING_MAX_LENGTH: int = 10000


class FeedbackPromptBuilder(BasePromptBuilder):
    def __init__(self, model_config: ModelConfig):
        super().__init__(model_config)

    def build(self,
              prompt: str | Template,
              feedback: str,
              mode: Literal["general", "insert", "select"] = "general",
              start_pos: Optional[int] = None,
              end_pos: Optional[int] = None,
              ) -> Optional[str]:
        prompt = TEMPLATE.get_string_prompt(prompt)
        self._is_valid_prompt(prompt, feedback)
        messages = self._format_feedback_template(prompt, feedback, mode, start_pos, end_pos)
        response = self._model.invoke(self._model_name, messages)
        if response is None:
            return None
        return response.content

    def stream_build(self,
                     prompt: str | Template,
                     feedback: str,
                     mode: Literal["general", "insert", "select"] = "general",
                     start_pos: Optional[int] = None,
                     end_pos: Optional[int] = None,
                     ) -> Generator:
        prompt = TEMPLATE.get_string_prompt(prompt)
        self._is_valid_prompt(prompt, feedback)
        messages = self._format_feedback_template(prompt, feedback, mode, start_pos, end_pos)
        chunks = self._model.stream(self._model_name, messages)
        for chunk in chunks:
            yield chunk.content

    def _format_feedback_template(self,
                              prompt: str,
                              feedback: str,
                              mode: Literal["general", "insert", "select"] = "general",
                              start_pos: Optional[int] = None,
                              end_pos: Optional[int] = None,
                              ) -> List[BaseMessage]:

        if mode == "insert":
            return self._format_feedback_template_insert(prompt, feedback, start_pos)
        elif mode == "select":
            return self._format_feedback_template_select(prompt, feedback, start_pos, end_pos)
        else:
            if mode != "general":
                logger.warning(f"Invalid mode: {mode}, using `general` instead")
            return self._format_feedback_template_general(prompt, feedback)

    def _format_feedback_template_general(self,
                                         prompt: str,
                                         feedback: str,
                                         ) -> List[BaseMessage]:
        feedback_general_template = TEMPLATE.PROMPT_FEEDBACK_GENERAL_TEMPLATE
        messages = feedback_general_template.format(
            dict(original_prompt=prompt,
                 suggestion=feedback
                 )
        ).to_messages()
        return messages

    def _format_feedback_template_insert(self,
                                         prompt: str,
                                         feedback: str,
                                         start_pos: Optional[int] = None,
                                         ) -> List[BaseMessage]:
        self._is_index_within_bounds(prompt, "insert", start_pos)
        optimized_feedback = self._is_feedback_valid(prompt, feedback)
        tagged_prompt = self._insert_sting(prompt, start_pos)
        feedback_insert_template = TEMPLATE.PROMPT_FEEDBACK_INSERT_TEMPLATE
        messages = feedback_insert_template.format(
            dict(original_prompt=tagged_prompt,
                 suggestion=optimized_feedback
                 )
        ).to_messages()
        return messages

    def _format_feedback_template_select(self,
                                         prompt: str,
                                         feedback: str,
                                         start_pos: Optional[int] = None,
                                         end_pos: Optional[int] = None,
                                         ) -> List[BaseMessage]:
        self._is_index_within_bounds(prompt, "select", start_pos, end_pos)
        optimized_feedback = self._is_feedback_valid(prompt, feedback)
        prompt_to_modify = prompt[start_pos:end_pos]
        feedback_select_template = TEMPLATE.PROMPT_FEEDBACK_SELECT_TEMPLATE
        messages = feedback_select_template.format(
            dict(original_prompt=prompt,
                 suggestion=optimized_feedback,
                 pending_optimized_prompt=prompt_to_modify
                 )
        ).to_messages()
        return messages

    def _insert_sting(self,
                      prompt: str,
                      insert: Optional[int]
                      ) -> Optional[str]:
        return prompt[:insert] + INSERT_STR + prompt[insert:]

    def _is_feedback_valid(self,
                           prompt: str,
                           feedback: str
                           ) -> str:
        feedback_intent_template = TEMPLATE.PROMPT_FEEDBACK_INTENT_TEMPLATE
        messages = feedback_intent_template.format(
            dict(original_prompt=prompt,
                 feedbacks=feedback
                 )
        ).to_messages()
        feedback_message = self._model.invoke(self._model_name, messages)
        try:
            intent, optimized_feedback = self._extract_intent_from_respones(feedback_message.content)
        except JiuWenBaseException:
            logger.warning(f"Intent recognition failed, using original feedback instead")
            return feedback
        if not intent or not optimized_feedback.strip():
            logger.warning(f"Intent recognition failed, using original feedback instead")
            return feedback
        return optimized_feedback.strip()

    def _is_index_within_bounds(self, prompt: str, mode, start_pos: int, end_pos: Optional[int] = None) -> bool:
        if mode == "select":
            if not isinstance(start_pos, int) or not isinstance(end_pos, int):
                raise JiuWenBaseException(
                    StatusCode.AGENT_BUILDER_FEEDBACK_TEMPLATE_ERROR.code,
                    StatusCode.AGENT_BUILDER_FEEDBACK_TEMPLATE_ERROR.errmsg.format(
                        error_msg=f"start_pos and end_pos must be provided for int type"
                    )
                )
            if start_pos is not None and end_pos is not None:
                if 0 <= start_pos < end_pos <= len(prompt):
                    return True
            raise JiuWenBaseException(
                StatusCode.AGENT_BUILDER_FEEDBACK_TEMPLATE_ERROR.code,
                StatusCode.AGENT_BUILDER_FEEDBACK_TEMPLATE_ERROR.errmsg.format(
                    error_msg=f"start_pos and end_pos must be provided for select mode. "
                              f"Additionally, they must satisfy the conditions: "
                              f"0 <= start_pos < end_pos <= len(prompt)."
                )
            )
        elif mode == "insert":
            if not isinstance(start_pos, int):
                raise JiuWenBaseException(
                    StatusCode.AGENT_BUILDER_FEEDBACK_TEMPLATE_ERROR.code,
                    StatusCode.AGENT_BUILDER_FEEDBACK_TEMPLATE_ERROR.errmsg.format(
                        error_msg=f"start_pos must be provided for int type"
                    )
                )
            if start_pos is not None:
                if 0 <= start_pos <= len(prompt):
                    return True
            raise JiuWenBaseException(
                StatusCode.AGENT_BUILDER_FEEDBACK_TEMPLATE_ERROR.code,
                StatusCode.AGENT_BUILDER_FEEDBACK_TEMPLATE_ERROR.errmsg.format(
                    error_msg=f"start_pos must be provided for insert mode. "
                              f"Additionally, it must satisfy the conditions: "
                              f"0 <= start_pos <= len(prompt)."
                )
            )
        return False

    def _is_valid_prompt(self, prompt: str, feedback: str):
        if prompt is None or feedback is None:
            raise JiuWenBaseException(
                StatusCode.AGENT_BUILDER_FEEDBACK_TEMPLATE_ERROR.code,
                StatusCode.AGENT_BUILDER_FEEDBACK_TEMPLATE_ERROR.errmsg.format(
                    error_msg=f"prompt or feedback cannot be None"
                )
            )
        if not prompt.strip() or not feedback.strip():
            raise JiuWenBaseException(
                StatusCode.AGENT_BUILDER_FEEDBACK_TEMPLATE_ERROR.code,
                StatusCode.AGENT_BUILDER_FEEDBACK_TEMPLATE_ERROR.errmsg.format(
                    error_msg=f"prompt or feedback cannot be empty"
                )
            )

    def _extract_intent_from_respones(self, input_json: str):
        pattern = rf"```json(.{{1,{JSON_STRING_MAX_LENGTH}}}?)```"
        try:
            match = re.search(pattern, input_json, re.DOTALL)
            if match:
                json_str = match.group(1).strip()
                parsed_json = json.loads(json_str)
                intent = parsed_json.get("intent", False) in ("true", True, "True")
                optimized_feedback = parsed_json.get("optimized_feedback", "").strip()
                return intent, optimized_feedback.strip()
            raise JiuWenBaseException(
                StatusCode.AGENT_BUILDER_FEEDBACK_TEMPLATE_ERROR.code,
                StatusCode.AGENT_BUILDER_FEEDBACK_TEMPLATE_ERROR.errmsg.format(
                    error_msg=f"No valid JSON string found"
                )
            )
        except json.JSONDecodeError:
            raise JiuWenBaseException(
                StatusCode.AGENT_BUILDER_FEEDBACK_TEMPLATE_ERROR.code,
                StatusCode.AGENT_BUILDER_FEEDBACK_TEMPLATE_ERROR.errmsg.format(
                    error_msg=f"An error occurred while parsing JSON"
                )
            )
        except Exception:
            raise JiuWenBaseException(
                StatusCode.AGENT_BUILDER_FEEDBACK_TEMPLATE_ERROR.code,
                StatusCode.AGENT_BUILDER_FEEDBACK_TEMPLATE_ERROR.errmsg.format(
                    error_msg=f"An error occurred while parsing intent JSON from message"
                )
            )
