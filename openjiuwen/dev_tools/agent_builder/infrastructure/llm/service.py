#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os
from typing import List, Union, Dict, Any

from openjiuwen.dev_tools.agent_builder.common.utils import load_yaml_file
from openjiuwen.core.utils.llm.model_utils.model_factory import ModelFactory
from openjiuwen.core.utils.llm.messages import BaseMessage


def get_model_info():
    current_file_path = os.path.abspath(__file__)
    target_dir = os.path.dirname(os.path.dirname(current_file_path))
    yaml_file = 'config.yaml'
    yaml_abs_path = os.path.join(target_dir, yaml_file)
    config_data = load_yaml_file(yaml_abs_path)
    model_info = config_data.get('llm_model_config', {})
    return model_info


def define_llm(model_info: dict):
    llm = ModelFactory().get_model(
        model_provider=model_info.get('model_provider', 'siliconflow'),
        api_key=model_info.get('api_key'),
        api_base=model_info.get('api_base'),
    )
    return llm


class LlmService:
    """Class for provides LLM service.

    Encapsulates LLM invocations, provides a unified interface, and supports both invoke and stream methods.

    Example:
        llm = LlmService({
            "model_provider": "siliconflow",
            "model_name": "your-model",
            "api_key": "sk-xxx"
        })
        response = llm.chat([HumanMessage(content="Hello")])
    """

    def __init__(self, model_info: dict):
        self.model_info = model_info if model_info else get_model_info()
        self.llm = define_llm(self.model_info)

    def chat(self, messages: Union[List[BaseMessage], List[Dict], str], method: str = 'invoke', **kwargs: Any):
        if method == 'stream':
            return self.llm.stream(self.model_info.get('model_name'), messages, **kwargs)
        else: # method == 'invoke':
            response = self.llm.invoke(self.model_info.get('model_name'), messages, **kwargs)
            return response.content
