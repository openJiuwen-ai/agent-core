#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""In memory template store"""
from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.utils.prompt.common.document import Document
from jiuwen.core.utils.prompt.index.template_store.in_memory import InMemory, TemplateId
from jiuwen.core.utils.prompt.index.template_store.template_store import TemplateStore, Template


class InMemoryTemplateStore(TemplateStore):
    """In memory template store"""
    def __init__(self):
        self.index = InMemory()

    @staticmethod
    def __get_memory_name(name: str, filters: dict):
        return name + "".join("###" + filters[item] for item in filters if filters.get(item)) if filters else name

    def register_template(self, template: Template):
        """register a template"""
        in_memory_name = self.__get_memory_name(name=template.name, filters=template.filters)
        template_id = TemplateId(in_memory_name, filter_data=template.filters)
        if self.index.get_documents(template_id):
            raise JiuWenBaseException(
                error_code=StatusCode.PROMPT_TEMPLATE_DUPLICATED_ERROR.code,
                message=f"Template is duplicated to register"
            )
        return self.index.add_document(
            record=Document(page_content='', metadata=template.model_dump()),
            template_id=template_id
        )

    def delete_template(self, name: str, filters: dict):
        """delete a template"""
        template_id = TemplateId(name, filter_data=filters)
        if not self.index.get_documents(template_id):
            raise JiuWenBaseException(
                error_code=StatusCode.PROMPT_TEMPLATE_NOT_FOUND_ERROR.code,
                message=f"Template: {name} not found to delete"
            )
        return self.index.delete_document(template_id)

    def update_template(self, template: Template, **kwargs):
        """update a template"""
        template_id = TemplateId(
            name=self.__get_memory_name(template.name, filters=template.filters),
            filter_data=template.filters
        )
        return self.index.update_document(
            template_id,
            data=Document(page_content='', metadata=template.model_dump())
        )

    def search_template(self, name: str, filters: dict) -> Template:
        """search a template"""
        result = self.__get_document(name, filters)
        if filters and not result:
            result = self.__get_document(name, filters={"model_name": "default"})
        if not result:
            raise JiuWenBaseException(
                error_code=StatusCode.PROMPT_TEMPLATE_NOT_FOUND_ERROR.code,
                message=StatusCode.PROMPT_TEMPLATE_NOT_FOUND_ERROR.errmsg
            )
        return Template(**result)

    def __get_document(self, name: str, filters: dict):
        template_id = TemplateId(
            name=self.__get_memory_name(name, filters),
            filter_data=filters
        )
        return self.index.get_documents(template_id)