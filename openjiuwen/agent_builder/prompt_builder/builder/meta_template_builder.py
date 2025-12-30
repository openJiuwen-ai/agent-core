# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import copy
from typing import List, Optional, Generator, Literal

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.utils.tool.schema import ToolInfo
from openjiuwen.core.utils.prompt.template.template import Template
from openjiuwen.core.component.common.configs.model_config import ModelConfig

from openjiuwen.agent_builder.prompt_builder.base import BasePromptBuilder
import openjiuwen.agent_builder.prompt_builder.builder.utils as TEMPLATE


META_TEMPLATE_NAME_PREFIX: str = "META_TEMPLATE_"


class MetaTemplateBuilder(BasePromptBuilder):
    def __init__(self, model_config: ModelConfig):
        super().__init__(model_config)
        self._meta_template_manager = dict()

    def register_meta_template(self, name: str, meta_template: str | Template):
        if isinstance(meta_template, str):
            template_to_reg = Template(name=f"{META_TEMPLATE_NAME_PREFIX}{name}", content=meta_template)
        elif isinstance(meta_template, Template):
            template_to_reg = copy.deepcopy(meta_template)
            template_to_reg.name = f"{META_TEMPLATE_NAME_PREFIX}{name}"
        else:
            raise JiuWenBaseException(
                StatusCode.AGENT_BUILDER_META_TEMPLATE_REGISTER_ERROR.code,
                StatusCode.AGENT_BUILDER_META_TEMPLATE_REGISTER_ERROR.errmsg.format(
                    error_msg=f"failed to register meta-template: {name}"
                )
            )
        self._meta_template_manager.update({template_to_reg.name: template_to_reg})

    def build(self,
              prompt: str | Template,
              tools: Optional[List[ToolInfo]] = None,
              template_type: Literal["general", "plan", "other"] = "general",
              custom_template_name: Optional[str] = None
              ) -> Optional[str]:
        prompt = TEMPLATE.get_string_prompt(prompt)
        self._is_valid_prompt(prompt, tools)
        messages = self._format_meta_template(prompt, tools, template_type, custom_template_name)
        response = self._model.invoke(self._model_name, messages)
        if response is None:
            return None
        return response.content

    def stream_build(self,
                     prompt: str | Template,
                     tools: Optional[List[ToolInfo]] = None,
                     template_type: Literal["general", "plan", "other"] = "general",
                     custom_template_name: Optional[str] = None
                     ) -> Generator:
        prompt = TEMPLATE.get_string_prompt(prompt)
        self._is_valid_prompt(prompt, tools)
        messages = self._format_meta_template(prompt, tools, template_type, custom_template_name)
        chunks = self._model.stream(self._model_name, messages)
        for chunk in chunks:
            yield chunk.content

    def _format_meta_template(self,
                              prompt: str,
                              tools: Optional[List[ToolInfo]] = None,
                              template_type: Literal["general", "plan", "other"] = "general",
                              custom_template_name: Optional[str] = None
                              ) -> str:
        if template_type == "other":
            return self._format_custom_meta_template(custom_template_name, prompt, tools)
        else:
            return self._format_predefined_meta_template(template_type, prompt, tools)

    @staticmethod
    def _format_predefined_meta_template(template_type: str,
                                         prompt: str,
                                         tools: Optional[List[ToolInfo]] = None
                                         ):
        if template_type == "plan":
            meta_system_template = TEMPLATE.PROMPT_BUILD_PLAN_META_SYSTEM_TEMPLATE
            meta_user_template = TEMPLATE.PROMPT_BUILD_PLAN_META_USER_TEMPLATE
        else:
            if template_type != "general":
                logger.warning(f"Invalid template_type: {template_type}, using `general` instead")
            meta_system_template = TEMPLATE.PROMPT_BUILD_GENERAL_META_SYSTEM_TEMPLATE
            meta_user_template = TEMPLATE.PROMPT_BUILD_GENERAL_META_USER_TEMPLATE

        messages = meta_system_template.to_messages()
        messages.extend(meta_user_template.format(
            dict(instruction=prompt, tools=str(tools))
        ).to_messages())
        return messages

    def _format_custom_meta_template(self,
                                     custom_meta_template_name: str,
                                     prompt: str,
                                     tools: Optional[List[ToolInfo]] = None
                                     ):
        if not custom_meta_template_name:
            raise JiuWenBaseException(
                StatusCode.AGENT_BUILDER_META_TEMPLATE_ERROR.code,
                StatusCode.AGENT_BUILDER_META_TEMPLATE_ERROR.errmsg.format(
                    error_msg=f"failed to get custom meta-template, please provide template name"
                )
            )
        custom_meta_template_name = f"{META_TEMPLATE_NAME_PREFIX}{custom_meta_template_name}"
        custom_meta_template = self._meta_template_manager.get(custom_meta_template_name)
        if not custom_meta_template:
            raise JiuWenBaseException(
                StatusCode.AGENT_BUILDER_META_TEMPLATE_ERROR.code,
                StatusCode.AGENT_BUILDER_META_TEMPLATE_ERROR.errmsg.format(
                    error_msg=f"failed to get custom meta-template: {custom_meta_template_name}"
                )
            )
        return custom_meta_template.format(
            dict(instruction=prompt, tools=str(tools))
        ).to_messages()

    def _is_valid_prompt(self, prompt: str, tools: List[ToolInfo]):
        if prompt is None:
            raise JiuWenBaseException(
                StatusCode.AGENT_BUILDER_META_TEMPLATE_ERROR.code,
                StatusCode.AGENT_BUILDER_META_TEMPLATE_ERROR.errmsg.format(
                    error_msg=f"prompt cannot be None"
                )
            )
        if not prompt.strip():
            raise JiuWenBaseException(
                StatusCode.AGENT_BUILDER_META_TEMPLATE_ERROR.code,
                StatusCode.AGENT_BUILDER_META_TEMPLATE_ERROR.errmsg.format(
                    error_msg=f"prompt cannot be empty"
                )
            )
        if tools and any(not isinstance(tool, ToolInfo) for tool in tools):
            raise JiuWenBaseException(
                StatusCode.AGENT_BUILDER_META_TEMPLATE_ERROR.code,
                StatusCode.AGENT_BUILDER_META_TEMPLATE_ERROR.errmsg.format(
                    error_msg=f"each tool must be an instance of ToolInfo"
                )
            )
