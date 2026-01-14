import pytest

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.foundation.llm import UserMessage, AssistantMessage, ToolMessage, ToolCall
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.foundation.prompt.assemble.assembler import PromptAssembler
from openjiuwen.core.foundation.prompt.assemble.variables.variable import Variable
from openjiuwen.core.foundation.prompt.assemble.variables.textable import TextableVariable


class TestPromptAssemble:
    @staticmethod
    def assert_equal(left, right):
        assert left == right

    def test_textable_variable(self):
        # Test empty placeholder throws exception
        pytest.raises(JiuWenBaseException, TextableVariable, text="{{}}")

        # Test single placeholder
        var1 = TextableVariable(text="{{x}}")
        self.assert_equal(["x"], var1.input_keys)
        self.assert_equal("default", var1.name)
        self.assert_equal("1", var1.eval(x="1"))

        # Test multiple placeholders
        var2 = TextableVariable(text="{{x}}{{y}}")
        self.assert_equal(["x", "y"], var2.input_keys)
        self.assert_equal("12", var2.eval(x="1", y="2"))
        self.assert_equal("12", var2.value)

    def test_textable_variables(self):
        # Duplicate test (can be retained or deleted) - retained and optimized here
        pytest.raises(JiuWenBaseException, TextableVariable, text="{{}}")
        var1 = TextableVariable(text="{{x}}")
        self.assert_equal(["x"], var1.input_keys)
        self.assert_equal("default", var1.name)

        var2 = TextableVariable(text="{{x}}{{y}}")
        self.assert_equal({"x", "y"}, set(var2.input_keys))
        self.assert_equal("12", var2.eval(x="1", y="2"))
        self.assert_equal("12", var2.value)

    def test_initialization(self):
        # Test standard placeholders ({{}} format)
        text = "You're an expert in the domain of {{domain}}"
        var = TextableVariable(text=text, name="role")
        self.assert_equal(text, var.text)
        self.assert_equal("role", var.name)
        self.assert_equal(["domain"], var.input_keys)
        self.assert_equal(["domain"], var.placeholders)

        # Test nested placeholders
        text = "Hello, {{user.name}}"  # Change to {{}} format, or specify prefix="{", suffix="}"
        var = TextableVariable(text=text)
        self.assert_equal(["user"], var.input_keys)
        self.assert_equal(["user.name"], var.placeholders)

        # Test empty placeholder (<<>>) - need to specify corresponding prefix and suffix
        text = "Hello, <<>>!"
        with pytest.raises(JiuWenBaseException):
            TextableVariable(text=text, prefix="<<", suffix=">>")  # Specify placeholder format

    def test_update(self):
        # Test normal placeholder replacement
        text = "You're an expert in the domain of {{domain}}."
        var = TextableVariable(text=text)
        var.update(domain="science")
        self.assert_equal("You're an expert in the domain of science.", var.value)

        # Test numeric type replacement
        text = "This value is {{value}}."
        var = TextableVariable(text=text)
        var.update(value=42)
        self.assert_equal("This value is 42.", var.value)

        # Test nested placeholder replacement
        text = "Hello, {{user.name}}!"
        var = TextableVariable(text=text)
        var.update(user={"name": "Alice"})
        self.assert_equal("Hello, Alice!", var.value)

    def test_eval(self):
        # Test normal placeholder eval
        text = "You're an expert in the domain of {{domain}}."
        var = TextableVariable(text=text)
        result = var.eval(domain="science")
        self.assert_equal("You're an expert in the domain of science.", result)

        # Test nested placeholder eval
        text = "Hello, {{user.name}}!"
        var = TextableVariable(text=text)
        result = var.eval(user={"name": "Alice"})
        self.assert_equal("Hello, Alice!", result)

        # Test multiple placeholders eval
        text = "{{greeting}}, {{user.name}}! You have {{count}} messages."
        var = TextableVariable(text=text)
        result = var.eval(greeting="Hi", user={"name": "Bob"}, count=3)
        self.assert_equal("Hi, Bob! You have 3 messages.", result)

    def test_variable_initialization(self):
        # Test Variable class initialization
        var = Variable(name="test_var", input_keys=["key1", "key2"])
        self.assert_equal("test_var", var.name)
        self.assert_equal(["key1", "key2"], var.input_keys)
        self.assert_equal("", var.value)  # Initial value is empty string

        # Test case where input_keys is None
        var = Variable(name="test_var", input_keys=None)
        assert var.input_keys is None

    def test_prepare_inputs(self):
        # Test Variable's _prepare_inputs method
        var = Variable(name="test_var", input_keys=["key1", "key2"])
        input_kwargs = var.prepare_inputs(key1="value1", key2="value2")
        self.assert_equal({"key1": "value1", "key2": "value2"}, input_kwargs)

        # Test redundant parameters are filtered out
        input_kwargs = var.prepare_inputs(key1="v1", key2="v2", key3="v3")
        self.assert_equal({"key1": "v1", "key2": "v2"}, input_kwargs)

    def test_variable_eval(self):
        # Test eval method of custom Variable subclass
        class MockVariable(Variable):
            def update(self, **kwargs):
                self.value = kwargs.get("key1", "") + kwargs.get("key2", "")

        var = MockVariable(name="test_var", input_keys=["key1", "key2"])
        result = var.eval(key1="value1", key2="value2")
        self.assert_equal("value1value2", result)

    def test_assemble(self):
        # 1. Test string template (using non-default placeholder ${}$)
        asm1 = PromptAssembler(
            prompt_template_content="`#system#`${role}$`#user#`${memory}$",
            placeholder_prefix="${",  # Specify placeholder prefix
            placeholder_suffix="}$",  # Specify placeholder suffix
            role=TextableVariable(
                text="你是一个精通${domain}$领域的问答助手。",
                prefix="${",
                suffix="}$"
            )
        )
        self.assert_equal({"domain", "memory"}, set(asm1.input_keys))
        assembled_result = asm1.prompt_assemble(
            memory=[{"role": "user", "content": "我是谁"}],
            domain="科学"
        )
        self.assert_equal(
            "`#system#`你是一个精通科学领域的问答助手。`#user#`[{'role': 'user', 'content': '我是谁'}]",
            assembled_result
        )

        asm2 = PromptAssembler(
            prompt_template_content="`#system#`{role}`#user#`{memory}",
            placeholder_prefix="{",  # Specify placeholder prefix
            placeholder_suffix="}",  # Specify placeholder suffix
            role=TextableVariable(
                text="你是一个精通{domain}领域的问答助手。",
                prefix="{",
                suffix="}"
            )
        )
        self.assert_equal({"domain", "memory"}, set(asm2.input_keys))
        assembled_result = asm2.prompt_assemble(
            memory=[{"role": "user", "content": "我是谁"}],
            domain="天文"
        )
        self.assert_equal(
            "`#system#`你是一个精通天文领域的问答助手。`#user#`[{'role': 'user', 'content': '我是谁'}]",
            assembled_result
        )

        # 3. Test BaseMessage type template content
        template = PromptTemplate(content=[
            UserMessage(content="Hi, {{user_inputs}}"),
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(type="test", name="func", arguments="x", id="test")
                ]
            ),
            ToolMessage(tool_call_id="test", content=[])
        ])
        asm3 = PromptAssembler(
            prompt_template_content=template.content,
            user_inputs=TextableVariable(text="张三")  # No placeholders, input_keys is empty
        )
        self.assert_equal([], asm3.input_keys)  # No variables need to be filled
        assembled_template = asm3.prompt_assemble()

        self.assert_equal(len(assembled_template), len(template.content))
        self.assert_equal(UserMessage(content="Hi, 张三"), assembled_template[0])
        self.assert_equal(template.content[1], assembled_template[1])
        self.assert_equal(template.content[2], assembled_template[2])

    def test_template_format(self):
        # 1. Test string template format (complete variable filling)
        template = PromptTemplate(
            content="`#system#`你是一个精通{{domain}}领域的问答助手`#user#`{{memory}}"
        )
        formatted_template = template.format({"memory": [{"role": "user", "content": "你是谁"}], "domain": "数学"})
        messages = formatted_template.to_messages()

        self.assert_equal(
            messages,
            [UserMessage(
                content="`#system#`你是一个精通数学领域的问答助手`#user#`[{'role': 'user', 'content': '你是谁'}]")]
        )

        # 2. Test partial variable filling (only pass memory, retain domain placeholder)
        template2 = PromptTemplate(
            content="`#system#`你是一个精通{{domain}}领域的问答助手`#user#`{{memory}}"
        )
        template2 = template2.format({"memory": [{"role": "user", "content": "你是谁"}]})
        self.assert_equal(
            "`#system#`你是一个精通{{domain}}领域的问答助手`#user#`[{'role': 'user', 'content': '你是谁'}]",
            template2.content
        )

        # 3. Test filling remaining variables (supplement domain to complete all replacements)
        template2 = template2.format({"domain": "数学"})
        self.assert_equal(
            "`#system#`你是一个精通数学领域的问答助手`#user#`[{'role': 'user', 'content': '你是谁'}]",
            template2.content
        )

        # 4. Test template with BaseMessage list format (multiple messages + multiple variables)
        template3 = PromptTemplate(
            content=[
                UserMessage(content="Hello {{name}}!"),
                AssistantMessage(content="I'm your assistant for {{domain}}.")
            ]
        )
        formatted3 = template3.format({"name": "Alice", "domain": "AI"})
        messages3 = formatted3.to_messages()

        self.assert_equal(2, len(messages3))
        self.assert_equal(UserMessage(content="Hello Alice!"), messages3[0])
        self.assert_equal(AssistantMessage(content="I'm your assistant for AI."), messages3[1])

        # Test keywords as None/empty dictionary (return deep copy of original template)
        template4 = PromptTemplate(card=None, content="Hello {{name}}")
        # Keywords is None
        template4_copy1 = template4.format()
        self.assert_equal(template4.content, template4_copy1.content)

        # Keywords is empty dictionary
        template4_copy2 = template4.format({})
        self.assert_equal(template4.content, template4_copy2.content)

        # Test passing redundant keywords
        template5 = PromptTemplate(card=None, content="Hi {{name}}")
        formatted5 = template5.format({"name": "Bob", "age": 20})
        self.assert_equal("Hi Bob", formatted5.content)