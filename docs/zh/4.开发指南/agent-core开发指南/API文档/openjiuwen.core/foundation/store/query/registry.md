# openjiuwen.core.foundation.store.query.registry

## func openjiuwen.core.foundation.store.query.registry.register_database_query_language

注册数据库查询语言定义，用于将查询表达式转换为特定数据库的查询格式。

```python
register_database_query_language(
    name: str,
    definition: QueryLanguageDefinition,
    force: bool = False
)
```

**参数**：

* **name**(str)：数据库名称（如 "milvus"、"chroma"）。
* **definition**(QueryLanguageDefinition)：查询语言定义对象，包含各种查询表达式的转换函数。
* **force**(bool, 可选)：是否强制覆盖已注册的查询语言定义。默认值：`False`，如果数据库名称已注册且 `force` 为 `False`，将抛出异常。

**样例**：

```python
from openjiuwen.core.foundation.store.query import (
    QueryLanguageDefinition,
    register_database_query_language,
    ComparisonExpr,
    RangeExpr,
    ArithmeticExpr,
    NullExpr,
    JSONExpr,
    ArrayExpr,
    LogicalExpr,
    MatchExpr,
)

# 定义自定义数据库的查询语言转换函数
def my_db_comparison_filter(expr: ComparisonExpr) -> str:
    """将比较表达式转换为自定义数据库格式"""
    return f"{expr.field} {expr.operator} {expr.value}"

def my_db_range_filter(expr: RangeExpr) -> str:
    """将范围表达式转换为自定义数据库格式"""
    if expr.operator == "in":
        values = ",".join(str(v) for v in expr.value)
        return f"{expr.field} IN ({values})"
    raise NotImplementedError(f"Unsupported range operator: {expr.operator}")

def raise_not_implemented(msg: str):
    """辅助函数：抛出 NotImplementedError"""
    raise NotImplementedError(msg)

# 创建查询语言定义
my_db_def = QueryLanguageDefinition(
    comparison=my_db_comparison_filter,
    range=my_db_range_filter,
    arithmetic=lambda expr: raise_not_implemented("Arithmetic operations not supported"),  # 不支持算术运算
    null=lambda expr: raise_not_implemented("Null checks not supported"),  # 不支持空值检查
    json_filter=lambda expr: raise_not_implemented("JSON filtering not supported"),  # 不支持 JSON 过滤
    array=lambda expr: raise_not_implemented("Array filtering not supported"),  # 不支持数组过滤
    logical=lambda expr: raise_not_implemented("Logical operations not supported"),  # 不支持逻辑运算
    text_match=lambda expr: raise_not_implemented("Text matching not supported"),  # 不支持文本匹配
)

# 注册查询语言
register_database_query_language("my_db", my_db_def)

# 现在可以使用 "my_db" 作为数据库名称
from openjiuwen.core.foundation.store.query import eq
filter_expr = eq("category", "tech")
result = filter_expr.to_expr("my_db")  # 使用自定义转换函数
```

> **说明**：框架已自动注册了 "milvus" 和 "chroma" 的查询语言定义，无需手动注册。只有在需要支持其他数据库或自定义转换逻辑时，才需要调用此函数。
