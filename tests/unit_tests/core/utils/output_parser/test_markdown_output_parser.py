import pytest

from openjiuwen.core.utils.llm.output_parser.markdown_output_parser import (
    MarkdownOutputParser, MarkdownContent, MarkdownElementType
)
from openjiuwen.core.utils.llm.messages import AIMessage
from openjiuwen.core.utils.llm.messages_chunk import AIMessageChunk

pytestmark = pytest.mark.asyncio

class TestMarkdownOutputParser:
    @pytest.fixture(autouse=True)
    def setUp(self):
        self.parser = MarkdownOutputParser()

    def assertEqual(self, actual, expect):
        assert actual == expect

    def assertIn(self, left, right):
        assert left in right


    async def test_parse_simple_markdown(self):
        """测试解析简单的Markdown内容"""
        markdown_text = """# Main Title

This is a paragraph with some **bold** text.

## Subtitle

Here's a code block:
```python
def hello():
    print("Hello, World!")
```

And a link: [OpenAI](https://openai.com)
"""
        result = await self.parser.parse(markdown_text)

        assert (isinstance(result, MarkdownContent))
        self.assertEqual(len(result.headers), 2)
        self.assertEqual(result.headers[0]["title"], "Main Title")
        self.assertEqual(result.headers[0]["level"], "1")
        self.assertEqual(result.headers[1]["title"], "Subtitle")
        self.assertEqual(result.headers[1]["level"], "2")

        self.assertEqual(len(result.code_blocks), 1)
        self.assertEqual(result.code_blocks[0]["language"], "python")
        self.assertIn("def hello():", result.code_blocks[0]["code"])

        self.assertEqual(len(result.links), 1)
        self.assertEqual(result.links[0]["text"], "OpenAI")
        self.assertEqual(result.links[0]["url"], "https://openai.com")

    async def test_parse_aimessage_markdown(self):
        """测试解析AIMessage对象中的Markdown"""
        ai_message = AIMessage(content="""## Analysis Results

The data shows:
- Item 1: Important finding
- Item 2: Another insight

![Chart](https://example.com/chart.png)
""")
        result = await self.parser.parse(ai_message)

        assert (isinstance(result, MarkdownContent))
        self.assertEqual(len(result.headers), 1)
        self.assertEqual(result.headers[0]["title"], "Analysis Results")

        self.assertEqual(len(result.images), 1)
        self.assertEqual(result.images[0]["alt"], "Chart")
        self.assertEqual(result.images[0]["url"], "https://example.com/chart.png")

        self.assertEqual(len(result.lists), 1)
        assert ("Item 1: Important finding" in result.lists[0])

    async def test_parse_code_blocks(self):
        """测试解析各种代码块"""
        markdown_text = """Here are some code examples:

```javascript
console.log("Hello");
```

```sql
SELECT * FROM users;
```

And inline code: `print("test")` in the text.
"""
        result = await self.parser.parse(markdown_text)

        self.assertEqual(len(result.code_blocks), 3)

        # JavaScript代码块
        js_block = next(block for block in result.code_blocks if block["language"] == "javascript")
        self.assertIn("console.log", js_block["code"])

        # SQL代码块
        sql_block = next(block for block in result.code_blocks if block["language"] == "sql")
        self.assertIn("SELECT", sql_block["code"])

        # 内联代码
        inline_block = next(block for block in result.code_blocks if block["language"] == "inline")
        self.assertEqual(inline_block["code"], 'print("test")')

    async def test_parse_tables(self):
        """测试解析表格"""
        markdown_text = """Here's a data table:

| Name | Age | City |
|------|-----|------|
| Alice | 30 | NYC |
| Bob | 25 | LA |

And another table:

| Product | Price |
|---------|-------|
| Apple | $1.00 |
"""
        result = await self.parser.parse(markdown_text)

        self.assertEqual(len(result.tables), 2)
        self.assertIn("Alice", result.tables[0])
        self.assertIn("Bob", result.tables[0])
        self.assertIn("Apple", result.tables[1])

    async def test_parse_lists(self):
        """测试解析列表"""
        markdown_text = """Shopping list:
- Milk
- Bread
- Eggs

Todo items:
1. Review code
2. Write tests
3. Deploy

Another list:
* Item A
* Item B
"""
        result = await self.parser.parse(markdown_text)

        self.assertEqual(len(result.lists), 3)

        # 无序列表
        unordered_list = result.lists[0]
        self.assertIn("Milk", unordered_list)
        self.assertIn("Bread", unordered_list)

        # 有序列表
        ordered_list = result.lists[1]
        self.assertIn("1. Review code", ordered_list)
        self.assertIn("2. Write tests", ordered_list)

        # 另一个无序列表
        another_list = result.lists[2]
        self.assertIn("Item A", another_list)

    async def test_parse_links_and_images(self):
        """测试解析链接和图片"""
        markdown_text = """Check out these resources:

[GitHub](https://github.com)
[Documentation](https://docs.example.com)

Here are some images:
![Logo](https://example.com/logo.png)
![Screenshot](https://example.com/screen.jpg)
"""
        result = await self.parser.parse(markdown_text)

        self.assertEqual(len(result.links), 2)
        self.assertEqual(result.links[0]["text"], "GitHub")
        self.assertEqual(result.links[0]["url"], "https://github.com")

        self.assertEqual(len(result.images), 2)
        self.assertEqual(result.images[0]["alt"], "Logo")
        self.assertEqual(result.images[0]["url"], "https://example.com/logo.png")

    async def test_parse_empty_content(self):
        """测试解析空内容"""
        result = await self.parser.parse("")
        assert (result is None)

        result = await self.parser.parse(None)
        assert (result is None)

    async def test_parse_plain_text(self):
        """测试解析纯文本（无Markdown元素）"""
        plain_text = "This is just plain text without any markdown formatting."
        result = await self.parser.parse(plain_text)

        assert isinstance(result, MarkdownContent)
        self.assertEqual(result.raw_content, plain_text)
        self.assertEqual(len(result.headers), 0)
        self.assertEqual(len(result.code_blocks), 0)
        self.assertEqual(len(result.links), 0)

    async def test_stream_parse_markdown_chunks(self):
        """测试流式解析Markdown块"""
        chunks = [
            "# Title\n\n",
            "This is a paragraph.\n\n",
            "```python\n",
            "print('hello')\n",
            "```\n\n",
            "[Link](https://example.com)"
        ]

        parsed_objects = []
        async for obj in self.parser.stream_parse(iter(chunks)):
            parsed_objects.append(obj)

        # 应该有多个中间结果，最后一个包含完整内容
        assert (len(parsed_objects) > 0)

        final_result = parsed_objects[-1]
        self.assertEqual(len(final_result.headers), 1)
        self.assertEqual(final_result.headers[0]["title"], "Title")
        self.assertEqual(len(final_result.code_blocks), 1)
        self.assertEqual(final_result.code_blocks[0]["language"], "python")
        self.assertEqual(len(final_result.links), 1)
        self.assertEqual(final_result.links[0]["text"], "Link")

    async def test_stream_parse_fragmented_markdown(self):
        """测试流式解析分片的Markdown"""
        chunks = [
            "## Sect",
            "ion Title\n\n",
            "Here's a co",
            "de block:\n```js\ncons",
            "ole.log('test');\n```\n\n",
            "And a [li",
            "nk](https://test.com)"
        ]

        parsed_objects = []
        async for obj in self.parser.stream_parse(iter(chunks)):
            parsed_objects.append(obj)

        final_result = parsed_objects[-1]
        self.assertEqual(len(final_result.headers), 1)
        self.assertEqual(final_result.headers[0]["title"], "Section Title")
        self.assertEqual(len(final_result.code_blocks), 1)
        self.assertEqual(final_result.code_blocks[0]["language"], "js")
        self.assertEqual(len(final_result.links), 1)

    async def test_stream_parse_aimessage_chunks(self):
        """测试流式解析AIMessageChunk"""
        chunks = [
            AIMessageChunk(content="# Report\n\n"),
            AIMessageChunk(content="## Summary\n"),
            AIMessageChunk(content="The analysis shows:\n"),
            AIMessageChunk(content="- Result 1\n- Result 2\n")
        ]

        parsed_objects = []
        async for obj in self.parser.stream_parse(iter(chunks)):
            parsed_objects.append(obj)

        final_result = parsed_objects[-1]
        self.assertEqual(len(final_result.headers), 2)
        self.assertEqual(final_result.headers[0]["title"], "Report")
        self.assertEqual(final_result.headers[1]["title"], "Summary")
        self.assertEqual(len(final_result.lists), 1)

        # 验证元素顺序
        assert (len(final_result.elements) > 0)
        # 第一个元素应该是 "Report" 标题
        self.assertEqual(final_result.elements[0].type, MarkdownElementType.HEADER)
        self.assertEqual(final_result.elements[0].content["title"], "Report")

    async def test_stream_parse_complex_markdown(self):
        """测试流式解析复杂Markdown"""
        chunks = [
            "# Main Title\n\n",
            "Introduction paragraph.\n\n",
            "## Code Examples\n\n",
            "```python\n",
            "def example():\n",
            "    return 'test'\n",
            "```\n\n",
            "## Links and Images\n\n",
            "Visit [our site](https://example.com)\n\n",
            "![Image](https://example.com/img.png)\n\n",
            "## Data Table\n\n",
            "| Col1 | Col2 |\n",
            "|------|------|\n",
            "| A    | B    |\n"
        ]

        parsed_objects = []
        async for obj in self.parser.stream_parse(iter(chunks)):
            parsed_objects.append(obj)

        final_result = parsed_objects[-1]

        # 验证所有元素都被正确解析
        self.assertEqual(len(final_result.headers), 4)  # Main Title + 3 sections
        self.assertEqual(len(final_result.code_blocks), 1)
        self.assertEqual(len(final_result.links), 1)
        self.assertEqual(len(final_result.images), 1)
        self.assertEqual(len(final_result.tables), 1)

        # 验证具体内容
        self.assertEqual(final_result.headers[0]["title"], "Main Title")
        self.assertEqual(final_result.code_blocks[0]["language"], "python")
        self.assertEqual(final_result.links[0]["url"], "https://example.com")
        self.assertEqual(final_result.images[0]["alt"], "Image")

    async def test_stream_parse_empty_chunks(self):
        """测试流式解析空块"""
        chunks = ["", None, ""]

        parsed_objects = []
        async for obj in self.parser.stream_parse(iter(chunks)):
            parsed_objects.append(obj)

        self.assertEqual(len(parsed_objects), 0)

    async def test_parse_mixed_headers(self):
        """测试解析不同级别的标题"""
        markdown_text = """# H1 Title
## H2 Title
### H3 Title
#### H4 Title
##### H5 Title
###### H6 Title
"""
        result = await self.parser.parse(markdown_text)

        self.assertEqual(len(result.headers), 6)
        for i, header in enumerate(result.headers, 1):
            self.assertEqual(header["level"], str(i))
            self.assertEqual(header["title"], f"H{i} Title")

    async def test_element_order_preservation(self):
        """测试元素顺序保持"""
        markdown_text = """# First Header

This is a paragraph.

[A Link](https://example.com)

## Second Header

```python
print("code")
```

![Image](https://example.com/img.png)

- List item 1
- List item 2
"""
        result = await self.parser.parse(markdown_text)

        # 验证元素按原文顺序排列
        assert (len(result.elements) > 0)

        # 验证顺序：Header -> Link -> Header -> Code -> Image -> List
        expected_order = [
            MarkdownElementType.HEADER,  # First Header
            MarkdownElementType.LINK,  # A Link
            MarkdownElementType.HEADER,  # Second Header
            MarkdownElementType.CODE_BLOCK,  # python code
            MarkdownElementType.IMAGE,  # Image
            MarkdownElementType.LIST  # List
        ]

        actual_order = [element.type for element in result.elements]
        self.assertEqual(actual_order, expected_order)

        # 验证位置信息
        for i in range(len(result.elements) - 1):
            assert (result.elements[i].start_pos < result.elements[i + 1].start_pos)

    async def test_get_elements_by_type(self):
        """测试按类型获取元素"""
        markdown_text = """# Title 1
## Title 2
[Link 1](url1)
[Link 2](url2)
"""
        result = await self.parser.parse(markdown_text)

        # 按类型筛选元素
        headers = [e for e in result.elements if e.type == MarkdownElementType.HEADER]
        links = [e for e in result.elements if e.type == MarkdownElementType.LINK]

        self.assertEqual(len(headers), 2)
        self.assertEqual(len(links), 2)

        # 验证内容
        self.assertEqual(headers[0].content["title"], "Title 1")
        self.assertEqual(headers[1].content["title"], "Title 2")
        self.assertEqual(links[0].content["text"], "Link 1")
        self.assertEqual(links[1].content["text"], "Link 2")
