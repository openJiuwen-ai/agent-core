# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.foundation.prompt.assemble.variables.dictable import DictableVariable


class TestDictableVariablePlaceholder:
    @staticmethod
    def assert_equal(left, right):
        assert left == right

    def test_empty_placeholder_kept_as_literal(self):
        data = {"content": "Hello, {{}}!"}
        var = DictableVariable(data=data)
        self.assert_equal([], var.input_keys)
        self.assert_equal({"content": "Hello, {{}}!"}, var.eval())

    def test_empty_placeholder_in_list_kept_as_literal(self):
        data = [{"text": "Hello, {{}}!"}]
        var = DictableVariable(data=data)
        self.assert_equal([], var.input_keys)
        self.assert_equal([{"text": "Hello, {{}}!"}], var.eval())

    def test_empty_placeholder_in_nested_dict_kept_as_literal(self):
        data = {"outer": {"inner": "Value: {{}}"}}
        var = DictableVariable(data=data)
        self.assert_equal([], var.input_keys)
        self.assert_equal({"outer": {"inner": "Value: {{}}"}}, var.eval())

    def test_plain_placeholder_still_interpolates(self):
        data = {"msg": "Hello, {{name}}!"}
        var = DictableVariable(data=data)
        self.assert_equal(["name"], var.input_keys)
        self.assert_equal({"msg": "Hello, alice!"}, var.eval(name="alice"))

    def test_single_braces_kept_as_literal(self):
        data = {"msg": '{"key": "value"}'}
        var = DictableVariable(data=data)
        self.assert_equal([], var.input_keys)
        self.assert_equal({"msg": '{"key": "value"}'}, var.eval())

    def test_orphan_suffix_alone_ignored(self):
        data = {"msg": "just }} here"}
        var = DictableVariable(data=data)
        self.assert_equal([], var.input_keys)
        self.assert_equal({"msg": "just }} here"}, var.eval())

    def test_whitespace_placeholder_replaced(self):
        data = {"msg": "Hello, {{ name }}!"}
        var = DictableVariable(data=data)
        self.assert_equal(["name"], var.input_keys)
        self.assert_equal({"msg": "Hello, alice!"}, var.eval(name="alice"))

    def test_value_containing_placeholder_not_rewritten(self):
        data = {"msg": "{{a}} {{b}}"}
        var = DictableVariable(data=data)
        self.assert_equal({"msg": "{{b}} 2"}, var.eval(a="{{b}}", b="2"))