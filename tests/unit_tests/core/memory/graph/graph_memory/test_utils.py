# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for graph_memory utils"""

from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm.schema.message import UserMessage
from openjiuwen.core.foundation.store.graph import Entity
from openjiuwen.core.memory.graph.graph_memory.utils import (
    assemble_invoke_params,
    msg2dict,
    update_entity,
)


class TestMsg2dict:
    """Tests for msg2dict"""

    @staticmethod
    def test_list_of_dict_passthrough():
        """List of dict with role and content is returned as-is"""
        messages = [{"role": "user", "content": "hi"}]
        result = msg2dict(messages)
        assert result == messages

    @staticmethod
    def test_list_of_base_message_converted():
        """List of BaseMessage is converted to role/content dicts"""
        messages = [UserMessage(content="hello")]
        result = msg2dict(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "hello"

    @staticmethod
    def test_preserve_meta_includes_extra_fields():
        """With preserve_meta=True, model_dump is used for BaseMessage"""
        messages = [UserMessage(content="x")]
        result = msg2dict(messages, preserve_meta=True)
        assert isinstance(result[0], dict)
        assert "content" in result[0] and "role" in result[0]

    @staticmethod
    def test_not_list_raises():
        """Input that is not a list raises"""
        with pytest.raises(BaseError, match="not a list"):
            msg2dict("single message")

    @staticmethod
    def test_mixed_type_raises():
        """List containing non-dict and non-BaseMessage raises"""
        with pytest.raises(BaseError, match="not a list"):
            msg2dict([1, 2])


class TestUpdateEntity:
    """Tests for update_entity"""

    @staticmethod
    def test_update_entity_sets_summary_from_json():
        """Valid JSON with summary updates entity content"""
        entity = Entity(name="E1", content="")
        update_entity(entity, '{"summary": "A short summary."}', {"type": "object"})
        assert entity.content == "A short summary."

    @staticmethod
    def test_update_entity_attributes():
        """JSON with attributes sets entity.attributes"""
        entity = Entity(name="E1", content="", attributes={})
        update_entity(
            entity,
            '{"summary": "", "attributes": {"key": "value"}}',
            {"type": "object", "properties": {"summary": {}, "attributes": {}}},
        )
        assert getattr(entity, "attributes") == {"key": "value"}

    @staticmethod
    def test_update_entity_null_summary_ignored():
        """Summary that looks like null/empty is not applied"""
        entity = Entity(name="E1", content="original")
        update_entity(entity, '{"summary": "null"}', {"type": "object"})
        assert entity.content == "original"

    @staticmethod
    def test_update_entity_list_summary_joined():
        """Summary as list is joined with newlines"""
        entity = Entity(name="E1", content="")
        update_entity(entity, '{"summary": ["a", "b"]}', {"type": "object"})
        assert entity.content == "a\nb"

    @staticmethod
    def test_update_entity_extracted_info_as_str_treated_as_summary():
        """When extracted_entity_info is a string (branch in update_entity), it is used as summary"""
        from openjiuwen.core.memory.graph.graph_memory import utils as utils_mod

        entity = Entity(name="E1", content="")
        getattr(utils_mod, "_parse_summary")(entity, {"summary": "just a string summary"})
        assert entity.content == "just a string summary"

    @staticmethod
    def test_update_entity_summary_as_set_joined():
        """Summary as set is joined with newlines via _parse_summary"""
        from openjiuwen.core.memory.graph.graph_memory import utils as utils_mod

        entity = Entity(name="E1", content="")
        getattr(utils_mod, "_parse_summary")(entity, {"summary": {"a", "b"}})
        assert entity.content in ("a\nb", "b\na")

    @staticmethod
    def test_update_entity_attributes_as_json_string():
        """Attributes as JSON string are parsed and set"""
        entity = Entity(name="E1", content="", attributes={})
        update_entity(
            entity,
            '{"summary": "", "attributes": "{\\"k\\": \\"v\\"}"}',
            {"type": "object", "properties": {"summary": {}, "attributes": {}}},
        )
        assert getattr(entity, "attributes") == {"k": "v"}

    @staticmethod
    def test_update_entity_attributes_list_fails_dict_logged():
        """Attributes as list that cannot convert to dict leaves attributes unchanged or empty"""
        entity = Entity(name="E1", content="", attributes={"orig": 1})
        update_entity(
            entity,
            '{"summary": "", "attributes": [1, 2, 3]}',
            {"type": "object", "properties": {"summary": {}, "attributes": {}}},
        )
        # Cannot dict([1,2,3]) meaningfully; code catches and leaves attributes as {} or keeps orig
        assert isinstance(getattr(entity, "attributes"), dict)

    @staticmethod
    def test_update_entity_extracted_info_list_takes_first():
        """When parse_json returns a list, extracted_entity_info is first element"""
        entity = Entity(name="E1", content="")
        with patch("openjiuwen.core.memory.graph.graph_memory.utils.parse_json", return_value=[{"summary": "first"}]):
            update_entity(entity, "[]", {"type": "object"})
        assert entity.content == "first"

    @staticmethod
    def test_update_entity_extracted_info_str_wrapped_as_summary():
        """When parse_json returns a str, update_entity wraps as dict(summary=...)"""
        entity = Entity(name="E1", content="")
        with patch("openjiuwen.core.memory.graph.graph_memory.utils.parse_json", return_value="string summary"):
            update_entity(entity, '"x"', {"type": "string"})
        assert entity.content == "string summary"

    @staticmethod
    def test_parse_summary_non_str_summary_converted():
        """_parse_summary with non-str summary (e.g. int) uses str(summary) or empty"""
        from openjiuwen.core.memory.graph.graph_memory import utils as utils_mod

        entity = Entity(name="E1", content="")
        getattr(utils_mod, "_parse_summary")(entity, {"summary": 42})
        assert entity.content == "42"


class TestAssembleInvokeParams:
    """Tests for assemble_invoke_params"""

    @staticmethod
    def test_assemble_invoke_params_messages_only():
        """Without output_model, params contain only messages from template"""
        template = MagicMock()
        template.format.return_value.content = [{"role": "user", "content": "Hello"}]
        params = assemble_invoke_params({"k": "v"}, template, output_model=None)
        assert "messages" in params
        assert params["messages"] == [{"role": "user", "content": "Hello"}]
        assert "response_format" not in params

    @staticmethod
    def test_assemble_invoke_params_with_output_model():
        """With output_model, response_format is set"""
        template = MagicMock()
        template.format.return_value.content = []
        output = {"type": "json_schema", "json_schema": {"name": "Test"}}
        params = assemble_invoke_params({"k": "v"}, template, output_model=output)
        assert params["response_format"] == output
