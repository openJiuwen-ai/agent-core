#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Callable, Type, get_args, get_origin, Annotated, overload
from inspect import Parameter, signature
from pydantic_core import PydanticUndefined
from pydantic import BaseModel

from openjiuwen.core.utils.tool.param import Param
from openjiuwen.core.utils.tool.types import ValueTypeEnum
from openjiuwen.core.utils.tool.function.function import LocalFunction
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode


def extract_basic_info(param_annotation):
    """Extract description, default value, required from annotation type"""
    description = None
    default_value = Parameter.empty
    required = True

    if hasattr(param_annotation, "__metadata__"):
        metadata = param_annotation.__metadata__
    else:
        metadata = get_args(param_annotation)[1:] if get_args(param_annotation) else []

    for meta in metadata:
        if hasattr(meta, "description") and meta.description:
            description = meta.description
        if hasattr(meta, "default") and meta.default is not Parameter.empty and meta.default is not PydanticUndefined:
            default_value = meta.default
            required = False
        if hasattr(meta, "default_factory") and meta.default_factory:
            default_value = meta.default_factory()
            required = False
    return description, default_value, required


def extract_model_fields(param_annotation):
    """Extract BaseModel type"""
    params = []
    for field_name, field_info in param_annotation.model_fields.items():
        name = field_name
        description = field_info.description
        default_value = (
            field_info.default
            if (field_info.default is not PydanticUndefined and field_info.default is not Parameter.empty)
            else None
        )
        required = default_value is None and field_info.default_factory is None
        param_type, inner_params = extract_type(field_info.annotation)
        param = Param(
            name=name,
            description=description,
            param_type=param_type.value,
            default_value=default_value,
            required=required,
            schema=inner_params,
        )
        params.append(param)
    return ValueTypeEnum.OBJECT, params


def extract_type(param_annotation):
    origin = get_origin(param_annotation)
    args = get_args(param_annotation)

    if origin is Annotated:
        inner_type = args[0]
        return extract_type(inner_type)

    # BaseModel
    if isinstance(param_annotation, Type) and issubclass(param_annotation, BaseModel):
        return extract_model_fields(param_annotation)

    # basic type
    if param_annotation is str:
        return ValueTypeEnum.STRING, None
    if param_annotation is int:
        return ValueTypeEnum.INTEGER, None
    if param_annotation is float:
        return ValueTypeEnum.NUMBER, None
    if param_annotation is bool:
        return ValueTypeEnum.BOOLEAN, None
    if origin is list or origin is List:
        if not args:
            return ValueTypeEnum.ARRAY, None
        inner_type = args[0]
        inner_type, inner_params = extract_type(inner_type)
        if inner_type is ValueTypeEnum.STRING:
            return ValueTypeEnum.ARRAY_STRING, None
        if inner_type is ValueTypeEnum.INTEGER:
            return ValueTypeEnum.ARRAY_INTEGER, None
        if inner_type is ValueTypeEnum.NUMBER:
            return ValueTypeEnum.ARRAY_NUMBER, None
        if inner_type is ValueTypeEnum.BOOLEAN:
            return ValueTypeEnum.ARRAY_BOOLEAN, None
        if inner_type is ValueTypeEnum.OBJECT:
            return ValueTypeEnum.ARRAY_OBJECT, inner_params

    raise JiuWenBaseException(
        error_code=StatusCode.PLUGIN_PARAMS_CHECK_FAILED.code,
        message=StatusCode.PLUGIN_PARAMS_CHECK_FAILED.errmsg,
    )


def extract_params(func: Callable):
    sig = signature(func)
    params = []

    for name, param in sig.parameters.items():
        annotation = param.annotation
        if annotation is Parameter.empty:
            continue

        # Extract description, default_value, required from Annotated
        description, default_value, required = extract_basic_info(annotation)
        if default_value is Parameter.empty:
            default_value = param.default if param.default is not Parameter.empty else None

        if default_value is Parameter.empty or default_value is None:
            required = True
        else:
            required = False

        # Extract type from Annotated
        param_type, inner_params = extract_type(annotation)

        params.append(
            Param(
                name=name,
                description=description,
                param_type=param_type.value,
                default_value=default_value,
                required=required,
                schema=inner_params,
            )
        )
    return params


@overload
def tool(func: Callable) -> LocalFunction:
    pass


@overload
def tool(*, name: str = None, description: str = None, params: List[Param] = None) -> LocalFunction:
    pass


def tool(
    func: Callable = None, *, name: str = None, description: str = None, params: List[Param] = None
) -> LocalFunction:
    if func:
        tmp_params = extract_params(func=func)
        return LocalFunction(name=func.__name__, description=func.__doc__, params=tmp_params, func=func)

    else:

        def decorator(func):
            last_description = description or func.__doc__
            last_name = name or func.__name__
            return LocalFunction(name=last_name, description=last_description, params=params, func=func)

        return decorator
