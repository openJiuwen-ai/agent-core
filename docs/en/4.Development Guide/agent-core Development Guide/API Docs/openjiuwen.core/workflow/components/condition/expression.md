# openjiuwen.core.workflow

## class openjiuwen.core.workflow.components.condition.expression.ExpressionCondition

```python
class openjiuwen.core.workflow.components.condition.expression.ExpressionCondition(expression: str)
```

Expression-based condition judgment implementation, supports using string expressions for complex condition judgment. Suitable for scenarios requiring combination of multiple conditions or complex logic judgment. Characterized by supporting rich expression syntax, suitable for complex condition combinations.

**Parameters**:

* **expression** (str): Condition expression string, expression length must satisfy `[0,5000]`, this expression follows the following rules:
  
  * Supported judgment types include: greater than, greater than or equal to, less than, less than or equal to, equal to, not equal to, contains, does not contain, empty, not empty, where equal to includes array length judgment.
  * Supported data types include: `string`, `number`, `integer`, `boolean`, `object`, `array`.
  * In scenarios requiring dynamic references, use placeholder syntax like `${start.query}` to build dynamic `bool` expressions, where `${` and `}` are placeholder start and end symbols, `start.query` is placeholder content, representing input fields or variables that need to be referenced.
  * In scenarios requiring logic judgment, use logic judgment symbols in the following table to build `bool` expressions:

| Type| Description | Example |
| :---: | :---: | :---:|
| `&&, and, \|\|, or` | AND, OR connection, connect multiple `bool` expressions (compound expressions, each expression needs parentheses)  |  `(${start.query} == "test_query") && ("query" not in ${start.var})`|
| `==, !=` | String comparison, whether two strings are the same or different | `${start.query} == "test_query"` |
| `in, not_in, not in` | String comparison, whether a string is in or not in another string |`"query" in ${start.var}` |
|`is_empty`| Judge string is empty, length is 0, or does not exist| `is_empty(${start.query})`|
|`is_not_empty`| Judge string is not empty| `is_not_empty(${start.query})`|
|`length, len, >=, >, <=, <`| String length comparison| `length(${start.query}) >= 1`|


**Exceptions**:

* **BaseError**: For detailed information and solutions, see [StatusCode](../../../common/exception/status_code.md).

**Examples**:

```python
>>> # 1. Import necessary classes
>>> from openjiuwen.core.workflow import ExpressionCondition
>>> 
>>> # 2. Create ExpressionCondition object
>>> # Example 1: String empty or not empty judgment
>>> condition1_1 = ExpressionCondition(expression="is_not_empty(${start.query})")
>>> condition1_2 = ExpressionCondition(expression="is_empty(${start.query})")
>>> 
>>> # Example 2: String equal or not equal
>>> condition2_1 = ExpressionCondition(expression="${start.query} != '1'")
>>> condition2_2 = ExpressionCondition(expression="${start.query} == '1'")
>>> 
>>> # Example 3: String length judgment
>>> condition3_1 = ExpressionCondition(expression="length(${custom_component.p_str}) > 1")
>>> condition3_2 = ExpressionCondition(expression="length(${custom_component.p_str}) == 1")
>>> condition3_3 = ExpressionCondition(expression="length(${custom_component.p_str}) != 1")
>>> condition3_4 = ExpressionCondition(expression="length(${custom_component.p_str}) < 1")
>>> 
>>> # Example 4: Number comparison
>>> condition4_1 = ExpressionCondition(expression="${custom_component.p_int} == 1")
>>> condition4_2 = ExpressionCondition(expression="${custom_component.p_int} > 1")
>>> condition4_3 = ExpressionCondition(expression="${custom_component.p_int} != 1")
>>> 
>>> # Example 5: boolean true or false judgment
>>> condition5_1 = ExpressionCondition(expression="${custom_component.p_true} == true")
>>> condition5_2 = ExpressionCondition(expression="${custom_component.p_true} == false")
>>> condition5_3 = ExpressionCondition(expression="${custom_component.p_true} != true")
>>> condition5_4 = ExpressionCondition(expression="${custom_component.p_true} != false")
>>> 
>>> # Example 6: object contains or does not contain
>>> # Note: For object, use ${custom_component.p_obj['key']} to access internal values
>>> condition6_1 = ExpressionCondition(expression="'a' in ${custom_component.p_obj}")
>>> condition6_2 = ExpressionCondition(expression="'a' not in ${custom_component.p_obj}")
>>> 
>>> # Example 7: array contains or does not contain
>>> # Note: For array, use ${custom_component.p_obj[index]} to access internal elements
>>> condition7_1 = ExpressionCondition(expression="'1' in ${custom_component.p_array}")
>>> 
>>> # Example 8: Multi-level nested checks
>>> condition8_1 = ExpressionCondition(expression="${start.p_multi_nested}['level1']['level2']['key3'] > 5")
>>> condition8_2 = ExpressionCondition(expression="(len(${start.p_complex}) > 1) && (${start.p_complex}[0]['subkey'] == 'subvalue')")
>>> condition8_3 = ExpressionCondition(expression="(${start.p_nested}['level1']['key1'] == 'value1') && ('key2' in ${start.p_nested}['level1'])")
>>> condition8_4 = ExpressionCondition(expression="(${start.p_int} > 1) && (${start.p_str} == '2') && (${start.p_array} not in ['3', '4']) && (length(${start.p_obj}) > 0)")
```
