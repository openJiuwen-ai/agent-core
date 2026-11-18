# SPDX-License-Identifier: Apache-2.0

from enum import Enum, IntEnum, StrEnum

import pytest
from jsonschema import Draft202012Validator

from openjiuwen.core.foundation.tool import tool
from openjiuwen.core.foundation.tool.utils.callable_schema_extractor import CallableSchemaExtractor


class TextChoice(Enum):
    FIRST = "first"
    SECOND = "second"


class StringChoice(StrEnum):
    FIRST = "first"
    SECOND = "second"


class Priority(IntEnum):
    LOW = 1
    HIGH = 2
    ALIAS = 1


class IntegerChoice(Enum):
    FIRST = 1
    SECOND = 2


class FloatChoice(Enum):
    FIRST = 1.5
    SECOND = 2.5


class NumberChoice(Enum):
    FIRST = 1
    SECOND = 2.5


class BooleanChoice(Enum):
    YES = True
    NO = False


class NullChoice(Enum):
    EMPTY = None


class MixedChoice(Enum):
    TEXT = "auto"
    NUMBER = 2
    EMPTY = None


@pytest.mark.parametrize(("enum_type", "expected_type"), [
    (TextChoice, "string"),
    (StringChoice, "string"),
    (Priority, "integer"),
    (IntegerChoice, "integer"),
    (FloatChoice, "number"),
    (NumberChoice, "number"),
    (BooleanChoice, "boolean"),
    (NullChoice, "null"),
    (MixedChoice, None),
])
def test_enum_schema_accepts_declared_values(enum_type: type[Enum], expected_type: str | None) -> None:
    schema = CallableSchemaExtractor.get_type_schema(enum_type)
    assert schema.get("type") == expected_type
    assert schema["enum"] == [member.value for member in enum_type]
    assert schema["description"] == "Enum values: " + ", ".join(str(member.value) for member in enum_type)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for member in enum_type:
        assert validator.is_valid(member.value)
    assert not validator.is_valid("undeclared")
    assert not validator.is_valid({"unexpected": 1})


@pytest.mark.parametrize("enum_type", [Priority, FloatChoice, BooleanChoice, MixedChoice])
def test_tool_card_enum_schema_accepts_real_parameter_values(enum_type: type[Enum]) -> None:
    def choose(value: enum_type):
        return value

    # 从公开装饰器入口观察最终工具卡片，避免只验证内部生成器。
    decorated = tool(choose)
    schema = decorated.card.input_params
    validator = Draft202012Validator(schema)
    for member in enum_type:
        assert validator.is_valid({"value": member.value})
    assert not validator.is_valid({"value": "undeclared"})
    assert not validator.is_valid({})


def test_string_enum_schema_remains_compatible() -> None:
    assert CallableSchemaExtractor.get_enum_schema(TextChoice) == {
        "type": "string",
        "enum": ["first", "second"],
        "description": "Enum values: first, second",
    }
