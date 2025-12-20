import pytest

from openjiuwen.core.utils.llm.output_parser.json_output_parser import JsonOutputParser
from openjiuwen.core.utils.llm.messages import AIMessage
from openjiuwen.core.utils.llm.messages_chunk import AIMessageChunk

pytestmark = pytest.mark.asyncio

class TestJsonOutputParser:
    @pytest.fixture(autouse=True)
    def setUp(self):
        self.parser = JsonOutputParser()

    async def test_parse_valid_json_string(self):
        """测试解析有效的JSON字符串"""
        json_str = '{"name": "test", "value": 123}'
        result = await self.parser.parse(json_str)
        assert (result == {"name": "test", "value": 123})

    async def test_parse_valid_json_in_markdown(self):
        """测试解析Markdown代码块中的JSON"""
        markdown_json = "Here is some info:\n```json\n{\"item\": \"apple\", \"price\": 1.5}\n```\nThanks!"
        result = await self.parser.parse(markdown_json)
        assert (result == {"item": "apple", "price": 1.5})

    async def test_parse_valid_json_in_aimessage(self):
        """测试解析AIMessage对象中的JSON"""
        ai_message = AIMessage(content="```json\n{\"status\": \"success\", \"code\": 200}\n```")
        result = await self.parser.parse(ai_message)
        assert (result == {"status": "success", "code": 200})

    async def test_parse_invalid_json_string(self):
        """测试解析无效的JSON字符串"""
        invalid_json = '{"name": "test", "value": 123,'
        result = await self.parser.parse(invalid_json)
        assert (result is None)

    async def test_parse_non_json_string(self):
        """测试解析非JSON文本"""
        non_json = "This is just plain text."
        result = await self.parser.parse(non_json)
        assert (result is None)

    async def test_parse_empty_string(self):
        """测试解析空字符串"""
        result = await self.parser.parse("")
        assert (result is None)

    async def test_parse_none_input(self):
        """测试解析None输入"""
        result = await self.parser.parse(None)
        assert (result is None)

    async def test_parse_complex_json(self):
        """测试解析复杂的JSON结构"""
        complex_json = '''```json
{
    "users": [
        {"id": 1, "name": "Alice", "active": true},
        {"id": 2, "name": "Bob", "active": false}
    ],
    "metadata": {
        "total": 2,
        "page": 1
    }
}
```'''
        result = await self.parser.parse(complex_json)
        expected = {
            "users": [
                {"id": 1, "name": "Alice", "active": True},
                {"id": 2, "name": "Bob", "active": False}
            ],
            "metadata": {
                "total": 2,
                "page": 1
            }
        }
        assert (result == expected)

    async def test_stream_parse_valid_json_chunks(self):
        """测试流式解析有效的JSON块"""
        chunks = [
            "```json\n",
            "{\"data\": ",
            "\"value\"}\n",
            "```"
        ]
        expected_result = {"data": "value"}

        parsed_objects = []
        async for obj in self.parser.stream_parse(iter(chunks)):
            parsed_objects.append(obj)

        assert (len(parsed_objects) == 1)
        assert (parsed_objects[0] == expected_result)

    async def test_stream_parse_fragmented_json_chunks(self):
        """测试流式解析分片的JSON块"""
        chunks = [
            "Some text before.\n",
            "```json\n",
            "{\"id\": 1,",
            "\"name\": \"",
            "Fragmented Item\"",
            "}\n",
            "```\n",
            "More text after."
        ]
        expected_result = {"id": 1, "name": "Fragmented Item"}

        parsed_objects = []
        async for obj in self.parser.stream_parse(iter(chunks)):
            parsed_objects.append(obj)

        assert (len(parsed_objects) == 1)
        assert (parsed_objects[0] == expected_result)

    async def test_stream_parse_multiple_json_objects(self):
        """测试流式解析多个JSON对象"""
        chunks = [
            "```json\n{\"a\":1}\n```",
            "Some text.",
            "```json\n{\"b\":2}\n```"
        ]
        expected_results = [{"a": 1}, {"b": 2}]

        parsed_objects = []
        async for obj in self.parser.stream_parse(iter(chunks)):
            parsed_objects.append(obj)

        assert (len(parsed_objects) == 2)
        assert (parsed_objects == expected_results)

    async def test_stream_parse_invalid_json_chunks(self):
        """测试流式解析无效的JSON块"""
        chunks = [
            "```json\n",
            "{\"data\": ",
            "\"value\" \n",  # Missing closing brace
            "```"
        ]
        parsed_objects = []
        async for obj in self.parser.stream_parse(iter(chunks)):
            parsed_objects.append(obj)

        assert (len(parsed_objects) == 0)  # Should not yield anything if invalid

    async def test_stream_parse_mixed_content_and_json(self):
        """测试流式解析混合内容和JSON"""
        chunks = [
            "Hello world. ",
            "```json\n{\"key\":",
            "\"value\"}\n```",
            " End of message."
        ]
        expected_result = {"key": "value"}

        parsed_objects = []
        async for obj in self.parser.stream_parse(iter(chunks)):
            parsed_objects.append(obj)

        assert (len(parsed_objects) == 1)
        assert (parsed_objects[0] == expected_result)

    async def test_stream_parse_aimessage_chunks(self):
        """测试流式解析AIMessageChunk"""
        chunks = [
            AIMessageChunk(content="```json\n{\"status\":"),
            AIMessageChunk(content="\"ok\"}\n```")
        ]
        expected_result = {"status": "ok"}

        parsed_objects = []
        async for obj in self.parser.stream_parse(iter(chunks)):
            parsed_objects.append(obj)

        assert (len(parsed_objects) == 1)
        assert (parsed_objects[0] == expected_result)

    async def test_stream_parse_direct_json_without_markdown(self):
        """测试流式解析不带Markdown的直接JSON"""
        chunks = [
            "{\"direct\":",
            "\"json\"}"
        ]
        expected_result = {"direct": "json"}

        parsed_objects = []
        async for obj in self.parser.stream_parse(iter(chunks)):
            parsed_objects.append(obj)

        assert (len(parsed_objects) == 1)
        assert (parsed_objects[0] == expected_result)

    async def test_stream_parse_empty_chunks(self):
        """测试流式解析空块"""
        chunks = ["", None, ""]

        parsed_objects = []
        async for obj in self.parser.stream_parse(iter(chunks)):
            parsed_objects.append(obj)

        assert (len(parsed_objects) == 0)

    async def test_stream_parse_complex_json_chunks(self):
        """测试流式解析复杂JSON块"""
        chunks = [
            "```json\n{",
            "\"users\":[",
            "{\"id\":1,\"name\":\"Alice\"},",
            "{\"id\":2,\"name\":\"Bob\"}",
            "],\"total\":2",
            "}\n```"
        ]
        expected_result = {
            "users": [
                {"id": 1, "name": "Alice"},
                {"id": 2, "name": "Bob"}
            ],
            "total": 2
        }

        parsed_objects = []
        async for obj in self.parser.stream_parse(iter(chunks)):
            parsed_objects.append(obj)

        assert (len(parsed_objects) == 1)
        assert (parsed_objects[0], expected_result)