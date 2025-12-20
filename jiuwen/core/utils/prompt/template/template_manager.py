#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import copy
import os
import re
from typing import Callable, List

from jiuwen.core.common.logging import logger

from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.utils.prompt.common.singleton import Singleton
from jiuwen.core.utils.prompt.index.template_store.in_memory_template_store import InMemoryTemplateStore
from jiuwen.core.utils.prompt.index.template_store.template_store import TemplateStore, Template


class TemplateManager(metaclass=Singleton):
    """Template manager class"""
    template_store: TemplateStore
    __filter_func: Callable = None

    def __init__(self):
        self.template_store = InMemoryTemplateStore()
        self.__filter_func = default_template_filter
        self.init_prompt_templates()

    @staticmethod
    def load_from_dir(dir_path: str, suffix: str = ".pr", max_file_size: int = 10 * 1024 * 1024) -> List[Template]:
        dir_path = os.path.abspath(dir_path)
        if not os.path.isdir(dir_path):
            raise JiuWenBaseException(
                error_code=StatusCode.PROMPT_TEMPLATE_INCORRECT_ERROR.code,
                message="Template dir doesn't exist."
            )

        dir_path_realpath = os.path.realpath(dir_path).lower()
        if not os.path.isdir(dir_path_realpath):
            raise JiuWenBaseException(
                error_code=StatusCode.PROMPT_TEMPLATE_INCORRECT_ERROR.code,
                message="Directory path is invalid or inaccessible"
            )

        files = os.listdir(dir_path_realpath)
        templates = []
        files = [f for f in files if f.endswith(suffix)]
        filename_pattern = re.compile(r'^[a-zA-Z0-9_.-]+$')

        for template_file in files:
            # Validate filename
            if not filename_pattern.match(template_file):
                raise JiuWenBaseException(
                    error_code=StatusCode.PROMPT_TEMPLATE_INCORRECT_ERROR.code,
                    message="Filename contains invalid characters"
                )

            template_path = os.path.join(dir_path_realpath, template_file)
            template_path_realpath = os.path.realpath(template_path)

            # Validate path
            template_path_normalized = os.path.normpath(template_path_realpath).lower()
            dir_path_normalized = os.path.normpath(dir_path_realpath).lower()
            if not (template_path_normalized.startswith(dir_path_normalized + os.sep) or
                    template_path_normalized == dir_path_normalized):
                raise JiuWenBaseException(
                    error_code=StatusCode.PROMPT_TEMPLATE_INCORRECT_ERROR.code,
                    message=f"Path traversal detected"
                )

            # Validate file type - must be a regular file, not directory, link, etc.
            if not os.path.isfile(template_path_realpath):
                raise JiuWenBaseException(
                    error_code=StatusCode.PROMPT_TEMPLATE_INCORRECT_ERROR.code,
                    message=f"Not a regular file"
                )

            # Validate file size
            try:
                file_size = os.path.getsize(template_path_realpath)
                if file_size > max_file_size:
                    raise JiuWenBaseException(
                        error_code=StatusCode.PROMPT_TEMPLATE_INCORRECT_ERROR.code,
                        message=f"File size {file_size} bytes exceeds limit {max_file_size} bytes"
                    )
            except OSError as e:
                raise JiuWenBaseException(
                    error_code=StatusCode.PROMPT_TEMPLATE_INCORRECT_ERROR.code,
                    message="Failed to get file size"
                ) from e

            name = os.path.splitext(template_file)[0]
            try:
                with open(template_path_realpath, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception as e:
                raise JiuWenBaseException(
                    error_code=StatusCode.PROMPT_TEMPLATE_NOT_FOUND_ERROR.code,
                    message=f"Failed to load template from file"
                ) from e
            templates.append(Template(name=name, content=content))
        return templates

    def format(self, keywords, template_name: str, filters: dict = None) -> Template:
        template = self.get(template_name, filters=filters)
        if not template:
            raise JiuWenBaseException(
                error_code=StatusCode.PROMPT_TEMPLATE_NOT_FOUND_ERROR.code,
                message=f"Template {template_name} not found error."
            )
        return template.format(keywords)

    def init_prompt_templates(self):
        self.__init_default_templates()
        self.__init_customer_templates()

    def __init_customer_templates(self):
        """init customer templates"""
        customer_templates_path = os.environ.get("PROMPT_DEFAULT_TEMPLATES_PATH", None)
        if not customer_templates_path:
            logger.warning("Customer templates path is not set")
            return
        self.__load_default_templates_dir(customer_templates_path)

    def __init_default_templates(self):
        """init default templates"""
        dir_path = os.path.join(os.path.dirname(__file__), "../resource")
        self.__load_default_templates_dir(dir_path)

    def __load_default_templates_dir(self, root_dir: str):
        """__load_default_templates_dir"""
        templates_path = os.path.join(root_dir, "templates")
        files = os.listdir(templates_path)
        for file_name in files:
            template_path = os.path.join(templates_path, file_name)
            if not os.path.isdir(template_path):
                continue
            templates = self.load_from_dir(template_path)
            for template in templates:
                template.filters = dict(model_name=file_name)
                if not self.register(template=template, force=True):
                    return ValueError("Invalid template to register")
        return None

    def register(self, template: Template, force: bool = False):
        """register template"""
        if not template.name:
            raise JiuWenBaseException(
                error_code=StatusCode.PROMPT_TEMPLATE_INCORRECT_ERROR.code,
                message="Template data is missing `name` field when registering."
            )
        template_copy = copy.deepcopy(template)
        if force:
            return self.template_store.update_template(template_copy)
        return self.template_store.register_template(template_copy)

    def get(self, name: str, filters: dict = None) -> Template:
        """query prompt template by template name"""
        all_filters = self.__filter_func(filters)
        result_template = self.template_store.search_template(name, filters=all_filters)
        return result_template

    def delete(self, name: str, filters: dict = None):
        """delete prompt template by template name"""
        all_filters = self.__filter_func(filters)
        return self.template_store.delete_template(name, filters=all_filters)

    def register_in_bulk(self, dir_path:str) -> bool:
        """template register with bulk template from specified dir_path"""
        if not os.path.isdir(dir_path):
            raise NotADirectoryError("dir path is not a folder")
        templates = self.load_from_dir(dir_path)
        results = []
        for template in templates:
            results.append(self.register(template=template, force=True))
        return all(results)

def default_template_filter(default_filters) -> dict:
    """default template filter"""
    return default_filters