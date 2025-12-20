#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Interface for In memory index
"""
import threading

from jiuwen.core.utils.prompt.common.document import Document


class TemplateId:
    """
    TemplateId class
    """
    def __init__(self, name: str, filter_data: dict = None):
        self.name = name
        self.filter_data = filter_data


class InMemory:
    """
    InMemoryIndex class
    """
    def __init__(self):
        self.lock = threading.Lock()
        self.cache = {}

    def add_document(self, record: Document, template_id: TemplateId = None):
        """add_document"""
        try:
            self.lock.acquire(True)
            self.cache[template_id.name] = record.metadata
            return True
        finally:
            self.lock.release()

    def get_documents(self, template_id: TemplateId):
        """get_document"""
        try:
            self.lock.acquire(True)
            return self.cache.get(template_id.name, None)
        finally:
            self.lock.release()

    def update_document(self, template_id: TemplateId, data: Document):
        """update_document"""
        try:
            self.lock.acquire(True)
            if template_id.name in self.cache:
                self.cache.pop(template_id.name)
                self.cache[template_id.name] = data.metadata
            else:
                self.cache[template_id.name] = data.metadata
            return True
        finally:
            self.lock.release()

    def delete_document(self, template_id: TemplateId):
        """delete_document"""
        try:
            self.lock.acquire(True)
            if template_id.name in self.cache:
                self.cache.pop(template_id.name)
                return True
            return False
        finally:
            self.lock.release()