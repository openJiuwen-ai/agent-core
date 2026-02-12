# openjiuwen.core.workflow

## class openjiuwen.core.workflow.components.condition.expression.ExpressionCondition

```python
class openjiuwen.core.workflow.components.condition.expression.ExpressionCondition(expression: str)
```

基于表达式的条件判断实现，支持使用字符串表达式进行复杂条件判断。适用于需要组合多个条件或进行复杂逻辑判断的场景。特点是支持丰富的表达式语法，适合复杂条件组合。

**参数**：

* **expression**(str)：条件表达式字符串，expression的长度必须满足`[0,5000]`，该表达式遵循以下规则：
  
  * 支持的判断类型包括：大于、大于或者等于、小于、小于或者等于、等于、不等于、包含、不包含、空、非空，其中等于包括数组长度的判断。
  * 支持的数据类型包括：`string`, `number`, `integer`, `boolean`, `object`, `array`。
  * 在需要动态引用的场景中，使用占位符语法如`${start.query}`来构建动态的`bool`表达式，其中`${`和`}`是占位符的起始和结束符号，`start.query`是占位符的内容，表示需要引用的输入字段或变量。
  * 在需要逻辑判断的场景中，使用以下表格中的逻辑判断符号来构建`bool`表达式：

| 类型| 描述 | 示例 |
| :---: | :---: | :---:|
| `&&, and, \|\|, or` |与、或连接，连接多个`bool`表达式（复合表达式，每个表达式需要加括号）  |  `(${start.query} == "test_query") && ("query" not in ${start.var})`|
| `==, !=` | 字符串比较，两个字符串是相同还是不同 | `${start.query} == "test_query"` |
| `in, not_in, not in` | 字符串比较，一个字符串是在还是不在另外一个字符串中 |`"query" in ${start.var}` |
|`is_empty`| 判断字符串为空，长度为0，或者不存在| `is_empty(${start.query})`|
|`is_not_empty`| 判断字符串不为空| `is_not_empty(${start.query})`|
|`length, len, >=, >, <=, <`| 字符串长度比较| `length(${start.query}) >= 1`|


**异常**：

* **BaseError**：具体详细信息和解决方法，参见[StatusCode](../../../common/exception/status_code.md)。

**样例**：

```python
>>> # 1. 导入必要的类
>>> from openjiuwen.core.workflow import ExpressionCondition
>>> 
>>> # 2. 创建ExpressionCondition对象
>>> # 示例1：字符串 空或者非空判断
>>> condition1_1 = ExpressionCondition(expression="is_not_empty(${start.query})")
>>> condition1_2 = ExpressionCondition(expression="is_empty(${start.query})")
>>> 
>>> # 示例2：字符串 等于不等于
>>> condition2_1 = ExpressionCondition(expression="${start.query} != '1'")
>>> condition2_2 = ExpressionCondition(expression="${start.query} == '1'")
>>> 
>>> # 示例3：字符串 长度判断
>>> condition3_1 = ExpressionCondition(expression="length(${custom_component.p_str}) > 1")
>>> condition3_2 = ExpressionCondition(expression="length(${custom_component.p_str}) == 1")
>>> condition3_3 = ExpressionCondition(expression="length(${custom_component.p_str}) != 1")
>>> condition3_4 = ExpressionCondition(expression="length(${custom_component.p_str}) < 1")
>>> 
>>> # 示例4：数字比较
>>> condition4_1 = ExpressionCondition(expression="${custom_component.p_int} == 1")
>>> condition4_2 = ExpressionCondition(expression="${custom_component.p_int} > 1")
>>> condition4_3 = ExpressionCondition(expression="${custom_component.p_int} != 1")
>>> 
>>> # 示例5：boolean true or false 判断
>>> condition5_1 = ExpressionCondition(expression="${custom_component.p_true} == true")
>>> condition5_2 = ExpressionCondition(expression="${custom_component.p_true} == false")
>>> condition5_3 = ExpressionCondition(expression="${custom_component.p_true} != true")
>>> condition5_4 = ExpressionCondition(expression="${custom_component.p_true} != false")
>>> 
>>> # 示例6：object 包含不包含
>>> # 注意：object取用内部值使用 ${custom_component.p_obj['key']} 的方式
>>> condition6_1 = ExpressionCondition(expression="'a' in ${custom_component.p_obj}")
>>> condition6_2 = ExpressionCondition(expression="'a' not in ${custom_component.p_obj}")
>>> 
>>> # 示例7：array包含不包含
>>> # 注意：array值取用内部元素使用 ${custom_component.p_obj[index]} 的方式
>>> condition7_1 = ExpressionCondition(expression="'1' in ${custom_component.p_array}")
>>> 
>>> # 示例8：多层嵌套检查
>>> condition8_1 = ExpressionCondition(expression="${start.p_multi_nested}['level1']['level2']['key3'] > 5")
>>> condition8_2 = ExpressionCondition(expression="(len(${start.p_complex}) > 1) && (${start.p_complex}[0]['subkey'] == 'subvalue')")
>>> condition8_3 = ExpressionCondition(expression="(${start.p_nested}['level1']['key1'] == 'value1') && ('key2' in ${start.p_nested}['level1'])")
>>> condition8_4 = ExpressionCondition(expression="(${start.p_int} > 1) && (${start.p_str} == '2') && (${start.p_array} not in ['3', '4']) && (length(${start.p_obj}) > 0)")
```
