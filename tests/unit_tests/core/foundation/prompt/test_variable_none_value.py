# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest import mock

from openjiuwen.core.foundation.prompt.assemble.variables.textable import TextableVariable
from openjiuwen.core.foundation.prompt.assemble.variables.dictable import DictableVariable
import openjiuwen.core.foundation.prompt.assemble.variables.textable as textable_module
import openjiuwen.core.foundation.prompt.assemble.variables.dictable as dictable_module


class _User:
    def __init__(self):
        self.name = None


class TestVariableNoneValue:
    @staticmethod
    def assert_equal(left, right):
        assert left == right

    def test_textable_none_value_keeps_placeholder(self):
        var = TextableVariable(text="Hello, {{name}}!")
        result = var.eval(name=None)
        self.assert_equal("Hello, {{name}}!", result)

    def test_textable_none_in_nested_path_keeps_placeholder(self):
        var = TextableVariable(text="Hello, {{user.name}}!")
        result = var.eval(user={"age": 30})
        self.assert_equal("Hello, {{user.name}}!", result)

    def test_textable_none_in_object_attribute_keeps_placeholder(self):
        var = TextableVariable(text="Hello, {{user.name}}!")
        result = var.eval(user=_User())
        self.assert_equal("Hello, {{user.name}}!", result)

    def test_textable_absent_key_keeps_placeholder(self):
        var = TextableVariable(text="Hello, {{name}}!")
        result = var.eval()
        self.assert_equal("Hello, {{name}}!", result)

    def test_textable_falsy_non_none_values_still_convert(self):
        var = TextableVariable(text="Count: {{num}}, Flag: {{flag}}, Empty: {{text}}")
        result = var.eval(num=0, flag=False, text="")
        self.assert_equal("Count: 0, Flag: False, Empty: ", result)

    def test_textable_none_emits_warning(self):
        var = TextableVariable(text="Hello, {{name}}!")
        with mock.patch.object(textable_module.prompt_logger, "warning") as warn:
            var.eval(name=None)
            warn.assert_called_once()
            _, kwargs = warn.call_args
            self.assert_equal("default", kwargs["metadata"]["name"])
            self.assert_equal("name", kwargs["metadata"]["placeholder"])

    def test_textable_mixed_empty_and_valid_placeholder(self):
        var = TextableVariable(text="Hi, {{}} and {{name}}!")
        result = var.eval(name="World")
        self.assert_equal("Hi, {{}} and World!", result)

    def test_dictable_none_value_keeps_placeholder(self):
        data = {"msg": "Hello, {{name}}!"}
        var = DictableVariable(data=data)
        result = var.update(name=None)
        self.assert_equal({"msg": "Hello, {{name}}!"}, result)

    def test_dictable_none_in_nested_dict_keeps_placeholder(self):
        data = {"msg": "Hello, {{user.name}}!"}
        var = DictableVariable(data=data)
        result = var.update(user={"age": 30})
        self.assert_equal({"msg": "Hello, {{user.name}}!"}, result)

    def test_dictable_none_in_object_attribute_keeps_placeholder(self):
        data = {"msg": "Hello, {{user.name}}!"}
        var = DictableVariable(data=data)
        result = var.update(user=_User())
        self.assert_equal({"msg": "Hello, {{user.name}}!"}, result)

    def test_dictable_absent_key_keeps_placeholder(self):
        data = {"msg": "Hello, {{name}}!"}
        var = DictableVariable(data=data)
        result = var.update()
        self.assert_equal({"msg": "Hello, {{name}}!"}, result)

    def test_dictable_falsy_non_none_values_still_convert(self):
        data = {"count": "Total: {{num}}", "flag": "Active: {{flag}}"}
        var = DictableVariable(data=data)
        result = var.update(num=0, flag=False)
        self.assert_equal({"count": "Total: 0", "flag": "Active: False"}, result)

    def test_dictable_none_emits_warning(self):
        data = {"msg": "Hello, {{name}}!"}
        var = DictableVariable(data=data)
        with mock.patch.object(dictable_module.prompt_logger, "warning") as warn:
            var.update(name=None)
            warn.assert_called_once()
            _, kwargs = warn.call_args
            self.assert_equal("default", kwargs["metadata"]["name"])
            self.assert_equal("name", kwargs["metadata"]["placeholder"])

    def test_dictable_mixed_empty_and_valid_placeholder(self):
        data = {"msg": "Hi, {{}} and {{name}}!"}
        var = DictableVariable(data=data)
        result = var.update(name="World")
        self.assert_equal({"msg": "Hi, {{}} and World!"}, result)
