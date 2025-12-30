# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import importlib
import os
from typing import Dict, Type

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.security.exception_utils import ExceptionUtils
from openjiuwen.core.common.security.user_config import UserConfig
from openjiuwen.core.common.utlis.singleton import Singleton
from openjiuwen.core.common.logging import logger
from openjiuwen.core.utils.llm.base import BaseModelClient


class ModelFactory(metaclass=Singleton):

    def __init__(self):
        self.model_map: Dict[str, Type[BaseModelClient]] = {}
        self._initialize_models()

    def _initialize_models(self):
        core_model_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'model_library'
        )
        self._load_model_dir(core_model_dir)

    @staticmethod
    def _load_models(model_dir: str) -> Dict[str, Type[BaseModelClient]]:
        model_dict = {}
        if not os.path.exists(model_dir):
            if UserConfig.is_sensitive():
                logger.warning("Model directory not found")
            else:
                logger.warning(f"Model directory not found: {model_dir}")
            return model_dict

        try:
            py_files = [
                f for f in os.listdir(model_dir)
                if (f.endswith('.py') or f.endswith('.pyc')) and f != "__init__.py" and f != "__init__.pyc"
            ]
            for py_file in py_files:
                module_name = os.path.splitext(py_file)[0]
                module_path = os.path.join(model_dir, py_file)

                try:
                    spec = importlib.util.spec_from_file_location(module_name, module_path)
                    if spec is None:
                        continue
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)

                    for _, obj in module.__dict__.items():
                        if isinstance(obj, type) and issubclass(obj, BaseModelClient) and obj != BaseModelClient:
                            model_dict[module_name] = obj
                            if not UserConfig.is_sensitive():
                                logger.info(f"Loaded model: {module_name} -> {obj.__name__}")
                except Exception as e:
                    if UserConfig.is_sensitive():
                        logger.error("Error loading module.")
                    else:
                        logger.error(f"Error loading module {py_file}: {str(e)}")
                    continue
        except Exception as e:
            if UserConfig.is_sensitive():
                logger.error("Error loading module.")
            else:
                logger.error(f"Error loading module: {str(e)}")
            raise Exception(f"module load error")
        return model_dict

    def _load_model_dir(self, model_dir: str):
        model_dict = self._load_models(model_dir)
        self.model_map.update(model_dict)

    def get_model(self, model_provider: str, api_key: str, api_base: str,
                  max_retries: int = 3, timeout: int = 60, **kwargs) -> BaseModelClient:
        model_cls = self.model_map.get(model_provider.lower())
        if not model_cls:
            if UserConfig.is_sensitive():
                ExceptionUtils.raise_exception(StatusCode.MODEL_PROVIDER_INVALID_ERROR,
                                               error_msg="unavailable model provider.")
            else:
                available_models = ", ".join(self.model_map.keys())
                ExceptionUtils.raise_exception(StatusCode.MODEL_PROVIDER_INVALID_ERROR,
                                               error_msg=f"unavailable model provider: {model_provider},"
                                                         f"and available providers are: {available_models}")
        params = {
            "max_retries": max_retries,
            "timeout": timeout,
            "temperature": kwargs.get("temperature", 0.95),
            "top_p": kwargs.get("top_p", 0.1),
            **kwargs
        }
        return model_cls(api_key=api_key, api_base=api_base, **params)
