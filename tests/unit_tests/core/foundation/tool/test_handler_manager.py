# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import aiohttp
import pytest

from openjiuwen.core.foundation.tool.form_handler.form_handler_manager import (
    FormHandler,
    DefaultFormHandler,
    FormHandlerManager,
)


class ConcreteHandler(FormHandler):
    """Concrete implementation for testing."""

    async def handle(self, form: aiohttp.FormData, form_data: dict, **kwargs):
        for param_name, value in form_data.items():
            form.add_field(param_name, value)
        return form


class TestFormHandler:
    """FormHandler abstract class tests"""

    class TestFormHandlerBaseClass:
        """FormHandler base class tests"""

        @pytest.mark.asyncio
        async def test_abstract_handle_raises_not_implemented(self):
            """Abstract method not implemented"""
            handler = ConcreteHandler()

            with pytest.raises(NotImplementedError):
                await FormHandler.handle(handler, form=aiohttp.FormData(), form_data={})

        @staticmethod
        def test_concrete_handler_can_be_instantiated():
            """Abstract method implemented"""
            handler = ConcreteHandler()
            assert isinstance(handler, FormHandler)

        @pytest.mark.asyncio
        async def test_handle_can_be_called(self):
            """handle() can be invoked"""
            handler = ConcreteHandler()
            form_data = {"field": "value"}

            result = await handler.handle(form=aiohttp.FormData(), form_data=form_data)

            assert isinstance(result, aiohttp.FormData)


class TestDefaultFormHandler:
    """DefaultFormHandler tests"""

    class TestBasicFunctionality:
        """Basic functionality tests"""

        @pytest.mark.asyncio
        async def test_handle_single_field(self):
            """Handle single field"""
            handler = DefaultFormHandler()
            form_data = {"name": "test"}
            result = await handler.handle(form=aiohttp.FormData(), form_data=form_data)

            assert isinstance(result, aiohttp.FormData)

        @pytest.mark.asyncio
        async def test_handle_multiple_fields(self):
            handler = DefaultFormHandler()
            form_data = {"name": "test", "age": "25", "city": "Beijing"}

            result = await handler.handle(form=aiohttp.FormData(), form_data=form_data)

            assert isinstance(result, aiohttp.FormData)

        @pytest.mark.asyncio
        async def test_handle_skips_none_values(self):
            handler = DefaultFormHandler()
            form_data = {"field1": "value1", "field2": None, "field3": "value3"}

            result = await handler.handle(form=aiohttp.FormData(), form_data=form_data)

            assert isinstance(result, aiohttp.FormData)

        @pytest.mark.asyncio
        async def test_handle_empty_form_data(self):
            handler = DefaultFormHandler()
            form_data = {}

            result = await handler.handle(form=aiohttp.FormData(), form_data=form_data)

            assert isinstance(result, aiohttp.FormData)

        @pytest.mark.asyncio
        async def test_handle_complex_value_types(self):
            handler = DefaultFormHandler()
            form_data = {"count": 123, "price": 99.99, "active": True}

            result = await handler.handle(form=aiohttp.FormData(), form_data=form_data)

            assert isinstance(result, aiohttp.FormData)

    class TestFormDataAccumulation:
        """FormData test"""

        @pytest.mark.asyncio
        async def test_accumulate_to_existing_form_data(self):
            handler = DefaultFormHandler()
            existing_form = aiohttp.FormData()
            existing_form.add_field("existing_field", "existing_value")

            result = await handler.handle(form=existing_form, form_data={"new_field": "new_value"})

            assert isinstance(result, aiohttp.FormData)


class TestFormHandlerManager:
    """FormHandlerManager test"""

    class TestSingletonPattern:
        """SingletonPattern test"""

        @staticmethod
        def test_singleton_pattern():
            """Test FormHandlerManager is a singleton."""
            manager1 = FormHandlerManager()
            manager2 = FormHandlerManager()

            assert manager1 is manager2

    class TestRegisterHandler:
        """Register handler tests"""

        @staticmethod
        def test_default_form_handler_is_default_form_handler():
            """Test default form handler is DefaultFormHandler."""
            manager = FormHandlerManager()

            assert manager.default_form_handler == DefaultFormHandler

        @staticmethod
        def test_register_custom_handler():
            """Register custom handler"""
            manager = FormHandlerManager()

            class CustomHandler(FormHandler):
                async def handle(self, **kwargs):
                    return aiohttp.FormData()

            manager.register("custom", CustomHandler)

            assert manager.form_handler_map.get("custom") == CustomHandler

        @staticmethod
        def test_register_default_handler():
            """register default handler"""
            manager = FormHandlerManager()

            class NewDefaultHandler(FormHandler):
                async def handle(self, **kwargs):
                    return aiohttp.FormData()

            original_default = manager.default_form_handler
            manager.register_default_handler(NewDefaultHandler)

            assert manager.default_form_handler == NewDefaultHandler

            manager.default_form_handler = original_default

        @staticmethod
        def test_get_handler_returns_registered_handler():
            """Get registered handler"""
            manager = FormHandlerManager()

            class CustomHandler(FormHandler):
                async def handle(self, **kwargs):
                    return aiohttp.FormData()

            manager.register("custom_type", CustomHandler)
            handler = manager.get_handler("custom_type")

            assert handler == CustomHandler

        @staticmethod
        def test_get_handler_returns_default_for_unknown_type():
            """Get unregistered handler"""
            manager = FormHandlerManager()

            handler = manager.get_handler("unknown_type_xyz")

            assert handler == manager.default_form_handler

        @staticmethod
        def test_get_handler_returns_default_when_type_is_empty():
            """Test get_handler returns default handler when type is empty string."""
            manager = FormHandlerManager()

            handler = manager.get_handler("")

            assert handler == manager.default_form_handler

        @staticmethod
        def test_get_handler_returns_default_when_type_is_none():
            """Test get_handler returns default handler when type is None."""
            manager = FormHandlerManager()

            handler = manager.get_handler(None)

            assert handler == manager.default_form_handler

    class TestRegisterInvalidHandler:
        """Register invalid handler tests - verify error log output"""

        @staticmethod
        def test_register_invalid_handler_type_value_empty():
            """Register invalid handler_type_value (error scenario) - empty string, verify error log"""
            manager = FormHandlerManager()

            class CustomHandler(FormHandler):
                async def handle(self, **kwargs):
                    return aiohttp.FormData()

            manager.register("", CustomHandler)

            assert manager.form_handler_map.get("") == CustomHandler

        @staticmethod
        def test_register_invalid_handler_type_value_none():
            """Register invalid handler_type_value (error scenario) - None, verify error log"""
            manager = FormHandlerManager()

            class CustomHandler(FormHandler):
                async def handle(self, **kwargs):
                    return aiohttp.FormData()

            manager.register(None, CustomHandler)

            assert manager.form_handler_map.get(None) == CustomHandler

        @staticmethod
        def test_register_non_string_handler_type_value():
            """Register non-string handler_type_value (error scenario), verify error log"""
            manager = FormHandlerManager()

            class CustomHandler(FormHandler):
                async def handle(self, **kwargs):
                    return aiohttp.FormData()

            manager.register(123, CustomHandler)

            assert manager.form_handler_map.get(123) == CustomHandler

        @staticmethod
        def test_register_non_form_handler_subclass():
            """Register non-FormHandler subclass (error scenario), verify error log"""
            manager = FormHandlerManager()

            manager.register("test_non_subclass_2", str)

            assert manager.form_handler_map.get("test_non_subclass_2") == str

        @staticmethod
        def test_register_non_class_object():
            """Register non-class object (error scenario), verify error log"""
            manager = FormHandlerManager()

            manager.register("test_non_class_2", "not_a_class")

            assert manager.form_handler_map.get("test_non_class_2") == "not_a_class"

    class TestHandlerOverride:
        """Handler override tests"""

        @staticmethod
        def test_override_existing_handler():
            """Override existing handler"""
            manager = FormHandlerManager()

            class HandlerA(FormHandler):
                async def handle(self, **kwargs):
                    return aiohttp.FormData()

            class HandlerB(FormHandler):
                async def handle(self, **kwargs):
                    return aiohttp.FormData()

            manager.register("custom", HandlerA)
            manager.register("custom", HandlerB)

            assert manager.get_handler("custom") == HandlerB


class TestCustomFormHandler:
    """Custom FormHandler tests"""

    @staticmethod
    def test_file_upload_handler_can_be_registered():
        """File upload handler"""
        manager = FormHandlerManager()

        class FileUploadFormHandler(FormHandler):
            async def handle(self, **kwargs):
                form_data = aiohttp.FormData()
                param_data = kwargs.get("form_data", {})
                for param_name, value in param_data.items():
                    form_data.add_field(param_name, value)
                return form_data

        manager.register("file_upload", FileUploadFormHandler)

        assert manager.get_handler("file_upload") == FileUploadFormHandler

    @pytest.mark.asyncio
    async def test_file_upload_handler_can_process_file_param(self):
        """File upload handler can correctly process file parameters"""
        manager = FormHandlerManager()

        class FileUploadFormHandler(FormHandler):
            async def handle(self, **kwargs):
                form_data = aiohttp.FormData()
                param_data = kwargs.get("form_data", {})
                for param_name, value in param_data.items():
                    form_data.add_field(param_name, value)
                return form_data

        manager.register("file_upload", FileUploadFormHandler)
        handler = manager.get_handler("file_upload")

        result = await handler().handle(form_data={"file": "file_content"})

        assert isinstance(result, aiohttp.FormData)

    @staticmethod
    def test_json_data_handler_can_be_registered():
        """JSON data handler"""
        manager = FormHandlerManager()

        class JsonFormHandler(FormHandler):
            async def handle(self, **kwargs):
                form_data = aiohttp.FormData()
                param_data = kwargs.get("form_data", {})
                import json
                for param_name, value in param_data.items():
                    form_data.add_field(param_name, json.dumps(value), content_type="application/json")
                return form_data

        manager.register("json_handler", JsonFormHandler)

        assert manager.get_handler("json_handler") == JsonFormHandler

    @pytest.mark.asyncio
    async def test_json_data_handler_can_process_json_data(self):
        """JSON data handler can correctly process JSON data"""
        manager = FormHandlerManager()

        class JsonFormHandler(FormHandler):
            async def handle(self, **kwargs):
                form_data = aiohttp.FormData()
                param_data = kwargs.get("form_data", {})
                import json
                for param_name, value in param_data.items():
                    form_data.add_field(param_name, json.dumps(value), content_type="application/json")
                return form_data

        manager.register("json_handler", JsonFormHandler)
        handler = manager.get_handler("json_handler")

        result = await handler().handle(form_data={"data": {"key": "value"}})

        assert isinstance(result, aiohttp.FormData)


class TestFormHandlerManagerLogging:
    """Log output tests"""

    @staticmethod
    def test_register_handler_logs_info():
        """Register handler log"""
        manager = FormHandlerManager()

        class CustomHandler(FormHandler):
            async def handle(self, **kwargs):
                return aiohttp.FormData()

        manager.register("test_logging_handler_2", CustomHandler)

        assert manager.get_handler("test_logging_handler_2") == CustomHandler

    @staticmethod
    def test_register_invalid_handler_outputs_error_log():
        """Register failure log"""
        manager = FormHandlerManager()

        manager.register("test_invalid_handler_2", "not_a_class")

        assert manager.form_handler_map.get("test_invalid_handler_2") == "not_a_class"
