import pytest

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.utils.llm.messages import HumanMessage, AIMessage, ToolMessage
from openjiuwen.core.utils.tool.schema import ToolCall
from openjiuwen.core.utils.prompt.assemble.variables.variable import Variable
from openjiuwen.core.utils.prompt.template.template import Assembler, Template
from openjiuwen.core.utils.prompt.assemble.variables.textable import TextableVariable

class TestPromptAssemble:
    def assertEqual(self, left, right):
        assert left == right

    def test_textable_variable(self):
        pytest.raises(JiuWenBaseException, TextableVariable, text="{{}}")
        var1 = TextableVariable(text="{{x}}")
        self.assertEqual(["x"], var1.input_keys)
        self.assertEqual("default", var1.name)

        var2 = TextableVariable(text="{{x}}{{y}}")
        self.assertEqual(["x", "y"], var2.input_keys)
        self.assertEqual("12", var2.eval(x="1", y="2"))
        self.assertEqual("12", var2.value)

    def test_textable_variables(self):
        pytest.raises(JiuWenBaseException, TextableVariable, text="{{}}")
        var1 = TextableVariable(text="{{x}}")
        self.assertEqual(["x"], var1.input_keys)
        self.assertEqual("default", var1.name)

        var2 = TextableVariable(text="{{x}}{{y}}")
        self.assertEqual({"x", "y"}, set(var2.input_keys))
        self.assertEqual("12", var2.eval(x="1", y="2"))
        self.assertEqual("12", var2.value)

    def test_initialization(self):
        text = "You're an expert in the domain of {{domain}}"
        var = TextableVariable(text=text, name="role")
        self.assertEqual(text, var.text)
        self.assertEqual("role", var.name)
        self.assertEqual(["domain"], var.input_keys)
        self.assertEqual(["domain"], var.placeholders)

        text = "Hello, {{user.name}}"
        var = TextableVariable(text=text)
        self.assertEqual(["user"], var.input_keys)
        self.assertEqual(["user.name"], var.placeholders)

        text = "Hello, {{}}!"
        with pytest.raises(JiuWenBaseException):
            TextableVariable(text=text)

    def test_update(self):
        text = "You're an expert in the domain of {{domain}}."
        var = TextableVariable(text=text)
        var.update(domain="science")
        self.assertEqual("You're an expert in the domain of science.", var.value)

        text = "This value is {{value}}."
        var = TextableVariable(text=text)
        var.update(value=42)
        self.assertEqual("This value is 42.", var.value)

    def test_eval(self):
        text = "You're an expert in the domain of {{domain}}."
        var = TextableVariable(text=text)
        result = var.eval(domain="science")
        self.assertEqual("You're an expert in the domain of science.", result)

        text = "Hello, {{user.name}}!"
        var = TextableVariable(text=text)
        result = var.eval(user={"name": "Alice"})
        self.assertEqual("Hello, Alice!", result)

    def test_variable_initialization(self):
        var = Variable(name="test_var", input_keys=["key1", "key2"])
        self.assertEqual("test_var", var.name)
        self.assertEqual(["key1", "key2"], var.input_keys)
        self.assertEqual(var.value, "")

        var = Variable(name="test_var", input_keys=None)
        assert (var.input_keys is None)

    def test_prepare_inputs(self):
        var = Variable(name="test_var", input_keys=["key1", "key2"])

        input_kwargs = var._prepare_inputs(key1="value1", key2="value2")
        self.assertEqual({"key1": "value1", "key2": "value2"}, input_kwargs)

    def test_variable_eval(self):
        class MockVariable(Variable):
            def update(self, **kwargs):
                self.value = kwargs.get("key1", "") + kwargs.get("key2", "")

        var = MockVariable(name="test_var", input_keys=["key1", "key2"])

        result = var.eval(key1="value1", key2="value2")
        self.assertEqual("value1value2", result)

    def test_assemble(self):
        # string template content
        asm1 = Assembler(
            template_content="`#system#`{{role}}`#user#`{{memory}}",
            role=TextableVariable(text="你是一个精通{{domain}}领域的问答助手。")
        )
        self.assertEqual({"domain", "memory"}, set(asm1.input_keys))
        self.assertEqual(
            asm1.assemble(memory=[{"role": "user", "content": "我是谁"}], domain="科学"),
            "`#system#`你是一个精通科学领域的问答助手。`#user#`[{'role': 'user', 'content': '我是谁'}]"
        )

        # dict template content
        dict_template_content = [
            {"role": "system", "content": "{{role}}"},
            {"role": "user", "content": "{{user_inputs}}"},
            {"role": "assistant", "content": [], "function_call": {"name": "func1", "arguments": "x"}},
            {"role": "function", "content": "result of function call", "name": "func1"}
        ]
        asm2 = Assembler(
            template_content=dict_template_content,
            role=TextableVariable(text="你是一个精通{{domain}}领域的问答助手"),
            user_inputs=TextableVariable(text="问题： {{query}}\n答案：")
        )
        self.assertEqual({"domain", "query"}, set(asm2.input_keys))
        asm2_assembled_template = asm2.assemble(domain="科学", query="牛顿第三定律")

        self.assertEqual(len(dict_template_content), len(asm2_assembled_template))
        self.assertEqual({"role": "system", "content": "你是一个精通科学领域的问答助手"}, asm2_assembled_template[0])
        self.assertEqual(dict_template_content[1], asm2_assembled_template[1])
        self.assertEqual(dict_template_content[2], asm2_assembled_template[2])
        self.assertEqual(dict_template_content[3], asm2_assembled_template[3])

        # BaseMessage template content
        template = Template(content=[
            HumanMessage(content="Hi, {{user_inputs}}"),
            AIMessage(
                content="",
                tool_calls=[
                    ToolCall(type="test",
                             name="func",
                             arguments="x",
                             id="test")]),
            ToolMessage(tool_call_id="test", content=[])
        ])
        asm3 = Assembler(template_content=template.content,
                         user_inputs=TextableVariable(text="张三"))
        assembled_template = asm3.assemble()
        self.assertEqual([], asm3.input_keys)
        self.assertEqual(len(assembled_template), len(template.content))
        self.assertEqual(assembled_template[0], HumanMessage(content="Hi, 张三"))
        self.assertEqual(assembled_template[1], template.content[1])
        self.assertEqual(assembled_template[2], template.content[2])
        print(assembled_template)

    def test_template_format(self):
        template = Template(
            name="test",
            content="`#system#`你是一个精通{{domain}}领域的问答助手`#user#`{{memory}}")
        messages = template.format({"memory": [{"role": "user", "content": "你是谁"}], "domain": "数学"}).to_messages()
        self.assertEqual(
            messages,
            [
                HumanMessage(
                    content="`#system#`你是一个精通数学领域的问答助手`#user#`[{'role': 'user', 'content': '你是谁'}]")
            ]
        )

        #
        template = template.format({"memory": [{"role": "user", "content": "你是谁"}]})
        self.assertEqual(template.content,
                         "`#system#`你是一个精通{{domain}}领域的问答助手`#user#`[{'role': 'user', 'content': '你是谁'}]")

        template = template.format({"domain": "数学"})
        self.assertEqual(template.content,
                         "`#system#`你是一个精通数学领域的问答助手`#user#`[{'role': 'user', 'content': '你是谁'}]")
