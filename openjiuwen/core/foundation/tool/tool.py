# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Callable, overload, get_type_hints
from inspect import Parameter, signature

from pydantic import Field, create_model

from openjiuwen.core.foundation.tool.function.function import LocalFunction, ToolCard

def extract_params(func: Callable) -> dict:
    name = func.__name__
    description = func.__doc__ or f"Function {name} description."
    type_hints = get_type_hints(func)
    sig = signature(func)
    fields = {}
    for param_name, param in sig.parameters.items():
        param_type = type_hints.get(param_name)
        if not param_type:
            continue

        metadata = getattr(param_type, "__metadata__", None)
        field_info = None
        if metadata:
            for metadata_item in metadata:
                if isinstance(metadata_item, Field):
                    field_info = metadata_item
                    break

        field_kwargs = {}
        if field_info:
            for k, v in field_info.kwargs.items():
                if k == "metadata":
                    continue
                if v is not None:
                    field_kwargs[k] = v

        if param.default is not Parameter.empty:
            field_kwargs["default"] = param.default

        field_def = (param_type, param.default if param.default is not Parameter.empty else ...)
        fields[param_name] = field_def
    model_name = f"{name}_tool_input"
    tmp_model = create_model(model_name, **fields)

    parameters_schema = tmp_model.model_json_schema()
    return {
        "name": name,
        "description": description,
        "input_params": parameters_schema
    }


@overload
def tool(func: Callable) -> LocalFunction:
    pass


@overload
def tool(*, card: ToolCard) -> LocalFunction:
    pass


def tool(func: Callable = None, *, card: ToolCard = None) -> LocalFunction:
    if func:
        tmp_params = extract_params(func=func)
        return LocalFunction(card=ToolCard(**tmp_params), func=func)

    else:
        def decorator(func):
            return LocalFunction(card=card, func=func)

        return decorator
