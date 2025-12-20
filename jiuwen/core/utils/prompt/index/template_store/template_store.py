#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Interface for template index
"""
from abc import ABC, abstractmethod

from jiuwen.core.utils.prompt.template.template import Template


class TemplateStore(ABC):
    """Template operation"""

    @abstractmethod
    def delete_template(self, name: str, filters: dict) -> bool:
        """delete template by name"""

    @abstractmethod
    def register_template(self, template: Template) -> bool:
        """register template"""

    @abstractmethod
    def search_template(self, name: str, filters: dict) -> Template:
        """search template by name"""

    @abstractmethod
    def update_template(self, template: Template, **kwargs) -> bool:
        """update template by name"""
