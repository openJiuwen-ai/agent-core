# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from inspect import getdoc
from typing import Any, Callable, Dict, Optional, Type, Union, overload

from pydantic import BaseModel

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.function.function import LocalFunction, ToolCard
from openjiuwen.core.foundation.tool.utils.callable_schema_extractor import CallableSchemaExtractor


@overload
def tool(func: Callable) -> LocalFunction:
    ...


@overload
def tool(
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        input_params: Optional[Union[Dict[str, Any], Type[BaseModel]]] = None,
        card: Optional[ToolCard] = None,
        auto_extract: bool = True,
        stateless: bool = False
) -> Callable[[Callable], LocalFunction]:
    ...


def tool(
        func: Optional[Callable] = None,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        input_params: Optional[Union[Dict[str, Any], Type[BaseModel]]] = None,
        card: Optional[ToolCard] = None,
        auto_extract: bool = True,
        stateless: bool = False
) -> LocalFunction:
    """
    Universal decorator to convert functions into LocalFunction tools.

    Comprehensive usage patterns supported:

    1.  Basic automatic:
        @tool
        @tool()

    2.  Custom naming:
        @tool(name="custom_name")
        @tool(name="tool_v2")

    3.  Custom description:
        @tool(description="Enhanced description")
        @tool(description="Fetches data from API")

    4.  Custom schema:
        @tool(input_params=jsonschema_dict)
        @tool(input_params=PydanticModel)

    5.  Combined customization:
        @tool(name="search", description="Search tool", input_params={...})

    6.  Pre-built ToolCard:
        @tool(card=existing_card)

    7.  Override card:
        @tool(card=card, name="override")
        @tool(card=card, description="new description")

    8.  Disable auto-extraction:
        @tool(auto_extract=False)
        @tool(auto_extract=False, input_params=custom_schema)

    9.  Non-decorator usage:
        tool(existing_function)
        tool(existing_function, name="renamed")


    Args:
        func: Function to decorate (for direct @tool usage)
        name: Override function name
        description: Override function description/docstring
        input_params: Custom parameter schema (jsonschema dict or Pydantic model)
        card: Pre-constructed ToolCard
        auto_extract: Whether to auto-extract schema from signature
        stateless: Mark the tool as stateless (shared across agents under its
            bare id) instead of agent-owned. Defaults to False.

    Returns:
        LocalFunction instance or decorator function
    """

    def decorator(func_: Callable) -> LocalFunction:
        if card is not None:
            return _handle_prebuilt_card(func_,
                                         card,
                                         final_name=name,
                                         description=description,
                                         input_params=input_params,
                                         stateless=stateless)
        final_name = name if name is not None else func_.__name__
        return _create_new_tool_card(func_, final_name, description, input_params, auto_extract, stateless)

    if func is not None:
        return decorator(func)
    return decorator


def _handle_prebuilt_card(
        func: Callable,
        card: ToolCard,
        final_name: str,
        description: Optional[str],
        input_params: Optional[Union[Dict[str, Any], Type[BaseModel]]],
        stateless: bool = False,
) -> LocalFunction:
    """Handle case where a pre-built ToolCard is provided."""
    overrides = {}
    if final_name is not None and final_name != card.name:
        overrides['name'] = final_name
        if final_name != func.__name__:
            logger.warning(
                f"Overriding card name '{card.name}' with '{final_name}'"
            )

    if description is not None and description != card.description:
        overrides['description'] = description

    if input_params is not None and input_params != card.input_params:
        overrides['input_params'] = input_params

    if stateless and not card.stateless:
        overrides['stateless'] = True

    if overrides:
        new_card = ToolCard(
            name=overrides.get('name', card.name),
            description=overrides.get('description', card.description),
            input_params=overrides.get('input_params', card.input_params),
            stateless=overrides.get('stateless', card.stateless),
        )
        return LocalFunction(card=new_card, func=func)

    # Use card as-is
    return LocalFunction(card=card, func=func)


def _create_new_tool_card(
        func: Callable,
        final_name: str,
        description: Optional[str],
        input_params: Optional[Union[Dict[str, Any], Type[BaseModel]]],
        auto_extract: bool,
        stateless: bool = False,
) -> LocalFunction:
    """Create a new ToolCard from function and configuration."""
    final_description = _get_final_description(func, description, auto_extract)
    final_input_params = _get_final_input_params(func, input_params, auto_extract)
    new_card = ToolCard(
        name=final_name,
        description=final_description,
        input_params=final_input_params,
        stateless=stateless,
    )
    local_func = LocalFunction(card=new_card, func=func)
    return local_func


def _get_final_description(
        func: Callable,
        description: Optional[str],
        auto_extract: bool,
) -> str:
    """Get the final description for the tool."""
    # Priority 1: Explicit description from config
    if description is not None:
        return description

    # Priority 2: Auto-extract from function
    if auto_extract:
        extracted = CallableSchemaExtractor.extract_function_description(func)
        if extracted:
            return extracted

    # Priority 3: Function docstring
    docstring = getdoc(func)
    if docstring:
        return docstring

    # Priority 4: Fallback
    return f"Function {func.__name__}"


def _get_final_input_params(
        func: Callable,
        input_params: Optional[Union[Dict[str, Any], Type[BaseModel]]],
        auto_extract: bool,
) -> Union[Dict[str, Any], Type[BaseModel]]:
    """Get the final input parameters schema."""
    # Priority 1: Explicit input params from config
    if input_params is not None:
        return input_params

    # Priority 2: Auto-extract from function signature
    if auto_extract:
        try:
            return CallableSchemaExtractor.generate_schema(func)
        except Exception as e:
            logger.warning(
                f"Failed to auto-extract schema for {func.__name__}: {e}. "
                "Using empty schema."
            )

    # Priority 3: Empty schema as fallback
    return {"type": "object", "properties": {}}
