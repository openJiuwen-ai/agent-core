Prompt templates often contain fillable placeholders, which can refer to tool information, examples, variables, and other content. openJiuwen provides a prompt template class `PromptTemplate`, where `content` represents the template content and is required. Use double curly braces `{{...}}` or custom placeholders to mark content that needs to be filled later. In practice, replace these placeholders with specific content to format and ultimately generate a complete prompt.

# Instantiating the PromptTemplate class

The PromptTemplate class supports the following two types of input for the `content` parameter: `str` (string) and `List[BaseMessage]` (list of BaseMessage). Below are the instantiation methods for `content` of these two types.

- `str` type: Instantiate the PromptTemplate class with `content` as a string.

  ```python
  from openjiuwen.core.foundation.prompt.template import PromptTemplate
  template = PromptTemplate(content="You are a Q&A assistant proficient in the field of {{domain}}\nUser question: {{query}}")
  ```

- `List[BaseMessage]` type: Instantiate the PromptTemplate class with `content` as a list of BaseMessage.

  ```python
  from openjiuwen.core.foundation.prompt.template import PromptTemplate
  from openjiuwen.core.foundation.llm.schema.message import SystemMessage, UserMessage
  template = PromptTemplate(
      content=[
          SystemMessage(role='system', content='You are a Q&A assistant proficient in the field of {{domain}}'),
          UserMessage(role='user', content="User question: {{query}}")
      ]
  )
  ```

- Custom placeholders (using `str` type as an example):

  ```python
  from openjiuwen.core.foundation.prompt.template import PromptTemplate
  template2 = PromptTemplate(
     name="custom",
     content="You are a Q&A assistant proficient in ${domain}$ field! User question: ${query}$",
     placeholder_prefix="${",
     placeholder_suffix="}$",
  )
  ```

# Filling the PromptTemplate

After instantiating the PromptTemplate class, you can call the `.format` method to fill `content`. The content marked with `{{...}}` or custom placeholders will be correctly replaced with actual values, thereby generating complete input content. Example code:

```python
messages = template.format({"domain": "Mathematics", "query": "1+1=?"}).to_messages()
```
- The prompt template generated via the `str` type, after filling, results in:

  ```python
  [
      UserMessage(role='user', content='You are a Q&A assistant proficient in the field of Mathematics\nUser question: 1+1=?', name=None)
  ]
  ```

- The prompt template generated via the `List[BaseMessage]` type, after filling, results in:

  ```python
  [
      SystemMessage(role='system', content='You are a Q&A assistant proficient in the field of Mathematics', name=None),
      UserMessage(role='user', content='User question: 1+1=?', name=None)
  ]
  ```

- The prompt template generated via the `str` type with custom placeholders `${}$`, after filling, results in:

  ```python
  [
      UserMessage(role='user', content='You are a Q&A assistant proficient in Mathematics field! User question: 1+1=?', name=None)
  ]
  ```
