# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from abc import ABC, abstractmethod
from typing import Dict, Type

import aiohttp
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.utils.singleton import Singleton


class FormHandler(ABC):
    @abstractmethod
    async def handle(
            self,
            form: aiohttp.FormData,
            form_data: Dict[str, any],
            **kwargs
    ) -> aiohttp.FormData:
        """
        Abstract interface for form data processing.

        Args:
            form: Form to be added,
            form_data: Data to be added to the form
            **kwargs:other params
            
        Returns:
            aiohttp.FormData object
        """
        raise NotImplementedError()


class DefaultFormHandler(FormHandler):
    """Generic form handler for simple key-value form data"""

    async def handle(
            self,
            form: aiohttp.FormData,
            form_data: Dict[str, any],
            **kwargs
    ) -> aiohttp.FormData:
        """
        handler interface for form data processing.

        Args:
            form: Form to be added,
            form_data: Data to be added to the form
            **kwargs:other params
        Returns:
            aiohttp.FormData object
        """

        for param_name, param_value in form_data.items():
            if param_value is None:
                continue
            form.add_field(name=param_name, value=str(param_value))

        return form


class FormHandlerManager(metaclass=Singleton):

    def __init__(self):
        self.form_handler_map: Dict[str, FormHandler] = dict()
        self.default_form_handler = DefaultFormHandler

    def register(self, handler_type_value: str, handler_class: Type[FormHandler]):
        """Public interface for registering handler logic for different form types."""
        if not isinstance(handler_type_value, str) or not handler_type_value:
            logger.error(f"register handler failed, {handler_type_value} is invalid")
        if not isinstance(handler_class, type) or not issubclass(handler_class, FormHandler):
            logger.error(f"register handler failed, {handler_class} is not a subclass of FormHandler")
        self.form_handler_map.update({handler_type_value: handler_class})
        logger.info(
            f"register handler success, handler_type_value: {handler_type_value}, handler_class: {handler_class}"
        )

    def register_default_handler(self, handler_class: Type[FormHandler]):
        """Public interface for registering default form handler logic."""
        if not isinstance(handler_class, type) or not issubclass(handler_class, FormHandler):
            logger.error(f"register default handler failed, {handler_class} is not a subclass of FormHandler")
        self.default_form_handler = handler_class
        logger.info(
            f"register default handler success, handler_class: {handler_class}"
        )

    def get_handler(self, handler_type: str) -> FormHandler:
        """Reference interface for retrieving registered handler logic."""
        return self.form_handler_map.get(handler_type, self.default_form_handler)
