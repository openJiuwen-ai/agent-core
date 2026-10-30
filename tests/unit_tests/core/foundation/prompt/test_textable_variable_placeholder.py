# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.foundation.prompt.assemble.variables.textable import TextableVariable


class TestTextableVariablePlaceholder:
    @staticmethod
    def assert_equal(left, right):
        assert left == right

    def test_empty_placeholder_kept_as_literal(self):
        var = TextableVariable(text="{{}}")
        self.assert_equal([], var.input_keys)
        self.assert_equal("{{}}", var.eval())

    def test_plain_placeholder_still_interpolates(self):
        var = TextableVariable(text="{{name}}")
        self.assert_equal(["name"], var.input_keys)
        self.assert_equal("alice", var.eval(name="alice"))

    def test_single_braces_kept_as_literal(self):
        var = TextableVariable(text='{"key": "value"}')
        self.assert_equal([], var.input_keys)
        self.assert_equal('{"key": "value"}', var.eval())

    def test_orphan_suffix_alone_ignored(self):
        var = TextableVariable(text="just }} here")
        self.assert_equal([], var.input_keys)
        self.assert_equal("just }} here", var.eval())

    def test_whitespace_placeholder_replaced(self):
        var = TextableVariable(text="{{ x }}")
        self.assert_equal(["x"], var.input_keys)
        self.assert_equal("1", var.eval(x="1"))

    def test_value_containing_placeholder_not_rewritten(self):
        var = TextableVariable(text="{{a}} {{b}}")
        self.assert_equal("{{b}} 2", var.eval(a="{{b}}", b="2"))