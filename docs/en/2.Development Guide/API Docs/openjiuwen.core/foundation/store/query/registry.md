# openjiuwen.core.foundation.store.query.registry

## func openjiuwen.core.foundation.store.query.registry.register_database_query_language

Register a database query language definition for converting query expressions to database-specific query formats.

```python
register_database_query_language(
    name: str,
    definition: QueryLanguageDefinition,
    force: bool = False
)
```

**Parameters**:

* **name**(str): Database name (e.g., "milvus", "chroma").
* **definition**(QueryLanguageDefinition): Query language definition object containing conversion functions for various query expressions.
* **force**(bool, optional): Whether to force overwrite an already registered query language definition. Default: `False`. If the database name is already registered and `force` is `False`, an exception will be raised.

**Example**:

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

# Define query language conversion functions for custom database
def my_db_comparison_filter(expr: ComparisonExpr) -> str:
    """Convert comparison expression to custom database format"""
    return f"{expr.field} {expr.operator} {expr.value}"

def my_db_range_filter(expr: RangeExpr) -> str:
    """Convert range expression to custom database format"""
    if expr.operator == "in":
        values = ",".join(str(v) for v in expr.value)
        return f"{expr.field} IN ({values})"
    raise NotImplementedError(f"Unsupported range operator: {expr.operator}")

def raise_not_implemented(msg: str):
    """Helper function: raise NotImplementedError"""
    raise NotImplementedError(msg)

# Create query language definition
my_db_def = QueryLanguageDefinition(
    comparison=my_db_comparison_filter,
    range=my_db_range_filter,
    arithmetic=lambda expr: raise_not_implemented("Arithmetic operations not supported"),  # Arithmetic operations not supported
    null=lambda expr: raise_not_implemented("Null checks not supported"),  # Null checks not supported
    json_filter=lambda expr: raise_not_implemented("JSON filtering not supported"),  # JSON filtering not supported
    array=lambda expr: raise_not_implemented("Array filtering not supported"),  # Array filtering not supported
    logical=lambda expr: raise_not_implemented("Logical operations not supported"),  # Logical operations not supported
    text_match=lambda expr: raise_not_implemented("Text matching not supported"),  # Text matching not supported
)

# Register query language
register_database_query_language("my_db", my_db_def)

# Now can use "my_db" as database name
from openjiuwen.core.foundation.store.query import eq
filter_expr = eq("category", "tech")
result = filter_expr.to_expr("my_db")  # Use custom conversion function
```

> **Note**: The framework has automatically registered query language definitions for "milvus" and "chroma", no manual registration is needed. This function should only be called when you need to support other databases or customize conversion logic.
