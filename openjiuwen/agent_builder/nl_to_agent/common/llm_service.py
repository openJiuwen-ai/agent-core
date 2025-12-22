# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os
from typing import List, Union, Dict, Any

from openjiuwen.agent_builder.nl_to_agent.utils.utils import load_yaml_file
from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.foundation.llm import ModelFactory


def get_model_info():
    current_file_path = os.path.abspath(__file__)
    target_dir = os.path.dirname(os.path.dirname(os.path.dirname(current_file_path)))
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
    def __init__(self, model_info: dict):
        self.model_info = model_info if model_info else get_model_info()
        self.llm = define_llm(self.model_info)

    def chat(self, messages: Union[List[BaseMessage], List[Dict], str], method: str = 'invoke', **kwargs: Any):
        if method == 'stream':
            return self.llm.stream(self.model_info.get('model_name'), messages, **kwargs)
        else: # method == 'invoke':
            response = self.llm.invoke(self.model_info.get('model_name'), messages, **kwargs)
            return response.content
