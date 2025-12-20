import os
import unittest

from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.utils.llm.messages import HumanMessage
from jiuwen.core.utils.prompt.index.template_store.template_store import Template
from jiuwen.core.utils.prompt.template.template_manager import TemplateManager


class TestTemplateManager(unittest.TestCase):
    def test_template_register_in_bulk(self):
        dir_path = os.path.join(os.path.dirname(__file__), "data/")
        TemplateManager().register_in_bulk(dir_path=dir_path)
        self.assertEqual(
            TemplateManager().get(name="summary").content,
            "你是一个文本总结高手，{{command}}，\n{{info}}"
        )

        self.assertEqual(
            TemplateManager().get(name="intent_recognition").content,
            "#角色：场景识别助手\n以下是用户的问题： {{query}}\n注意：只输出'是'或'否'，不要回复对于内容"
        )

    def test_template_consistency(self):
        template = Template(name="test_template_consistent", content=[{"role": "system", "content": "here is a test"}])
        TemplateManager().register(template=template, force=True)
        self.assertEqual(isinstance(template.content, list), True)
        content = TemplateManager().get(name="test_template_consistent").content
        self.assertEqual(len(content), 1)
        self.assertEqual(
            content[0].get("role"), "system"
        )
        self.assertEqual(content[0].get("content"), "here is a test")

        template = Template(name="test_template_consistent", content="here is a test")
        TemplateManager().register(template=template, force=True)
        self.assertEqual(
            TemplateManager().get(name="test_template_consistent").content,
            "here is a test"
        )

        TemplateManager().delete(name="test_template_consistent")
        try:
            TemplateManager().get(name="test_template_consistent")
        except JiuWenBaseException as e:
            self.assertEqual(e.error_code, StatusCode.PROMPT_TEMPLATE_NOT_FOUND_ERROR.code)

    def test_template_manager_format(self):
        template = Template(
            name="test_template_manager_format",
            content="`#system#`你是一个精通{{domain}}领域的问答助手`#user#`{{memory}}")
        TemplateManager().register(template=template, force=True)
        keyword = {"memory": [{"role": "user", "content": "你是谁"}], "domain": "数学"}
        template = TemplateManager().format(keyword, "test_template_manager_format")
        self.assertEqual(
            template.to_messages(),
            [HumanMessage(
                content="`#system#`你是一个精通数学领域的问答助手`#user#`[{'role': 'user', 'content': '你是谁'}]"
            )]
        )
